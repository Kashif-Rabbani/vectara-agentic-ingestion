"""
Generic SPARQL 1.1 MCP server.

Exposes 12 tools covering SPARQL query forms, SPARQL Update, the Graph Store
Protocol, SHACL validation, and an endpoint connectivity check.

Compatible with any SPARQL 1.1 endpoint:
  Apache Jena Fuseki · GraphDB · Stardog · Virtuoso · Amazon Neptune · Blazegraph

Configuration — set in .env or environment before starting:

  # Option A — single base URL (auto-derives /query, /update, /data — works for Fuseki/Stardog)
  SPARQL_ENDPOINT=http://localhost:3030/ds

  # Option B — explicit URLs per operation (use for GraphDB, Virtuoso, Neptune, etc.)
  SPARQL_QUERY_URL=http://localhost:3030/ds/query
  SPARQL_UPDATE_URL=http://localhost:3030/ds/update
  SPARQL_GRAPH_STORE_URL=http://localhost:3030/ds/data

  # Auth (optional)
  SPARQL_USERNAME=admin
  SPARQL_PASSWORD=secret

  # Server binding
  MCP_HOST=0.0.0.0
  MCP_PORT=8000

Run:
  python server.py
  uvicorn server:app --host 0.0.0.0 --port 8000
"""

import logging
import os
import sys
import time
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

from fastmcp import FastMCP

from tools._common import QUERY_URL, UPDATE_URL, GRAPH_STORE_URL, AUTH
from tools.graph_store import graph_delete, graph_get, graph_list, graph_post, graph_put
from tools.shacl import validate_shacl
from tools.sparql_query import sparql_ask, sparql_construct, sparql_describe, sparql_select
from tools.sparql_update import sparql_update

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
_LOG = logging.getLogger(__name__)

# ── startup validation ────────────────────────────────────────────────────────

def _check_config() -> None:
    if not QUERY_URL:
        print(
            "\nERROR: No SPARQL endpoint configured.\n"
            "Set one of:\n"
            "  SPARQL_ENDPOINT=http://host/dataset   (auto-derives /query, /update, /data)\n"
            "  SPARQL_QUERY_URL + SPARQL_UPDATE_URL + SPARQL_GRAPH_STORE_URL  (explicit)\n",
            file=sys.stderr,
        )
        sys.exit(1)

    _LOG.info("SPARQL query URL      : %s", QUERY_URL)
    _LOG.info("SPARQL update URL     : %s", UPDATE_URL)
    _LOG.info("SPARQL graph store URL: %s", GRAPH_STORE_URL)
    _LOG.info("Auth                  : %s", "yes" if AUTH else "none")

    try:
        r = httpx.post(
            QUERY_URL,
            content="ASK {}",
            headers={"Content-Type": "application/sparql-query", "Accept": "application/sparql-results+json"},
            auth=AUTH,
            timeout=5,
        )
        _LOG.info("Endpoint reachable: HTTP %s", r.status_code)
    except Exception as exc:
        _LOG.warning("Could not reach endpoint at startup: %s (will retry on first tool call)", exc)


_check_config()

# ── MCP server ────────────────────────────────────────────────────────────────

mcp = FastMCP(
    name="sparql-mcp",
    instructions=(
        "Generic SPARQL 1.1 MCP server. The endpoint connection is pre-configured "
        "in the server environment — tools take no connection parameters.\n\n"
        "Start with endpoint_ping to confirm the endpoint is reachable.\n\n"
        "QUERY tools (read-only):\n"
        "  sparql_select   — SELECT → tabular rows; paginate with limit/offset/has_more\n"
        "  sparql_ask      — ASK → boolean; use for existence checks\n"
        "  sparql_construct — CONSTRUCT → RDF graph string; use to extract subgraphs\n"
        "  sparql_describe  — DESCRIBE <uri> → all triples about one resource\n\n"
        "UPDATE tool (write):\n"
        "  sparql_update — any SPARQL 1.1 Update: INSERT DATA, DELETE DATA, "
        "INSERT/DELETE WHERE, CLEAR, DROP, CREATE, COPY, MOVE, ADD, LOAD\n\n"
        "GRAPH STORE tools (operate on whole named graphs):\n"
        "  graph_list   — list all named graphs with triple counts\n"
        "  graph_get    — fetch graph as Turtle; paginated (limit/offset/has_more)\n"
        "  graph_put    — replace a graph (content string or source_url for remote LOAD)\n"
        "  graph_post   — append to a graph (content string or source_url)\n"
        "  graph_delete — permanently delete a named graph\n\n"
        "UTILITY tools:\n"
        "  validate_shacl — validate RDF against SHACL shapes (pure function, no endpoint)\n"
        "  endpoint_ping  — check connectivity and show configured endpoint URLs"
    ),
)

# ── ping tool (defined inline — no external module needed) ────────────────────

@mcp.tool()
def endpoint_ping() -> dict[str, Any]:
    """
    Check connectivity to the configured SPARQL endpoint.

    No parameters — pings the pre-configured query endpoint with a minimal
    ASK {} query and reports reachability, response time, and the three
    configured URLs (query, update, graph store).

    Use this tool first when debugging connectivity issues or verifying setup.

    Returns {success, reachable, http_status, response_ms,
             query_url, update_url, graph_store_url} on success,
            {success, reachable, error, response_ms, ...urls} if unreachable.
    """
    start = time.monotonic()
    try:
        r = httpx.post(
            QUERY_URL,
            content="ASK {}",
            headers={
                "Content-Type": "application/sparql-query",
                "Accept": "application/sparql-results+json",
            },
            auth=AUTH,
            timeout=5,
        )
        ms = round((time.monotonic() - start) * 1000)
        _LOG.info("endpoint_ping: HTTP %s in %dms", r.status_code, ms)
        return {
            "success": True,
            "reachable": r.is_success,
            "http_status": r.status_code,
            "response_ms": ms,
            "query_url": QUERY_URL,
            "update_url": UPDATE_URL,
            "graph_store_url": GRAPH_STORE_URL,
        }
    except Exception as exc:
        ms = round((time.monotonic() - start) * 1000)
        _LOG.error("endpoint_ping failed: %s", exc)
        return {
            "success": False,
            "reachable": False,
            "error": str(exc),
            "response_ms": ms,
            "query_url": QUERY_URL,
            "update_url": UPDATE_URL,
            "graph_store_url": GRAPH_STORE_URL,
        }


# ── register tools ────────────────────────────────────────────────────────────

# SPARQL query forms
mcp.tool()(sparql_select)
mcp.tool()(sparql_ask)
mcp.tool()(sparql_construct)
mcp.tool()(sparql_describe)

# SPARQL 1.1 Update
mcp.tool()(sparql_update)

# Graph Store Protocol
mcp.tool()(graph_list)
mcp.tool()(graph_get)
mcp.tool()(graph_put)
mcp.tool()(graph_post)
mcp.tool()(graph_delete)

# Utility
mcp.tool()(validate_shacl)

# ASGI app for uvicorn
app = mcp.http_app(transport="sse")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MCP_PORT", "8000")),
    )
