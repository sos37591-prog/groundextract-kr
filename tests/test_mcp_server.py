"""Integration test for the MCP stdio server (newline-delimited JSON-RPC).

Spawns the real server as a subprocess (``python -m groundextract.mcp_server``),
drives one full session over stdin — initialize -> notifications/initialized ->
tools/list -> tools/call — and asserts on the parsed responses. Key-free and
offline: verify_extraction is deterministic, and extract_verified is pointed at
a closed local port (via the server-side OLLAMA_HOST setting, which is the only
way to move it) so its Ollama error path is exercised without a server.
Windows-safe: text mode with explicit utf-8 and a communicate() timeout.

Also covers the hardening: doc_type is allowlisted, ``host`` is not a caller
parameter, and one malformed line never ends the session.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from groundextract import available_doc_types
from groundextract.llm.openweight import FIELD_SPECS
from groundextract.mcp_server import SUPPORTED_DOC_TYPES

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT_S = 60
CLOSED_PORT_HOST = "http://127.0.0.1:9"

# Same document as the tax_hallucinated_vat_01 golden doc: the VAT value
# (250,000) appears nowhere in the text and its cited quote is fabricated.
DOC = (
    "전자세금계산서\n"
    "공급가액  1,000,000원\n"
    "세액        100,000원\n"
    "합계금액  1,100,000원\n"
)

# Field swap: 250,000원 IS printed on this document (as a unit price), so
# grounding passes and only the arithmetic can tell it is not the VAT.
SWAP_DOC = (
    "전자세금계산서\n"
    "품목 단가  250,000원\n"
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

SWAP_VALUES = [
    _value("supply", "1,000,000원", 1_000_000, "공급가액  1,000,000원"),
    _value("vat", "250,000원", 250_000, "품목 단가  250,000원"),  # swapped field
    _value("total", "1,100,000원", 1_100_000, "합계금액  1,100,000원"),
]

# The same extraction from a client that leaves the optional 'number' out.
SWAP_VALUES_NO_NUMBER = [
    {k: v for k, v in value.items() if k != "number"} for value in SWAP_VALUES
]


def _run_server(requests: list[dict], extra_env: dict[str, str] | None = None) -> list[str]:
    """Send all requests, close stdin, return the server's stdout lines."""
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests)
    return _run_server_raw(payload, extra_env)


def _run_server_raw(payload: str, extra_env: dict[str, str] | None = None) -> list[str]:
    """Feed raw stdin text (may be malformed) and return the stdout lines."""
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.update(extra_env or {})
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
                # The endpoint comes from the server's OLLAMA_HOST (set to a
                # closed local port below), never from these arguments: the
                # Ollama call must fail fast and deterministically, regardless
                # of any real local server.
                "name": "extract_verified",
                "arguments": {"full_text": DOC, "doc_type": "tax_invoice", "timeout": 5},
            },
        },
        {"jsonrpc": "2.0", "id": 5, "method": "ping"},
        {"jsonrpc": "2.0", "id": 6, "method": "no/such_method"},
        {
            # doc_type typo: not in the schema enum -> refused outright
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "verify_extraction",
                "arguments": {
                    "full_text": SWAP_DOC,
                    "doc_type": "tax-invoice",
                    "values": SWAP_VALUES,
                },
            },
        },
        {
            # a valid doc_type, but the wrong one: no rule of that pack
            # references these fields, so nothing may come back verified
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "verify_extraction",
                "arguments": {
                    "full_text": SWAP_DOC,
                    "doc_type": "statement",
                    "values": SWAP_VALUES,
                },
            },
        },
        {
            # the optional 'number' omitted by an ordinary client
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "verify_extraction",
                "arguments": {
                    "full_text": SWAP_DOC,
                    "doc_type": "tax_invoice",
                    "values": SWAP_VALUES_NO_NUMBER,
                },
            },
        },
        {
            # SSRF attempt: the endpoint is server config, not caller input
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "extract_verified",
                "arguments": {
                    "full_text": DOC,
                    "doc_type": "tax_invoice",
                    "host": "http://attacker.example",
                },
            },
        },
        {
            # path traversal through doc_type
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "verify_extraction",
                "arguments": {
                    "full_text": SWAP_DOC,
                    "doc_type": "../rules/tax_invoice",
                    "values": SWAP_VALUES,
                },
            },
        },
        {
            # the matching pack: the swap is caught by the arithmetic
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "verify_extraction",
                "arguments": {
                    "full_text": SWAP_DOC,
                    "doc_type": "tax_invoice",
                    "values": SWAP_VALUES,
                },
            },
        },
    ]
    out: dict[int, dict] = {}
    for line in _run_server(requests, {"OLLAMA_HOST": CLOSED_PORT_HOST}):
        msg = json.loads(line)
        if msg.get("id") is not None:
            out[msg["id"]] = msg
    return out


def _payload(response: dict) -> dict:
    """The tool result's JSON body."""
    result = response["result"]
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


def test_initialize(responses: dict[int, dict]) -> None:
    result = responses[1]["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "groundextract-mcp"
    assert "tools" in result["capabilities"]


def test_notification_gets_no_response(responses: dict[int, dict]) -> None:
    # every request carried an id; the initialized notification must not
    # produce an extra response line.
    assert set(responses) == set(range(1, 13))


def test_tools_list_exposes_both_tools(responses: dict[int, dict]) -> None:
    tools = responses[2]["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {"verify_extraction", "extract_verified"}
    for tool in tools:
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert "full_text" in schema["properties"]
        # the Ollama endpoint is server configuration and must not be offered
        # as a tool parameter (SSRF / confused deputy)
        assert "host" not in schema["properties"]
        assert schema["properties"]["doc_type"]["enum"] == list(SUPPORTED_DOC_TYPES)


def test_doc_types_agree_across_the_three_surfaces() -> None:
    """A doc_type has to exist on all three surfaces or it is unusable.

    A rule pack with no ``FIELD_SPECS`` entry cannot be extracted for; a
    ``FIELD_SPECS`` entry with no pack extracts values the gate then discards for
    want of arithmetic; and either one missing from the MCP enum is unreachable by
    an agent. The three drifted apart silently before, so assert them equal.
    """
    packs = set(available_doc_types())
    assert set(SUPPORTED_DOC_TYPES) == packs
    assert set(FIELD_SPECS) == packs


def test_verify_extraction_discards_hallucinated_vat(responses: dict[int, dict]) -> None:
    payload = _payload(responses[3])

    by_field = {f["field"]: f for f in payload["fields"]}
    vat = by_field["vat"]
    assert vat["verdict"] == "discarded"
    assert vat["confidence"] == 0.0
    assert any(c["name"] == "grounding" and not c["passed"] for c in vat["checks"])

    # Both invariants break. `vat` and `supply` each appear in both, so arithmetic
    # cannot say which one is the liar and both are discarded; `total` appears in
    # only one, so it cannot be the single explanation and survives.
    # The summary also states which pack ran and how many rules it applied, so
    # the caller can tell verification apart from "nothing was checked".
    assert payload["summary"] == {
        "total": 3,
        "verified": 1,
        "discarded": 2,
        "ungrounded": 1,
        "rule_pack": "tax_invoice",
        "rules_applied": 2,
    }
    assert by_field["supply"]["verdict"] == "discarded"
    assert by_field["total"]["verdict"] == "verified"


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


# --- fail-closed gate over the wire --------------------------------------------


def test_misspelled_doc_type_is_rejected(responses: dict[int, dict]) -> None:
    # 'tax-invoice' used to resolve to "no rule pack" and verify everything.
    error = responses[7]["error"]
    assert error["code"] == -32602
    assert "tax_invoice" in error["message"]  # names the accepted values


def test_wrong_but_valid_doc_type_verifies_nothing(responses: dict[int, dict]) -> None:
    payload = _payload(responses[8])
    assert payload["summary"]["verified"] == 0
    assert payload["summary"]["rules_applied"] == 0
    assert payload["summary"]["rule_pack"] == "statement"
    for field in payload["fields"]:
        assert field["verdict"] == "discarded"
        assert field["confidence"] == 0.0
        assert any(c["name"] == "rules_applied" and not c["passed"] for c in field["checks"])


def test_values_without_number_are_still_verified_arithmetically(
    responses: dict[int, dict],
) -> None:
    payload = _payload(responses[9])
    # the swapped `vat` and its indistinguishable sibling `supply` are discarded
    assert payload["summary"]["discarded"] == 2
    assert payload["summary"]["rules_applied"] == 2  # the arithmetic really ran
    vat = next(f for f in payload["fields"] if f["field"] == "vat")
    assert vat["value"]["number"] is None  # client omitted the optional field
    assert vat["verdict"] == "discarded"
    assert any(c["name"] == "vat_equals_supply_x_10pct" and not c["passed"] for c in vat["checks"])


def test_host_argument_is_rejected(responses: dict[int, dict]) -> None:
    # SSRF: a caller-chosen endpoint would receive the whole document and its
    # reply would be handed back to the caller.
    error = responses[10]["error"]
    assert error["code"] == -32602
    assert "host" in error["message"] and "OLLAMA_HOST" in error["message"]


def test_doc_type_path_traversal_is_rejected(responses: dict[int, dict]) -> None:
    assert responses[11]["error"]["code"] == -32602


def test_field_swap_is_discarded_with_the_right_pack(responses: dict[int, dict]) -> None:
    payload = _payload(responses[12])
    assert payload["summary"]["discarded"] == 2
    assert payload["summary"]["rules_applied"] == 2
    by_field = {f["field"]: f for f in payload["fields"]}
    # the swap makes `vat` and `supply` equally plausible culprits; `total` is
    # referenced by only one of the two broken rules, so it is not implicated
    assert by_field["vat"]["verdict"] == "discarded"
    assert by_field["supply"]["verdict"] == "discarded"
    assert by_field["total"]["verdict"] == "verified"


# --- the loop survives malformed input -----------------------------------------

_PING = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})


# Built lazily and keyed by a short label: the payloads are ~100 KB each and
# pytest puts parametrized values into the test id (and into an environment
# variable, which Windows caps at 32 KB).
_BAD_LINES = {
    "deep_array": lambda: "[" * 100_000 + "]" * 100_000,      # RecursionError
    "deep_object": lambda: '{"a":' * 100_000 + "1" + "}" * 100_000,  # RecursionError
    "huge_int": lambda: "1" * 5001,  # ValueError: exceeds int digit limit
}


@pytest.mark.parametrize("label", sorted(_BAD_LINES))
def test_malformed_line_does_not_kill_the_session(label: str) -> None:
    # RecursionError (nesting) and ValueError (int digit limit) are not
    # JSONDecodeError; leaving them uncaught ended the process, and every later
    # request on that pipe then went unanswered.
    lines = _run_server_raw(f"{_PING}\n{_BAD_LINES[label]()}\n{_PING}\n")
    responses = [json.loads(line) for line in lines]
    pings = [r for r in responses if r.get("result") == {}]
    assert len(pings) == 2, f"{label}: server stopped answering ({responses})"
    assert any("error" in r for r in responses)  # the bad line was reported


def test_overlong_line_is_rejected_without_dropping_the_session() -> None:
    # The oversized line ends with a well-formed request: its tail must be
    # dropped with the rest of the line, not replayed as the next message.
    huge = '{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":"' + "a" * (5 * 1024 * 1024)
    lines = _run_server_raw(f"{huge}{_PING}\n{_PING}\n")
    responses = [json.loads(line) for line in lines]
    assert any(r.get("error", {}).get("code") == -32600 for r in responses)
    assert len([r for r in responses if r.get("result") == {}]) == 1  # only the real ping
