"""SPARQL 1.1 Update tool — INSERT, DELETE, CLEAR, DROP, LOAD, and more."""

import logging
from typing import Any

import httpx

from tools._common import UPDATE_URL, AUTH

_LOG = logging.getLogger(__name__)
_TIMEOUT_DEFAULT = 60.0
_TIMEOUT_LOAD    = 300.0   # LOAD fetches a remote file; endpoint needs time


def sparql_update(update: str) -> dict[str, Any]:
    """
    Execute any SPARQL 1.1 Update operation against the configured endpoint.

    Covers all update forms defined in the SPARQL 1.1 Update specification:

      INSERT DATA { ... }              — add explicit triples (no variables)
      DELETE DATA { ... }              — remove explicit triples (no variables)
      INSERT { ... } WHERE { ... }     — pattern-matched insert
      DELETE { ... } WHERE { ... }     — pattern-matched delete
      DELETE { ... } INSERT { ... }
        WHERE { ... }                  — atomic replace (delete + insert in one)
      CLEAR [SILENT] GRAPH <g>         — remove all triples; graph still exists
      DROP   [SILENT] GRAPH <g>        — delete the named graph entirely
      CREATE [SILENT] GRAPH <g>        — create an empty named graph
      COPY <src> TO <dst>              — copy all triples from one graph to another
      MOVE <src> TO <dst>              — move (copy + drop source)
      ADD  <src> TO <dst>              — merge source into destination
      LOAD <url> INTO GRAPH <g>        — server-side HTTP fetch of a remote RDF file;
                                         no bytes flow through this server

    Multiple operations may be chained with semicolons in a single call.
    Include PREFIX declarations at the top when using abbreviated URIs.
    Auth credentials are taken from the server's environment configuration.
    For LOAD operations the timeout is automatically extended to 300 s.

    update: A complete SPARQL 1.1 Update string.
            Example — add triples to a named graph:
              PREFIX schema: <https://schema.org/>
              PREFIX xsd:    <http://www.w3.org/2001/XMLSchema#>
              INSERT DATA {
                GRAPH <http://example.org/companies> {
                  <http://example.org/entity/vectara>
                      a schema:Organization ;
                      schema:name "Vectara" ;
                      schema:foundingDate "2020"^^xsd:gYear .
                }
              }

            Example — load a remote dataset:
              LOAD <https://example.org/data/companies.ttl>
              INTO GRAPH <http://example.org/companies>

    Returns {success, http_status} on success,
            {success, http_status, error} on failure.
    """
    timeout = _TIMEOUT_LOAD if "LOAD" in update.upper() else _TIMEOUT_DEFAULT
    _LOG.info("sparql_update (timeout=%.0fs): %s", timeout, update[:120].replace("\n", " "))
    try:
        r = httpx.post(
            UPDATE_URL,
            content=update,
            headers={"Content-Type": "application/sparql-update"},
            auth=AUTH,
            timeout=timeout,
        )
        if not r.is_success:
            _LOG.error("sparql_update HTTP %s: %s", r.status_code, r.text[:300])
        return {
            "success": r.is_success,
            "http_status": r.status_code,
            "error": r.text if not r.is_success else None,
        }
    except Exception as exc:
        _LOG.error("sparql_update failed: %s", exc)
        return {"success": False, "http_status": None, "error": str(exc)}
