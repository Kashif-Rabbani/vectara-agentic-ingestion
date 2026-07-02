#!/usr/bin/env python3
"""
Backfills `rating` into document metadata for the eval corpora, so the
metadata-baseline arm can UDF-sort by rating. (year/type/title were stored
at ingest; rating wasn't.)

Usage: python eval/backfill_rating.py [--tiers 100 1k 9k]
"""
import argparse
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from dotenv import load_dotenv

load_dotenv()
BASE_URL = os.getenv("VECTARA_BASE_URL", "https://api.vectara.io/v2")
API_KEY = os.getenv("VECTARA_API_KEY")
HERE = os.path.dirname(__file__)

_local = threading.local()


def client() -> httpx.Client:
    if not hasattr(_local, "c"):
        _local.c = httpx.Client(timeout=60)
    return _local.c


def patch_doc(corpus_key: str, doc_id: str, rating: float) -> str:
    r = client().patch(
        f"{BASE_URL}/corpora/{corpus_key}/documents/{doc_id}",
        headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
        json={"metadata": {"rating": rating}},
    )
    return "ok" if r.status_code == 200 else f"ERR {r.status_code}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tiers", nargs="+", default=["100", "1k", "9k"])
    args = parser.parse_args()

    with open(os.path.join(HERE, "data", "movies_full.json")) as f:
        movies = {m["movieId"]: m for m in json.load(f)["movies"]}
    with open(os.path.join(HERE, "data", "tiers.json")) as f:
        tier_ids = json.load(f)

    for tier in args.tiers:
        corpus = f"movies_eval_{tier}"
        jobs = [(f"movie-{mid}", movies[mid].get("imdbRating"))
                for mid in tier_ids[tier] if movies[mid].get("imdbRating") is not None]
        print(f"{corpus}: patching {len(jobs)} docs")
        ok = err = 0
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = [ex.submit(patch_doc, corpus, d, float(r)) for d, r in jobs]
            for i, fut in enumerate(as_completed(futures), 1):
                if fut.result() == "ok":
                    ok += 1
                else:
                    err += 1
                if i % 500 == 0 or i == len(jobs):
                    print(f"  {i}/{len(jobs)} (ok={ok} err={err})", flush=True)
        print(f"  done: ok={ok} err={err}")


if __name__ == "__main__":
    main()
