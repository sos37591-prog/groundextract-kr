"""Integration test for the MCP stdio server (newline-delimited JSON-RPC).

Spawns the real server as a subprocess (``python -m groundextract.mcp_server``),
drives one full session over stdin — initialize -> notifications/initialized ->
tools/list -> tools/call — and asserts on the parsed responses. Key-free and
offline: verify_extraction is deterministic, and extract_verified is pointed at
a closed local port so its Ollama error path is exercised without a server.
Windows-safe: text mode with explicit utf-8 and a communicate() timeout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT_S = 60

# Same document as the tax_hallucinated_vat_01 golden doc: the VAT value
# (250,000) appears nowhere in the text and its cited quote is fabricated.
DOC = (
    "전자세금계산서\n"
    "공급가액  1,000,000원\n"
    "세액        100,000원\n"
    "합계금액  1,100,000원\n"
)


def _value(field: str, raw: str, number: int, quote: str) -> dict:
    return {"field": field, "raw": raw, "number": number, "grounding_quote": quote}


HALLUCINATED_VAT_VALUES = [
    _value("supply", "1,000,000원", 1_000_000, "공급가액  1,000,000원"),
    _value("vat", "250,000원", 250_000, "세액  250,000원"),  # hallucinated
    _value("total", "1,100,000원", 1_100_000, "합계금액  1,100,000원"),
]


def _run_server(requests: list[dict]) -> list[str]:
    """Send all requests, close stdin, return the server's stdout lines."""
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests)
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "groundextract.mcp_server"],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )
    try:
        stdout, stderr = proc.communicate(payload, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise
    assert proc.returncode == 0, f"server exited {proc.returncode}: {stderr}"
    return [line for line in stdout.splitlines() if line.strip()]


@pytest.fixture(scope="module")
def responses() -> dict[int, dict]:
    """One full MCP session; responses indexed by request id."""
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "verify_extraction",
                "arguments": {
                    "full_text": DOC,
                    "doc_type": "tax_invoice",
                    "values": HALLUCINATED_VAT_VALUES,
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "extract_verified",
                # Closed local port: the Ollama call must fail fast and
                # deterministically, regardless of any real local server.
                "arguments": {
                    "full_text": DOC,
                    "doc_type": "tax_invoice",
                    "host": "http://127.0.0.1:9",
                    "timeout": 5,
                },
            },
        },
        {"jsonrpc": "2.0", "id": 5, "method": "ping"},
        {"jsonrpc": "2.0", "id": 6, "method": "no/such_method"},
    ]
    out: dict[int, dict] = {}
    for line in _run_server(requests):
        msg = json.loads(line)
        if msg.get("id") is not None:
            out[msg["id"]] = msg
    return out


def test_initialize(responses: dict[int, dict]) -> None:
    result = responses[1]["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "groundextract-mcp"
    assert "tools" in result["capabilities"]


def test_notification_gets_no_response(responses: dict[int, dict]) -> None:
    # 6 requests carried ids (1,2,3,4,5,6); the initialized notification must not
    # produce an extra response line.
    assert set(responses) == {1, 2, 3, 4, 5, 6}


def test_tools_list_exposes_both_tools(responses: dict[int, dict]) -> None:
    tools = responses[2]["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {"verify_extraction", "extract_verified"}
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert "full_text" in tool["inputSchema"]["properties"]


def test_verify_extraction_discards_hallucinated_vat(responses: dict[int, dict]) -> None:
    result = responses[3]["result"]
    assert not result.get("isError")
    payload = json.loads(result["content"][0]["text"])

    by_field = {f["field"]: f for f in payload["fields"]}
    vat = by_field["vat"]
    assert vat["verdict"] == "discarded"
    assert vat["confidence"] == 0.0
    assert any(c["name"] == "grounding" and not c["passed"] for c in vat["checks"])

    # The violated invariants taint the sibling fields they reference (the
    # documented over-discard design), so the whole doc is discarded here.
    assert payload["summary"] == {"total": 3, "verified": 0, "discarded": 3, "ungrounded": 1}


def test_extract_verified_reports_error_without_ollama(responses: dict[int, dict]) -> None:
    # Whether the backend module is missing or the (closed-port) server is
    # unreachable, the tool must answer with isError + actionable guidance.
    result = responses[4]["result"]
    assert result.get("isError") is True
    text = result["content"][0]["text"]
    assert "verify_extraction" in text  # guidance points to the key-free path


def test_ping(responses: dict[int, dict]) -> None:
    assert responses[5]["result"] == {}


def test_unknown_method_is_32601(responses: dict[int, dict]) -> None:
    assert responses[6]["error"]["code"] == -32601
