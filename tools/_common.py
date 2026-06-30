"""
Connection config and shared utilities for the SPARQL MCP tool suite.

Connection is resolved once at import time (after load_dotenv() in server.py).

Priority for each URL:
  1. Explicit env var (SPARQL_QUERY_URL / SPARQL_UPDATE_URL / SPARQL_GRAPH_STORE_URL)
  2. Derived from SPARQL_ENDPOINT by appending /query, /update, /data
     — works out of the box for Apache Jena Fuseki and Stardog.
     — for Virtuoso, Neptune, Blazegraph (single-URL endpoints) or GraphDB
       (non-standard paths) set the three URLs explicitly instead.

Example .env for Jena Fuseki:
    SPARQL_ENDPOINT=http://localhost:3030/ds

Example .env for Virtuoso:
    SPARQL_QUERY_URL=http://localhost:8890/sparql
    SPARQL_UPDATE_URL=http://localhost:8890/sparql
    SPARQL_GRAPH_STORE_URL=http://localhost:8890/sparql-graph-crud

Example .env for GraphDB:
    SPARQL_QUERY_URL=http://localhost:7200/repositories/myrepo
    SPARQL_UPDATE_URL=http://localhost:7200/repositories/myrepo/statements
    SPARQL_GRAPH_STORE_URL=http://localhost:7200/repositories/myrepo/rdf-graphs/service
"""

import os

_RDF_CONTENT_TYPES: dict[str, str] = {
    "turtle":    "text/turtle",
    "json-ld":   "application/ld+json",
    "n-triples": "application/n-triples",
    "n-quads":   "application/n-quads",
}

_base = os.getenv("SPARQL_ENDPOINT", "").rstrip("/")


def _url(explicit_var: str, suffix: str) -> str:
    explicit = os.getenv(explicit_var, "").strip()
    if explicit:
        return explicit
    return f"{_base}/{suffix}" if _base else ""


QUERY_URL       = _url("SPARQL_QUERY_URL",       "query")
UPDATE_URL      = _url("SPARQL_UPDATE_URL",       "update")
GRAPH_STORE_URL = _url("SPARQL_GRAPH_STORE_URL",  "data")

_user = os.getenv("SPARQL_USERNAME", "").strip()
_pass = os.getenv("SPARQL_PASSWORD", "").strip()
AUTH: tuple[str, str] | None = (_user, _pass) if _user else None


def rdf_content_type(fmt: str) -> str:
    """Return the HTTP Content-Type header value for an RDF format string."""
    return _RDF_CONTENT_TYPES.get(fmt, "text/turtle")
