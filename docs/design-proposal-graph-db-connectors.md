# Design Proposal: Graph Database Connectivity for the Vectara Platform

| | |
|---|---|
| **Status** | Draft v5 |
| **Author** | Kashif Rabbani |
| **Working demo** | [vectara-agentic-ingestion](https://github.com/Kashif-Rabbani/vectara-agentic-ingestion) · [mcp-server-sparql](https://github.com/Kashif-Rabbani/mcp-server-sparql) |
| **How to read this** | Sections 1–6 are the business case (~7 minutes). Section 7 onward is technical design detail — read it only if the first half convinces you. |

---

## 1. TL;DR

Vectara's search is very good at finding text that is *similar* to a question. But some questions need facts to be *connected or counted*: "list **all** movies from 1968," "**how many** films did this director make," "who **both** directed and acted in the same movie."

For these questions, top-k retrieval cannot guarantee a correct answer. We measured this on the Neo4j movies dataset (§4). Asked how many movies Woody Allen directed, our tuned pipeline answered **25**, with a citation for every one. The true count is **42**. The answer is fluent and grounded — and silently wrong. Neither we nor the customer can tell.

Knowledge graphs answer exactly these questions. Many enterprise customers already own one. Today, Vectara has no first-party way to connect to them.

We built that connector. It runs end-to-end with a Vectara agent, and we benchmarked it at three scales. **The ask (§6) is a small, time-boxed pilot:** ship the connector as a first-party tool, scale the benchmark, and validate with design partners. If the quality improvement or the demand is not there, we stop. If it is, the long-term opportunity is a real differentiator: graph facts and semantic search fused into one answer, with one citation model, through the pipeline Vectara already ships.

## 2. Problem Statement

Every Vectara answer today is built the same way: find the passages most similar to the question, then have an LLM write an answer from them. This works very well when the answer *lives inside a passage*.

But a whole class of questions does not live in any passage. All examples below come from our benchmark (§4), so every claim here is a measured result:

- **"List ALL movies featuring Robert De Niro."** Top-k retrieval cannot promise *all*. Any movie ranked too low is silently dropped, and the answer still looks complete. Measured at 9,076 documents: only 41% of his movies were found. And "featuring" is a relationship — no metadata filter can express it.
- **"How many movies did Woody Allen direct?"** Counting needs to see every movie, not just the 25 most similar text chunks. Measured: the pipeline answered 25, which is exactly its context size. The truth is 42.
- **"Who both directed and acted in the same movie?"** This is a join across documents that never mention each other. There are 291 correct answers. The pipeline found none of them, and the two names it offered were wrong.

These are movie questions only because our benchmark uses the movies dataset. The shapes are exactly the enterprise questions: "all contracts expiring this year" (completeness), "how many portfolio companies are EU-based" (counting), "which vendors reach a sanctioned entity through subsidiaries" (multi-hop).

These failures are quiet. The customer gets a confident, well-cited answer that is wrong by omission. Nothing in our stack can flag it, because every cited fragment is *true* — the error is in what retrieval never surfaced. To be clear, this is not a Vectara defect. It is an architectural property of every top-k retrieval system on the market. That is exactly what makes it an opportunity: the brand built on grounded, hallucination-free answers is best positioned to close it first.

Meanwhile, the systems that answer these questions perfectly — knowledge graphs — already sit inside our customers' data centers: compliance graphs, supply-chain graphs, org and product graphs. A customer who asks *"can Vectara use our knowledge graph?"* today effectively hears **no**: build and host your own tool server, with no support, no vetting, and no credential management.

**In one sentence: Vectara cannot guarantee correct answers to connection-and-completeness questions, and cannot connect to the customer systems that can.**

## 3. What we're proposing (plain language)

Think of it as extending hybrid search. Today, every query blends two signals — keyword matching and semantic similarity — and a reranker merges them into one result list. We propose adding a third signal: the customer's knowledge graph.

The query goes to all three sources. The graph returns precise facts ("Annie Hall — directed by Woody Allen, 1977"). The corpus returns relevant text passages. The existing reranker merges them. The existing generation step writes one answer with one set of citations — some pointing at documents, some at graph facts.

For the customer: the same `/v2/query`, one more data source, and answers that are *complete* (from the graph) and *contextual* (from the documents) at the same time.

## 4. The evidence — measured, not hypothetical

We tested this on the **Neo4j "recommendations" movies dataset**, the standard demo dataset of the graph-database industry: 9,076 movies, 19,047 people, and about 46,000 actor and director relationships.

The setup:

- Every movie is stored in **both systems**: as a text document in a Vectara corpus, and as triples in a knowledge graph.
- The correct answer to every question is computed directly from the relationship data. No hand labeling.
- We ran the test at three corpus sizes: **100, 1,000, and 9,076 documents**.
- We compared **three methods**:
  1. **Vector search** — tuned, not default settings: neural reranker over 100 candidates, gpt-5 generation, 25 results in context.
  2. **Vector search + metadata tools** — for each question, we added the best metadata filter or metadata sort that Vectara ships today. We even wrote the optimal filters by hand. That is generous to the baseline: in production, the system would have to derive those filters automatically.
  3. **The knowledge graph** — one SPARQL query per question.

Everything is reproducible from [`eval/`](https://github.com/Kashif-Rabbani/vectara-agentic-ingestion/tree/main/eval) in the demo repo.

The headline failure, quoted exactly, at full scale:

> **"How many movies in the dataset did Woody Allen direct?"** — correct answer: **42**
>
> The pipeline answered: *"Woody Allen directed 25 movies in this dataset. [1] [2] … [25]"*
>
> This is not a random mistake. **25 is exactly the number of retrieved results the LLM was given.** The model counted what was in its context window and presented that as the total — with a citation for every item. The graph answers 42, every time.

The other failures look the same:

- Asked who both directed and acted in the same movie (**291** correct answers), it returned two names. Both were wrong.
- Asked for the highest-rated movie (Band of Brothers, 9.6), it confidently named a real movie rated 8.4.

Every one of these answers is fluent, cited, and based on genuinely retrieved text. That is what makes them dangerous: **a factual-consistency checker cannot flag any of them**, because every cited fragment is true. The error is in what retrieval never surfaced.

**Scoreboard.** Each cell shows mean recall as `vector / vector+metadata`. The graph scored **1.00 on every graph-shaped question, at every scale**:

| Question class | 100 docs | 1,000 docs | 9,076 docs |
|---|---|---|---|
| Completeness ("list ALL…") | 0.80 / 1.00 | 0.75 / 0.94 | 0.64 / 0.71 |
| Aggregation ("how many…") | 1.00 / 1.00 | 0.00 / 0.00 | 0.50 / 0.00 |
| Ordering ("oldest / highest-rated…") | 0.00 / 0.50 | 1.00 / 1.00 | 0.00 / 0.00 |
| Multi-hop ("actors in X's movies…") | 0.50 / *n.e.* | 0.36 / *n.e.* | 0.26 / *n.e.* |
| **Control — plot similarity (vector's home turf)** | **1.00** | **1.00** | **1.00** |

*n.e. = not expressible. The metadata filter language has no joins, so relationship questions cannot be written as filters.*

What the numbers show:

1. **The failure grows with the size of the answer.** When the correct answer is 8 names, the pipeline finds them all. At 132 names, it finds about half (recall 1.00 → 0.71 → 0.52). At 291 names, it finds none — at every corpus size. The reason is simple: the LLM only sees about 25 retrieved chunks. If the answer is spread across more documents than that, no tuning can help.
2. **Metadata tools help — but only where they apply, and only up to a point.** A year filter fixes "list all movies from 1927" (recall 1.00 at every scale). But a metadata sort can only sort what retrieval already found: "oldest movie" works at 1,000 documents and fails completely at 9,076. Counting fails even when retrieval is perfect: with all 8 correct movies in its context, the model counted 6. And relationship questions cannot be written as filters at all.
3. **Vector search keeps winning its own game.** The control questions (find a movie from its plot description) score 1.00 at every scale. So the eval is not rigged against vector search. Each method is strong where it was built to be strong. That is why we propose *combining* them, not replacing one with the other.

In short: what stays out of reach for vector search, no matter the tuning, is **relationships and computation** — counting, sorting, joining. Those are exactly what a graph query does.

*Notes on rigor: one question per class per tier, single run, 0.4% of documents failed to index, dev tenant. An earlier scoring bug (movie titles stored as "Sound of Music, The") was found and fixed; the fix raised the baseline's scores, and all numbers above are the corrected ones. Details in [`eval/README.md`](https://github.com/Kashif-Rabbani/vectara-agentic-ingestion/blob/main/eval/README.md). The failure class is well documented in research: "RAG fails on global questions directed at an entire text corpus" (Microsoft Research GraphRAG, [arXiv:2404.16130](https://arxiv.org/abs/2404.16130)).*

## 5. What Vectara gains

1. **A stronger anti-hallucination story — our brand.** "Confidently incomplete" answers are a grounding failure that nothing in today's stack — including HHEM — can detect (§4 shows why). Graph fusion eliminates this failure for the question classes above, and the improvement is measurable.
2. **A door into graph-owning enterprises.** Banks, pharma, manufacturing, and MDM-mature companies already own knowledge graphs. First-party connectivity turns "can you use our KG?" from a no into a demo.
3. **One connector covers most of the market.** The connector implements the W3C SPARQL 1.1 standard, not a vendor API. We verified it against Apache Jena Fuseki. GraphDB, Stardog, Virtuoso, Blazegraph, and Amazon Neptune all expose SPARQL 1.1 endpoints, so the same build should work against them; validating two or three of these is a pilot task. There is no per-vendor connector treadmill here.
4. **Timing.** GraphRAG features are shipping across the industry, almost entirely as agent-tool bolt-ons. We are not aware of any platform that fuses graph results through a production reranking, citation, and grounding pipeline (*validate via competitive research before using this claim externally*). Vectara already owns that pipeline. We would be adding a source, not building a system.

**Demand signal — named.** **Broadcom**, an existing customer, asked for a knowledge graph in their CLM project, to model the hierarchy between contracts. Vectara has already built external knowledge graphs for that engagement — as bespoke, per-customer work. This proposal turns that work into a platform capability. Note that the contract questions in §2's mapping are literally this customer's use case. *(Confirm engagement specifics with the account team. More signal to gather: ☐ other prospects with KGs · ☐ deals where this came up.)*

## 6. The ask

Approve a **small, time-boxed pilot**. Size: S — the connector and the benchmark harness already exist.

1. **Ship** the SPARQL connector as a first-party tool. The precedent already exists: the catalog's `sql_query` tool connects to external customer databases (PostgreSQL, MySQL, MariaDB, ClickHouse). SPARQL simply joins that family — this is not a new integration paradigm. The open-source [mcp-server-sparql](https://github.com/Kashif-Rabbani/mcp-server-sparql) is the reference implementation.
2. **Measure** — scale the §4 experiment: about 50 questions per tier to remove the small-sample noise, multiple runs, HHEM and Open RAG Eval scoring, and keep the control questions so the eval keeps showing where graphs do *not* help.
3. **Validate** with one or two design partners who already own a knowledge graph.

**Exit criteria:** a measured quality improvement, plus at least one design partner who wants more. Otherwise we stop, and the sunk cost is the pilot itself. Nothing in the pilot touches the public query API.

The two phases differ in **depth, not deployment.** The pilot lives at the *agent-tool level*: the agent calls the graph the same way it calls `sql_query` today. The *retrieval-pipeline level* — `search.graphs[]` fused inside `/v2/query`, where graph and corpus results are reranked and cited together (§7–8) — is the Phase-2 opportunity that the pilot de-risks. It is not today's ask.

---

**That's the business case. Everything below is technical design detail — read on only if the above convinced you.**

---

## 7. Technical design — the Phase-2 architecture

Section 3 described the idea in one paragraph. This section shows how it maps onto the pipeline Vectara already runs.

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

The flow is the same one multi-corpus search performs today: take the top results from each source, rerank the union, and return the best of it. We add one more source type.

### The key design decision: graph hits become normal search results

Each matched entity is serialized into the exact shape a corpus hit already has:

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

*(The entity and vocabulary come from the §4 experiment's actual graph — our eval already produces this shape.)*

Because graph hits look like ordinary results, **reranking, generation, citations, and every existing customer UI work without modification.** The neural reranker treats a serialized graph fact and a text passage the same way: as text to score. A `userfn` stage can blend the graph's own score into the ranking (§8 shows a starting expression). Citations resolve `entity_uri` instead of a document URL — a template change, not new plumbing.

### What exists vs. what is new

| Needed | Already ships | New work |
|---|---|---|
| SPARQL execution (5+ vendors, 1 protocol) | ✅ mcp-server-sparql | wrap as an internal retrieval source |
| Multi-source fan-out and interleaving | ✅ `search.corpora[]` | add the sibling `search.graphs[]` |
| Cross-source reranking and score fusion | ✅ `chain` / `customer_reranker` / `userfn` | write the default fusion expression |
| Citations over mixed origins | ✅ `citations.url_pattern` templating | an `entity_uri` template variant |
| Credential storage, masking, encryption | ✅ `agent.secrets` (the Wolken pattern) | apply it to graph endpoints |
| Schema grounding for linking and traversal | ✅ SHACL shapes (already used at ingest) | reuse them at query time |
| Answer-quality measurement | ✅ HHEM / Open RAG Eval | build the multi-hop eval set |

### How graph retrieval behaves

- **Entity linking starts explicit.** The caller or the agent supplies entity URIs, or entities are matched against the graph's schema. Fully automatic entity linking from natural language is a later mode; the pilot's eval tells us how feasible it is. Version 1 does not depend on it.
- **Bounded traversal, not LLM-generated SPARQL.** The default is a depth-limited walk from the linked entities (depth ≤ 2, hard timeout). It is deterministic, safe from injection, and cost-capped. Free-form natural-language-to-SPARQL stays an agent-tool capability, never the query-path default.
- **Read-only.** The retrieval path only reads (SELECT/CONSTRUCT-class queries). Writes exist only as explicitly enabled agent tools.

## 8. Proposed API surface

The shapes below are illustrative — concrete enough to react to, not final.

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

The response is a normal `/v2/query` response: `search_results[]` interleaving graph and corpus hits (exactly as multi-corpus interleaves today), one `summary`, `[N]` citations spanning both sources, one HHEM score.

## 9. Security model

- **Credentials** live in `agent.secrets`: encrypted at rest with the agent's KMS key, masked as `****` in event streams. This is the same service-account pattern Wolken uses today — and an improvement over `sql_query`, which takes connection details per call.
- **Outbound calls** to customer database endpoints are an already-shipped pattern (`sql_query`, `web_get`). The SSRF review extends an accepted precedent rather than opening a new risk class.
- **Writes are off by default** (`write_enabled: false`). Enabling them exposes write tools only to explicitly configured agents — never to the retrieval path.

## 10. Alternatives considered

| Alternative | Why not (as the end state) |
|---|---|
| **Status quo — customer self-hosts a generic MCP server** | No adoption path: no credentials, no vetting, no discoverability. Answer quality depends on the LLM choosing to call the tool correctly on every turn. |
| **Agent-tool-only forever (pilot as the end state)** | Never reaches the direct `/v2/query` surface. Graph and corpus results are never reranked or cited together. Differentiation stays at parity with everyone else's bolt-ons. |
| **Skip the pilot, build `search.graphs[]` now** | Commits public API surface before there is measured evidence or demand. |
| **Vendor-specific connector (e.g. Neo4j/Cypher only)** | SPARQL 1.1 covers five-plus vendors in one implementation. A Cypher adapter can be added later behind the same `search.graphs[]` shape. |

## 11. Risks & open questions

- **Entity-linking quality** is the hardest part of a future automatic mode. It gets its own eval line in the pilot; version 1 does not depend on it.
- **Fusion weights** (`graph_score_weight`, the `userfn` expression) need real query data to tune.
- **Traversal cost** needs a hard depth ceiling and timeout, so a pathological graph shape cannot slow down queries.
- **Ownership** — which team owns graph connectivity long-term.

## 12. Success metrics

1. **The structural-gap number:** the fraction of eval questions that vector-only retrieval cannot answer at *any* k, but graph fusion answers correctly. This is the headline.
2. **Answer correctness and completeness** against golden answers (Open RAG Eval methodology), graph-augmented vs. vector-only. HHEM alone cannot measure completeness: an incomplete answer is still "consistent" with its incomplete sources.
3. **Design-partner conversion:** does anyone who sees the pilot want the native integration badly enough to co-design it.

## Appendix A — Sources & confidence levels

Every claim in this document falls into one of three evidence tiers. Reviewers should weigh them accordingly.

### Tier 1 — Verified by direct execution (strongest)

Demonstrated live against the Vectara platform (dev environment, `api.vectara.dev`) during the reference-implementation build:

| Claim | How verified |
|---|---|
| **The §4 experiment**: three methods at three scales on the Neo4j movies dataset; the graph scored 1.00 on every graph-shaped question, tuned vector search failed the large-answer and computation questions (0.00 on the 291-answer join at every scale, wrong counts at 1k/9k), controls scored 1.00 for vector | Executed 2026-07-02/03; harness + per-question raw answers in `eval/`; scoring correction disclosed in `eval/README.md` |
| Agents support `mcp` and `dynamic_vectara` tool types; MCP tool servers register + sync via `POST /v2/tool_servers` | We registered `mcp-server-sparql`, synced 12 tools, ran the agent |
| Full dual-ingestion pipeline works (read → dedup → SHACL → KG write → Vectara index) | 6 companies ingested and cross-verified in both stores; event traces reproducible via `run_ingestion.py` |
| `POST /v2/query` takes `search.corpora[]` (array of `KeyedSearchCorpus`, each with `corpus_key`, `metadata_filter`, `lexical_interpolation`) | [OpenAPI spec](https://api.vectara.io/v2/openapi.json), `SearchCorporaParameters` schema, fetched 2026-07-02 |
| Reranker types `customer_reranker`, `mmr`, `userfn`, `chain` exist | Same OpenAPI spec: `CustomerSpecificReranker`, `MMRReranker`, `UserFunctionReranker`, `ChainReranker` schemas |
| Agent secrets API (`/v2/agents/{key}/secrets`) and tool-server endpoints exist | Same OpenAPI spec, paths section |
| `search.graphs[]` does **not** exist today (this proposal is genuinely new surface) | Same OpenAPI spec: no `graphs` property in `SearchCorporaParameters` |
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
| Customer demand (Broadcom CLM knowledge-graph engagement) | **Author's account knowledge** — directionally solid (existing customer, real KG ask, bespoke KGs already built); confirm specifics with the account team before quoting externally. |

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
