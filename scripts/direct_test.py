#!/usr/bin/env python3
"""
Direct integration test — exercises the generic SPARQL MCP tools without
going through the Vectara cloud agent.

Requires:
  - A SPARQL 1.1 endpoint running (e.g. Apache Jena Fuseki via ./setup_fuseki.sh)
  - .env with SPARQL_ENDPOINT (or SPARQL_QUERY_URL) and VECTARA_API_KEY set

Run from project root:
  python scripts/direct_test.py
  python scripts/direct_test.py --skip-vectara
  python scripts/direct_test.py --reset
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from rdflib import Graph

from tools._common import QUERY_URL
from tools.graph_store import graph_list, graph_get
from tools.shacl import validate_shacl
from tools.sparql_query import sparql_ask, sparql_select
from tools.sparql_update import sparql_update
from tools.vectara import vectara_index_document

GRAPH_URI    = "http://agentic-ingestion/graph/tech-companies"
CORPUS_KEY   = "agentic_ingestion_kashif"
ENTITY_BASE  = "http://agentic-ingestion/entity/company"
SHAPES_FILE  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "shapes", "company.ttl")

COMPANIES = [
    {
        "id": "vectara",
        "title": "Vectara",
        "uri": f"{ENTITY_BASE}/vectara",
        "doc_id": "company-vectara",
        "source_doc_id": "company_profiles/vectara.txt",
        "description": (
            "Vectara is an AI-native platform for building trusted search and retrieval "
            "augmented generation (RAG) applications. Founded in 2020, Vectara provides "
            "a fully managed platform for semantic search, hybrid retrieval, hallucination "
            "detection (HHEM), and agentic AI workflows with MCP and Lambda integrations."
        ),
        "url": "https://vectara.com",
        "founding_year": "2020",
        "turtle": """\
@prefix schema: <https://schema.org/> .
@prefix xsd:    <http://www.w3.org/2001/XMLSchema#> .

<{uri}>
    a schema:Organization ;
    schema:name "Vectara" ;
    schema:url <https://vectara.com> ;
    schema:description "AI-native platform for trusted search and RAG applications." ;
    schema:foundingDate "2020"^^xsd:gYear .
""",
    },
    {
        "id": "anthropic",
        "title": "Anthropic",
        "uri": f"{ENTITY_BASE}/anthropic",
        "doc_id": "company-anthropic",
        "source_doc_id": "company_profiles/anthropic.txt",
        "description": (
            "Anthropic is an AI safety company founded in 2021. It builds Claude, a family "
            "of AI assistants prioritising safety, helpfulness, and honesty, and pioneered "
            "Constitutional AI and the Model Context Protocol (MCP) open standard."
        ),
        "url": "https://anthropic.com",
        "founding_year": "2021",
        "turtle": """\
@prefix schema: <https://schema.org/> .
@prefix xsd:    <http://www.w3.org/2001/XMLSchema#> .

<{uri}>
    a schema:Organization ;
    schema:name "Anthropic" ;
    schema:url <https://anthropic.com> ;
    schema:description "AI safety company building reliable, interpretable AI systems and the MCP open standard." ;
    schema:foundingDate "2021"^^xsd:gYear .
""",
    },
    {
        "id": "pinecone",
        "title": "Pinecone",
        "uri": f"{ENTITY_BASE}/pinecone",
        "doc_id": "company-pinecone",
        "source_doc_id": "company_profiles/pinecone.txt",
        "description": (
            "Pinecone is a cloud-native vector database founded in 2019 by Edo Liberty. "
            "It provides managed vector storage for semantic search, recommendation engines, "
            "and RAG pipelines, supporting billions of vectors with metadata filtering."
        ),
        "url": "https://pinecone.io",
        "founding_year": "2019",
        "turtle": """\
@prefix schema: <https://schema.org/> .
@prefix xsd:    <http://www.w3.org/2001/XMLSchema#> .

<{uri}>
    a schema:Organization ;
    schema:name "Pinecone" ;
    schema:url <https://pinecone.io> ;
    schema:description "Cloud-native managed vector database for AI applications at scale." ;
    schema:foundingDate "2019"^^xsd:gYear .
""",
    },
    {
        "id": "weaviate",
        "title": "Weaviate",
        "uri": f"{ENTITY_BASE}/weaviate",
        "doc_id": "company-weaviate",
        "source_doc_id": "company_profiles/weaviate.txt",
        "description": (
            "Weaviate is an open-source vector database founded in 2019 in Amsterdam. "
            "It combines vector search with schema-aware object storage, supporting "
            "GraphQL, REST, and gRPC. Available open-source and as Weaviate Cloud Services."
        ),
        "url": "https://weaviate.io",
        "founding_year": "2019",
        "turtle": """\
@prefix schema: <https://schema.org/> .
@prefix xsd:    <http://www.w3.org/2001/XMLSchema#> .

<{uri}>
    a schema:Organization ;
    schema:name "Weaviate" ;
    schema:url <https://weaviate.io> ;
    schema:description "Open-source vector database combining semantic search with schema-aware object storage." ;
    schema:foundingDate "2019"^^xsd:gYear .
""",
    },
]


# ── helpers ───────────────────────────────────────────────────────────────────

def _check_endpoint() -> bool:
    import httpx
    from tools._common import QUERY_URL, AUTH
    try:
        r = httpx.post(
            QUERY_URL,
            content="ASK {}",
            headers={"Content-Type": "application/sparql-query", "Accept": "application/sparql-results+json"},
            auth=AUTH,
            timeout=5,
        )
        return r.is_success
    except Exception:
        return False


def _turtle_to_nt(turtle: str) -> str:
    g = Graph()
    g.parse(data=turtle, format="turtle")
    return "\n".join(
        line for line in g.serialize(format="nt").splitlines()
        if line and not line.startswith("#")
    )


def _ingest(turtle: str, source_doc_id: str) -> dict:
    """Validate with SHACL, then INSERT DATA into the named graph."""
    shacl = validate_shacl(turtle, SHAPES_FILE)
    if not shacl["success"] or not shacl["conforms"]:
        violations = "; ".join(v["message"] for v in shacl.get("violations", []))
        return {"ok": False, "reason": f"SHACL violation: {violations}"}

    nt = _turtle_to_nt(turtle)
    provenance = (
        f'<urn:source:{source_doc_id}> '
        f'<http://purl.org/dc/terms/source> "{source_doc_id}" .'
    )
    update = (
        f"INSERT DATA {{\n"
        f"  GRAPH <{GRAPH_URI}> {{\n"
        f"{nt}\n"
        f"    {provenance}\n"
        f"  }}\n"
        f"}}"
    )
    result = sparql_update(update)
    if not result["success"]:
        return {"ok": False, "reason": result.get("error", "update failed")}

    triple_count = len(Graph().parse(data=turtle, format="turtle")) + 1
    return {"ok": True, "triples_inserted": triple_count}


def sep(title: str = ""):
    width = 60
    if title:
        pad = (width - len(title) - 2) // 2
        print("\n" + "─" * pad + f" {title} " + "─" * pad)
    else:
        print("─" * width)


# ── main ──────────────────────────────────────────────────────────────────────

def run(skip_vectara: bool = False, reset: bool = False):
    sep("Preflight checks")

    if not _check_endpoint():
        print(f"ERROR: SPARQL endpoint not reachable at {QUERY_URL}")
        print("  Check SPARQL_ENDPOINT in .env, then start the endpoint.")
        sys.exit(1)
    print(f"  SPARQL endpoint OK: {QUERY_URL}")

    if skip_vectara:
        print("  Vectara: SKIPPED (--skip-vectara flag)")
    else:
        key = os.getenv("VECTARA_API_KEY", "")
        print(f"  Vectara API key: {key[:8]}..." if key else "  WARNING: VECTARA_API_KEY is empty")

    if reset:
        sep("Resetting graph")
        result = sparql_update(f"CLEAR GRAPH <{GRAPH_URI}>")
        print(f"  CLEAR GRAPH → {'OK' if result['success'] else 'FAIL: ' + str(result.get('error'))}")

    sep("Named graphs")
    gl = graph_list()
    if gl["success"] and gl["graphs"]:
        for g in gl["graphs"]:
            print(f"  {g['uri']}  ({g['triple_count']} triples)")
    else:
        print("  (none yet)")

    results = []

    sep("Ingesting companies")
    for company in COMPANIES:
        name = company["title"]
        uri  = company["uri"]
        turtle = company["turtle"].format(uri=uri)

        print(f"\n  [{name}]")

        # duplicate check via sparql_ask
        ask = sparql_ask(f"ASK {{ GRAPH <{GRAPH_URI}> {{ <{uri}> ?p ?o }} }}")
        if not ask["success"]:
            print(f"    FAIL — ASK error: {ask.get('error')}")
            results.append({"company": name, "kg_status": "error", "vectara_status": "skipped"})
            continue
        if ask["result"]:
            print(f"    SKIP — already in KG: {uri}")
            results.append({"company": name, "kg_status": "duplicate", "vectara_status": "skipped"})
            continue

        # SHACL validate + INSERT
        ingest = _ingest(turtle, company["source_doc_id"])
        if not ingest["ok"]:
            print(f"    FAIL — {ingest['reason']}")
            results.append({"company": name, "kg_status": "error", "vectara_status": "skipped"})
            continue

        print(f"    KG OK — {ingest['triples_inserted']} triples inserted (SHACL ✓)")

        if skip_vectara:
            print(f"    Vectara: skipped")
            results.append({"company": name, "kg_status": "ok", "vectara_status": "skipped"})
            continue

        vr = vectara_index_document(
            corpus_key=CORPUS_KEY,
            document_id=company["doc_id"],
            title=name,
            text=company["description"],
            metadata={
                "entity_type": "organization",
                "source": "company_profiles",
                "founding_year": company["founding_year"],
                "website": company["url"],
            },
        )
        if vr.get("status") == "ok":
            print(f"    Vectara OK — indexed as '{company['doc_id']}'")
            results.append({"company": name, "kg_status": "ok", "vectara_status": "ok"})
        else:
            print(f"    Vectara FAIL — HTTP {vr.get('http_status')}: {str(vr.get('response', ''))[:120]}")
            results.append({"company": name, "kg_status": "ok", "vectara_status": f"error:{vr.get('http_status')}"})

    sep("Post-ingest SHACL validation")
    gg = graph_get(GRAPH_URI, limit=10000)
    if gg["success"]:
        shacl = validate_shacl(gg["content"], SHAPES_FILE)
        print(f"  Triples in graph : {gg['triple_count']}")
        print(f"  SHACL conforms   : {shacl.get('conforms')}")
        if not shacl.get("conforms"):
            for v in shacl.get("violations", []):
                print(f"    VIOLATION: {v['message']} (path={v['path']}, node={v['focus_node']})")
    else:
        print(f"  graph_get failed: {gg.get('error')}")

    sep("SPARQL query — all organizations")
    rows_result = sparql_select(
        f"""
        PREFIX schema: <https://schema.org/>
        SELECT ?company ?name ?url ?year WHERE {{
            GRAPH <{GRAPH_URI}> {{
                ?company a schema:Organization ;
                         schema:name ?name .
                OPTIONAL {{ ?company schema:url ?url }}
                OPTIONAL {{ ?company schema:foundingDate ?year }}
            }}
        }}
        ORDER BY ?name
        """,
    )
    rows = rows_result.get("rows", [])
    if rows:
        print(f"  {'Name':<20} {'Founded':<10} URL")
        print(f"  {'─'*20} {'─'*10} {'─'*35}")
        for row in rows:
            print(f"  {row.get('name',''):<20} {str(row.get('year','')):<10} {row.get('url','')}")
    else:
        print("  (no results)")

    sep("Summary")
    for r in results:
        kg = "✓" if r["kg_status"] == "ok" else ("DUP" if r["kg_status"] == "duplicate" else "✗")
        v  = "✓" if r["vectara_status"] == "ok" else r["vectara_status"]
        print(f"  {r['company']:<20} KG={kg}  Vectara={v}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Direct integration test for SPARQL MCP tools")
    parser.add_argument("--skip-vectara", action="store_true")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    run(skip_vectara=args.skip_vectara, reset=args.reset)
