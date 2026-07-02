#!/usr/bin/env python3
"""
Re-scores all saved eval results in-place using the (fixed) entity matcher
from run_eval.py — no API calls; answers were stored verbatim.

Fixes the article-inversion false negatives: dataset titles are stored as
'Sound of Music, The' but answers naturally say 'The Sound of Music'.

Usage: python eval/rescore.py
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from run_eval import entity_recall, count_correct  # fixed matcher

RESULTS = os.path.join(os.path.dirname(__file__), "results")

for path in sorted(glob.glob(os.path.join(RESULTS, "results_*.json"))):
    with open(path) as f:
        rows = json.load(f)
    changed = 0
    for r in rows:
        if r.get("not_expressible") or r.get("vector_answer") is None:
            continue
        old = r["vector_score"]
        if r.get("gold_count") is not None:
            new = 1.0 if count_correct(r["gold_count"], r["vector_answer"]) else 0.0
            missing = [] if new else [f"expected count {r['gold_count']}"]
        else:
            new, missing = entity_recall(r["gold"], r["vector_answer"])
        if round(new, 3) != old:
            print(f"{os.path.basename(path):>24} {r['id']}: {old} → {round(new,3)}")
            changed += 1
        r["vector_score"] = round(new, 3)
        r["vector_missing"] = missing[:20]
    with open(path, "w") as f:
        json.dump(rows, f, indent=2)
    if not changed:
        print(f"{os.path.basename(path):>24} — no changes")
