"""Vectara indexing tool — httpx.Client replaced by a fake."""

import httpx

import tools.vectara as vectara


class FakeResponse:
    def __init__(self, status_code, json_body):
        self.status_code = status_code
        self._json = json_body
        self.is_success = 200 <= status_code < 300
        self.text = str(json_body)

    def json(self):
        return self._json


class FakeClient:
    """Context-manager stand-in for httpx.Client."""

    def __init__(self, response=None, raise_exc=None):
        self._response = response
        self._raise = raise_exc
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json, headers):
        self.calls.append({"url": url, "json": json, "headers": headers})
        if self._raise:
            raise self._raise
        return self._response


def _patch_client(monkeypatch, **kwargs):
    fake = FakeClient(**kwargs)
    monkeypatch.setattr(vectara.httpx, "Client", lambda *a, **k: fake)
    return fake


def test_successful_index(monkeypatch):
    fake = _patch_client(
        monkeypatch, response=FakeResponse(201, {"id": "doc-1"})
    )
    result = vectara.vectara_index_document(
        "my-corpus", "doc-1", "Acme", "body text", {"entity_type": "Company"}
    )
    assert result["status"] == "ok"
    assert result["http_status"] == 201
    assert result["document_id"] == "doc-1"

    call = fake.calls[0]
    assert call["url"].endswith("/corpora/my-corpus/documents")
    assert call["headers"]["x-api-key"] == "test-key"
    assert call["json"]["type"] == "structured"
    assert call["json"]["sections"][0]["text"] == "body text"
    assert call["json"]["metadata"]["entity_type"] == "Company"


def test_api_error_status(monkeypatch):
    _patch_client(monkeypatch, response=FakeResponse(400, {"messages": ["bad"]}))
    result = vectara.vectara_index_document(
        "my-corpus", "doc-2", "T", "x", {}
    )
    assert result["status"] == "error"
    assert result["http_status"] == 400


def test_network_error(monkeypatch):
    _patch_client(
        monkeypatch, raise_exc=httpx.ConnectError("boom")
    )
    result = vectara.vectara_index_document(
        "my-corpus", "doc-3", "T", "x", {}
    )
    assert result["status"] == "error"
    assert result["http_status"] is None
    assert "boom" in result["response"]["error"]
