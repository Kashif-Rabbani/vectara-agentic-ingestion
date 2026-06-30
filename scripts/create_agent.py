#!/usr/bin/env python3
"""
Registers the generic SPARQL MCP tool server with Vectara and creates the
agentic ingestion agent.

Prerequisites:
  - MCP server running: python server.py
  - MCP server publicly accessible (Vectara cloud must reach it):
      ngrok http 8000     # then pass the printed https URL as --mcp-url
  - Valid VECTARA_API_KEY in .env (corpus agentic_ingestion_kashif must exist)
  - Apache Jena Fuseki running: ./setup_fuseki.sh

Usage:
  python scripts/create_agent.py --mcp-url https://abc123.ngrok.io
  python scripts/create_agent.py --mcp-url https://abc123.ngrok.io --delete-existing
"""
import argparse
import json
import os
import sys
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL    = os.getenv("VECTARA_BASE_URL", "https://api.vectara.io/v2")
API_KEY     = os.getenv("VECTARA_API_KEY")
CORPUS_KEY  = "agentic_ingestion_kashif"
GRAPH_URI   = "http://agentic-ingestion/graph/tech-companies"
ENTITY_BASE = "http://agentic-ingestion/entity/company"
AGENT_NAME  = "agentic-ingestion-agent"
SERVER_NAME = "agentic-ingestion-jena-mcp"
STATE_FILE  = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".agent_state.json")

SHAPES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "shapes", "company.ttl",
)

INGESTION_INSTRUCTIONS = f"""\
You are an agentic ingestion pipeline. Your job is to read company profile documents,
extract structured organization data, and dual-ingest it into:
  1. A SPARQL Knowledge Graph (pre-configured endpoint)
  2. A Vectara semantic search corpus

## Configuration
- Target graph    : {GRAPH_URI}
- SHACL shapes    : {SHAPES_FILE}
- Vectara corpus  : {CORPUS_KEY}
- Entity base URI : {ENTITY_BASE}

The SPARQL endpoint connection is pre-configured in the MCP server — do NOT pass
endpoint_url or auth to any SPARQL tool. All tools connect automatically.

## Workflow — follow every step for every company you find

### Step 0 — Verify connectivity
Call endpoint_ping. If reachable is false, stop and report the error.

### Step 1 — Read the document
Call artifact_read with the provided artifact_id.

### Step 2 — Extract company data
From the text, identify:
- Company name (required)
- Website URL (optional; must start with https://)
- One-sentence description (optional)
- Founding year as a 4-digit string, e.g. "2021" (optional)

### Step 3 — Build the entity URI
slug = lowercase company name, spaces → hyphens
URI  = {ENTITY_BASE}/<slug>
Example: "Vectara" → {ENTITY_BASE}/vectara

### Step 4 — Duplicate check
Call sparql_ask:
  query: "ASK {{ GRAPH <{GRAPH_URI}> {{ <ENTITY_URI> ?p ?o }} }}"

If result is true → skip to step 8 (do NOT re-ingest).

### Step 5 — Build the Turtle snippet
Use this exact format, omitting optional fields you have no data for:

@prefix schema: <https://schema.org/> .
@prefix xsd:    <http://www.w3.org/2001/XMLSchema#> .

<ENTITY_URI>
    a schema:Organization ;
    schema:name "Company Name" ;
    schema:url <https://example.com> ;          {{# optional #}}
    schema:description "One sentence." ;         {{# optional #}}
    schema:foundingDate "2021"^^xsd:gYear .      {{# optional #}}

Rules:
- schema:name is mandatory.
- schema:url value must be a URI in angle brackets — NOT a quoted string.
- foundingDate MUST use ^^xsd:gYear datatype suffix.

### Step 6 — SHACL validation
Call validate_shacl:
  data_graph   : <your Turtle snippet from step 5>
  shapes_graph : "{SHAPES_FILE}"

If conforms is false → do NOT continue. Report the violations and stop for this company.

### Step 7 — Ingest into the Knowledge Graph
Call sparql_update with the update string (include PREFIX declarations):

  PREFIX schema: <https://schema.org/>
  PREFIX xsd:    <http://www.w3.org/2001/XMLSchema#>
  INSERT DATA {{
    GRAPH <{GRAPH_URI}> {{
      <ENTITY_URI> a schema:Organization ;
                   schema:name "..." ;
                   schema:url <...> ;
                   schema:description "..." ;
                   schema:foundingDate "..."^^xsd:gYear .
    }}
  }}

Include only the fields you have data for. Omit optional fields entirely if missing.

### Step 7b — Index into Vectara
Two sub-steps:

a. Call text_to_core_document_20260526 to convert the original text artifact into a
   CoreDocument artifact ready for indexing:
     artifact_id : (the same artifact_id from Step 1 — the uploaded .txt file)

   This returns a new artifact_id containing the CoreDocument JSON.

b. Call core_document_index_20260220:
     corpus_key  : "{CORPUS_KEY}"
     artifact_id : (the CoreDocument artifact_id returned in step 7b-a)
     document_id : "company-<slug>"

### Step 8 — After processing all companies
Call graph_list (no parameters needed).

Report clearly:
- Which companies were newly ingested (KG + Vectara)
- Which were skipped as duplicates
- Any errors or SHACL violations encountered
"""


def api_headers():
    return {"x-api-key": API_KEY, "Content-Type": "application/json"}


def check_api_key():
    r = httpx.get(f"{BASE_URL}/corpora", headers={"x-api-key": API_KEY}, timeout=10)
    if r.status_code == 401:
        print(f"ERROR: Vectara API key rejected (401): {r.text}")
        print("\nTip: Get a fresh Application API Key from https://console.vectara.com → API Keys")
        sys.exit(1)
    if r.status_code != 200:
        print(f"WARNING: /corpora returned {r.status_code}: {r.text[:200]}")
    else:
        corpora = [c["key"] for c in r.json().get("corpora", [])]
        if CORPUS_KEY not in corpora:
            print(f"WARNING: corpus '{CORPUS_KEY}' not found in account. Available: {corpora}")
        else:
            print(f"  Corpus '{CORPUS_KEY}' confirmed")
    print("  Vectara API key OK")


def _find_tool_server(name: str) -> dict | None:
    r = httpx.get(f"{BASE_URL}/tool_servers", headers={"x-api-key": API_KEY}, timeout=10)
    if r.status_code != 200:
        return None
    for server in r.json().get("tool_servers", []):
        if server.get("name") == name:
            return server
    return None


def register_mcp_server(mcp_url: str) -> str:
    existing = _find_tool_server(SERVER_NAME)
    if existing:
        print(f"  Tool server already registered: {existing['id']}")
        if existing.get("uri") != mcp_url:
            r = httpx.patch(
                f"{BASE_URL}/tool_servers/{existing['id']}",
                headers=api_headers(),
                json={"uri": mcp_url},
                timeout=10,
            )
            if r.status_code in (200, 201):
                print(f"  URI updated to: {mcp_url}")
        return existing["id"]

    payload = {
        "name": SERVER_NAME,
        "description": "Generic SPARQL 1.1 MCP server — 12 tools covering query, update, graph store, SHACL, and connectivity plus Vectara built-in tools",
        "type": "mcp",
        "uri": mcp_url,
        "transport": "sse",
    }
    r = httpx.post(f"{BASE_URL}/tool_servers", headers=api_headers(), json=payload, timeout=30)
    if r.status_code not in (200, 201):
        print(f"ERROR: Failed to register tool server ({r.status_code}): {r.text}")
        sys.exit(1)

    server_id = r.json()["id"]
    print(f"  Registered tool server: {server_id}")
    return server_id


def sync_tool_server(server_id: str):
    r = httpx.post(f"{BASE_URL}/tool_servers/{server_id}/sync", headers={"x-api-key": API_KEY}, timeout=30)
    if r.status_code not in (200, 201, 202, 204):
        print(f"ERROR: Sync failed ({r.status_code}): {r.text}")
        sys.exit(1)
    print("  Tool server synced — discovering tools...")
    time.sleep(3)


def list_mcp_tools(server_id: str) -> dict[str, str]:
    """Returns {tool_name: tool_id} for all tools from this server."""
    r = httpx.get(
        f"{BASE_URL}/tools",
        params={"tool_server_id": server_id},
        headers={"x-api-key": API_KEY},
        timeout=10,
    )
    if r.status_code != 200:
        print(f"ERROR: Could not list tools ({r.status_code}): {r.text}")
        sys.exit(1)

    tools = {}
    for tool in r.json().get("tools", []):
        tools[tool["name"]] = tool["id"]
        print(f"    {tool['name']:40s} → {tool['id']}")

    return tools


def _list_agents_by_name(name: str):
    page_key = None
    while True:
        params = {"limit": 100}
        if page_key:
            params["page_key"] = page_key
        r = httpx.get(f"{BASE_URL}/agents", params=params, headers={"x-api-key": API_KEY}, timeout=10)
        r.raise_for_status()
        data = r.json()
        for a in data.get("agents", []):
            if a.get("name") == name:
                yield a
        page_key = data.get("metadata", {}).get("page_key")
        if not page_key:
            return


def delete_agent_by_name(name: str):
    deleted = 0
    for agent in _list_agents_by_name(name):
        httpx.delete(f"{BASE_URL}/agents/{agent['key']}", headers={"x-api-key": API_KEY}, timeout=10)
        print(f"  Deleted existing agent: {agent['key']}")
        deleted += 1
    if deleted:
        time.sleep(3)


def build_tool_configurations(mcp_tools: dict[str, str]) -> dict:
    """Build the tool_configurations block for the agent."""
    configs = {
        # Built-in artifact tools
        "artifact_read": {"type": "artifact_read"},
        # Built-in Vectara tools (dynamic_vectara type, referenced by tool_id)
        "text_to_core_document_20260526": {
            "type": "dynamic_vectara",
            "tool_id": "tol_vectara_text_to_core_document_20260526",
        },
        "core_document_index_20260220": {
            "type": "dynamic_vectara",
            "tool_id": "tol_vectara_core_document_index_20260220",
        },
        # Built-in corpus search (for verification queries)
        "corpus_search": {
            "type": "corpora_search",
            "query_configuration": {
                "search": {
                    "corpora": [{"corpus_key": CORPUS_KEY, "lexical_interpolation": 0.05}],
                    "limit": 5,
                }
            },
        },
    }

    expected_tools = {
        "sparql_select",
        "sparql_ask",
        "sparql_construct",
        "sparql_describe",
        "sparql_update",
        "graph_list",
        "graph_get",
        "graph_put",
        "graph_post",
        "graph_delete",
        "validate_shacl",
        "endpoint_ping",
    }
    missing = expected_tools - set(mcp_tools.keys())
    if missing:
        print(f"  WARNING: expected tools not found in MCP server: {missing}")

    for tool_name, tool_id in mcp_tools.items():
        configs[tool_name] = {"type": "mcp", "tool_id": tool_id}

    return configs


def create_agent(mcp_tools: dict[str, str], delete_existing: bool) -> str:
    if delete_existing:
        print("\n  Removing any existing agent with this name...")
        delete_agent_by_name(AGENT_NAME)

    agent_config = {
        "name": AGENT_NAME,
        "description": (
            "Agentic ingestion pipeline — reads company profile documents, extracts "
            "organization entities, validates with SHACL, and dual-ingests into "
            "Apache Jena Fuseki (Knowledge Graph via generic SPARQL tools) and Vectara corpus."
        ),
        "model": {"name": "gpt-5.4"},
        "first_step_name": "main",
        "steps": {
            "main": {
                "instructions": [
                    {
                        "type": "inline",
                        "name": "ingestion-instructions",
                        "template": INGESTION_INSTRUCTIONS,
                    }
                ],
                "output_parser": {"type": "default"},
            }
        },
        "tool_configurations": build_tool_configurations(mcp_tools),
    }

    for attempt in range(4):
        r = httpx.post(f"{BASE_URL}/agents", headers=api_headers(), json=agent_config, timeout=30)
        if r.status_code == 201:
            return r.json()["key"]
        if r.status_code == 409 and attempt < 3:
            print(f"  409 conflict — cleaning up and retrying...")
            delete_agent_by_name(AGENT_NAME)
            time.sleep(2 ** attempt)
            continue
        print(f"ERROR: Agent creation failed ({r.status_code}): {r.text}")
        sys.exit(1)

    raise RuntimeError("Agent creation failed after 4 attempts")


def main():
    parser = argparse.ArgumentParser(description="Create Vectara agentic ingestion agent")
    parser.add_argument(
        "--mcp-url",
        required=True,
        help=(
            "Public SSE URL of the MCP server. Must be reachable from Vectara cloud. "
            "Example: https://abc123.ngrok.io  (run: ngrok http 8000)"
        ),
    )
    parser.add_argument(
        "--delete-existing",
        action="store_true",
        help="Delete any existing agent with the same name before creating",
    )
    args = parser.parse_args()

    mcp_url = args.mcp_url.rstrip("/")
    if not mcp_url.endswith("/sse"):
        mcp_url = mcp_url + "/sse"

    print("\n=== Agentic Ingestion — Agent Setup ===\n")

    print("[1/4] Validating Vectara API key...")
    check_api_key()

    print(f"\n[2/4] Registering MCP tool server: {mcp_url}")
    server_id = register_mcp_server(mcp_url)

    print("\n[3/4] Syncing tool server to discover tools...")
    sync_tool_server(server_id)
    print("  Tools discovered:")
    mcp_tools = list_mcp_tools(server_id)

    if not mcp_tools:
        print("\nERROR: No tools found. Possible causes:")
        print("  - MCP server is not running (python server.py)")
        print("  - ngrok tunnel not pointing to port 8000")
        print("  - Vectara could not reach the SSE endpoint")
        sys.exit(1)

    print(f"\n[4/4] Creating agent '{AGENT_NAME}'...")
    agent_key = create_agent(mcp_tools, delete_existing=args.delete_existing)
    print(f"  Agent created: {agent_key}")

    state = {
        "agent_key": agent_key,
        "server_id": server_id,
        "mcp_tools": mcp_tools,
        "corpus_key": CORPUS_KEY,
        "graph_uri": GRAPH_URI,
        "mcp_url": mcp_url,
    }
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

    print(f"\n  State saved → {STATE_FILE}")
    print(f"\nNext step:")
    print(f"  python scripts/run_ingestion.py")


if __name__ == "__main__":
    main()
