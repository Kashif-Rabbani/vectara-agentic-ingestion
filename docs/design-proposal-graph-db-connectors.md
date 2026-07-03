# Design Proposal: Graph Database Connectivity for the Vectara Platform

| | |
|---|---|
| **Status** | Draft v4 |
| **Author** | Kashif Rabbani |
| **Working demo** | [vectara-agentic-ingestion](https://github.com/Kashif-Rabbani/vectara-agentic-ingestion) · [mcp-server-sparql](https://github.com/Kashif-Rabbani/mcp-server-sparql) |
| **How to read this** | Sections 1–6 are a ~5-minute business read. Section 7 onward is technical design detail — read only if the first half convinces you. |

---

## 1. TL;DR

Vectara's search finds text *similar* to a question. It cannot **guarantee** answers that require *connecting or enumerating* facts — "list **all**…", "**how many**…", "who is **connected** to…". Measured on the Neo4j movies dataset (§4): asked how many movies Woody Allen directed, our tuned pipeline answered **25**, with a citation for every one — the truth is **42**. Fluent, grounded, silently wrong.

Knowledge graphs answer these questions exactly, and many enterprise customers already own one. Vectara has no first-party way to connect to them.

We built the connector, ran it end-to-end with a Vectara agent, and benchmarked it at three scales. **The ask (§6) is a small, time-boxed pilot** — host the connector officially, scale the benchmark, validate with design partners. If the quality delta or the demand isn't there, we stop. If it is, the long-term play is a real differentiator: graph facts and semantic search fused into one answer, one citation model, through the pipeline Vectara already ships.

## 2. Problem Statement

Every Vectara answer today is built the same way: retrieve the passages most similar to the question, have an LLM answer from them. Excellent when the answer lives in a passage. A whole class of questions doesn't — examples below are from our benchmark domain, each measured (§4):

- **"List ALL movies featuring Robert De Niro."** — Top-k retrieval cannot promise *all*: anything ranked too low silently drops, and the answer still reads as complete. Measured at 9k docs: 41% of his movies found. (And "featuring" is a relationship — no metadata filter can express it.)
- **"How many movies did Woody Allen direct?"** — Counting needs every movie, not the 25 most-similar chunks. Measured: it answered 25 — exactly its context size — when the truth is 42.
- **"Who both directed and acted in the same movie?"** — A join across documents that never mention each other. 291 correct answers exist; the pipeline found 0 and offered two names that don't qualify.

The shapes are exactly the enterprise questions — "all contracts expiring this year," "how many portfolio companies are EU-based," "which vendors reach a sanctioned entity through subsidiaries" — we use the movie forms because we can back each one with data.

These failures are quiet: confident, well-cited, wrong by omission. Nothing in our stack can flag them — every cited fragment is *true*; the error lives in what retrieval never surfaced. For a brand built on grounded, hallucination-free answers, that is the worst kind of failure.

Meanwhile, the systems that answer these questions perfectly — knowledge graphs — already sit in customers' data centers (compliance, supply-chain, org/MDM graphs). A customer asking *"can Vectara use our knowledge graph?"* today effectively hears **no**: self-host your own tool server, with no support, vetting, or credential management.

**In one sentence: Vectara cannot guarantee correct answers to connection-and-completeness questions, and cannot connect to the customer systems that can.**

## 3. What we're proposing (plain-language)

Extend hybrid search. Today every query blends two signals — keyword and semantic — and a reranker merges them into one result list. We add a third: the customer's knowledge graph. Graph hits arrive as precise facts, corpus hits as text passages; the existing reranker merges them, and the existing generation step writes one answer with one set of citations spanning both. Same `/v2/query`, one more source. The customer gets an answer that is *complete* (graph) and *contextual* (documents) at once.

## 4. The evidence — measured, not hypothetical

Dataset: the **Neo4j "recommendations" movies dataset** — the graph industry's standard demo set (9,076 movies, 19,047 people, ~46,000 relationships). Every movie lives identically in both stores: a text document in a Vectara corpus, triples in a knowledge graph. Ground truth is computed **from the relationship data itself**. Three nested corpus scales (100 / 1,000 / 9,076 docs) and **three arms**: a tuned vector baseline (neural reranker over 100 candidates, gpt-5 generation, 25 results in context); a **"+metadata" arm** giving the baseline every additional tool Vectara ships today — hand-derived optimal `metadata_filter`s and UDF-reranker sorts (a deliberate generosity: production would need query rewriting to derive them); and the graph. Harness and raw outputs: [`eval/`](https://github.com/Kashif-Rabbani/vectara-agentic-ingestion/tree/main/eval).

The headline failure, verbatim, at full scale:

> **"How many movies in the dataset did Woody Allen direct?"** — truth: **42**
>
> Vector + generation: *"Woody Allen directed 25 movies in this dataset. [1] [2] … [25]"*
>
> Not a random error: **25 is exactly the number of retrieved results the LLM was given.** It counted its context window and presented that as the dataset total, citing every one. The graph answers 42, deterministically.

The other failures are the same species: asked who both directed and acted in the same movie (**291** correct answers), it returned two names — both wrong. Asked the highest-rated movie (Band of Brothers, 9.6), it confidently cited a real 8.4-rated film. Every answer is fluent, cited, and grounded in genuinely retrieved text — **a factual-consistency checker can flag none of them.**

**Scoreboard — mean recall per class, cells are `vector / +metadata` (graph = 1.00 on every graph-shaped question, at every scale):**

| Question class | 100 docs | 1,000 docs | 9,076 docs |
|---|---|---|---|
| Completeness ("list ALL…") | 0.80 / 1.00 | 0.75 / 0.94 | 0.64 / 0.71 |
| Aggregation ("how many…") | 1.00 / 1.00 | 0.00 / 0.00 | 0.50 / 0.00 |
| Ordering ("oldest / highest-rated…") | 0.00 / 0.50 | 1.00 / 1.00 | 0.00 / 0.00 |
| Multi-hop ("actors in X's movies…") | 0.50 / *n.e.* | 0.36 / *n.e.* | 0.26 / *n.e.* |
| **Control — plot similarity (vector's home turf)** | **1.00** | **1.00** | **1.00** |

*n.e. = not expressible: the metadata filter language has no joins, so relationship questions cannot be filtered into existence.*

Three findings. **(1) Failure scales with answer size**: the multi-hop join degrades monotonically as the true answer grows (8 → 28 → 132 names: 1.00 → 0.71 → 0.52), actor-completeness likewise (3 → 8 → 56 movies: 1.00 → 0.88 → 0.41), and the 291-answer question scores 0.00 at every scale — an answer set larger than the context budget cannot be assembled by any tuning. **(2) Metadata tools patch only what they can express, and only below a scale ceiling**: a hand-derived year filter fixes attribute completeness (→ 1.00); but UDF sorting is retrieval-bounded — it can only sort candidates retrieval happened to surface, so "oldest movie" works at 1k docs and collapses to 0.00 at 9k; and counting fails even with *perfect* filtered retrieval — given all 8 qualifying movies in context, the LLM counted 6. Relationships remain out of reach entirely. **(3) The controls hold**: plot-similarity questions score 1.00 for vector search at every tier. Each method wins where it's structurally suited — the case is *fusion*, not replacement.

The irreducible gap, precisely: **relationships** (inexpressible in any filter) and **computation** (counting, sorting, aggregating beyond the context/retrieval budget). Those are exactly what a graph query is.

*Caveats (details in [`eval/README.md`](https://github.com/Kashif-Rabbani/vectara-agentic-ingestion/blob/main/eval/README.md)): one question per class per tier, single run, 0.4% of docs failed indexing, dev tenant; an earlier scoring bug (article-inverted titles) was found and fixed — all published numbers are post-correction, and the fix raised *baseline* scores. The literature agrees on the failure class — "RAG fails on global questions directed at an entire text corpus" (Microsoft Research GraphRAG, [arXiv:2404.16130](https://arxiv.org/abs/2404.16130)).*

## 5. What Vectara gains

1. **A stronger anti-hallucination story — our brand.** "Confidently incomplete" answers are a grounding failure nothing in today's stack detects (HHEM included — every cited fragment is true). Graph fusion eliminates it for these question classes, measurably.
2. **A door into graph-owning enterprises.** Banks, pharma, manufacturing, MDM shops already own knowledge graphs; first-party connectivity turns "can you use our KG?" from a no into a demo.
3. **One connector, most of the market.** The connector implements the W3C SPARQL 1.1 standard — verified against Jena Fuseki; GraphDB, Stardog, Virtuoso, Blazegraph, and Neptune expose compliant endpoints (validating 2–3 is a pilot task). No per-vendor treadmill.
4. **Differentiated timing.** GraphRAG is shipping industry-wide, almost entirely as agent-tool bolt-ons. We are not aware of any platform fusing graph results through a production reranking + citation + grounding pipeline (*validate before external use*). Vectara already owns that pipeline.

**Honest gap:** no named customer demand yet — to gather from sales/CS in parallel. *(☐ prospects with KGs · ☐ deals where this came up · ☐ community asks.)*

## 6. The ask

Approve a **time-boxed pilot** (size S — the connector and benchmark harness already exist):

1. **Ship** the SPARQL connector as a first-party tool (today's self-host-and-tunnel path is a non-starter for customers). **The precedent already exists:** the catalog's `sql_query` tool connects to external customer databases — PostgreSQL, MySQL, MariaDB, ClickHouse. This is SPARQL joining that family, not a new integration paradigm; the open-source [mcp-server-sparql](https://github.com/Kashif-Rabbani/mcp-server-sparql) is the reference implementation.
2. **Measure** — scale the §4 experiment: ~50 questions per tier, multiple runs, HHEM + Open RAG Eval scoring, controls retained so the eval keeps showing where graphs *don't* help.
3. **Validate** with 1–2 design partners who already own a knowledge graph.

**Exit criteria:** a measured quality delta plus at least one design partner who wants more — otherwise we stop, and the sunk cost is the pilot. Nothing in it touches the public query API. The native integration (`search.graphs[]` in `/v2/query`, §7–8) is the Phase-2 opportunity the pilot de-risks — not today's ask.

---

**That's the business case. Everything below is technical design detail — read on only if the above convinced you.**

---

## 7. Technical design — end-state architecture (Phase 2)

Today `/v2/query` fuses two signals — lexical (BM25-style) and semantic (dense vectors) — via `lexical_interpolation`, then reranks the merged candidate pool. The proposal adds graph traversal as the third:

```
                              /v2/query
       "Which actors appeared in movies directed by Woody Allen?"
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        ▼                         ▼                          ▼
  Lexical (BM25)          Semantic (vectors)          Graph (SPARQL)
  search.corpora[]        search.corpora[]            search.graphs[]   ← NEW
        │                         │                          │
        └────────── top-k ────────┴───────── top-k ──────────┘
                                  │
                   unified candidate pool — every hit in the
                   existing search_results[] shape
                                  ▼
                   reranker chain (EXISTS: customer_reranker
                   → userfn score fusion → mmr)
                                  ▼
                   generation + citations + HHEM (EXISTS)
```

Top-k from each source, rerank the union, return the best top-n — the same fusion pattern multi-corpus search performs today across N corpora, with one more source type.

### The load-bearing design decision: normalize graph hits into `search_results[]`

Each matched entity/subgraph is serialized into the exact shape a corpus hit already has:

```json
{
  "text": "Annie Hall (1977) — directed by Woody Allen. Actors: Diane Keaton, Woody Allen, Tony Roberts, ...",
  "document_id": "graph:movie-kg:entity/movie/1230",
  "document_metadata": {
    "source": "graph",
    "graph_key": "movie-kg",
    "entity_uri": "http://vectara-eval/entity/movie/1230",
    "relation_path": ["schema:director", "schema:actor"],
    "graph_score": 0.8
  }
}
```
*(Entity and vocabulary taken from the §4 experiment's actual graph — this is the shape our eval already produces.)*

Consequence: **reranking, generation, citations, and every existing customer UI work unmodified.** The neural reranker scores serialized graph facts and corpus passages as what they both are — text candidates. A `userfn` stage blends `graph_score` in (expression below is a starting point, not a tuned default). Citation templates resolve `entity_uri` instead of a doc URL — a template variant, not new plumbing.

### Reuse table — why this is additive, not a from-scratch build

| Needed | Already ships | New work |
|---|---|---|
| SPARQL execution (5+ vendors, 1 protocol) | ✅ mcp-server-sparql | wrap as internal retrieval source |
| Multi-source fan-out + interleave | ✅ `search.corpora[]` | add sibling `search.graphs[]` |
| Cross-source rerank + score fusion | ✅ `chain` / `customer_reranker` / `userfn` | author default fusion expression |
| Citations over mixed origins | ✅ `citations.url_pattern` templating | `entity_uri` template variant |
| Credential storage, masking, encryption | ✅ `agent.secrets` (Wolken pattern) | apply to graph endpoints |
| Schema grounding for linking/traversal | ✅ SHACL shapes (already used at ingest) | reuse at query time |
| Answer-quality measurement | ✅ HHEM / Open RAG Eval | build the multi-hop eval set |

### Graph retrieval semantics

- **Entity linking:** `explicit` mode first (caller/agent supplies `entity_uris`, or schema-driven matching against the graph's ontology). Fully-automatic NL entity linking is a later mode whose feasibility the pilot's eval informs — v1 deliberately does not depend on solving it.
- **Bounded traversal, not LLM-generated SPARQL:** the default execution path is a depth-bounded neighborhood expansion (`traversal_depth` ≤ 2, hard timeout) from linked entities — deterministic, injection-proof, cost-capped. Free-form NL→SPARQL remains an *agent-tool* capability, never the query-path default.
- **Read-only by construction:** the retrieval path issues only SELECT/CONSTRUCT-class queries. Graph writes (`sparql_update`) exist solely as explicitly opted-in agent tools, unchanged from the demo's posture.

## 8. Proposed API surface (illustrative — for reaction, not final)

```yaml
# New resource — peer to /v2/corpora
POST /v2/graphs
{
  "graph_key": "movie-kg",
  "protocol": "sparql11",                      # one protocol, many vendors
  "endpoint": { "query_url": "https://kg.customer.com/ds/query" },
  "credentials_ref": "agent.secrets.KG_BASIC_AUTH",    # never plaintext in payload
  "schema_ref": "shapes/movie.ttl",             # SHACL/ontology for linking + traversal scoping
  "write_enabled": false                         # default
}

# Standalone graph query — mirrors /v2/corpora/{key}/query
POST /v2/graphs/{graph_key}/query
{
  "query": "movies released before 1970",
  "search": { "entity_linking": "explicit", "entity_uris": [...],
              "traversal_depth": 2, "limit": 20 }
}
# → returns search_results[] — identical contract to a corpus query

# THE integration point — /v2/query grows a sibling array
POST /v2/query
{
  "query": "Which actors appeared in movies directed by Woody Allen?",
  "search": {
    "corpora": [ { "corpus_key": "movies", "lexical_interpolation": 0.025 } ],
    "graphs":  [ { "graph_key": "movie-kg", "traversal_depth": 2,
                   "graph_score_weight": 1.0 } ],
    "limit": 50,
    "reranker": {
      "type": "chain",
      "rerankers": [
        { "type": "customer_reranker", "reranker_name": "Rerank_Multilingual_v1",
          "cutoff": 0.5, "limit": 50 },
        { "type": "userfn",   # starting point — tuned during Phase 2
          "user_function": "if (get('$.document_metadata.source') == 'graph') get('$.score', 0.7) + get('$.document_metadata.graph_score') * 0.3 else get('$.score')" },
        { "type": "mmr", "diversity_bias": 0.3, "limit": 10 }
      ]
    }
  },
  "generation": { "generation_preset_name": "mockingbird-2.0", "max_used_search_results": 8 }
}
```

Response: a normal `/v2/query` response — `search_results[]` interleaving graph- and corpus-origin hits (exactly as multi-corpus interleaves today), one `summary`, `[N]` citations spanning both sources, one HHEM score.

## 9. Security model

- Graph endpoint credentials via `agent.secrets`-equivalent storage: encrypted at rest with the agent's KMS key, masked `****` in observability events — the same service-account pattern Wolken uses today (`PATCH /v2/agents/{key}/secrets`).
- Outbound calls to customer-supplied database endpoints are an **already-shipped pattern**: the catalog's `sql_query` (PostgreSQL/MySQL/MariaDB/ClickHouse) and `web_get` do exactly this today. The SSRF review extends an accepted precedent rather than introducing a new risk class. One improvement over `sql_query`'s per-call connection details: resolve graph credentials via `agent.secrets` `$ref`s so they're encrypted at rest and masked in event streams.
- Write access is off by default at graph registration (`write_enabled: false`); enabling it exposes write *tools* only to explicitly configured agents, never to the retrieval path.

## 10. Alternatives considered

| Alternative | Why not (as the end state) |
|---|---|
| **Status quo — customer self-hosts a generic MCP server** | Zero adoption path; no credentials/vetting/discoverability; answer quality hostage to per-turn LLM tool choice |
| **Agent-tool-only forever (pilot as terminus)** | Never reaches the direct-RAG `/v2/query` surface; graph and corpus results never reranked/cited together; caps differentiation at parity with everyone else's bolt-ons |
| **Skip the pilot, build `search.graphs[]` now** | Commits public API surface before any measured quality evidence or demand signal exists |
| **Vendor-specific connector (e.g. Neo4j/Cypher only)** | SPARQL 1.1 covers 5+ vendors in one implementation; a Cypher adapter is a future *additive* source behind the same `search.graphs[]` shape |

## 11. Risks & open questions

- **Entity linking quality** is the crux for a future fully-automatic mode — it gets its own eval line in the pilot, and v1 does not depend on it.
- **Fusion weights** (`graph_score_weight`, the `userfn` expression) need real query data to tune.
- **Traversal cost bounds** — hard depth ceiling + timeout so a pathological graph shape cannot blow up query latency.
- **Ownership** — which team owns "graph connectivity" long-term.
- ~~Internal precedent check~~ **Settled** (console tool catalog, 2026-07-03): Jira/Slack/Wolken are first-party catalog tools with dedicated categories, versioned IDs, and connector-managed credentials — and `sql_query` already connects to external customer databases (PostgreSQL, MySQL, MariaDB, ClickHouse). A first-party SPARQL tool joins an existing family rather than creating a new one.

## 12. Success metrics

1. **The structural-gap number:** fraction of eval questions unanswerable by vector-only retrieval at *any* k, answered correctly with graph fusion. This is the headline.
2. **Answer correctness/completeness delta** against labeled golden answers (Open RAG Eval methodology), graph-augmented vs. vector-only. (Note: HHEM alone cannot measure this — an incomplete answer is still "consistent" with its incomplete sources; completeness requires golden-answer comparison.)
3. Design-partner conversion: does anyone who sees the pilot want the native integration badly enough to co-design it.

## Appendix A — Sources & confidence levels

Every claim in this document falls into one of three evidence tiers. Reviewers should weigh them accordingly.

### Tier 1 — Verified by direct execution (strongest)

Demonstrated live against the Vectara platform (dev environment, `api.vectara.dev`) during the reference-implementation build:

| Claim | How verified |
|---|---|
| **The §4 scaled experiment**: on the Neo4j movies dataset (9,076 movies, 3 tiers), tuned vector-only retrieval scored 0.32–0.44 mean recall on graph-shaped questions vs. 1.00 for SPARQL, with controls at 1.00 for vector | Executed 2026-07-02; harness + per-question raw answers in `eval/` (extract → dual-ingest → battery → score, fully reproducible) |
| Agents support `mcp` and `dynamic_vectara` tool types; MCP tool servers register + sync via `POST /v2/tool_servers` | We registered `mcp-server-sparql`, synced 12 tools, ran the agent |
| Full dual-ingestion pipeline works (read → dedup → SHACL → KG write → Vectara index) | 6 companies ingested and cross-verified in both stores; event traces reproducible via `run_ingestion.py` |
| `POST /v2/query` takes `search.corpora[]` (array of `KeyedSearchCorpus`, each with `corpus_key`, `metadata_filter`, `lexical_interpolation`) | [OpenAPI spec](https://api.vectara.io/v2/openapi.json), `SearchCorporaParameters` schema, fetched 2026-07-02 |
| Reranker types `customer_reranker`, `mmr`, `userfn`, `chain` exist | Same OpenAPI spec: `CustomerSpecificReranker`, `MMRReranker`, `UserFunctionReranker`, `ChainReranker` schemas |
| Agent secrets API (`/v2/agents/{key}/secrets`) and tool-server endpoints exist | Same OpenAPI spec, paths section |
| `search.graphs[]` does **not** exist today (i.e., this proposal is genuinely new surface) | Same OpenAPI spec: no `graphs` property in `SearchCorporaParameters` |
| Jira/Slack/Wolken ship as first-party catalog tools (dedicated categories, versioned IDs, connector credentials); `sql_query` already targets external customer databases (PostgreSQL/MySQL/MariaDB/ClickHouse) | Vectara console tool catalog, inspected 2026-07-03 |

### Tier 2 — Vectara public documentation (citable)

| Claim | Source |
|---|---|
| Hybrid search blends semantic + keyword; `lexical_interpolation` 0.0 = pure semantic, 1.0 = pure keyword ("equivalent to traditional BM25") | [Hybrid search](https://docs.vectara.com/docs/search-and-retrieval/hybrid-search) — quoted verbatim, checked 2026-07-02 |
| Reranker types and semantics (multilingual neural, MMR diversity, UDF custom scoring, chain composition) | [Reranking](https://docs.vectara.com/docs/search-and-retrieval/reranking) — checked 2026-07-02 |
| Citations, generation presets, factual-consistency scoring surface | [docs.vectara.com](https://docs.vectara.com) query/generation sections |
| Service-account secrets pattern (encrypted at rest, masked in events) | Vectara agent auth documentation + [toolkits-auth-demo](https://github.com/vectara/toolkits-auth-demo) |

### Tier 3 — Author's design reasoning & assumptions (validate internally before relying on)

| Claim | Status |
|---|---|
| Internal implementation of multi-corpus fan-out/interleaving (and hence how cheaply a third source slots in) | **Inferred from API behavior** — describes observable behavior, not internal architecture. Needs a conversation with the query-platform team. |
| Entity-linking approach, traversal bounds, fusion weights | **Original design proposal** — no precedent claimed. |
| SPARQL connector works unmodified against GraphDB/Stardog/Virtuoso/Neptune | **Standard-compliance inference** — only Fuseki is tested. Pilot task. |
| Competitive landscape (agent-tool bolt-ons; no reranking-fused competitor) | **Author's knowledge, early 2026, not systematically researched.** Validate before external use. |
| Customer demand | **No evidence yet** — placeholders in §5 to be filled from sales/CS. |

## Appendix B — references

- Working demo: [vectara-agentic-ingestion](https://github.com/Kashif-Rabbani/vectara-agentic-ingestion) (6 companies dual-ingested + cross-verified; agent event traces in README)
- Connector: [mcp-server-sparql](https://github.com/Kashif-Rabbani/mcp-server-sparql)
- Auth pattern: [vectara/toolkits-auth-demo](https://github.com/vectara/toolkits-auth-demo)
- Authoritative API contract: [OpenAPI spec](https://api.vectara.io/v2/openapi.json)
- Hybrid search: [docs.vectara.com/docs/search-and-retrieval/hybrid-search](https://docs.vectara.com/docs/search-and-retrieval/hybrid-search)
- Reranking: [docs.vectara.com/docs/search-and-retrieval/reranking](https://docs.vectara.com/docs/search-and-retrieval/reranking)
- Eval: [Open RAG Eval](https://github.com/vectara/open-rag-eval) + HHEM factual-consistency scoring
- SPARQL 1.1 Protocol: [W3C Recommendation](https://www.w3.org/TR/sparql11-protocol/)
- Academic grounding for the failure class: Edge et al., *From Local to Global: A Graph RAG Approach to Query-Focused Summarization* (Microsoft Research), [arXiv:2404.16130](https://arxiv.org/abs/2404.16130) — "RAG fails on global questions directed at an entire text corpus"
