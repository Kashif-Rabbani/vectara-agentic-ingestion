# Design Proposal: Graph Database Connectivity for the Vectara Platform

| | |
|---|---|
| **Status** | Draft v4 |
| **Author** | Kashif Rabbani |
| **Working demo** | [vectara-agentic-ingestion](https://github.com/Kashif-Rabbani/vectara-agentic-ingestion) · [mcp-server-sparql](https://github.com/Kashif-Rabbani/mcp-server-sparql) |
| **How to read this** | Sections 1–6 are a ~4-minute business read. Section 7 onward is technical design detail — read only if the first half convinces you. |

---

## 1. TL;DR

Vectara's search is excellent at finding text that is *similar* to a question. It cannot **guarantee** answers to questions that require *connecting or enumerating facts* — "list **all** companies founded before 2020," "**how many** vendors are EU-based," "which two customers **share** a parent company." For those, today's pipeline can return a fluent answer that is silently incomplete — and neither we nor the customer can tell. (This is an architectural property of top-k retrieval, documented in the literature; we have also reproduced it on our own demo corpus — how *often* it bites at production scale is exactly what the proposed pilot measures.)

Knowledge graphs answer exactly these questions — deterministically and completely. Many enterprise customers already own one. Vectara currently has no first-party way to connect to any of them.

We built and demoed the missing piece: a generic graph-database connector that already works end-to-end with a Vectara agent. **The ask is a small, time-boxed pilot** to host it officially and measure the answer-quality improvement. If the measurements or customer interest aren't there, we stop — the sunk cost is a few weeks. The long-term opportunity, if the pilot succeeds, is a genuine differentiator: graph facts and document search fused into one answer, with one citation model, through the pipeline Vectara already ships.

## 2. Problem Statement

Every Vectara answer today is built the same way: find the passages of text most similar to the user's question, then have an LLM write an answer from those passages. This works remarkably well when the answer *lives in a passage*.

But a whole class of business questions doesn't live in any passage:

- **"List all suppliers with contracts expiring this year."** — *All* is a promise similarity search cannot make. It retrieves the best-matching passages; if supplier #14's contract is mentioned in a document that didn't rank high enough, it's simply missing from the answer — and the answer still reads as complete.
- **"How many of our portfolio companies are based in the EU?"** — Counting requires seeing every company, not the 10 most relevant paragraphs.
- **"Which vendors are connected to a sanctioned entity through subsidiaries?"** — The connection is spread across documents that never mention each other. No single passage contains the answer, so no passage-retrieval system can find it.

These failures are quiet. The customer gets a confident, well-cited, *wrong-by-omission* answer. For a company whose brand is grounded, hallucination-free answers, this is exactly the kind of failure we should refuse to ship — and today we have no mechanism that even detects it.

Meanwhile, the systems that answer such questions perfectly — knowledge graphs and graph databases — are already sitting inside our customers' data centers: compliance graphs in banks, supply-chain graphs in manufacturing, org and product graphs everywhere. A customer who asks *"can Vectara use our knowledge graph?"* today gets, effectively, a **no**: their only option is to build, host, and expose their own tool server, with no Vectara support, security vetting, or credential management.

**The problem, in one sentence: Vectara cannot guarantee correct answers to connection-and-completeness questions, and cannot connect to the customer systems that can.**

## 3. What we're proposing (plain-language)

Think of it as extending Vectara's existing hybrid search. Today, every query already blends **two** retrieval signals — keyword matching and semantic (vector) similarity — and a reranker merges them into the final result list. We propose adding a **third signal: the customer's knowledge graph.**

The query goes to all three sources. Each returns its best results. The graph's results are precise facts ("DeepMind, founded 2010"; "A and B share investor X"); the corpus's results are relevant text passages. The existing reranker merges them, and the existing generation step writes one answer with one set of citations — some pointing at documents, some at graph facts.

The customer experience: ask the question you actually have, get an answer that is both *complete* (graph) and *contextual* (documents). No new UI, no new query API concepts to learn — it's the same `/v2/query` with one more data source attached.

## 4. A concrete example — measured, not hypothetical

We ran this test on **2026-07-02** against our live demo systems: a Vectara corpus of AI-company profiles (dev tenant, corpus `agentic_ingestion_kashif`) and the knowledge graph populated by our agent pipeline.

> **Query: "List all companies founded before 2020, oldest first, with their founding years."**

**Path A — Vectara vector search + generation, as shipped today**
(`POST /v2/corpora/.../query`, gpt-5 generation preset, `limit: 10`, `max_used_search_results: 8` — standard settings)

```
- Weaviate — 2019
- Pinecone — 2019
```

That is the complete generated answer. The corpus actually contains **six** companies founded before 2020: Google DeepMind (2010), OpenAI (2015), Hugging Face (2016), Cohere (2019), Pinecone (2019), Weaviate (2019). The answer found **2 of 6 (33% recall)**, omitted the four oldest companies entirely — while answering a question that literally asked for "oldest first" — and presented itself as complete, with citations. The retrieval trace shows why: chunks from a leftover test document and from profiles of *non-qualifying* companies outranked the DeepMind, OpenAI, and Hugging Face chunks, which never reached the LLM at all. Every cited fact in the answer is true — the answer is grounded, fluent, and wrong by omission. **No existing mechanism (including factual-consistency scoring) flags it.**

**Path B — the knowledge graph, same question as a SPARQL filter**

```
Google DeepMind (2010)
OpenAI (2015)
Hugging Face (2016)
Cohere (2019)
```

Complete with respect to the graph's contents, correctly ordered, deterministic — the same result every time, or an explicit empty result.

**What this test proves — and what it doesn't.** It is an **existence proof**: the failure occurs in the shipped product, with standard settings, on real data — the mechanism (relevant chunks outranked and never reaching the LLM) is real, not theoretical. It is **not** a measurement of how often this happens at production scale: it is one query, on a small corpus that contains leftover test documents, with untuned retrieval settings — a fair skeptic would note that raising limits or enabling a reranker chain could likely recover the missing companies *at this size*. Two things survive that objection: (1) no top-k configuration at any size *guarantees* completeness — that guarantee requires a retrieval layer that computes over all entities, i.e. a database query; and (2) the frequency-at-scale question is empirical, and answering it rigorously — on a large corpus, against a well-tuned baseline — is precisely the pilot's eval (§6, step 2). The stores' contents also differ slightly (the corpus holds legacy profiles the graph doesn't), so each answer above is judged against its own store's ground truth. Full request/response for reproduction is in the demo repo.

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
2. **Measure — the centerpiece.** A controlled, scaled experiment, because the single demo run in §4 is an existence proof, not a conclusion:
   - **Dataset:** a synthetic entity corpus (~1,000 organizations with profiles generated from structured records) dual-ingested into a fresh corpus and graph — ground truth is exact *by construction*, since every profile is derived from known structured facts.
   - **Question battery:** ~50 questions across the failure classes (completeness "list all X", aggregation "how many X", ordering "oldest/largest X", multi-hop "which X share Y") plus control questions vector search *should* win (descriptive, similarity-style), so the eval can also show where graphs *don't* help.
   - **Fair baseline:** vector-only runs with a well-tuned configuration — reranker chain enabled, generous limits — not the defaults that failed in §4.
   - **Headline output:** answer recall/precision vs. golden answers at increasing corpus sizes (100 → 1,000 → 10,000 docs). If the thesis is right, the vector-only recall curve degrades with scale while the graph-fused curve stays flat — that plot is the deliverable.
3. **Validate**: run it with 1–2 design partners who already own a knowledge graph.

### Preview — a first run of this experiment already exists

We ran a first version of the scaled eval on the **Neo4j "recommendations" movies dataset** (the standard graph-database demo dataset: 9,076 movies, 19,047 people, 35,778 acting + 9,955 directing relationships, pulled from Neo4j's public demo server). Three nested, seeded tiers (100 ⊂ 1,000 ⊂ 9,076 movies), each dual-ingested into its own Vectara corpus and its own named graph; a data-driven question battery with exact ground truth computed from the relationship data; the vector baseline **tuned** (neural reranker over 100 candidates, gpt-5 generation, 25 results in context). Harness and raw answers: [`eval/`](https://github.com/Kashif-Rabbani/vectara-agentic-ingestion/tree/main/eval) in the demo repo.

**Vector-only recall per question (graph = 1.00 on every graph-shaped question, at every scale):**

| Question (class) | T-100 | T-1k | T-9k | gold size at 9k |
|---|---|---|---|---|
| List all movies from year X *(completeness)* | 0.20 | 0.12 | 0.62 | 8 |
| List all movies featuring actor X *(completeness)* | 0.33 | 0.62 | 0.38 | 56 |
| How many movies did director X direct? *(aggregation)* | 1.00 | 0.00 | 0.00 | 42 |
| How many movies released in year X? *(aggregation)* | 1.00 | 0.00 | 1.00 | 8 |
| Oldest movie? *(ordering)* | 0.00 | 1.00 | 0.00 | 1 |
| Highest IMDb rating? *(ordering)* | 0.00 | 1.00 | 0.00 | 1 |
| Actors in movies directed by X? *(multi-hop)* | 1.00 | 0.71 | 0.52 | 132 |
| Who both directed and acted in same movie? *(multi-hop)* | **0.00** | **0.00** | **0.00** | 291 |
| Which movie is this plot? *(control ×2 — vector's home turf)* | 1.00 | 1.00 | 1.00 | 1 |
| **Mean, graph-shaped questions** | **0.44** | **0.43** | **0.32** | |

Three findings:

1. **The failure scales with answer size, not just corpus size.** The multi-hop join ("actors in X's movies") degrades monotonically as the true answer grows (8 → 28 → 132 names: recall 1.00 → 0.71 → 0.52), and the self-join with 291 correct answers scores 0.00 at every scale. The mechanism is structural: the LLM sees at most ~25 retrieved chunks — an answer set larger than the context budget **cannot** be assembled by any tuning. A graph computes it in one query.
2. **Counting collapses once the count exceeds what fits in context.** "How many did Woody Allen direct?" (42): the tuned pipeline confidently answers with the wrong number at 1k and 9k.
3. **The controls hold.** Plot-similarity questions score 1.00 for vector search at every tier — each method wins where it's structurally suited, which is precisely the case for *fusion* rather than replacement.

**Caveats, honestly:** one question per class per tier (small n — ordering flips 0→1→0 on famous-vs-obscure titles, visible noise); single run; 36 of 9,076 documents (0.4%) failed to index in the 9k corpus; dev tenant. The full pilot scales the battery to ~50 questions and adds HHEM/Open RAG Eval scoring — but the structural pattern is already unambiguous.

**Exit criteria:** a measured quality delta plus at least one design partner who wants more. If either is missing, we stop — total cost is the pilot itself. Nothing in it touches the public query API.

The deeper integration (graphs as a native source inside `/v2/query`) is the Phase-2 opportunity the pilot de-risks — it is described below, and it is **not** today's ask.

---

**That's the business case. Everything below is technical design detail — read on only if the above convinced you.**

---

## 7. Technical design — end-state architecture (Phase 2)

Today `/v2/query` fuses two signals — lexical (BM25-style) and semantic (dense vectors) — via `lexical_interpolation`, then reranks the merged candidate pool. The proposal adds graph traversal as the third:

```
                              /v2/query
       "Which companies founded before 2020 share an investor?"
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
  "text": "Mistral AI — founded 2023, Paris. Investors: a16z, ... Shares investor a16z with: ...",
  "document_id": "graph:company-kg:entity/mistral-ai",
  "document_metadata": {
    "source": "graph",
    "graph_key": "company-kg",
    "entity_uri": "http://agentic-ingestion/entity/company/mistral-ai",
    "relation_path": ["schema:investor"],
    "graph_score": 0.8
  }
}
```

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
  "graph_key": "company-kg",
  "protocol": "sparql11",                      # one protocol, many vendors
  "endpoint": { "query_url": "https://kg.customer.com/ds/query" },
  "credentials_ref": "agent.secrets.KG_BASIC_AUTH",    # never plaintext in payload
  "schema_ref": "shapes/company.ttl",           # SHACL/ontology for linking + traversal scoping
  "write_enabled": false                         # default
}

# Standalone graph query — mirrors /v2/corpora/{key}/query
POST /v2/graphs/{graph_key}/query
{
  "query": "companies founded before 2020",
  "search": { "entity_linking": "explicit", "entity_uris": [...],
              "traversal_depth": 2, "limit": 20 }
}
# → returns search_results[] — identical contract to a corpus query

# THE integration point — /v2/query grows a sibling array
POST /v2/query
{
  "query": "Which companies founded before 2020 share an investor?",
  "search": {
    "corpora": [ { "corpus_key": "company-profiles", "lexical_interpolation": 0.025 } ],
    "graphs":  [ { "graph_key": "company-kg", "traversal_depth": 2,
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
| **The §6 scaled experiment**: on the Neo4j movies dataset (9,076 movies, 3 tiers), tuned vector-only retrieval scored 0.32–0.44 mean recall on graph-shaped questions vs. 1.00 for SPARQL, with controls at 1.00 for vector | Executed 2026-07-02; harness + per-question raw answers in `eval/` (extract → dual-ingest → battery → score, fully reproducible) |
| **The §4 side-by-side failure is real**: vector search + generation returned 2 of 6 qualifying companies (33% recall) on a completeness question, while SPARQL returned its complete set | Executed live 2026-07-02 against corpus `agentic_ingestion_kashif` (gpt-5 preset) and the demo knowledge graph; raw outputs quoted verbatim in §4 |
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
