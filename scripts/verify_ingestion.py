#!/usr/bin/env python3
"""
Verifies that the agentic ingestion scenario succeeded by querying both
Apache Jena (SPARQL) and Vectara (semantic search) and printing a comparison.

Run from project root after direct_test.py or run_ingestion.py:
  python scripts/verify_ingestion.py
  python scripts/verify_ingestion.py --search "AI safety"
"""
import argparse
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE_URL = os.getenv("VECTARA_BASE_URL", "https://api.vectara.io/v2")
API_KEY = os.getenv("VECTARA_API_KEY")
CORPUS_KEY = "agentic_ingestion_kashif"
GRAPH_URI = "http://agentic-ingestion/graph/tech-companies"


def _sparql_endpoint() -> str:
    base = os.getenv("SPARQL_ENDPOINT", "http://localhost:3030/ds")
    query_url = os.getenv("SPARQL_QUERY_URL", f"{base}/query")
    return query_url


def _sparql_auth():
    user = os.getenv("SPARQL_USERNAME", "admin")
    pwd = os.getenv("SPARQL_PASSWORD", "")
    return (user, pwd) if user else None


def _check_fuseki() -> bool:
    base = os.getenv("SPARQL_ENDPOINT", "http://localhost:3030/ds")
    ping_url = base.rstrip("/ds").rstrip("/") + "/$/ping"
    try:
        r = httpx.get(ping_url, timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def _sparql_select(query: str) -> list[dict]:
    """Run a SPARQL SELECT via HTTP POST, return list of binding dicts."""
    r = httpx.post(
        _sparql_endpoint(),
        content=query.encode(),
        headers={"Content-Type": "application/sparql-query", "Accept": "application/sparql-results+json"},
        auth=_sparql_auth(),
        timeout=15,
    )
    r.raise_for_status()
    bindings = r.json()["results"]["bindings"]
    return [{k: v["value"] for k, v in b.items()} for b in bindings]


def query_jena_organizations() -> list[dict]:
    """SPARQL SELECT for all Organization entities in the named graph."""
    return _sparql_select(f"""
        PREFIX schema: <https://schema.org/>
        SELECT ?uri ?name ?url ?description ?foundingDate WHERE {{
            GRAPH <{GRAPH_URI}> {{
                ?uri a schema:Organization ;
                     schema:name ?name .
                OPTIONAL {{ ?uri schema:url ?url }}
                OPTIONAL {{ ?uri schema:description ?description }}
                OPTIONAL {{ ?uri schema:foundingDate ?foundingDate }}
            }}
        }}
        ORDER BY ?name
    """)


def query_jena_graph_stats() -> dict:
    """Get triple count for the named graph."""
    rows = _sparql_select(f"""
        SELECT (COUNT(*) AS ?count) WHERE {{
            GRAPH <{GRAPH_URI}> {{ ?s ?p ?o }}
        }}
    """)
    count = int(rows[0]["count"]) if rows else 0
    return {"triple_count": count, "graph_uri": GRAPH_URI}


def search_vectara(query: str, limit: int = 10) -> list[dict]:
    """Run a semantic search on the Vectara corpus."""
    r = httpx.post(
        f"{BASE_URL}/corpora/{CORPUS_KEY}/query",
        headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
        json={
            "query": query,
            "search": {
                "limit": limit,
                "lexical_interpolation": 0.05,
            },
        },
        timeout=30,
    )
    if r.status_code != 200:
        return []
    return r.json().get("search_results", [])


def list_vectara_documents() -> list[dict]:
    """List all documents in the corpus."""
    r = httpx.get(
        f"{BASE_URL}/corpora/{CORPUS_KEY}/documents",
        headers={"x-api-key": API_KEY},
        params={"limit": 50},
        timeout=30,
    )
    if r.status_code != 200:
        return []
    return r.json().get("documents", [])


def print_section(title: str):
    print(f"\n{'═'*60}")
    print(f"  {title}")
    print('═'*60)


def main():
    parser = argparse.ArgumentParser(description="Verify agentic ingestion results")
    parser.add_argument("--search", default="large language model research company",
                        help="Search query for Vectara verification (default: 'AI company vector database RAG')")
    args = parser.parse_args()

    print("\n=== Agentic Ingestion — Verification ===")

    # ── Jena (KG) ──────────────────────────────────────────────────────────
    print_section("Knowledge Graph (Apache Jena Fuseki)")

    if not _check_fuseki():
        print("  ERROR: Fuseki not reachable — is it running? (./setup_fuseki.sh)")
    else:
        stats = query_jena_graph_stats()
        print(f"  Graph   : {stats['graph_uri']}")
        print(f"  Triples : {stats['triple_count']}")

        orgs = query_jena_organizations()
        if not orgs:
            print("\n  No organizations found — run direct_test.py or run_ingestion.py first.")
        else:
            print(f"\n  Organizations ({len(orgs)} found):")
            print(f"  {'Name':<22} {'Founded':<10} {'URL'}")
            print(f"  {'─'*22} {'─'*10} {'─'*35}")
            for org in orgs:
                name = org.get("name", "")
                year = org.get("foundingDate", "")
                url = org.get("url", "")
                print(f"  {name:<22} {year:<10} {url}")

            print(f"\n  Descriptions:")
            for org in orgs:
                name = org.get("name", "?")
                desc = org.get("description", "(none)")[:100]
                print(f"  {name}: {desc}")

    # ── Vectara ────────────────────────────────────────────────────────────
    print_section(f"Vectara Corpus '{CORPUS_KEY}'")

    if not API_KEY:
        print("  VECTARA_API_KEY not set — skipping Vectara verification")
    else:
        docs = list_vectara_documents()
        if docs:
            print(f"  Indexed documents ({len(docs)}):")
            for doc in docs:
                doc_id = doc.get("id", "?")
                title = doc.get("metadata", {}).get("title", "") or doc.get("title", "")
                print(f"    • {doc_id:<30} {title}")
        else:
            print("  No documents found (or API key invalid)")

        print(f"\n  Semantic search: '{args.search}'")
        results = search_vectara(args.search)
        if results:
            for i, res in enumerate(results[:5], 1):
                score = res.get("score", 0)
                text = (res.get("text") or "")[:100]
                doc_id = res.get("document_id", "?")
                print(f"  {i}. [{score:.3f}] {doc_id}: {text}...")
        else:
            print("  No search results (check API key or that indexing ran)")

    # ── Cross-check ─────────────────────────────────────────────────────────
    print_section("Cross-check Summary")

    try:
        kg_names = {org.get("name") for org in query_jena_organizations()} if _check_fuseki() else set()
    except Exception:
        kg_names = set()

    try:
        vectara_docs = list_vectara_documents() if API_KEY else []
        vectara_ids = {d.get("id", "") for d in vectara_docs if d.get("id", "").startswith("company-")}
    except Exception:
        vectara_ids = set()

    expected = {"OpenAI", "Google DeepMind", "Hugging Face", "Mistral AI", "Cohere"}

    print(f"  {'Company':<20} {'In KG':<10} {'In Vectara'}")
    print(f"  {'─'*20} {'─'*10} {'─'*10}")
    for company in sorted(expected):
        in_kg = "✓" if company in kg_names else "✗"
        slug = company.lower()
        in_v = "✓" if f"company-{slug.replace(' ', '-')}" in vectara_ids else "✗"
        print(f"  {company:<20} {in_kg:<10} {in_v}")

    kg_ok = expected <= kg_names
    print(f"\n  KG complete     : {'YES ✓' if kg_ok else 'NO — run ingestion'}")
    print()


if __name__ == "__main__":
    main()
