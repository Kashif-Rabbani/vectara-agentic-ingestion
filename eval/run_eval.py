#!/usr/bin/env python3
"""
Runs the graph-vs-vector retrieval experiment on the Neo4j movies dataset.

For each tier (100 / 1k / 9k movies), generates a DATA-DRIVEN question battery
with exact ground truth computed from the structured source data, then answers
every question two ways:

  A. Vector-only, TUNED  — Vectara query with neural reranker (limit 100) and
     generation (gpt-5 preset, generous max_used). This is a fair baseline,
     not the untuned defaults.
  B. Graph — a SPARQL query against the tier's named graph.

Scoring: recall of gold entities (title/name substring match, normalized) in
each answer. Count questions score 1 if the exact number appears.

Question classes:
  completeness  "List ALL movies released in <year>"        (top-k ≠ all)
  completeness  "List all movies featuring <actor>"
  aggregation   "How many movies did <director> direct?"
  aggregation   "How many movies were released in <year>?"
  ordering      "What is the oldest movie?"
  ordering      "Which movie has the highest IMDb rating?"
  multihop      "Which actors appeared in movies directed by <director>?"
  multihop      "Who both directed and acted in the same movie?"
  control       "Which movie is this: <plot>?"  (vector SHOULD win these)

Usage:
  python eval/run_eval.py                 # all tiers
  python eval/run_eval.py --tiers 100 1k
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("VECTARA_BASE_URL", "https://api.vectara.io/v2")
API_KEY = os.getenv("VECTARA_API_KEY")
SPARQL_BASE = os.getenv("SPARQL_ENDPOINT", "http://localhost:3030/ds")
SPARQL_AUTH = (os.getenv("SPARQL_USERNAME", "admin"), os.getenv("SPARQL_PASSWORD", ""))

HERE = os.path.dirname(__file__)
DATA = os.path.join(HERE, "data", "movies_full.json")
TIERS_FILE = os.path.join(HERE, "data", "tiers.json")
RESULTS_DIR = os.path.join(HERE, "results")

GEN_PRESET = "vectara-summary-ext-25-09-gpt-5"
SEED = 42


# ── normalization / scoring ────────────────────────────────────────────────

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def entity_recall(gold: list[str], answer: str) -> tuple[float, list[str]]:
    """Fraction of gold entities whose normalized name appears in the answer."""
    ans = norm(answer)
    missing = [g for g in gold if norm(g) not in ans]
    hit = len(gold) - len(missing)
    return (hit / len(gold) if gold else 0.0), missing


def count_correct(gold_count: int, answer: str) -> bool:
    return bool(re.search(rf"\b{gold_count}\b", answer))


# ── question battery (data-driven, seeded) ──────────────────────────────────

def build_battery(tier_movies, acted_by_movie, directed_by_movie, people_by_id, graph_uri):
    """Build questions whose gold answers are computed from the structured data."""
    q = []
    mids = {m["movieId"] for m in tier_movies}
    by_id = {m["movieId"]: m for m in tier_movies}

    # actor -> movies, director -> movies (within tier)
    actor_movies, director_movies = {}, {}
    for mid in mids:
        for pid, _ in acted_by_movie.get(mid, []):
            if pid in people_by_id:
                actor_movies.setdefault(pid, set()).add(mid)
        for pid in directed_by_movie.get(mid, []):
            if pid in people_by_id:
                director_movies.setdefault(pid, set()).add(mid)

    year_counts = Counter(m["year"] for m in tier_movies)

    def name(pid):
        return people_by_id[pid]["name"]

    def titles(ms):
        return sorted(by_id[m]["title"] for m in ms)

    # C1 — completeness by year: pick the year with count closest to 8 (>=4)
    year = min((y for y, c in year_counts.items() if c >= 4),
               key=lambda y: abs(year_counts[y] - 8), default=None)
    if year:
        gold = sorted(m["title"] for m in tier_movies if m["year"] == year)
        q.append({
            "id": "C1", "class": "completeness",
            "question": f"List the titles of ALL movies in the dataset released in {year}.",
            "gold": gold,
            "sparql": f'PREFIX schema: <https://schema.org/> PREFIX ev: <http://vectara-eval/vocab#> '
                      f'SELECT ?t WHERE {{ GRAPH <{graph_uri}> {{ ?m a schema:Movie ; schema:name ?t ; ev:year {year} }} }} ORDER BY ?t',
        })
        # A2 — count for same year
        q.append({
            "id": "A2", "class": "aggregation",
            "question": f"How many movies in the dataset were released in {year}?",
            "gold_count": len(gold),
            "sparql": f'PREFIX schema: <https://schema.org/> PREFIX ev: <http://vectara-eval/vocab#> '
                      f'SELECT (COUNT(?m) AS ?n) WHERE {{ GRAPH <{graph_uri}> {{ ?m a schema:Movie ; ev:year {year} }} }}',
        })

    # C2 — completeness by actor: busiest actor (ties broken by name for determinism)
    if actor_movies:
        top_actor = max(actor_movies, key=lambda p: (len(actor_movies[p]), name(p)))
        gold = titles(actor_movies[top_actor])
        if len(gold) >= 3:
            q.append({
                "id": "C2", "class": "completeness",
                "question": f"List all movies in the dataset featuring {name(top_actor)} as an actor.",
                "gold": gold,
                "sparql": f'PREFIX schema: <https://schema.org/> '
                          f'SELECT ?t WHERE {{ GRAPH <{graph_uri}> {{ ?m schema:actor ?p ; schema:name ?t . ?p schema:name "{name(top_actor)}" }} }} ORDER BY ?t',
            })

    # A1 — count by director: busiest director
    if director_movies:
        top_dir = max(director_movies, key=lambda p: (len(director_movies[p]), name(p)))
        if len(director_movies[top_dir]) >= 2:
            q.append({
                "id": "A1", "class": "aggregation",
                "question": f"How many movies in the dataset did {name(top_dir)} direct?",
                "gold_count": len(director_movies[top_dir]),
                "sparql": f'PREFIX schema: <https://schema.org/> '
                          f'SELECT (COUNT(?m) AS ?n) WHERE {{ GRAPH <{graph_uri}> {{ ?m schema:director ?p . ?p schema:name "{name(top_dir)}" }} }}',
            })
            # M1 — multihop join over same director
            actors = set()
            for mid in director_movies[top_dir]:
                actors.update(name(pid) for pid, _ in acted_by_movie.get(mid, []) if pid in people_by_id)
            if actors:
                q.append({
                    "id": "M1", "class": "multihop",
                    "question": f"Which actors appeared in movies directed by {name(top_dir)}? List their names.",
                    "gold": sorted(actors),
                    "sparql": f'PREFIX schema: <https://schema.org/> '
                              f'SELECT DISTINCT ?an WHERE {{ GRAPH <{graph_uri}> {{ ?m schema:director ?d ; schema:actor ?a . ?d schema:name "{name(top_dir)}" . ?a schema:name ?an }} }} ORDER BY ?an',
                })

    # O1 — oldest movie(s)
    min_year = min(m["year"] for m in tier_movies)
    gold = sorted(m["title"] for m in tier_movies if m["year"] == min_year)
    q.append({
        "id": "O1", "class": "ordering",
        "question": "What is the oldest movie in the dataset, and what year is it from?",
        "gold": gold,
        "sparql": f'PREFIX schema: <https://schema.org/> PREFIX ev: <http://vectara-eval/vocab#> '
                  f'SELECT ?t ?y WHERE {{ GRAPH <{graph_uri}> {{ ?m a schema:Movie ; schema:name ?t ; ev:year ?y }} }} ORDER BY ?y LIMIT {max(1, len(gold))}',
    })

    # O2 — highest rated
    rated = [m for m in tier_movies if m.get("imdbRating") is not None]
    if rated:
        max_r = max(float(m["imdbRating"]) for m in rated)
        gold = sorted(m["title"] for m in rated if float(m["imdbRating"]) == max_r)
        q.append({
            "id": "O2", "class": "ordering",
            "question": "Which movie in the dataset has the highest IMDb rating?",
            "gold": gold,
            "sparql": f'PREFIX schema: <https://schema.org/> PREFIX ev: <http://vectara-eval/vocab#> '
                      f'SELECT ?t WHERE {{ GRAPH <{graph_uri}> {{ ?m a schema:Movie ; schema:name ?t ; ev:imdbRating ?r }} }} ORDER BY DESC(?r) LIMIT {len(gold)}',
        })

    # M2 — self-join: directed AND acted in the same movie
    both = set()
    for mid in mids:
        actors_here = {pid for pid, _ in acted_by_movie.get(mid, [])}
        for pid in directed_by_movie.get(mid, []):
            if pid in actors_here and pid in people_by_id:
                both.add(name(pid))
    if both:
        q.append({
            "id": "M2", "class": "multihop",
            "question": "Which people both directed and acted in the same movie in the dataset? Name them.",
            "gold": sorted(both),
            "sparql": f'PREFIX schema: <https://schema.org/> '
                      f'SELECT DISTINCT ?n WHERE {{ GRAPH <{graph_uri}> {{ ?m schema:director ?p ; schema:actor ?p . ?p schema:name ?n }} }} ORDER BY ?n',
        })

    # V1/V2 — controls: plot-similarity questions vector search should win
    import random as _random
    rng = _random.Random(SEED)
    ctrl = rng.sample(tier_movies, min(2, len(tier_movies)))
    for i, m in enumerate(ctrl, 1):
        q.append({
            "id": f"V{i}", "class": "control",
            "question": f"Which movie matches this description: {m['plot']}",
            "gold": [m["title"]],
            "sparql": None,  # not a graph-shaped question — that's the point
        })

    return q


# ── the two answer paths ────────────────────────────────────────────────────

def vector_answer(corpus_key: str, question: str) -> tuple[str, int]:
    r = httpx.post(
        f"{BASE_URL}/corpora/{corpus_key}/query",
        headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
        json={
            "query": question,
            "search": {
                "limit": 100,
                "lexical_interpolation": 0.05,
                "context_configuration": {"sentences_before": 2, "sentences_after": 2},
                "reranker": {
                    "type": "customer_reranker",
                    "reranker_name": "Rerank_Multilingual_v1",
                    "limit": 100,
                },
            },
            "generation": {
                "generation_preset_name": GEN_PRESET,
                "max_used_search_results": 25,
                "response_language": "eng",
            },
        },
        timeout=120,
    )
    if r.status_code != 200:
        return f"HTTP {r.status_code}: {r.text[:200]}", 0
    body = r.json()
    return body.get("summary") or "", len(body.get("search_results", []))


def sparql_answer(query: str) -> list[str]:
    r = httpx.post(
        f"{SPARQL_BASE}/query",
        content=query.encode(),
        headers={"Content-Type": "application/sparql-query",
                 "Accept": "application/sparql-results+json"},
        auth=SPARQL_AUTH,
        timeout=60,
    )
    r.raise_for_status()
    out = []
    for b in r.json()["results"]["bindings"]:
        out.append(" ".join(v["value"] for v in b.values()))
    return out


# ── main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tiers", nargs="+", default=["100", "1k", "9k"])
    args = parser.parse_args()

    with open(DATA) as f:
        data = json.load(f)
    with open(TIERS_FILE) as f:
        tier_ids = json.load(f)

    people_by_id = {p["tmdbId"]: p for p in data["people"]}
    by_id = {m["movieId"]: m for m in data["movies"]}
    acted_by_movie, directed_by_movie = {}, {}
    for pid, mid, role in data["acted_in"]:
        acted_by_movie.setdefault(mid, []).append((pid, role))
    for pid, mid in data["directed"]:
        directed_by_movie.setdefault(mid, []).append(pid)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    all_results = {}

    for tier in args.tiers:
        corpus_key = f"movies_eval_{tier}"
        graph_uri = f"http://vectara-eval/graph/movies-{tier}"
        tier_movies = [by_id[mid] for mid in tier_ids[tier]]
        print(f"\n{'='*70}\n  TIER {tier} — {len(tier_movies)} movies\n{'='*70}")

        battery = build_battery(tier_movies, acted_by_movie, directed_by_movie,
                                people_by_id, graph_uri)
        results = []

        for item in battery:
            print(f"\n[{item['id']} {item['class']}] {item['question'][:90]}")

            vec_text, n_results = vector_answer(corpus_key, item["question"])

            if "gold_count" in item:
                vec_score = 1.0 if count_correct(item["gold_count"], vec_text) else 0.0
                missing = [] if vec_score else [f"expected count {item['gold_count']}"]
            else:
                vec_score, missing = entity_recall(item["gold"], vec_text)

            graph_score = None
            if item.get("sparql"):
                rows = sparql_answer(item["sparql"])
                if "gold_count" in item:
                    graph_score = 1.0 if rows and str(item["gold_count"]) in rows[0] else 0.0
                else:
                    graph_score, g_missing = entity_recall(item["gold"], " | ".join(rows))

            gold_n = item.get("gold_count", len(item.get("gold", [])))
            print(f"  gold size : {gold_n}")
            print(f"  vector    : {vec_score:.2f}" + (f"  (missing {len(missing)})" if missing else ""))
            print(f"  graph     : {'—' if graph_score is None else f'{graph_score:.2f}'}")

            results.append({
                "id": item["id"], "class": item["class"], "question": item["question"],
                "gold": item.get("gold"), "gold_count": item.get("gold_count"),
                "vector_score": round(vec_score, 3),
                "graph_score": graph_score if graph_score is None else round(graph_score, 3),
                "vector_answer": vec_text,
                "vector_missing": missing[:20],
            })

        all_results[tier] = results
        with open(os.path.join(RESULTS_DIR, f"results_{tier}.json"), "w") as f:
            json.dump(results, f, indent=2)

    # ── summary table ────────────────────────────────────────────────────────
    print(f"\n\n{'='*70}\n  SUMMARY — mean score by class and tier\n{'='*70}")
    classes = ["completeness", "aggregation", "ordering", "multihop", "control"]
    header = f"{'class':<15}" + "".join(f"{'T'+t+' vec':>10}{'T'+t+' kg':>9}" for t in args.tiers)
    print(header)
    for cls in classes:
        row = f"{cls:<15}"
        for tier in args.tiers:
            rs = [r for r in all_results[tier] if r["class"] == cls]
            if rs:
                v = sum(r["vector_score"] for r in rs) / len(rs)
                gs = [r["graph_score"] for r in rs if r["graph_score"] is not None]
                g = sum(gs) / len(gs) if gs else None
                row += f"{v:>10.2f}" + (f"{g:>9.2f}" if g is not None else f"{'—':>9}")
            else:
                row += f"{'—':>10}{'—':>9}"
        print(row)

    with open(os.path.join(RESULTS_DIR, "all_results.json"), "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nFull answers saved → {RESULTS_DIR}/")


if __name__ == "__main__":
    main()
