"""Shared pytest fixtures and fakes.

These tests run fully offline. Network-bound pieces (Fuseki SPARQL, Vectara
REST) are replaced with fakes; SHACL/Turtle logic runs for real against
rdflib + pyshacl.
"""

import os

import pytest


@pytest.fixture(autouse=True)
def _fuseki_env(monkeypatch):
    """Ensure env lookups in the wrapper factories never KeyError."""
    monkeypatch.setenv("FUSEKI_ENDPOINT", "http://localhost:3030")
    monkeypatch.setenv("FUSEKI_DATASET", "ds")
    monkeypatch.setenv("FUSEKI_USERNAME", "admin")
    monkeypatch.setenv("FUSEKI_PASSWORD", "changeme")
    monkeypatch.setenv("VECTARA_API_KEY", "test-key")


class FakeSPARQLWrapper:
    """Stand-in for SPARQLWrapper.

    Records every query it is given and returns a canned ``convert()`` result.
    ``query()`` returns ``self`` so ``w.query().convert()`` works.
    """

    def __init__(self, convert_result=None):
        self.convert_result = convert_result
        self.queries = []
        self.return_format = None

    # -- recording / no-op setters --
    def setQuery(self, q):
        self.queries.append(q)

    def setReturnFormat(self, fmt):
        self.return_format = fmt

    # -- execution --
    def query(self):
        return self

    def convert(self):
        return self.convert_result

    @property
    def last_query(self):
        return self.queries[-1] if self.queries else None


# ---- sample Turtle payloads -------------------------------------------------

VALID_COMPANY_TTL = """
@prefix schema: <https://schema.org/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<http://example.org/company/acme>
    a schema:Organization ;
    schema:name "Acme Corporation" ;
    schema:url <https://acme.example.org/> ;
    schema:foundingDate "2010"^^xsd:gYear .
"""

# Missing required schema:name -> should fail the CompanyShape.
INVALID_COMPANY_TTL = """
@prefix schema: <https://schema.org/> .

<http://example.org/company/noname>
    a schema:Organization ;
    schema:url <https://noname.example.org/> .
"""

# Turtle that will not parse.
MALFORMED_TTL = "this is not turtle <<< @@@"
