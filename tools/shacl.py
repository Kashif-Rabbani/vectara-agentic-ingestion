"""SHACL validation tool — pure function, no SPARQL endpoint required."""

import logging
import os
from typing import Any

import httpx
import pyshacl
from rdflib import Graph

_LOG = logging.getLogger(__name__)


def validate_shacl(
    data_graph: str,
    shapes_graph: str,
    data_format: str = "turtle",
    shapes_format: str = "turtle",
) -> dict[str, Any]:
    """
    Validate an RDF document against SHACL shapes.

    This is a pure function — it needs no SPARQL endpoint. Call it before writing
    data with sparql_update or graph_put/graph_post to catch constraint violations
    before they reach the database.

    data_graph:    RDF content to validate, as a string.
                   Example (Turtle):
                     @prefix schema: <https://schema.org/> .
                     <http://example.org/alice> a schema:Person ;
                       schema:name "Alice" .

    shapes_graph:  SHACL shapes definition. Accepts three forms:
                   - A raw Turtle or JSON-LD string containing the shapes.
                   - A local file path to a .ttl shapes file (resolved relative
                     to the server's working directory).
                     Example: "shapes/person.ttl"
                   - An HTTP(S) URL from which shapes are fetched at call time.
                     Example: "https://example.org/shapes/person.ttl"

    data_format:   RDF format of data_graph.
                   One of "turtle" (default), "json-ld", "n-triples".

    shapes_format: RDF format when shapes_graph is a raw string.
                   One of "turtle" (default), "json-ld".
                   Ignored when shapes_graph is a file path or URL
                   (format is detected automatically).

    Returns {success, conforms, violations, violation_count} on success.
    violations is a list of {message, path, focus_node, value} objects.
    Returns {success, error, conforms: false, violations: []} on parse or fetch failure.
    """
    _LOG.info(
        "validate_shacl: data_format=%s shapes=%s",
        data_format,
        shapes_graph[:60] if len(shapes_graph) < 80 else shapes_graph[:40] + "…",
    )
    shapes_content, resolved_fmt = _resolve_shapes(shapes_graph, shapes_format)
    if shapes_content is None:
        return {
            "success": False,
            "error": resolved_fmt,
            "conforms": False,
            "violations": [],
            "violation_count": 0,
        }

    try:
        dg = Graph()
        dg.parse(data=data_graph, format=data_format)

        sg = Graph()
        sg.parse(data=shapes_content, format=resolved_fmt)

        conforms, results_graph, _ = pyshacl.validate(
            dg,
            shacl_graph=sg,
            inference="rdfs",
            abort_on_first=False,
        )
        violations = _extract_violations(results_graph)
        _LOG.info("validate_shacl: conforms=%s violations=%d", conforms, len(violations))
        return {
            "success": True,
            "conforms": conforms,
            "violations": violations,
            "violation_count": len(violations),
        }
    except Exception as exc:
        _LOG.error("validate_shacl failed: %s", exc)
        return {
            "success": False,
            "error": str(exc),
            "conforms": False,
            "violations": [],
            "violation_count": 0,
        }


# ── internal helpers ──────────────────────────────────────────────────────────

def _resolve_shapes(shapes_graph: str, fmt: str) -> tuple[str | None, str]:
    """Return (content_str, format_str) or (None, error_message)."""
    if shapes_graph.startswith(("http://", "https://")):
        try:
            r = httpx.get(shapes_graph, timeout=10, follow_redirects=True)
            r.raise_for_status()
            ct = r.headers.get("content-type", "")
            detected = "json-ld" if "json" in ct else "turtle"
            return r.text, detected
        except Exception as exc:
            return None, f"Failed to fetch shapes from {shapes_graph}: {exc}"

    if os.path.exists(shapes_graph):
        with open(shapes_graph, encoding="utf-8") as f:
            return f.read(), fmt

    return shapes_graph, fmt


def _extract_violations(results_graph: Graph) -> list[dict[str, Any]]:
    q = """
    PREFIX sh: <http://www.w3.org/ns/shacl#>
    SELECT ?message ?path ?focusNode ?value WHERE {
        ?r a sh:ValidationResult ;
           sh:resultMessage ?message ;
           sh:focusNode ?focusNode .
        OPTIONAL { ?r sh:resultPath ?path }
        OPTIONAL { ?r sh:value ?value }
    }
    """
    return [
        {
            "message":    str(row.message),
            "path":       str(row.path) if row.path else None,
            "focus_node": str(row.focusNode),
            "value":      str(row.value) if row.value else None,
        }
        for row in results_graph.query(q)
    ]
