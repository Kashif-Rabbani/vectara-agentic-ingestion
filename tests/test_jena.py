"""Fuseki/Jena query tools — SPARQLWrapper replaced by a fake."""

import tools.jena as jena
from .conftest import FakeSPARQLWrapper


def test_list_named_graphs(monkeypatch):
    fake = FakeSPARQLWrapper(
        {"results": {"bindings": [
            {"g": {"value": "http://example.org/graph/companies"}},
            {"g": {"value": "http://example.org/graph/people"}},
        ]}}
    )
    monkeypatch.setattr(jena, "_query_wrapper", lambda: fake)
    assert jena.list_named_graphs() == [
        "http://example.org/graph/companies",
        "http://example.org/graph/people",
    ]


def test_query_named_graph_flattens_rows(monkeypatch):
    fake = FakeSPARQLWrapper(
        {"results": {"bindings": [
            {"s": {"value": "http://x/1"}, "name": {"value": "Acme"}},
        ]}}
    )
    monkeypatch.setattr(jena, "_query_wrapper", lambda: fake)
    rows = jena.query_named_graph("http://g", "SELECT ?s ?name WHERE { ?s ?p ?name }")
    assert rows == [{"s": "http://x/1", "name": "Acme"}]
    assert "SELECT" in fake.last_query


def test_check_duplicate_true(monkeypatch):
    fake = FakeSPARQLWrapper({"boolean": True})
    monkeypatch.setattr(jena, "_query_wrapper", lambda: fake)
    assert jena.check_duplicate("http://g", "http://x/1") is True
    # ASK must be scoped to the graph + entity
    assert "ASK" in fake.last_query
    assert "http://x/1" in fake.last_query


def test_check_duplicate_false_when_absent(monkeypatch):
    fake = FakeSPARQLWrapper({})  # no "boolean" key
    monkeypatch.setattr(jena, "_query_wrapper", lambda: fake)
    assert jena.check_duplicate("http://g", "http://x/1") is False
