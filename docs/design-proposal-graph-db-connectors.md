# Design Proposal: Graph Database Connectivity for the Vectara Platform

| | |
|---|---|
| **Status** | Draft v4 |
| **Author** | Kashif Rabbani |
| **Working demo** | [vectara-agentic-ingestion](https://github.com/Kashif-Rabbani/vectara-agentic-ingestion) · [mcp-server-sparql](https://github.com/Kashif-Rabbani/mcp-server-sparql) |
| **How to read this** | Sections 1–6 are a ~4-minute business read. Section 7 onward is technical design detail — read only if the first half convinces you. |

---

## 1. TL;DR

Vectara's search is excellent at finding text that is *similar* to a question. It cannot **guarantee** answers to questions that require *connecting or enumerating facts* — "list **all** movies released in 1968," "**how many** films did this director make," "who **both** directed and acted in the same movie." We measured this on the Neo4j movies dataset (§4): asked how many movies Woody Allen directed, the tuned pipeline answered **25**, with a citation for every one — the true count is **42**. The answer is fluent, grounded, and silently wrong, and neither we nor the customer can tell. (An architectural property of top-k retrieval, documented in the literature and measured across three corpus scales in §4.)

Knowledge graphs answer exactly these questions — deterministically and completely. Many enterprise customers already own one. Vectara currently has no first-party way to connect to any of them.

We built and demoed the missing piece: a generic graph-database connector that already works end-to-end with a Vectara agent. **The ask is a small, time-boxed pilot** to host it officially and measure the answer-quality improvement. If the measurements or customer interest aren't there, we stop — the sunk cost is a few weeks. The long-term opportunity, if the pilot succeeds, is a genuine differentiator: graph facts and document search fused into one answer, with one citation model, through the pipeline Vectara already ships.

## 2. Problem Statement

Every Vectara answer today is built the same way: find the passages of text most similar to the user's question, then have an LLM write an answer from those passages. This works remarkably well when the answer *lives in a passage*.

But a whole class of questions doesn't live in any passage. Every example below comes from our benchmark domain — the Neo4j movies dataset (§4) — so every claim here is a measured result, not a thought experiment:

- **"List ALL movies released in 1968."** — *All* is a promise similarity search cannot make. It retrieves the best-matching passages; any movie whose document didn't rank high enough is simply missing — and the answer still reads as complete.
- **"How many movies did Woody Allen direct?"** — Counting requires seeing every movie, not the 25 most relevant chunks. Measured: the pipeline answered **25** — exactly the size of its context window — when the truth is **42**.
- **"Who both directed and acted in the same movie?"** — The answer is a join across documents that never mention each other. There are **291** correct answers in the dataset; the measured pipeline found **0** of them, and the two names it offered don't qualify.

The question *shapes* are exactly the enterprise ones — "all suppliers with contracts expiring this year" (completeness), "how many portfolio companies are EU-based" (aggregation), "which vendors connect to a sanctioned entity through subsidiaries" (multi-hop) — we use the movie forms throughout because we can back each one with data.

These failures are quiet. The customer gets a confident, well-cited, *wrong-by-omission* answer. For a company whose brand is grounded, hallucination-free answers, this is exactly the kind of failure we should refuse to ship — and today we have no mechanism that even detects it.

Meanwhile, the systems that answer such questions perfectly — knowledge graphs and graph databases — are already sitting inside our customers' data centers: compliance graphs in banks, supply-chain graphs in manufacturing, org and product graphs everywhere. A customer who asks *"can Vectara use our knowledge graph?"* today gets, effectively, a **no**: their only option is to build, host, and expose their own tool server, with no Vectara support, security vetting, or credential management.

**The problem, in one sentence: Vectara cannot guarantee correct answers to connection-and-completeness questions, and cannot connect to the customer systems that can.**

## 3. What we're proposing (plain-language)

Think of it as extending Vectara's existing hybrid search. Today, every query already blends **two** retrieval signals — keyword matching and semantic (vector) similarity — and a reranker merges them into the final result list. We propose adding a **third signal: the customer's knowledge graph.**

The query goes to all three sources. Each returns its best results. The graph's results are precise facts ("Annie Hall — directed by Woody Allen, 1977"; "42 films match"); the corpus's results are relevant text passages. The existing reranker merges them, and the existing generation step writes one answer with one set of citations — some pointing at documents, some at graph facts.

The customer experience: ask the question you actually have, get an answer that is both *complete* (graph) and *contextual* (documents). No new UI, no new query API concepts to learn — it's the same `/v2/query` with one more data source attached.

## 4. The evidence — measured, not hypothetical

This proposal's experiment has already had its first run. Dataset: the **Neo4j "recommendations" movies dataset** — the graph-database industry's own standard demo dataset (9,076 movies, 19,047 people, ~46,000 acting/directing relationships, pulled from Neo4j's public demo server). Every movie exists identically in both stores: as a text document (plot, cast, directors, rating) in a Vectara corpus, and as triples in a knowledge graph. Ground truth for every question is computed **from the relationship data itself** — no hand labeling. Three corpus scales (100 / 1,000 / 9,076 documents, nested and seeded), and a **tuned** vector baseline: neural reranker over 100 candidates, gpt-5 generation, 25 results in context — not the defaults. Harness and raw outputs: [`eval/`](https://github.com/Kashif-Rabbani/vectara-agentic-ingestion/tree/main/eval).

**Three questions at full scale (9,076 movies) — vector answers verbatim:**

> **"How many movies in the dataset did Woody Allen direct?"** — truth: **42**
>
> Vector + generation answered: *"Woody Allen directed 25 movies in this dataset. [1] [2] … [25]"*
>
> Not a random error: **25 is exactly the number of retrieved results the LLM was given** (`max_used_search_results: 25`). It counted its context window and presented that as the dataset total — with a citation for every one. Fully grounded, confidently wrong. The graph answers 42, deterministically.

> **"Which people both directed and acted in the same movie?"** — truth: **291 people**
>
> Vector + generation answered: *"Tony Scott (directed and acted in The Hunger); Brett Morgen (directed and acted in On the Ropes)"* — two names, and per the dataset's relationship structure **both are wrong**; all 291 correct answers were missed. The graph: one SPARQL self-join, all 291.

> **"Which movie has the highest IMDb rating?"** — truth: **Band of Brothers (9.6)**
>
> Vector + generation answered: *"Man with the Movie Camera (1929) with a rating of 8.4. [2]"* — a real movie, its real rating, correctly cited, and not the answer.

Every one of these answers is fluent, cited, and grounded in genuinely retrieved text. **A factual-consistency checker cannot flag any of them** — each cited fragment is true. The failure lives in what retrieval never surfaced.

**Full scoreboard — mean vector-only recall per class (graph = 1.00 on every graph-shaped question, at every scale):**

| Question class | 100 docs | 1,000 docs | 9,076 docs |
|---|---|---|---|
| Completeness ("list ALL…") | 0.27 | 0.38 | 0.50 |
| Aggregation ("how many…") | 1.00 | 0.00 | 0.50 |
| Ordering ("oldest / highest-rated…") | 0.00 | 1.00 | 0.00 |
| Multi-hop ("actors in X's movies…") | 0.50 | 0.36 | 0.26 |
| **Control — plot similarity (vector's home turf)** | **1.00** | **1.00** | **1.00** |

Two structural findings: (1) **failure scales with answer size** — the multi-hop join degrades monotonically as the true answer grows (8 → 28 → 132 names: recall 1.00 → 0.71 → 0.52), and the 291-answer question scores 0.00 at every scale, because an answer set larger than the context budget cannot be assembled by any amount of tuning; (2) **the controls hold** — plot-similarity questions score 1.00 for vector search at every tier. Each method wins where it is structurally suited: the case is *fusion*, not replacement.

*Caveats: one question per class per tier (the ordering 0→1→0 flip is small-n noise), single run, 36 of 9,076 documents (0.4%) failed to index, dev tenant. The pilot (§6) scales the battery to ~50 questions.*

This failure class is documented in the literature, not just our experiment: Microsoft Research's GraphRAG paper opens from the same observation — "RAG fails on global questions directed at an entire text corpus" (Edge et al., [arXiv:2404.16130](https://arxiv.org/abs/2404.16130)).

## 5. What Vectara gains

1. **A stronger anti-hallucination story — our core brand.** "Confidently incomplete" answers are a grounding failure that today's stack can neither prevent nor detect — note that HHEM cannot catch them either, since every cited fragment in an incomplete answer *is* consistent with its sources. Graph fusion eliminates the failure for the question classes above, and a labeled eval set (Open RAG Eval methodology, golden answers) lets us *quantify* the improvement — a marketable number, not a claim.
2. **A door into graph-owning enterprises.** Banks, pharma, manufacturing, and MDM-mature companies already own knowledge graphs. First-party connectivity turns "can you use our KG?" from a no into a demo.
3. **One connector, most of the market.** Our reference server implements the W3C SPARQL 1.1 Protocol — a formal standard, not a vendor API. Verified end-to-end against Apache Jena Fuseki; GraphDB, Stardog, Virtuoso, Blazegraph, and Amazon Neptune all publish SPARQL 1.1-compliant endpoints, so the same build is expected to work against them (validating 2–3 of these is a pilot task). This is not a per-vendor connector treadmill.
4. **Differentiated timing.** GraphRAG approaches are shipping across the industry (Microsoft GraphRAG, Neo4j GenAI integrations, Neptune with Bedrock) — predominantly as agent-tool bolt-ons. We are not aware of any platform that fuses graph results through a production reranking, citation, and grounding pipeline (*validate via competitive research before using externally*). Vectara already owns that pipeline; we'd be adding a source, not building a system.

**Honest gap:** we don't yet have named customer demand — that signal should be gathered from sales/CS in parallel with the pilot. *(To fill in: ☐ prospects with existing KGs · ☐ deals where this came up · ☐ community asks.)*

## 6. The ask

Approve a **small, time-boxed pilot** (size S — the hard part is already built and demoed):

1. **Host** the existing SPARQL connector as a Vectara-certified tool (today a customer must self-host and tunnel it — a non-starter).
2. **Measure — scale the §4 experiment.** The harness is built and committed ([`eval/`](https://github.com/Kashif-Rabbani/vectara-agentic-ingestion/tree/main/eval)); the pilot scales it: ~50 questions per tier (killing the small-n noise visible in §4's ordering row), multiple runs, HHEM + Open RAG Eval scoring alongside entity recall, and control questions retained so the eval keeps showing where graphs *don't* help.
3. **Validate**: run it with 1–2 design partners who already own a knowledge graph.

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
- Hosting the connector means Vectara makes outbound calls to customer-supplied URLs → requires the standard SSRF-class network review.
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
- **Internal precedent check** — whether Jira/Slack/Wolken integrations are backend-native or hosted connectors under the hood (unverified; affects how novel a "new source type" is architecturally).

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
| Jira/Slack/Wolken are backend-native integrations | **Unverified assumption**, flagged throughout. |
| SPARQL connector works unmodified against GraphDB/Stardog/Virtuoso/Neptune | **Standard-compliance inference** — only Fuseki is tested. Pilot task. |
| Competitive landscape (agent-tool bolt-ons; no reranking-fused competitor) | **Author's knowledge, early 2026, not systematically researched.** Validate before external use. |
| Entity-linking approach, traversal bounds, fusion weights | **Original design proposal** — no precedent claimed. |
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
