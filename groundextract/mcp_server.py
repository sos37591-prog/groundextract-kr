"""MCP stdio server exposing the GroundExtract-KR verification gate.

Speaks MCP (protocol ``2024-11-05``) over stdio using newline-delimited
JSON-RPC 2.0: one JSON message per line on stdin, one response per line on
stdout (no ``Content-Length`` framing — MCP's stdio transport is
newline-delimited). Standard library only; no new runtime dependencies.

Tools:

  * ``verify_extraction`` — deterministic, key-free. Takes already-extracted
    values plus the source text, runs verbatim grounding + the arithmetic rule
    pack, and returns per-field verdicts and a summary.
  * ``extract_verified`` — extraction + gate in one call. Requires the optional
    Ollama backend (``groundextract.llm.OllamaExtractor``); when the backend is
    missing or the Ollama server is unreachable, returns an ``isError`` tool
    result with guidance instead of crashing.

Run:  python -m groundextract.mcp_server
"""

from __future__ import annotations

import json
import sys
from typing import Any

from . import __version__
from .gate import run_gate, summarize
from .models import ExtractedValue
from .rules import RulePack, default_rules_dir, load_rule_pack

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "groundextract-mcp"

_RULES_DIR = default_rules_dir()

# JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class MethodNotFound(Exception):
    """Unknown JSON-RPC method -> error -32601."""


class InvalidParamsError(ValueError):
    """Request/tool params failed validation -> error -32602."""


# --- tool catalogue -------------------------------------------------------------

_VALUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "field": {"type": "string", "description": "Field name (e.g. 'supply', 'vat')."},
        "raw": {"type": "string", "description": "Value string as extracted."},
        "number": {"type": "number", "description": "Parsed numeric value, if numeric."},
        "grounding_quote": {
            "type": "string",
            "description": "Verbatim source span the value was read from.",
        },
    },
    "required": ["field", "raw"],
}

_DOC_TYPE_SCHEMA: dict[str, Any] = {
    "type": "string",
    "enum": ["tax_invoice", "statement", "balance_sheet"],
    "description": "Document type; selects the arithmetic rule pack (rules/<doc_type>.yaml).",
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "verify_extraction",
        "description": (
            "Deterministic, key-free verification gate for extracted document values: "
            "each value must be grounded verbatim in the document text AND satisfy the "
            "arithmetic rule pack for the doc type. Ungrounded or rule-violating values "
            "are auto-discarded at confidence 0."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "full_text": {"type": "string", "description": "Full document text."},
                "doc_type": _DOC_TYPE_SCHEMA,
                "values": {
                    "type": "array",
                    "items": _VALUE_SCHEMA,
                    "description": "Extracted values to verify.",
                },
            },
            "required": ["full_text", "doc_type", "values"],
        },
    },
    {
        "name": "extract_verified",
        "description": (
            "Extract values from the document with the local Ollama backend, then run "
            "the verification gate. Requires a running Ollama server; use "
            "verify_extraction for the key-free deterministic path."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "full_text": {"type": "string", "description": "Full document text."},
                "doc_type": _DOC_TYPE_SCHEMA,
                "model": {
                    "type": "string",
                    "description": "Optional Ollama model tag (default qwen2.5:7b).",
                },
                "host": {
                    "type": "string",
                    "description": "Optional Ollama base URL (default http://localhost:11434).",
                },
                "timeout": {
                    "type": "number",
                    "description": "Optional HTTP timeout in seconds for the Ollama call.",
                },
            },
            "required": ["full_text", "doc_type"],
        },
    },
]


# --- helpers --------------------------------------------------------------------


def _require_str(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise InvalidParamsError(f"'{key}' must be a non-empty string")
    return value


def _parse_values(raw_values: object) -> list[ExtractedValue]:
    if not isinstance(raw_values, list):
        raise InvalidParamsError("'values' must be an array of objects")
    parsed: list[ExtractedValue] = []
    for i, item in enumerate(raw_values):
        if not isinstance(item, dict):
            raise InvalidParamsError(f"values[{i}] must be an object")
        field = item.get("field")
        raw = item.get("raw")
        if not isinstance(field, str) or not isinstance(raw, str):
            raise InvalidParamsError(f"values[{i}] needs string 'field' and 'raw'")
        number = item.get("number")
        if number is not None and (
            isinstance(number, bool) or not isinstance(number, int | float)
        ):
            raise InvalidParamsError(f"values[{i}].number must be a number")
        quote = item.get("grounding_quote")
        if quote is not None and not isinstance(quote, str):
            raise InvalidParamsError(f"values[{i}].grounding_quote must be a string")
        parsed.append(
            ExtractedValue(
                field=field,
                raw=raw,
                number=float(number) if number is not None else None,
                grounding_quote=quote,
            )
        )
    return parsed


def _rule_pack_for(doc_type: str) -> RulePack | None:
    """Load ``rules/<doc_type>.yaml`` from the repo root, or None when absent."""
    if not doc_type.replace("_", "").isalnum():  # no path separators / traversal
        return None
    path = _RULES_DIR / f"{doc_type}.yaml"
    return load_rule_pack(path) if path.exists() else None


def _text_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


def _gate_payload(values: list[ExtractedValue], full_text: str, doc_type: str) -> str:
    """Run the gate and serialize {fields, summary} as a JSON string."""
    fields = run_gate(values, full_text, _rule_pack_for(doc_type))
    payload = {"fields": [f.to_dict() for f in fields], "summary": summarize(fields)}
    return json.dumps(payload, ensure_ascii=False)


# --- tools ----------------------------------------------------------------------


def _tool_verify_extraction(args: dict[str, Any]) -> dict[str, Any]:
    """Deterministic gate over caller-supplied extracted values. No keys, no network."""
    full_text = _require_str(args, "full_text")
    doc_type = _require_str(args, "doc_type")
    values = _parse_values(args.get("values"))
    return _text_result(_gate_payload(values, full_text, doc_type))


def _extractor_kwargs(args: dict[str, Any]) -> dict[str, Any]:
    """Optional OllamaExtractor overrides (model/host/timeout) from tool args."""
    kwargs: dict[str, Any] = {}
    for key in ("model", "host"):
        value = args.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value:
            raise InvalidParamsError(f"'{key}' must be a non-empty string")
        kwargs[key] = value
    timeout = args.get("timeout")
    if timeout is not None:
        if isinstance(timeout, bool) or not isinstance(timeout, int | float) or timeout <= 0:
            raise InvalidParamsError("'timeout' must be a positive number")
        kwargs["timeout"] = float(timeout)
    return kwargs


def _tool_extract_verified(args: dict[str, Any]) -> dict[str, Any]:
    """Ollama extraction + gate. Degrades to an isError result when unavailable."""
    full_text = _require_str(args, "full_text")
    doc_type = _require_str(args, "doc_type")
    kwargs = _extractor_kwargs(args)
    try:
        from .llm import OllamaExtractor
    except ImportError:
        return _text_result(
            "OllamaExtractor is not available in this build (groundextract.llm has no "
            "Ollama backend). Extract values with your own model and call "
            "verify_extraction for the key-free deterministic gate instead.",
            is_error=True,
        )
    try:
        values = OllamaExtractor(**kwargs).extract(full_text, doc_type=doc_type)
    except Exception as exc:  # connection refused / model missing / bad response
        return _text_result(
            f"Ollama extraction failed: {exc}. Check that a local Ollama server is "
            "running (`ollama serve`) and the model is pulled, or call "
            "verify_extraction for the key-free deterministic gate.",
            is_error=True,
        )
    return _text_result(_gate_payload(values, full_text, doc_type))


_TOOL_HANDLERS = {
    "verify_extraction": _tool_verify_extraction,
    "extract_verified": _tool_extract_verified,
}


def _call_tool(name: object, arguments: object) -> dict[str, Any]:
    if not isinstance(name, str) or name not in _TOOL_HANDLERS:
        raise InvalidParamsError(f"unknown tool: {name!r}")
    if not isinstance(arguments, dict):
        raise InvalidParamsError("'arguments' must be an object")
    try:
        return _TOOL_HANDLERS[name](arguments)
    except InvalidParamsError:
        raise
    except Exception as exc:  # MCP: tool execution failures -> isError result
        return _text_result(f"tool execution failed: {exc}", is_error=True)


# --- JSON-RPC plumbing ----------------------------------------------------------


def _dispatch(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Compute the JSON-RPC ``result`` for one request (raises on error)."""
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        return _call_tool(params.get("name"), params.get("arguments") or {})
    raise MethodNotFound(method)


def _error_response(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one parsed JSON-RPC message; return the response, or None for
    notifications (e.g. ``notifications/initialized``), which never get replies."""
    method = msg.get("method")
    has_id = "id" in msg
    msg_id = msg.get("id")

    if not isinstance(method, str):
        if not has_id:
            return None
        return _error_response(msg_id, INVALID_REQUEST, "Invalid Request: missing method")

    if not has_id:  # notification — including unknown ones — gets no response
        return None

    params = msg.get("params")
    try:
        result = _dispatch(method, params if isinstance(params, dict) else {})
    except MethodNotFound:
        return _error_response(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")
    except InvalidParamsError as exc:
        return _error_response(msg_id, INVALID_PARAMS, str(exc))
    except Exception as exc:
        return _error_response(msg_id, INTERNAL_ERROR, f"Internal error: {exc}")
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _write(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    """Serve MCP over stdio: newline-delimited JSON-RPC, one message per line."""
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # Windows pipes default to cp949

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            _write(_error_response(None, PARSE_ERROR, f"Parse error: {exc.msg}"))
            continue
        if not isinstance(msg, dict):
            _write(_error_response(None, INVALID_REQUEST, "Invalid Request: expected object"))
            continue
        response = handle_message(msg)
        if response is not None:
            _write(response)


if __name__ == "__main__":
    main()
