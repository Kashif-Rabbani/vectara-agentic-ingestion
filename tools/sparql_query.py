"""SPARQL 1.1 query tools — SELECT, ASK, CONSTRUCT, DESCRIBE."""

import logging
from typing import Any

import httpx
from rdflib import Graph

from tools._common import QUERY_URL, AUTH

_LOG = logging.getLogger(__name__)
_TIMEOUT = 30.0


def _post(query: str, accept: str) -> httpx.Response:
    return httpx.post(
        QUERY_URL,
        content=query,
        headers={"Content-Type": "application/sparql-query", "Accept": accept},
        auth=AUTH,
        timeout=_TIMEOUT,
    )


def sparql_select(
    query: str,
    limit: int | None = None,
    offset: int | None = None,
) -> dict[str, Any]:
    """
    Execute a SPARQL SELECT query and return tabular results.

    Use this tool when you need structured rows of data — variable bindings from
    the graph. For retrieving an RDF subgraph use sparql_construct. For a yes/no
    existence check use sparql_ask.

    Paginate large result sets with limit + offset: call repeatedly, incrementing
    offset by limit each time, until has_more is false.

    query:  A complete SPARQL 1.1 SELECT query string including PREFIX declarations.
            Example:
              PREFIX schema: <https://schema.org/>
              SELECT ?name ?url WHERE {
                GRAPH <http://example.org/companies> {
                  ?org a schema:Organization ; schema:name ?name .
                  OPTIONAL { ?org schema:url ?url }
                }
              }
    limit:  Maximum rows to return. Appended as LIMIT if the query has none.
    offset: Rows to skip. Appended as OFFSET. Use with limit for pagination.

    Returns {success, columns, rows, count, has_more} on success.
    All row values are strings — SPARQL binding values are not type-cast.
    Returns {success, error} on failure.
    """
    _LOG.info("sparql_select: %s", query[:120].replace("\n", " "))
    if "LIMIT" not in query.upper():
        if limit is not None:
            query = f"{query.rstrip()} LIMIT {limit}"
        if offset:
            query = f"{query} OFFSET {offset}"
    try:
        r = _post(query, "application/sparql-results+json")
        r.raise_for_status()
        data = r.json()
        columns: list[str] = data["head"]["vars"]
        rows = [
            {col: b[col]["value"] if col in b else None for col in columns}
            for b in data["results"]["bindings"]
        ]
        has_more = (limit is not None) and len(rows) == limit
        return {"success": True, "columns": columns, "rows": rows, "count": len(rows), "has_more": has_more}
    except Exception as exc:
        _LOG.error("sparql_select failed: %s", exc)
        return {"success": False, "error": str(exc), "columns": [], "rows": [], "count": 0}


def sparql_ask(query: str) -> dict[str, Any]:
    """
    Execute a SPARQL ASK query and return a boolean answer.

    Use this tool for existence checks and boolean conditions:
      - "Does this URI already exist in the graph?"
      - "Are there any triples matching this pattern?"
    Do NOT use ASK to retrieve data — use sparql_select or sparql_construct for that.

    query: A complete SPARQL 1.1 ASK query string.
           Example (duplicate check):
             ASK {
               GRAPH <http://example.org/companies> {
                 <http://example.org/entity/vectara> ?p ?o
               }
             }

    Returns {success, result: bool} on success, {success, error} on failure.
    """
    _LOG.info("sparql_ask: %s", query[:120].replace("\n", " "))
    try:
        r = _post(query, "application/sparql-results+json")
        r.raise_for_status()
        return {"success": True, "result": r.json()["boolean"]}
    except Exception as exc:
        _LOG.error("sparql_ask failed: %s", exc)
        return {"success": False, "error": str(exc), "result": None}


def sparql_construct(
    query: str,
    rdf_format: str = "turtle",
) -> dict[str, Any]:
    """
    Execute a SPARQL CONSTRUCT query and return the result as an RDF graph string.

    Use this tool to extract or reshape a subgraph — materialise inferences,
    convert data into a target shape, or copy a subset of triples.
    For tabular bindings use sparql_select. For a full named graph use graph_get.

    query:      A complete SPARQL 1.1 CONSTRUCT query string.
                Example (extract a subgraph):
                  CONSTRUCT { ?s ?p ?o }
                  WHERE {
                    GRAPH <http://example.org/companies> { ?s ?p ?o }
                    FILTER(?s = <http://example.org/entity/vectara>)
                  }
    rdf_format: Serialisation for the returned graph string.
                One of "turtle" (default), "json-ld", "n-triples".

    Returns {success, graph, rdf_format, triple_count} on success,
            {success, error} on failure.
    """
    _LOG.info("sparql_construct (rdf_format=%s): %s", rdf_format, query[:120].replace("\n", " "))
    try:
        r = _post(query, "text/turtle")
        r.raise_for_status()
        turtle = r.text
        g = Graph()
        g.parse(data=turtle, format="turtle")
        graph_out = g.serialize(format=rdf_format) if rdf_format != "turtle" else turtle
        return {"success": True, "graph": graph_out, "rdf_format": rdf_format, "triple_count": len(g)}
    except Exception as exc:
        _LOG.error("sparql_construct failed: %s", exc)
        return {"success": False, "error": str(exc), "graph": None, "triple_count": 0}


def sparql_describe(
    resource_uri: str,
    rdf_format: str = "turtle",
) -> dict[str, Any]:
    """
    Fetch a concise bounded description of a resource.

    Returns all triples where the URI appears as subject or object, using the
    endpoint's own DESCRIBE semantics (typically Concise Bounded Description).
    Use for quick "tell me everything about X" lookups when you have a URI.
    For targeted graph patterns use sparql_construct instead.

    resource_uri: Full URI of the resource to describe, without angle brackets.
                  Example: "http://example.org/entity/company/vectara"
    rdf_format:   Serialisation for the returned description string.
                  One of "turtle" (default), "json-ld", "n-triples".

    Returns {success, description, rdf_format, resource_uri} on success,
            {success, error} on failure.
    """
    _LOG.info("sparql_describe: <%s>", resource_uri)
    query = f"DESCRIBE <{resource_uri}>"
    try:
        r = _post(query, "text/turtle")
        r.raise_for_status()
        turtle = r.text
        if rdf_format != "turtle":
            g = Graph()
            g.parse(data=turtle, format="turtle")
            turtle = g.serialize(format=rdf_format)
        return {"success": True, "description": turtle, "rdf_format": rdf_format, "resource_uri": resource_uri}
    except Exception as exc:
        _LOG.error("sparql_describe failed: %s", exc)
        return {"success": False, "error": str(exc), "description": None, "resource_uri": resource_uri}
