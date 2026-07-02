"""SHACL validation logic — runs pyshacl for real, no network."""

import os

import rdflib

from tools.shacl import _validate_graph_with_shapes
from .conftest import VALID_COMPANY_TTL, INVALID_COMPANY_TTL

SHAPES = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "shapes", "company.ttl"
)


def _graph(ttl: str) -> rdflib.Graph:
    g = rdflib.Graph()
    g.parse(data=ttl, format="turtle")
    return g


def test_valid_company_conforms():
    conforms, report = _validate_graph_with_shapes(_graph(VALID_COMPANY_TTL), SHAPES)
    assert conforms is True, report


def test_missing_name_violates():
    conforms, report = _validate_graph_with_shapes(_graph(INVALID_COMPANY_TTL), SHAPES)
    assert conforms is False
    assert "name" in report.lower()


def test_shapes_accepts_raw_turtle_string():
    shapes_ttl = open(SHAPES).read()
    conforms, _ = _validate_graph_with_shapes(_graph(VALID_COMPANY_TTL), shapes_ttl)
    assert conforms is True
