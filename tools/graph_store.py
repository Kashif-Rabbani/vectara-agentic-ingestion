"""SPARQL 1.1 Graph Store Protocol tools — list, get, put, post, delete."""

import logging
from typing import Any

import httpx
from rdflib import Graph

from tools._common import QUERY_URL, UPDATE_URL, GRAPH_STORE_URL, AUTH, rdf_content_type

_LOG = logging.getLogger(__name__)
_TIMEOUT_READ  = 30.0
_TIMEOUT_WRITE = 120.0
_TIMEOUT_LOAD  = 300.0   # server-side LOAD from a remote URL


def graph_list() -> dict[str, Any]:
    """
    List all named graphs in the SPARQL dataset with their triple counts.

    No parameters — the endpoint is pre-configured in the server environment.
    Executes a SPARQL SELECT with COUNT(*) grouped by graph URI.

    Use this tool to discover what graphs exist before querying or writing to them.

    Returns {success, graphs: [{uri, triple_count}], count} on success,
            {success, error} on failure.
    """
    _LOG.info("graph_list")
    query = """
    SELECT ?g (COUNT(*) AS ?n)
    WHERE { GRAPH ?g { ?s ?p ?o } }
    GROUP BY ?g
    ORDER BY ?g
    """
    try:
        r = httpx.post(
            QUERY_URL,
            content=query,
            headers={
                "Content-Type": "application/sparql-query",
                "Accept": "application/sparql-results+json",
            },
            auth=AUTH,
            timeout=_TIMEOUT_READ,
        )
        r.raise_for_status()
        bindings = r.json()["results"]["bindings"]
        graphs = [
            {"uri": b["g"]["value"], "triple_count": int(b["n"]["value"])}
            for b in bindings
        ]
        return {"success": True, "graphs": graphs, "count": len(graphs)}
    except Exception as exc:
        _LOG.error("graph_list failed: %s", exc)
        return {"success": False, "error": str(exc), "graphs": []}


def graph_get(
    graph_uri: str,
    limit: int = 1000,
    offset: int = 0,
    output_file: str | None = None,
) -> dict[str, Any]:
    """
    Fetch triples from a named graph as Turtle, with pagination support.

    For graphs larger than `limit` triples call this tool repeatedly, incrementing
    offset by limit each time, until has_more is false in the response.
    Blank node identifiers are not guaranteed to be stable across pages.

    graph_uri:   URI of the named graph to read.
                 Example: "http://example.org/graph/companies"
    limit:       Maximum triples per call (default 1000). Reduce if the agent
                 context window is too small to hold the response.
    offset:      Triples to skip (default 0). Increment by limit each page.
    output_file: When set, Turtle is appended to this local file path and the
                 response omits content. Use for large graph exports that would
                 overflow the agent context.

    Returns {success, content|file_path, triple_count, has_more, limit, offset}
            on success, {success, error} on failure.
    """
    _LOG.info("graph_get: <%s> limit=%d offset=%d", graph_uri, limit, offset)
    query = (
        f"CONSTRUCT {{ ?s ?p ?o }}"
        f" WHERE {{ GRAPH <{graph_uri}> {{ ?s ?p ?o }} }}"
        f" LIMIT {limit} OFFSET {offset}"
    )
    try:
        r = httpx.post(
            QUERY_URL,
            content=query,
            headers={"Content-Type": "application/sparql-query", "Accept": "text/turtle"},
            auth=AUTH,
            timeout=_TIMEOUT_READ,
        )
        r.raise_for_status()
        turtle = r.text
        g = Graph()
        g.parse(data=turtle, format="turtle")
        triple_count = len(g)
        base = {
            "success": True,
            "triple_count": triple_count,
            "has_more": triple_count == limit,
            "limit": limit,
            "offset": offset,
        }
        if output_file:
            with open(output_file, "a", encoding="utf-8") as f:
                f.write(turtle)
            return {**base, "file_path": output_file}
        return {**base, "content": turtle}
    except Exception as exc:
        _LOG.error("graph_get failed: %s", exc)
        return {"success": False, "error": str(exc)}


def graph_put(
    graph_uri: str,
    content: str | None = None,
    source_url: str | None = None,
    rdf_format: str = "turtle",
) -> dict[str, Any]:
    """
    Replace a named graph entirely — all existing triples are removed first.

    Use graph_put when you want to overwrite the graph contents.
    Use graph_post to append without removing existing triples.

    Provide exactly one of content or source_url:
    - content:    RDF string sent through this server. Suitable for small graphs.
    - source_url: HTTP(S) URL of an RDF file. The SPARQL endpoint fetches it
                  directly via LOAD — no file bytes flow through this server.
                  Use for large files or remote datasets.

    graph_uri:  URI of the named graph to replace.
                Example: "http://example.org/graph/companies"
    content:    RDF string (serialisation set by rdf_format).
    source_url: Publicly accessible URL of the RDF file to load.
                Example: "https://example.org/datasets/companies.ttl"
    rdf_format: Serialisation of content — "turtle" (default), "json-ld",
                "n-triples". Ignored when source_url is used.

    Returns {success, http_status, triple_count?} on success.
    triple_count is omitted when source_url is used (count is unknown locally).
    Returns {success, error} on failure.
    """
    if bool(content) == bool(source_url):
        return {
            "success": False,
            "error": "Provide exactly one of 'content' or 'source_url'",
            "http_status": None,
        }

    if source_url:
        _LOG.info("graph_put via LOAD: <%s> ← %s", graph_uri, source_url)
        update = f"DROP SILENT GRAPH <{graph_uri}> ; LOAD <{source_url}> INTO GRAPH <{graph_uri}>"
        try:
            r = httpx.post(
                UPDATE_URL,
                content=update,
                headers={"Content-Type": "application/sparql-update"},
                auth=AUTH,
                timeout=_TIMEOUT_LOAD,
            )
            return {
                "success": r.is_success,
                "http_status": r.status_code,
                "source_url": source_url,
                "error": r.text if not r.is_success else None,
            }
        except Exception as exc:
            _LOG.error("graph_put (LOAD) failed: %s", exc)
            return {"success": False, "http_status": None, "error": str(exc)}

    _LOG.info("graph_put (content, rdf_format=%s): <%s>", rdf_format, graph_uri)
    try:
        g = Graph()
        g.parse(data=content, format=rdf_format)
    except Exception as exc:
        return {"success": False, "error": f"Invalid RDF ({rdf_format}): {exc}", "http_status": None}

    try:
        r = httpx.put(
            GRAPH_STORE_URL,
            params={"graph": graph_uri},
            content=content.encode(),
            headers={"Content-Type": rdf_content_type(rdf_format)},
            auth=AUTH,
            timeout=_TIMEOUT_WRITE,
        )
        return {
            "success": r.is_success,
            "http_status": r.status_code,
            "triple_count": len(g),
            "error": r.text if not r.is_success else None,
        }
    except Exception as exc:
        _LOG.error("graph_put failed: %s", exc)
        return {"success": False, "http_status": None, "error": str(exc)}


def graph_post(
    graph_uri: str,
    content: str | None = None,
    source_url: str | None = None,
    rdf_format: str = "turtle",
) -> dict[str, Any]:
    """
    Append RDF triples to a named graph without removing existing triples.

    Use graph_post to add data to a graph. Use graph_put to replace it entirely.
    For large content without a URL, split into valid self-contained Turtle chunks
    and call graph_post once per chunk — each call appends to the same graph.

    Provide exactly one of content or source_url:
    - content:    RDF string to append directly.
    - source_url: HTTP(S) URL fetched directly by the endpoint via LOAD.

    graph_uri:  URI of the named graph to append to.
                Example: "http://example.org/graph/companies"
    content:    RDF string to append.
    source_url: Publicly accessible URL of the RDF file to load.
    rdf_format: Serialisation of content — "turtle" (default), "json-ld",
                "n-triples". Ignored when source_url is used.

    Returns {success, http_status, triples_added?} on success,
            {success, error} on failure.
    """
    if bool(content) == bool(source_url):
        return {
            "success": False,
            "error": "Provide exactly one of 'content' or 'source_url'",
            "http_status": None,
        }

    if source_url:
        _LOG.info("graph_post via LOAD: <%s> ← %s", graph_uri, source_url)
        update = f"LOAD <{source_url}> INTO GRAPH <{graph_uri}>"
        try:
            r = httpx.post(
                UPDATE_URL,
                content=update,
                headers={"Content-Type": "application/sparql-update"},
                auth=AUTH,
                timeout=_TIMEOUT_LOAD,
            )
            return {
                "success": r.is_success,
                "http_status": r.status_code,
                "source_url": source_url,
                "error": r.text if not r.is_success else None,
            }
        except Exception as exc:
            _LOG.error("graph_post (LOAD) failed: %s", exc)
            return {"success": False, "http_status": None, "error": str(exc)}

    _LOG.info("graph_post (content, rdf_format=%s): <%s>", rdf_format, graph_uri)
    try:
        g = Graph()
        g.parse(data=content, format=rdf_format)
    except Exception as exc:
        return {"success": False, "error": f"Invalid RDF ({rdf_format}): {exc}", "http_status": None}

    try:
        r = httpx.post(
            GRAPH_STORE_URL,
            params={"graph": graph_uri},
            content=content.encode(),
            headers={"Content-Type": rdf_content_type(rdf_format)},
            auth=AUTH,
            timeout=_TIMEOUT_WRITE,
        )
        return {
            "success": r.is_success,
            "http_status": r.status_code,
            "triples_added": len(g),
            "error": r.text if not r.is_success else None,
        }
    except Exception as exc:
        _LOG.error("graph_post failed: %s", exc)
        return {"success": False, "http_status": None, "error": str(exc)}


def graph_delete(graph_uri: str) -> dict[str, Any]:
    """
    Delete an entire named graph and all its triples.

    This operation is irreversible. To empty a graph without deleting it,
    use sparql_update with CLEAR GRAPH <uri> instead — CLEAR leaves the
    empty graph in existence while DELETE removes it entirely.

    graph_uri: URI of the named graph to delete.
               Example: "http://example.org/graph/companies"

    Returns {success, http_status, graph_uri} on success.
    HTTP 204 = deleted, HTTP 404 = graph did not exist (reported as success=False).
    Returns {success, error} on connection failure.
    """
    _LOG.info("graph_delete: <%s>", graph_uri)
    try:
        r = httpx.delete(
            GRAPH_STORE_URL,
            params={"graph": graph_uri},
            auth=AUTH,
            timeout=_TIMEOUT_WRITE,
        )
        return {
            "success": r.is_success,
            "http_status": r.status_code,
            "graph_uri": graph_uri,
            "error": r.text if not r.is_success else None,
        }
    except Exception as exc:
        _LOG.error("graph_delete failed: %s", exc)
        return {"success": False, "http_status": None, "error": str(exc), "graph_uri": graph_uri}
