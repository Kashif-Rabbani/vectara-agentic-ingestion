"""ingest_entities — SHACL gate + SPARQL INSERT building.

The Fuseki UPDATE call is faked so the test stays offline, but the captured
query is asserted on to confirm the data is normalised and provenance added.
"""

import tools.jena as jena
from .conftest import (
    FakeSPARQLWrapper,
    VALID_COMPANY_TTL,
    INVALID_COMPANY_TTL,
    MALFORMED_TTL,
)

GRAPH = "http://example.org/graph/companies"


def _capture_update(monkeypatch):
    fake = FakeSPARQLWrapper()
    monkeypatch.setattr(jena, "_update_wrapper", lambda: fake)
    return fake


def test_malformed_turtle_returns_error(monkeypatch):
    fake = _capture_update(monkeypatch)
    result = jena.ingest_entities(GRAPH, "Company", MALFORMED_TTL, "doc-1")
    assert result["status"] == "error"
    assert result["validation_passed"] is False
    assert "parse error" in result["validation_report"].lower()
    # nothing should have been written
    assert fake.last_query is None


def test_shacl_failure_blocks_insert(monkeypatch):
    fake = _capture_update(monkeypatch)
    result = jena.ingest_entities(GRAPH, "Company", INVALID_COMPANY_TTL, "doc-2")
    assert result["status"] == "error"
    assert result["validation_passed"] is False
    assert "validation_report" in result
    assert fake.last_query is None  # no write on validation failure


def test_valid_company_inserts(monkeypatch):
    fake = _capture_update(monkeypatch)
    result = jena.ingest_entities(GRAPH, "Company", VALID_COMPANY_TTL, "doc-3")
    assert result["status"] == "ok"
    assert result["validation_passed"] is True
    # 4 data triples (type, name, url, foundingDate) + 1 provenance
    assert result["triples_inserted"] == 5

    q = fake.last_query
    assert "INSERT DATA" in q
    assert f"GRAPH <{GRAPH}>" in q
    assert "http://purl.org/dc/terms/source" in q  # provenance triple
    assert "doc-3" in q


def test_unknown_entity_type_skips_validation(monkeypatch):
    """No shapes file for 'Widget' -> validation skipped, insert proceeds."""
    fake = _capture_update(monkeypatch)
    ttl = """
    @prefix schema: <https://schema.org/> .
    <http://example.org/w/1> a schema:Thing ; schema:name "W" .
    """
    result = jena.ingest_entities(GRAPH, "Widget", ttl, "doc-4")
    assert result["status"] == "ok"
    assert fake.last_query is not None
