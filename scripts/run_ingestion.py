#!/usr/bin/env python3
"""
Runs the agentic ingestion scenario: uploads company profile documents to
the Vectara agent as artifacts and lets the agent extract entities and call
the MCP tools (check_duplicate, ingest_entities, vectara_index_document).

Prerequisites:
  - create_agent.py has been run (produces .agent_state.json)
  - Apache Jena Fuseki is running (./setup_fuseki.sh)
  - MCP server is running and reachable at the URL used in create_agent.py

Usage:
  python scripts/run_ingestion.py
  python scripts/run_ingestion.py --doc anthropic.txt   # single document
  python scripts/run_ingestion.py --stream              # stream agent output
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("VECTARA_BASE_URL", "https://api.vectara.io/v2")
API_KEY = os.getenv("VECTARA_API_KEY")
STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".agent_state.json")
PROFILES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "company_profiles")

INGESTION_PROMPT_TEMPLATE = """\
I have uploaded a company profile document. The artifact ID is: {artifact_id}

Please:
1. Read the document using artifact_read with artifact_id="{artifact_id}".
2. Extract the organization(s) described in it.
3. For each organization, check for duplicates, then ingest into the Knowledge Graph and Vectara.
4. Report what was ingested and what (if anything) was skipped as a duplicate.
"""


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        print(f"ERROR: {STATE_FILE} not found.")
        print("  Run first:  python scripts/create_agent.py --mcp-url <url>")
        sys.exit(1)
    with open(STATE_FILE) as f:
        return json.load(f)


def create_session(agent_key: str) -> str:
    r = httpx.post(
        f"{BASE_URL}/agents/{agent_key}/sessions",
        headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
        json={"name": f"ingestion-{int(time.time())}"},
        timeout=10,
    )
    if r.status_code not in (200, 201):
        print(f"ERROR: Could not create session ({r.status_code}): {r.text}")
        sys.exit(1)
    session_key = r.json()["key"]
    print(f"  Session: {session_key}")
    return session_key


def upload_artifact(agent_key: str, session_key: str, filepath: str) -> tuple[str | None, list[dict]]:
    """Upload a text file as an artifact. Returns (artifact_id, events).

    Uploading to the events endpoint with stream_response=false causes the agent
    to run the full ingestion pipeline immediately. We capture those events here
    so the caller can display them and skip the redundant send_message step.
    """
    with open(filepath, "rb") as f:
        r = httpx.post(
            f"{BASE_URL}/agents/{agent_key}/sessions/{session_key}/events",
            headers={"x-api-key": API_KEY},
            files={"files": (os.path.basename(filepath), f, "text/plain")},
            data={"stream_response": "false"},
            timeout=300,
        )

    if r.status_code not in (200, 201):
        print(f"  ERROR: Upload failed ({r.status_code}): {r.text[:200]}")
        return None, []

    events = r.json().get("events", [])
    artifact_id = None
    for event in events:
        if event.get("type") == "artifact_upload":
            artifacts = event.get("artifacts", [])
            if artifacts:
                artifact_id = artifacts[0]["artifact_id"]
                print(f"  Uploaded {os.path.basename(filepath)} → artifact_id={artifact_id}")

    if not artifact_id:
        print(f"  WARNING: Upload succeeded but no artifact_upload event found")

    return artifact_id, events


def send_message(agent_key: str, session_key: str, message: str) -> list[dict]:
    """Send a message and return all events."""
    r = httpx.post(
        f"{BASE_URL}/agents/{agent_key}/sessions/{session_key}/events",
        headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
        json={
            "messages": [{"type": "text", "content": message}],
            "stream_response": False,
        },
        timeout=300,
    )
    if r.status_code not in (200, 201):
        print(f"  ERROR: Message failed ({r.status_code}): {r.text[:300]}")
        return []
    return r.json().get("events", [])


def print_events(events: list[dict]):
    """Print a human-readable trace of agent events."""
    for event in events:
        event_type = event.get("type", "")

        if event_type == "thinking":
            content = (event.get("content") or "")[:120]
            print(f"  [thinking]  {content}...")

        elif event_type == "tool_input":
            tool_name = event.get("tool_configuration_name", "?")
            content = (event.get("content") or "")[:200]
            print(f"  [tool_call] {tool_name}")
            if content:
                print(f"              {content}")

        elif event_type == "tool_output":
            tool_name = event.get("tool_configuration_name", "?")
            output = event.get("tool_output", {})
            summary = json.dumps(output)[:200] if output else ""
            print(f"  [tool_out]  {tool_name} → {summary}")

        elif event_type == "agent_output":
            content = event.get("content", "")
            print(f"\n  [reply]\n{_indent(content, 4)}")

        elif event_type == "step_transition":
            print(f"  [step]      {event.get('from_step')} → {event.get('to_step')}")


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line for line in text.splitlines())


def process_document(agent_key: str, session_key: str, filepath: str) -> bool:
    """Upload one document and let the agent ingest it. Returns True on success.

    Uploading triggers the full agent pipeline immediately (stream_response=false).
    We display the events from the upload step directly — no separate send_message
    needed, as that would re-run the pipeline on a now-duplicate entity.
    """
    filename = os.path.basename(filepath)
    print(f"\n  Uploading: {filename}")

    artifact_id, events = upload_artifact(agent_key, session_key, filepath)
    if not artifact_id:
        return False

    print_events(events)

    tool_calls = [
        e.get("tool_configuration_name")
        for e in events
        if e.get("type") == "tool_input"
    ]
    kg_written = "sparql_update" in tool_calls
    vectara_indexed = "core_document_index_20260220" in tool_calls
    print(f"\n  Tool calls made: {tool_calls}")
    print(f"  KG write called     : {'YES ✓' if kg_written else 'NO'}")
    print(f"  Vectara index called: {'YES ✓' if vectara_indexed else 'NO'}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Run agentic company profile ingestion")
    parser.add_argument("--doc", help="Process only this filename (e.g. anthropic.txt)")
    args = parser.parse_args()

    print("\n=== Agentic Ingestion — Run ===\n")

    state = load_state()
    agent_key = state["agent_key"]
    print(f"  Agent   : {agent_key}")
    print(f"  Corpus  : {state['corpus_key']}")
    print(f"  Graph   : {state['graph_uri']}")
    print(f"  MCP URL : {state['mcp_url']}")

    profiles_dir = Path(PROFILES_DIR)
    if args.doc:
        docs = [profiles_dir / args.doc]
    else:
        docs = sorted(profiles_dir.glob("*.txt"))

    if not docs:
        print(f"\nERROR: No .txt files found in {PROFILES_DIR}")
        sys.exit(1)

    print(f"\n  Documents to process: {[d.name for d in docs]}")

    # Each document gets its own session for a clean context
    success = 0
    for doc_path in docs:
        print(f"\n{'─'*60}")
        print(f"  Document: {doc_path.name}")

        session_key = create_session(agent_key)
        ok = process_document(agent_key, session_key, str(doc_path))
        if ok:
            success += 1

    print(f"\n{'─'*60}")
    print(f"\nDone — {success}/{len(docs)} documents processed successfully.")
    print(f"\nNext step: python scripts/verify_ingestion.py")


if __name__ == "__main__":
    main()
