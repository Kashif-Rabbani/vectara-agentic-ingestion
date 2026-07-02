| Question | arm | T-100 | T-1k | T-9k |
|---|---|---|---|---|
| **All movies from year X (completeness)** | vector | 0.60 | 0.62 | 0.88 |
| | +meta | 1.00 | 1.00 | — |
| | graph | 1.00 | 1.00 | 1.00 |
| **All movies featuring actor X (completeness)** | vector | 1.00 | 0.88 | 0.41 |
| | +meta | *not expressible* | | |
| | graph | 1.00 | 1.00 | 1.00 |
| **How many did director X direct? (aggregation)** | vector | 1.00 | 0.00 | 0.00 |
| | +meta | *not expressible* | | |
| | graph | 1.00 | 1.00 | 1.00 |
| **How many released in year X? (aggregation)** | vector | 1.00 | 0.00 | 1.00 |
| | +meta | 1.00 | 0.00 | — |
| | graph | 1.00 | 1.00 | 1.00 |
| **Oldest movie? (ordering)** | vector | 0.00 | 1.00 | 0.00 |
| | +meta | 1.00 | 1.00 | — |
| | graph | 1.00 | 1.00 | 1.00 |
| **Highest IMDb rating? (ordering)** | vector | 0.00 | 1.00 | 0.00 |
| | +meta | 0.00 | 1.00 | — |
| | graph | 1.00 | 1.00 | 1.00 |
| **Actors in X's movies? (multi-hop)** | vector | 1.00 | 0.71 | 0.52 |
| | +meta | *not expressible* | | |
| | graph | 1.00 | 1.00 | 1.00 |
| **Directed AND acted in same movie? (multi-hop)** | vector | 0.00 | 0.00 | 0.00 |
| | +meta | *not expressible* | | |
| | graph | 1.00 | 1.00 | 1.00 |
| **Which movie is this plot? (control)** | vector | 1.00 | 1.00 | 1.00 |
| | +meta | *not expressible* | | |
| | graph | — | — | — |
| **Which movie is this plot? #2 (control)** | vector | 1.00 | 1.00 | 1.00 |
| | +meta | *not expressible* | | |
| | graph | — | — | — |

**Class means (vector / +meta / graph):**

| Class | T-100 | T-1k | T-9k |
|---|---|---|---|
| completeness | 0.80 / 1.00 / 1.00 | 0.75 / 0.94 / 1.00 | 0.64 / 0.64 / 1.00 |
| aggregation | 1.00 / 1.00 / 1.00 | 0.00 / 0.00 / 1.00 | 0.50 / 0.50 / 1.00 |
| ordering | 0.00 / 0.50 / 1.00 | 1.00 / 1.00 / 1.00 | 0.00 / 0.00 / 1.00 |
| multihop | 0.50 / 0.50 / 1.00 | 0.36 / 0.36 / 1.00 | 0.26 / 0.26 / 1.00 |
| control | 1.00 / 1.00 / — | 1.00 / 1.00 / — | 1.00 / 1.00 / — |
