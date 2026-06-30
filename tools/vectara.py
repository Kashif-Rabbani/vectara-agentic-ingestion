"""Vectara REST API v2 indexing tool."""

import os
from typing import Any

import httpx

def vectara_index_document(
    corpus_key: str,
    document_id: str,
    title: str,
    text: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """
    Index a document into a Vectara corpus using the v2 REST API.

    The document is stored as a structured document with a single text section.
    Metadata is attached at both the document and section level (Vectara merges
    them for retrieval filtering).

    Args:
        corpus_key: The corpus key shown in the Vectara console
                    (e.g. "my-corpus-1").
        document_id: A stable unique identifier for this document. Re-indexing
                     with the same ID will replace the existing document.
        title: Human-readable title for the document.
        text: The main body text to embed and index.
        metadata: Arbitrary key/value pairs attached to the document for
                  metadata filtering during retrieval
                  (e.g. {"source": "arxiv", "entity_type": "Company"}).

    Returns:
        {
          "status": "ok" | "error",
          "document_id": str,
          "corpus_key": str,
          "http_status": int,
          "response": dict   # parsed JSON from Vectara, or error detail
        }
    """
    api_key = os.environ.get("VECTARA_API_KEY", "")
    base_url = os.environ.get("VECTARA_BASE_URL", "https://api.vectara.io/v2")
    url = f"{base_url}/corpora/{corpus_key}/documents"

    payload: dict[str, Any] = {
        "id": document_id,
        "type": "structured",
        "title": title,
        # Per the v2 API, a structured-document section's only required field
        # is "text"; "id" is an optional string (not an int) and is omitted
        # here since a single-section document does not need one.
        "sections": [
            {
                "text": text,
                "metadata": metadata,
            }
        ],
        "metadata": metadata,
    }

    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        with httpx.Client(timeout=90.0) as client:
            response = client.post(url, json=payload, headers=headers)
    except httpx.RequestError as exc:
        return {
            "status": "error",
            "document_id": document_id,
            "corpus_key": corpus_key,
            "http_status": None,
            "response": {"error": str(exc)},
        }

    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text}

    return {
        "status": "ok" if response.is_success else "error",
        "document_id": document_id,
        "corpus_key": corpus_key,
        "http_status": response.status_code,
        "response": body,
    }
