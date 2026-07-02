#!/usr/bin/env python3
"""
Extracts the Neo4j 'recommendations' movie dataset (MovieLens + TMDb/OMDb,
the dataset used across Neo4j GraphAcademy) from Neo4j's public read-only
demo server into a local JSON file.

Output: eval/data/movies_full.json
  {
    "movies":   [{movieId, title, year, plot, imdbRating, imdbVotes, released,
                  runtime, countries, languages, genres: [...]}, ...],
    "people":   [{tmdbId, name, born, bornIn, died, bio?}, ...],
    "acted_in": [[person_tmdbId, movieId, role], ...],
    "directed": [[person_tmdbId, movieId], ...]
  }

Usage:  python eval/extract_neo4j.py
"""
import json
import os
import sys

import httpx

DEMO_URL = "https://demo.neo4jlabs.com:7473/db/recommendations/tx/commit"
AUTH = ("recommendations", "recommendations")
OUT = os.path.join(os.path.dirname(__file__), "data", "movies_full.json")

BATCH = 1000


def cypher(client: httpx.Client, statement: str, params: dict | None = None) -> list[list]:
    r = client.post(
        DEMO_URL,
        auth=AUTH,
        json={"statements": [{"statement": statement, "parameters": params or {}}]},
        timeout=120,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise RuntimeError(body["errors"])
    return [row["row"] for row in body["results"][0]["data"]]


def paginate(client: httpx.Client, statement: str) -> list[list]:
    """Run a query with SKIP/LIMIT pagination until exhausted."""
    rows: list[list] = []
    skip = 0
    while True:
        batch = cypher(client, f"{statement} SKIP $skip LIMIT $limit",
                       {"skip": skip, "limit": BATCH})
        rows.extend(batch)
        print(f"    {len(rows)} rows...", end="\r")
        if len(batch) < BATCH:
            print()
            return rows
        skip += BATCH


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with httpx.Client() as client:
        print("[1/4] Movies (with plot + year only)...")
        movie_rows = paginate(client, """
            MATCH (m:Movie)
            WHERE m.plot IS NOT NULL AND m.year IS NOT NULL AND m.title IS NOT NULL
            OPTIONAL MATCH (m)-[:IN_GENRE]->(g:Genre)
            WITH m, collect(g.name) AS genres
            RETURN m.movieId, m.title, m.year, m.plot, m.imdbRating, m.imdbVotes,
                   m.released, m.runtime, m.countries, m.languages, genres
            ORDER BY m.movieId
        """)
        movies = [
            {
                "movieId": r[0], "title": r[1], "year": int(r[2]), "plot": r[3],
                "imdbRating": r[4], "imdbVotes": r[5], "released": r[6],
                "runtime": r[7], "countries": r[8], "languages": r[9], "genres": r[10],
            }
            for r in movie_rows
        ]
        movie_ids = {m["movieId"] for m in movies}
        print(f"  {len(movies)} movies")

        print("[2/4] People...")
        people_rows = paginate(client, """
            MATCH (p:Person)
            WHERE (p)-[:ACTED_IN|DIRECTED]->(:Movie)
            RETURN p.tmdbId, p.name, p.born, p.bornIn, p.died
            ORDER BY p.tmdbId
        """)
        people = [
            {"tmdbId": r[0], "name": r[1], "born": r[2], "bornIn": r[3], "died": r[4]}
            for r in people_rows if r[1]
        ]
        print(f"  {len(people)} people")

        print("[3/4] ACTED_IN edges...")
        acted_rows = paginate(client, """
            MATCH (p:Person)-[r:ACTED_IN]->(m:Movie)
            RETURN p.tmdbId, m.movieId, r.role
            ORDER BY m.movieId, p.tmdbId
        """)
        acted = [r for r in acted_rows if r[1] in movie_ids]
        print(f"  {len(acted)} edges (filtered to kept movies)")

        print("[4/4] DIRECTED edges...")
        directed_rows = paginate(client, """
            MATCH (p:Person)-[r:DIRECTED]->(m:Movie)
            RETURN p.tmdbId, m.movieId
            ORDER BY m.movieId, p.tmdbId
        """)
        directed = [r for r in directed_rows if r[1] in movie_ids]
        print(f"  {len(directed)} edges (filtered to kept movies)")

    data = {"movies": movies, "people": people, "acted_in": acted, "directed": directed}
    with open(OUT, "w") as f:
        json.dump(data, f)
    print(f"\nSaved → {OUT} ({os.path.getsize(OUT) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
