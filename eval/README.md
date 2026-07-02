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

## Results (2026-07-03, dev tenant — post scoring-fix)

Cells are `vector / +metadata` recall; graph = 1.00 on every graph-shaped
question at every scale. *(n.e. = not expressible: the metadata filter
language has no joins.)*

| Question (class) | T-100 | T-1k | T-9k | gold at 9k |
|---|---|---|---|---|
| All movies from year X (completeness) | 0.60 / 1.00 | 0.62 / 1.00 | 0.88 / 1.00 | 8 |
| All movies featuring actor X (completeness) | 1.00 / n.e. | 0.88 / n.e. | 0.41 / n.e. | 56 |
| How many did director X direct? (aggregation) | 1.00 / n.e. | 0.00 / n.e. | 0.00 / n.e. | 42 |
| How many released in year X? (aggregation) | 1.00 / 1.00 | 0.00 / 0.00 | 1.00 / 0.00 | 8 |
| Oldest movie? (ordering) | 0.00 / 1.00 | 1.00 / 1.00 | 0.00 / 0.00 | 1 |
| Highest IMDb rating? (ordering) | 0.00 / 0.00 | 1.00 / 1.00 | 0.00 / 0.00 | 1 |
| Actors in X's movies? (multi-hop) | 1.00 / n.e. | 0.71 / n.e. | 0.52 / n.e. | 132 |
| Directed AND acted in same movie? (multi-hop) | 0.00 / n.e. | 0.00 / n.e. | 0.00 / n.e. | 291 |
| Which movie is this plot? (control ×2) | 1.00 | 1.00 | 1.00 | 1 |

Full merged table: `results/REPORT.md` (regenerate with `python eval/report.py`).

Findings:

1. **Failure scales with answer size.** Multi-hop join: 1.00 → 0.71 → 0.52 as
   gold grows 8 → 28 → 132; actor-completeness: 1.00 → 0.88 → 0.41 (3 → 8 → 56);
   the 291-answer self-join scores 0.00 at every scale. Answer sets larger than
   the context budget (~25 chunks) cannot be assembled by any tuning.
2. **Metadata tools patch only what they can express, below a scale ceiling.**
   Hand-derived `doc.year` filters fix attribute completeness (→ 1.00 at all
   tiers). But UDF sorting is retrieval-bounded — it sorts only the candidates
   retrieval surfaced, so "oldest movie" works at 1k and collapses to 0.00 at
   9k. And counting fails even with perfect filtered retrieval: given all 8
   qualifying movies in context, the LLM counted 6. Relationships (actor,
   director, joins) are not expressible in the filter language at all.
3. **Controls stay perfect for vector search** — the eval is not rigged;
   each method wins its own class. The conclusion is fusion, not replacement.
4. **Scoring-fix disclosure:** dataset titles are article-inverted ("Sound of
   Music, The"); the original matcher under-counted natural-form mentions.
   `rescore.py` re-scored all saved answers with variant matching — the fix
   *raised baseline scores* (e.g. actor-completeness at T-100: 0.33 → 1.00).
   All published numbers are post-correction.

Caveats: one question per class per tier (small n), single run, 36/9,076 docs
(0.4%) failed indexing at the 9k tier, dev tenant. The design proposal's pilot
scales this to a ~50-question battery.

## Files

| file | role |
|---|---|
| `extract_neo4j.py` | pull dataset from the public demo server → `data/movies_full.json` |
| `load_tiers.py` | build seeded tiers; create corpora + index docs; build RDF + load named graphs |
| `run_eval.py` | generate battery, answer via each arm (`--arm vector|meta`), score, write `results/` |
| `backfill_rating.py` | add `rating` to document metadata (enables UDF-sort arm) |
| `rescore.py` | re-score saved answers offline after matcher changes |
| `report.py` | merge all arms into `results/REPORT.md` |
| `results/results_<tier>.json` | per-question scores + full generated answers (audit trail) |
