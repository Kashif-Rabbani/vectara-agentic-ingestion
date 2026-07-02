# vectara-agentic-ingestion

Two things live in this repo:

1. **An agentic dual-ingestion demo** — a [Vectara](https://vectara.com) agent reads plain-text documents, extracts structured data, validates it with SHACL, and writes it into a Knowledge Graph (Apache Jena Fuseki, via the generic [SPARQL 1.1 MCP server](https://github.com/Kashif-Rabbani/mcp-server-sparql)) *and* a Vectara corpus, in a single agent session.
2. **A graph-vs-vector retrieval evaluation** ([`eval/`](eval/)) — a reproducible experiment on the Neo4j movies dataset (9,076 movies) measuring the question classes vector search structurally cannot answer completely, and that a knowledge graph answers exactly. This is the empirical evidence behind [`docs/design-proposal-graph-db-connectors.md`](docs/design-proposal-graph-db-connectors.md).

## Headline result (eval)

Tuned vector search + generation vs. one SPARQL query, on data-driven questions with exact ground truth, at three corpus scales:

| Question class | Vector T-100 | Vector T-1k | Vector T-9k | Graph (all) |
|---|---|---|---|---|
| Completeness ("list ALL…") | 0.27 | 0.38 | 0.50 | **1.00** |
| Aggregation ("how many…") | 1.00 | 0.00 | 0.50 | **1.00** |
| Ordering ("oldest / highest…") | 0.00 | 1.00 | 0.00 | **1.00** |
| Multi-hop ("actors in X's movies…") | 0.50 | 0.36 | 0.26 | **1.00** |
| Control — plot similarity (vector's home turf) | **1.00** | **1.00** | **1.00** | — |

Key finding: the failure scales with **answer size** — a multi-hop question with 291 correct answers scores 0.00 at every tier, because no top-k retrieval can hand the LLM an answer set larger than its context budget. Controls stay perfect for vector search: each method wins where it's structurally suited, which is the case for *fusion*, not replacement.

Reproduce (~30 min, mostly indexing):

```bash
python eval/extract_neo4j.py    # pull dataset from Neo4j's public demo server
python eval/load_tiers.py       # dual-ingest 3 nested tiers (corpora + named graphs)
python eval/run_eval.py         # run the battery, score, write eval/results/
```

---

# The agentic ingestion demo

The agent reads company profile documents and dual-ingests in one session:

1. **Apache Jena Fuseki** — a Knowledge Graph via SPARQL UPDATE (Schema.org triples)
2. **Vectara corpus** — for semantic search via the built-in `text_to_core_document` + `core_document_index` tools

---

## Architecture

```
 Company profile .txt
        │  (uploaded as artifact)
        ▼
 ┌─────────────────────────────────────────────────────────────┐
 │  Vectara Agent  (agt_agentic-ingestion-agent)               │
 │                                                             │
 │  Step 0  endpoint_ping          ← connectivity check        │
 │  Step 1  artifact_read          ← read uploaded .txt        │
 │  Step 2  sparql_ask             ← duplicate check in KG     │
 │  Step 3  validate_shacl         ← SHACL conformance check   │
 │  Step 4  sparql_update          ← INSERT triples into KG    │
 │  Step 5  text_to_core_document  ← chunk text artifact       │
 │  Step 6  core_document_index    ← index into Vectara corpus │
 │  Step 7  graph_list             ← confirm KG state          │
 └─────────────────────────────────────────────────────────────┘
        │                          │
        ▼                          ▼
 Apache Jena Fuseki          Vectara Corpus
 (SPARQL 1.1 KG)             (semantic search)
```

**MCP tools (Steps 0–2, 4, 7)** are provided by [`mcp-server-sparql`](https://github.com/Kashif-Rabbani/mcp-server-sparql) — a generic SPARQL 1.1 MCP server that exposes 12 tools with zero connection parameters in tool calls (endpoint configured once via `.env`).

**Vectara built-in tools (Steps 3, 5, 6)** are registered directly on the agent: `validate_shacl`, `text_to_core_document_20260526`, `core_document_index_20260220`.

---

## Repository layout

```
.
├── server.py                      # MCP server entry point (FastMCP, SSE transport)
├── tools/
│   ├── _common.py                 # Reads SPARQL_* env vars, exports URL constants
│   ├── sparql_query.py            # sparql_select / ask / construct / describe
│   ├── sparql_update.py           # sparql_update
│   ├── graph_store.py             # graph_list / get / put / post / delete
│   ├── shacl.py                   # validate_shacl (local pyshacl — no endpoint needed)
│   └── vectara.py                 # standalone Vectara REST indexing helper (unused by agent)
├── scripts/
│   ├── create_agent.py            # Register MCP server + create Vectara agent
│   ├── run_ingestion.py           # Upload profiles → agent processes each one
│   ├── direct_test.py             # Test MCP tools directly (no agent needed)
│   └── verify_ingestion.py        # Cross-check KG vs Vectara after ingestion
├── data/
│   └── company_profiles/          # Six AI company .txt profiles
│       ├── openai.txt
│       ├── deepmind.txt
│       ├── huggingface.txt
│       ├── mistral.txt
│       ├── cohere.txt
│       └── perplexity.txt
├── shapes/
│   └── company.ttl                # SHACL shapes for schema:Organization
├── setup_fuseki.sh                # Start Apache Jena Fuseki in Docker
├── requirements.txt
└── .env.example
```

---

## Setup

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Fill in your VECTARA_API_KEY and adjust SPARQL_* if not using local Fuseki
```

### 3. Start Apache Jena Fuseki

The Docker image's entrypoint does not process `--update --mem /ds` correctly on ARM64 (Apple Silicon). Call the binary directly to bypass it:

```bash
docker run --rm -d --name jena-fuseki -p 3030:3030 \
    stain/jena-fuseki /jena-fuseki/fuseki-server --update --mem /ds
```

> **Note on the admin password:** when the entrypoint is bypassed, `ADMIN_PASSWORD` is never processed. The default from `shiro.ini` is used. To find it:
> ```bash
> docker exec jena-fuseki grep admin /fuseki/shiro.ini
> ```
> Set `SPARQL_PASSWORD` in your `.env` to that value.

### 4. Start the MCP server

```bash
python server.py
# SSE endpoint: http://localhost:8000/sse
```

### 5. Expose the MCP server publicly

Vectara's cloud agent must reach your MCP server. Use ngrok or any tunnel:

```bash
ngrok http 8000
# Copy the https URL, e.g. https://abc123.ngrok-free.app
```

### 6. Create the Vectara agent

```bash
python scripts/create_agent.py --mcp-url https://abc123.ngrok-free.app
```

This registers the MCP server with Vectara, syncs the 12 SPARQL tools, and creates the ingestion agent. Agent key and server ID are saved to `.agent_state.json`.

To recreate an agent (delete the old one first):

```bash
python scripts/create_agent.py --mcp-url https://... --delete-existing
```

---

## Running the ingestion

```bash
# All six company profiles
python scripts/run_ingestion.py

# Single document
python scripts/run_ingestion.py --doc openai.txt
```

Each document gets its own agent session. The agent prints a full event trace:

```
[tool_call] endpoint_ping
[tool_out]  endpoint_ping → {"reachable": true, "http_status": 200, ...}
[tool_call] artifact_read
[tool_out]  artifact_read → {"content": "1:OpenAI Company Profile\n..."}
[tool_call] sparql_ask
[tool_out]  sparql_ask → {"result": false}
[tool_call] validate_shacl
[tool_out]  validate_shacl → {"conforms": true, "violations": []}
[tool_call] sparql_update
[tool_out]  sparql_update → {"http_status": 204}
[tool_call] text_to_core_document_20260526
[tool_out]  text_to_core_document_20260526 → {"num_parts": 2, "chars_read": 1762}
[tool_call] core_document_index_20260220
[tool_out]  core_document_index_20260220 → {"document_id": "company-openai"}
```

Duplicate detection: if `sparql_ask` returns `true`, the agent skips KG + Vectara ingestion and reports the company as already present.

---

## Verifying results

```bash
python scripts/verify_ingestion.py
```

Queries both stores and prints a cross-check table:

```
  Company              In KG      In Vectara
  ──────────────────── ────────── ──────────
  Cohere               ✓          ✓
  Google DeepMind      ✓          ✓
  Hugging Face         ✓          ✓
  Mistral AI           ✓          ✓
  OpenAI               ✓          ✓
```

---

## Testing MCP tools directly (no agent)

`direct_test.py` calls the MCP tool functions directly — useful for iterating on SHACL shapes or Turtle formats without needing a Vectara agent or ngrok.

```bash
# Full test (requires Fuseki + valid Vectara API key)
python scripts/direct_test.py

# Skip Vectara indexing (Fuseki only)
python scripts/direct_test.py --skip-vectara

# Reset the named graph and start fresh
python scripts/direct_test.py --reset --skip-vectara
```

---

## Environment variables

| Variable | Description | Default |
|---|---|---|
| `SPARQL_ENDPOINT` | Base URL; auto-derives `/query`, `/update`, `/data` | `http://localhost:3030/ds` |
| `SPARQL_QUERY_URL` | Override query URL (for GraphDB, Virtuoso, etc.) | _(derived)_ |
| `SPARQL_UPDATE_URL` | Override update URL | _(derived)_ |
| `SPARQL_GRAPH_STORE_URL` | Override graph store URL | _(derived)_ |
| `SPARQL_USERNAME` | Basic-auth username | _(empty)_ |
| `SPARQL_PASSWORD` | Basic-auth password | _(empty)_ |
| `VECTARA_BASE_URL` | Vectara API base URL | `https://api.vectara.io/v2` |
| `VECTARA_API_KEY` | Vectara Application API key | _(required)_ |
| `MCP_HOST` | MCP server bind address | `0.0.0.0` |
| `MCP_PORT` | MCP server bind port | `8000` |

---

## SHACL shapes

`shapes/company.ttl` enforces the `schema:Organization` pattern used for KG ingestion. The agent runs `validate_shacl` before any `sparql_update` call — non-conforming data is rejected and reported without touching the KG.

---

## Related

- [`mcp-server-sparql`](https://github.com/Kashif-Rabbani/mcp-server-sparql) — the standalone generic SPARQL MCP server extracted from this project
- [Vectara documentation](https://docs.vectara.com)
