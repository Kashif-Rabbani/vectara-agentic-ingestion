#!/usr/bin/env python3
"""
Builds the three eval tiers from the extracted Neo4j movie dataset and
dual-ingests each tier:
  - a Vectara corpus per tier   (movies_eval_100 / _1k / _9k)
  - a named graph per tier in Fuseki (http://vectara-eval/graph/movies-<tier>)

Tiers are NESTED and SEEDED (tier100 ⊂ tier1k ⊂ tier9k) so results are
reproducible and comparable across scales.

Prerequisites:
  python eval/extract_neo4j.py       (produces eval/data/movies_full.json)
  Fuseki running (./setup_fuseki.sh), .env configured

Usage:
  python eval/load_tiers.py                # all tiers
  python eval/load_tiers.py --tiers 100 1k # subset
"""
import argparse
import json
import os
import random
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from dotenv import load_dotenv
from rdflib import Graph, Literal, Namespace, RDF, URIRef
from rdflib.namespace import XSD

load_dotenv()

BASE_URL = os.getenv("VECTARA_BASE_URL", "https://api.vectara.io/v2")
API_KEY = os.getenv("VECTARA_API_KEY")
SPARQL_BASE = os.getenv("SPARQL_ENDPOINT", "http://localhost:3030/ds")
SPARQL_AUTH = (os.getenv("SPARQL_USERNAME", "admin"), os.getenv("SPARQL_PASSWORD", ""))

DATA = os.path.join(os.path.dirname(__file__), "data", "movies_full.json")
TIERS_OUT = os.path.join(os.path.dirname(__file__), "data", "tiers.json")

SEED = 42
TIER_SIZES = {"100": 100, "1k": 1000, "9k": None}  # None = all

SCHEMA = Namespace("https://schema.org/")
EV = Namespace("http://vectara-eval/vocab#")
ENT = "http://vectara-eval/entity"

_local = threading.local()


def client() -> httpx.Client:
    if not hasattr(_local, "c"):
        _local.c = httpx.Client(timeout=60)
    return _local.c


# ── tier construction ──────────────────────────────────────────────────────

def build_tiers(movies: list[dict]) -> dict[str, list[dict]]:
    rng = random.Random(SEED)
    shuffled = movies[:]
    rng.shuffle(shuffled)
    tiers = {}
    for name, size in TIER_SIZES.items():
        tiers[name] = shuffled if size is None else shuffled[:size]
    return tiers


# ── document text ───────────────────────────────────────────────────────────

def movie_doc_text(m: dict, cast: list[tuple[str, str | None]], directors: list[str]) -> str:
    lines = [f"{m['title']} ({m['year']})"]
    if m.get("genres"):
        lines.append(f"Genres: {', '.join(m['genres'])}")
    meta_bits = []
    if m.get("released"):
        meta_bits.append(f"Released: {m['released']}")
    if m.get("runtime"):
        meta_bits.append(f"Runtime: {m['runtime']} min")
    if m.get("imdbRating") is not None:
        meta_bits.append(f"IMDb rating: {m['imdbRating']} ({m.get('imdbVotes') or '?'} votes)")
    if meta_bits:
        lines.append(" | ".join(meta_bits))
    if m.get("countries"):
        c = m["countries"]
        lines.append(f"Countries: {', '.join(c) if isinstance(c, list) else c}")
    lines.append("")
    lines.append(f"Plot: {m['plot']}")
    lines.append("")
    if directors:
        lines.append(f"Directed by: {', '.join(directors)}")
    if cast:
        cast_strs = [f"{n} as {r}" if r else n for n, r in cast[:15]]
        lines.append(f"Cast: {'; '.join(cast_strs)}")
    return "\n".join(lines)


# ── Vectara side ────────────────────────────────────────────────────────────

def create_corpus(key: str):
    r = httpx.post(
        f"{BASE_URL}/corpora",
        headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
        json={
            "key": key,
            "name": key,
            "description": "Neo4j movies dataset eval tier — graph-vs-vector retrieval experiment",
            "filter_attributes": [
                {"name": "year", "level": "document", "type": "integer", "indexed": True},
                {"name": "type", "level": "document", "type": "text", "indexed": True},
            ],
        },
        timeout=30,
    )
    if r.status_code == 201:
        print(f"  corpus {key} created")
    elif r.status_code in (400, 409) and "already exists" in r.text.lower():
        print(f"  corpus {key} exists — reusing")
    else:
        print(f"  ERROR creating corpus {key} ({r.status_code}): {r.text[:200]}")
        sys.exit(1)


def index_doc(corpus_key: str, doc: dict) -> str:
    r = client().post(
        f"{BASE_URL}/corpora/{corpus_key}/documents",
        headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
        json=doc,
    )
    if r.status_code == 201:
        return "ok"
    if r.status_code == 409:
        return "skip"  # already indexed (idempotent rerun)
    return f"ERR {r.status_code}: {r.text[:120]}"


def index_tier(corpus_key: str, docs: list[dict]):
    ok = skip = err = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(index_doc, corpus_key, d): d["id"] for d in docs}
        for i, fut in enumerate(as_completed(futures), 1):
            res = fut.result()
            if res == "ok":
                ok += 1
            elif res == "skip":
                skip += 1
            else:
                err += 1
                if err <= 3:
                    print(f"\n    {futures[fut]}: {res}")
            if i % 200 == 0 or i == len(docs):
                print(f"    {i}/{len(docs)} (ok={ok} skip={skip} err={err})", end="\r")
    print()
    return ok, skip, err


# ── RDF / Fuseki side ───────────────────────────────────────────────────────

def build_rdf(tier_movies: list[dict], acted_by_movie: dict, directed_by_movie: dict,
              people_by_id: dict) -> Graph:
    g = Graph()
    g.bind("schema", SCHEMA)
    g.bind("ev", EV)
    seen_people = set()

    def person_uri(pid):
        return URIRef(f"{ENT}/person/{pid}")

    for m in tier_movies:
        mid = m["movieId"]
        mu = URIRef(f"{ENT}/movie/{mid}")
        g.add((mu, RDF.type, SCHEMA.Movie))
        g.add((mu, SCHEMA.name, Literal(m["title"])))
        g.add((mu, EV.year, Literal(m["year"], datatype=XSD.integer)))
        if m.get("imdbRating") is not None:
            g.add((mu, EV.imdbRating, Literal(float(m["imdbRating"]), datatype=XSD.decimal)))
        for genre in m.get("genres") or []:
            g.add((mu, SCHEMA.genre, Literal(genre)))
        for pid, _role in acted_by_movie.get(mid, []):
            if pid in people_by_id:
                g.add((mu, SCHEMA.actor, person_uri(pid)))
                seen_people.add(pid)
        for pid in directed_by_movie.get(mid, []):
            if pid in people_by_id:
                g.add((mu, SCHEMA.director, person_uri(pid)))
                seen_people.add(pid)

    for pid in seen_people:
        pu = person_uri(pid)
        g.add((pu, RDF.type, SCHEMA.Person))
        g.add((pu, SCHEMA.name, Literal(people_by_id[pid]["name"])))

    return g


def load_graph(graph_uri: str, g: Graph):
    nt = g.serialize(format="nt")
    r = httpx.put(
        f"{SPARQL_BASE}/data",
        params={"graph": graph_uri},
        content=nt.encode(),
        headers={"Content-Type": "application/n-triples"},
        auth=SPARQL_AUTH,
        timeout=120,
    )
    if r.status_code not in (200, 201, 204):
        print(f"  ERROR loading graph ({r.status_code}): {r.text[:200]}")
        sys.exit(1)
    print(f"  graph {graph_uri} loaded ({len(g)} triples)")


# ── main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tiers", nargs="+", default=list(TIER_SIZES.keys()),
                        choices=list(TIER_SIZES.keys()))
    parser.add_argument("--skip-vectara", action="store_true")
    parser.add_argument("--skip-kg", action="store_true")
    args = parser.parse_args()

    with open(DATA) as f:
        data = json.load(f)

    movies = data["movies"]
    people_by_id = {p["tmdbId"]: p for p in data["people"]}
    acted_by_movie: dict = {}
    for pid, mid, role in data["acted_in"]:
        acted_by_movie.setdefault(mid, []).append((pid, role))
    directed_by_movie: dict = {}
    for pid, mid in data["directed"]:
        directed_by_movie.setdefault(mid, []).append(pid)

    tiers = build_tiers(movies)
    with open(TIERS_OUT, "w") as f:
        json.dump({name: [m["movieId"] for m in tier] for name, tier in tiers.items()}, f)
    print(f"Tier definitions saved → {TIERS_OUT}")

    for name in args.tiers:
        tier = tiers[name]
        corpus_key = f"movies_eval_{name}"
        graph_uri = f"http://vectara-eval/graph/movies-{name}"
        print(f"\n=== Tier {name}: {len(tier)} movies ===")

        if not args.skip_kg:
            g = build_rdf(tier, acted_by_movie, directed_by_movie, people_by_id)
            load_graph(graph_uri, g)

        if not args.skip_vectara:
            create_corpus(corpus_key)
            docs = []
            for m in tier:
                cast = [(people_by_id[pid]["name"], role)
                        for pid, role in acted_by_movie.get(m["movieId"], [])
                        if pid in people_by_id]
                directors = [people_by_id[pid]["name"]
                             for pid in directed_by_movie.get(m["movieId"], [])
                             if pid in people_by_id]
                docs.append({
                    "id": f"movie-{m['movieId']}",
                    "type": "structured",
                    "title": m["title"],
                    "sections": [{"text": movie_doc_text(m, cast, directors)}],
                    "metadata": {"type": "movie", "year": m["year"], "title": m["title"]},
                })
            ok, skip, err = index_tier(corpus_key, docs)
            print(f"  indexed: ok={ok} skip={skip} err={err}")

    print("\nDone. Next: python eval/run_eval.py")


if __name__ == "__main__":
    main()
