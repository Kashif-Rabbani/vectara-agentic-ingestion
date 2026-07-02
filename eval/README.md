# Graph-vs-Vector Retrieval Evaluation

Reproducible experiment measuring which question classes top-k vector retrieval
cannot answer completely — and that a knowledge graph answers exactly. This is
the empirical evidence behind
[`docs/design-proposal-graph-db-connectors.md`](../docs/design-proposal-graph-db-connectors.md).

## Dataset

The **Neo4j "recommendations" movies dataset** (MovieLens + TMDb — the standard
graph-database demo dataset), pulled live from Neo4j's public read-only demo
server (`demo.neo4jlabs.com`, user/pass `recommendations`):

- 9,076 movies (title, year, plot, genres, IMDb rating)
- 19,047 people, 35,778 `ACTED_IN` edges, 9,955 `DIRECTED` edges

Ground truth for every question is computed **from the relationship data
itself** — no hand labeling, no ambiguity.

## Design

- **Three nested, seeded tiers** — 100 ⊂ 1,000 ⊂ 9,076 movies (single shuffle,
  seed 42). Each tier gets its own Vectara corpus (`movies_eval_100/1k/9k`) and
  its own Fuseki named graph (`http://vectara-eval/graph/movies-<tier>`). The
  same movie appears identically in both stores: a text document (plot, cast,
  director, rating) and RDF triples (schema.org vocabulary).
- **Data-driven question battery** per tier, five classes:
  | class | example | why it's hard for top-k |
  |---|---|---|
  | completeness | "List ALL movies from 1968" | top-k ≠ all |
  | aggregation | "How many movies did X direct?" | counting needs the full set |
  | ordering | "Oldest movie?" | comparison across all entities |
  | multi-hop | "Actors in movies directed by X?" | join across documents |
  | control | "Which movie is this plot?" | **vector should win** — fairness check |
- **Fair baseline**: the vector side runs *tuned* — neural reranker
  (`Rerank_Multilingual_v1`) over 100 candidates, gpt-5 generation preset,
  25 results in context. Not the defaults.
- **Scoring**: recall of gold entities (normalized substring match) in the
  generated answer; exact-number match for counts. The SPARQL side answers the
  same questions against the tier's named graph.

## Run it

```bash
# prerequisites: Fuseki running (../setup_fuseki.sh), .env configured
python eval/extract_neo4j.py    # ~2 min  → eval/data/movies_full.json
python eval/load_tiers.py       # ~30 min (10k docs indexed; --tiers 100 for a quick start)
python eval/run_eval.py         # ~10 min → eval/results/
```

## Results (2026-07-02, dev tenant)

Per-question vector-only recall (graph = 1.00 on every graph-shaped question at every scale):

| Question (class) | T-100 | T-1k | T-9k | gold size at 9k |
|---|---|---|---|---|
| All movies from year X (completeness) | 0.20 | 0.12 | 0.62 | 8 |
| All movies featuring actor X (completeness) | 0.33 | 0.62 | 0.38 | 56 |
| How many did director X direct? (aggregation) | 1.00 | 0.00 | 0.00 | 42 |
| How many released in year X? (aggregation) | 1.00 | 0.00 | 1.00 | 8 |
| Oldest movie? (ordering) | 0.00 | 1.00 | 0.00 | 1 |
| Highest IMDb rating? (ordering) | 0.00 | 1.00 | 0.00 | 1 |
| Actors in X's movies? (multi-hop) | 1.00 | 0.71 | 0.52 | 132 |
| Directed AND acted in same movie? (multi-hop) | 0.00 | 0.00 | 0.00 | 291 |
| Which movie is this plot? (control ×2) | 1.00 | 1.00 | 1.00 | 1 |
| **Mean, graph-shaped questions** | **0.44** | **0.43** | **0.32** | |

Findings:

1. **Failure scales with answer size.** The multi-hop join degrades
   monotonically as the true answer grows (8 → 28 → 132 names: 1.00 → 0.71 →
   0.52); the 291-answer self-join scores 0.00 at every scale. An answer set
   larger than the LLM's context budget (~25 chunks) cannot be assembled by any
   tuning — the limit is architectural.
2. **Counting collapses** once the count exceeds what retrieval hands the
   LLM — with a *confidently wrong* number, not an "I don't know."
3. **Controls stay perfect for vector search** — the eval is not rigged;
   each method wins its own class. The conclusion is fusion, not replacement.

Caveats: one question per class per tier (small n — the ordering flip
0→1→0 is visible noise), single run, 36/9,076 docs (0.4%) failed indexing at
the 9k tier, dev tenant. The design proposal's pilot scales this to a
~50-question battery.

## Files

| file | role |
|---|---|
| `extract_neo4j.py` | pull dataset from the public demo server → `data/movies_full.json` |
| `load_tiers.py` | build seeded tiers; create corpora + index docs; build RDF + load named graphs |
| `run_eval.py` | generate battery, answer via both paths, score, write `results/` |
| `results/results_<tier>.json` | per-question scores + full generated answers (audit trail) |
