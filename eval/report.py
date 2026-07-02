#!/usr/bin/env python3
"""
Merges the vector-arm, metadata-arm, and graph results into the final
three-arm comparison table (markdown, printed to stdout and saved to
eval/results/REPORT.md).

Arms:
  vector : tuned baseline (neural reranker + gpt-5 generation)
  +meta  : vector + best metadata strategy expressible per question
           (hand-derived optimal filter / UDF sort — generous to the baseline)
  graph  : one SPARQL query
"""
import json
import os

HERE = os.path.dirname(__file__)
RESULTS = os.path.join(HERE, "results")
TIERS = ["100", "1k", "9k"]

QLABELS = {
    "C1": "All movies from year X (completeness)",
    "C2": "All movies featuring actor X (completeness)",
    "A1": "How many did director X direct? (aggregation)",
    "A2": "How many released in year X? (aggregation)",
    "O1": "Oldest movie? (ordering)",
    "O2": "Highest IMDb rating? (ordering)",
    "M1": "Actors in X's movies? (multi-hop)",
    "M2": "Directed AND acted in same movie? (multi-hop)",
    "V1": "Which movie is this plot? (control)",
    "V2": "Which movie is this plot? #2 (control)",
}


def load(tier: str, meta: bool) -> dict:
    prefix = "results_meta_" if meta else "results_"
    path = os.path.join(RESULTS, f"{prefix}{tier}.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return {r["id"]: r for r in json.load(f)}


def fmt(score) -> str:
    return "—" if score is None else f"{score:.2f}"


def main():
    vec = {t: load(t, False) for t in TIERS}
    meta = {t: load(t, True) for t in TIERS}

    lines = []
    lines.append("| Question | arm | T-100 | T-1k | T-9k |")
    lines.append("|---|---|---|---|---|")
    for qid, label in QLABELS.items():
        v_scores = [vec[t].get(qid, {}).get("vector_score") for t in TIERS]
        m_rows = [meta[t].get(qid, {}) for t in TIERS]
        m_scores = [
            None if (not r or r.get("not_expressible")) else r.get("vector_score")
            for r in m_rows
        ]
        not_expr = all((not r or r.get("not_expressible")) for r in m_rows if r is not None)
        g_scores = [vec[t].get(qid, {}).get("graph_score") for t in TIERS]

        lines.append(f"| **{label}** | vector | {' | '.join(fmt(s) for s in v_scores)} |")
        if not_expr and any(m_rows):
            lines.append(f"| | +meta | *not expressible* | | |")
        else:
            lines.append(f"| | +meta | {' | '.join(fmt(s) for s in m_scores)} |")
        lines.append(f"| | graph | {' | '.join(fmt(s) for s in g_scores)} |")

    # class-level means for the three arms
    lines.append("")
    lines.append("**Class means (vector / +meta / graph):**")
    lines.append("")
    lines.append("| Class | T-100 | T-1k | T-9k |")
    lines.append("|---|---|---|---|")
    classes = ["completeness", "aggregation", "ordering", "multihop", "control"]
    for cls in classes:
        cells = []
        for t in TIERS:
            vs = [r["vector_score"] for r in vec[t].values()
                  if r["class"] == cls and r["vector_score"] is not None]
            # meta arm: fall back to vector score where not expressible
            ms = []
            for qid, r in vec[t].items():
                if r["class"] != cls or r["vector_score"] is None:
                    continue
                mr = meta[t].get(qid)
                if mr and not mr.get("not_expressible") and mr.get("vector_score") is not None:
                    ms.append(mr["vector_score"])
                else:
                    ms.append(r["vector_score"])  # no meta strategy → baseline stands
            gs = [r["graph_score"] for r in vec[t].values()
                  if r["class"] == cls and r["graph_score"] is not None]
            v = sum(vs) / len(vs) if vs else None
            m = sum(ms) / len(ms) if ms else None
            g = sum(gs) / len(gs) if gs else None
            cells.append(f"{fmt(v)} / {fmt(m)} / {fmt(g)}")
        lines.append(f"| {cls} | {' | '.join(cells)} |")

    out = "\n".join(lines)
    print(out)
    with open(os.path.join(RESULTS, "REPORT.md"), "w") as f:
        f.write(out + "\n")
    print(f"\nSaved → {os.path.join(RESULTS, 'REPORT.md')}")


if __name__ == "__main__":
    main()
