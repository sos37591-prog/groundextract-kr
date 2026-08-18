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

Trust boundary: every argument here is untrusted, and the documents this server
verifies may themselves carry prompt injection aimed at the agent calling it.
So the two things a caller could otherwise steer are not parameters at all —
the Ollama endpoint is server configuration (``OLLAMA_HOST``) and ``doc_type``
is an allowlist — and a bad line is answered with an error rather than ending
the process, since a dead server silently drops every later request.

Run:  python -m groundextract.mcp_server
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from typing import Any, TextIO

from . import __version__
from .gate import run_gate, summarize
from .models import ExtractedValue
from .rules import RulePack, default_rules_dir, load_rule_pack

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "groundextract-mcp"

_RULES_DIR = default_rules_dir()

#: Document types this server accepts — the single source of truth for both the
#: input schema ``enum`` and the runtime check in :func:`_rule_pack_for`. A
#: doc_type outside this list is rejected instead of quietly resolving to "no
#: rule pack", which used to mean "nothing arithmetic ran".
SUPPORTED_DOC_TYPES: tuple[str, ...] = (
    "tax_invoice",
    "statement",
    "balance_sheet",
    "income_statement",
    "corporate_tax_return",
)

#: Hard limits for the stdio loop. One malformed or hostile line must never be
#: able to take the server down (see :func:`main`).
MAX_LINE_CHARS = 4 * 1024 * 1024
MAX_ERROR_DETAIL_CHARS = 200

#: Hard limits on the *shape* of a request, not just its size on the wire.
#:
#: A single line under ``MAX_LINE_CHARS`` can still describe an enormous amount
#: of work — thousands of values, each compared against a multi-megabyte
#: ``full_text``. The stdio loop answers requests one at a time, so a request
#: that runs for minutes blocks every other caller on the same pipe. These caps
#: bound the work a well-formed request can ask for; the grounding matcher has
#: its own fail-closed cap for the same reason
#: (:data:`groundextract.grounding.MAX_FUZZY_SOURCE_CHARS`).
MAX_VALUES = 256
MAX_VALUE_CHARS = 1024
#: 256 KiB of extracted text is a very long regulatory document — the Korean
#: forms this gate targets run to a few KiB. The cap is what bounds the worst
#: case a caller can ask for (MAX_VALUES fields x this much text). Both tools
#: apply it: ``extract_verified`` sends its ``full_text`` to a model and then
#: gates the answer, so an unbounded string there costs strictly more than one
#: handed to ``verify_extraction``.
MAX_FULL_TEXT_CHARS = 256 * 1024

#: Ceiling on a caller-chosen ``timeout`` for the Ollama call. The stdio loop is
#: sequential, so this is how long one caller may keep every other caller
#: waiting; ``DEFAULT_TIMEOUT`` (120s) is the deployment's own choice and this
#: only bounds how far a request may push past it.
MAX_TIMEOUT_SECONDS = 600.0

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
    "enum": list(SUPPORTED_DOC_TYPES),
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
            "the verification gate. Requires a running Ollama server, whose endpoint is "
            "server configuration (OLLAMA_HOST) and cannot be chosen by the caller; use "
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
                # No "host": the Ollama endpoint is server configuration
                # (OLLAMA_HOST), never caller input. See _extractor_kwargs.
                "timeout": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": MAX_TIMEOUT_SECONDS,
                    "description": (
                        "Optional HTTP timeout in seconds for the Ollama call "
                        f"(default 120, maximum {MAX_TIMEOUT_SECONDS:g})."
                    ),
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


def _require_doc_type(args: dict[str, Any]) -> str:
    """Validate ``doc_type`` against the advertised enum before anything runs."""
    doc_type = _require_str(args, "doc_type")
    if doc_type not in SUPPORTED_DOC_TYPES:
        raise InvalidParamsError(
            f"unsupported doc_type {doc_type!r}; expected one of "
            f"{', '.join(SUPPORTED_DOC_TYPES)}"
        )
    return doc_type


def _require_bounded_str(args: dict[str, Any], key: str, limit: int) -> str:
    """Validate a required string and cap its length."""
    value = _require_str(args, key)
    if len(value) > limit:
        raise InvalidParamsError(f"'{key}' exceeds {limit} characters")
    return value


def _parse_values(raw_values: object) -> list[ExtractedValue]:
    if not isinstance(raw_values, list):
        raise InvalidParamsError("'values' must be an array of objects")
    if len(raw_values) > MAX_VALUES:
        raise InvalidParamsError(f"'values' holds more than {MAX_VALUES} entries")
    parsed: list[ExtractedValue] = []
    for i, item in enumerate(raw_values):
        if not isinstance(item, dict):
            raise InvalidParamsError(f"values[{i}] must be an object")
        field = item.get("field")
        raw = item.get("raw")
        if not isinstance(field, str) or not isinstance(raw, str):
            raise InvalidParamsError(f"values[{i}] needs string 'field' and 'raw'")
        for key, text in (("field", field), ("raw", raw)):
            if len(text) > MAX_VALUE_CHARS:
                raise InvalidParamsError(
                    f"values[{i}].{key} exceeds {MAX_VALUE_CHARS} characters"
                )
        number = item.get("number")
        if number is not None and (
            isinstance(number, bool) or not isinstance(number, int | float)
        ):
            raise InvalidParamsError(f"values[{i}].number must be a number")
        quote = item.get("grounding_quote")
        if quote is not None and not isinstance(quote, str):
            raise InvalidParamsError(f"values[{i}].grounding_quote must be a string")
        if quote is not None and len(quote) > MAX_VALUE_CHARS:
            raise InvalidParamsError(
                f"values[{i}].grounding_quote exceeds {MAX_VALUE_CHARS} characters"
            )
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
    """Load the bundled ``<doc_type>.yaml`` pack for an allowlisted doc type.

    Only names in :data:`SUPPORTED_DOC_TYPES` (the same list the input schema
    advertises) are accepted, so a typo like ``tax-invoice`` is refused rather
    than silently yielding "no rule pack". That also rules out path separators
    / traversal by construction. Defence in depth only: the gate itself is
    fail-closed and discards numeric values no rule reached, so a missing pack
    can never turn into a free pass.
    """
    if doc_type not in SUPPORTED_DOC_TYPES:
        raise InvalidParamsError(
            f"unsupported doc_type {doc_type!r}; expected one of "
            f"{', '.join(SUPPORTED_DOC_TYPES)}"
        )
    path = _RULES_DIR / f"{doc_type}.yaml"
    return load_rule_pack(path) if path.exists() else None


def _text_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


def _gate_payload(values: list[ExtractedValue], full_text: str, doc_type: str) -> str:
    """Run the gate and serialize {fields, summary} as a JSON string.

    The summary names the rule pack and how many arithmetic rules actually ran,
    so the caller can see whether verification happened at all.
    """
    pack = _rule_pack_for(doc_type)
    fields = run_gate(values, full_text, pack)
    payload = {"fields": [f.to_dict() for f in fields], "summary": summarize(fields, pack)}
    return json.dumps(payload, ensure_ascii=False)


# --- tools ----------------------------------------------------------------------


def _reject_host(args: dict[str, Any]) -> None:
    """Refuse a caller-supplied ``host`` on either tool.

    ``extract_verified`` rejects it because honouring it would make the server a
    confused deputy. This tool opens no socket at all, so the argument is merely
    meaningless here — but *silently* meaningless is the wrong answer twice over.
    A client that sends it believes it is choosing an endpoint, and SECURITY.md
    tells readers a request carrying ``host`` is rejected, without qualifying
    which tool. One of those had to give, and the safe one is to say no.
    """
    if "host" in args:
        raise InvalidParamsError(
            "'host' is not accepted: the Ollama endpoint is server configuration "
            "(set OLLAMA_HOST on the server running groundextract.mcp_server)"
        )


def _tool_verify_extraction(args: dict[str, Any]) -> dict[str, Any]:
    """Deterministic gate over caller-supplied extracted values. No keys, no network."""
    _reject_host(args)
    full_text = _require_bounded_str(args, "full_text", MAX_FULL_TEXT_CHARS)
    doc_type = _require_doc_type(args)
    values = _parse_values(args.get("values"))
    return _text_result(_gate_payload(values, full_text, doc_type))


def _extractor_kwargs(args: dict[str, Any]) -> dict[str, Any]:
    """Optional OllamaExtractor overrides (model/timeout) from tool args.

    ``host`` is deliberately **not** a tool parameter: the Ollama endpoint is
    server configuration (the ``OLLAMA_HOST`` environment variable). A
    caller-chosen host would make this tool a confused deputy — it would POST
    the whole document to any host named in the request and hand the reply back
    to the caller, which is exactly what a prompt injection hidden inside the
    document under verification would aim for. Requests that still carry it are
    rejected loudly rather than silently ignored.
    """
    _reject_host(args)
    kwargs: dict[str, Any] = {}
    model = args.get("model")
    if model is not None:
        if not isinstance(model, str) or not model:
            raise InvalidParamsError("'model' must be a non-empty string")
        kwargs["model"] = model
    timeout = args.get("timeout")
    if timeout is not None:
        if isinstance(timeout, bool) or not isinstance(timeout, int | float) or timeout <= 0:
            raise InvalidParamsError("'timeout' must be a positive number")
        if timeout > MAX_TIMEOUT_SECONDS:
            raise InvalidParamsError(
                f"'timeout' exceeds {MAX_TIMEOUT_SECONDS} seconds"
            )
        kwargs["timeout"] = float(timeout)
    return kwargs


def _tool_extract_verified(args: dict[str, Any]) -> dict[str, Any]:
    """Ollama extraction + gate. Degrades to an isError result when unavailable."""
    # Same cap as verify_extraction. This tool used to take an unbounded string,
    # so everything MAX_LINE_CHARS allowed (4 MiB) went into the prompt, over the
    # wire to Ollama, and back through the gate — on a loop that answers one
    # caller at a time. A limit only the cheaper of two tools honours is not a
    # limit.
    full_text = _require_bounded_str(args, "full_text", MAX_FULL_TEXT_CHARS)
    doc_type = _require_doc_type(args)
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


def _response_for_line(line: str) -> dict[str, Any] | None:
    """Parse and handle one input line; return the response (None = notification).

    Parsing is where hostile input bites, and ``json.JSONDecodeError`` does not
    cover it: deeply nested arrays/objects raise ``RecursionError`` (a
    ``RuntimeError``, *not* a ``ValueError``) and an integer literal beyond
    ``sys.get_int_max_str_digits()`` raises a plain ``ValueError``. Both are
    answered with a parse error instead of ending the session.
    """
    if len(line) > MAX_LINE_CHARS:
        return _error_response(
            None, INVALID_REQUEST, f"Invalid Request: line exceeds {MAX_LINE_CHARS} characters"
        )
    try:
        msg = json.loads(line)
    except RecursionError:
        return _error_response(None, PARSE_ERROR, "Parse error: input nested too deeply")
    except ValueError as exc:  # includes json.JSONDecodeError
        detail = str(exc)[:MAX_ERROR_DETAIL_CHARS]
        return _error_response(None, PARSE_ERROR, f"Parse error: {detail}")
    if not isinstance(msg, dict):
        return _error_response(None, INVALID_REQUEST, "Invalid Request: expected object")
    return handle_message(msg)


def _read_lines(stream: TextIO) -> Iterator[str | None]:
    """Yield input lines with a size bound; ``None`` marks a dropped oversize line.

    ``for line in stream`` buffers a line of *any* length, so one newline-free
    flood would grow this process without limit. Reading with an explicit bound
    caps that, and the rest of an oversized line is drained rather than handed
    back as if its tail were the next request.
    """
    while True:
        chunk = stream.readline(MAX_LINE_CHARS + 1)
        if not chunk:
            return
        if len(chunk) > MAX_LINE_CHARS and not chunk.endswith("\n"):
            while chunk and not chunk.endswith("\n"):  # drop the remainder
                chunk = stream.readline(MAX_LINE_CHARS + 1)
            yield None
            continue
        yield chunk


def main() -> None:
    """Serve MCP over stdio: newline-delimited JSON-RPC, one message per line.

    The loop is the server's lifetime: a single bad line must never end the
    session, because every later request on that stdio pipe would then go
    unanswered. Anything the per-line handling can raise is caught here and
    reported as an error response.
    """
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # Windows pipes default to cp949

    for raw_line in _read_lines(sys.stdin):
        try:
            if raw_line is None:
                _write(
                    _error_response(
                        None,
                        INVALID_REQUEST,
                        f"Invalid Request: line exceeds {MAX_LINE_CHARS} characters",
                    )
                )
                continue
            line = raw_line.strip()
            if not line:
                continue
            response = _response_for_line(line)
            if response is not None:
                _write(response)
        except Exception as exc:  # noqa: BLE001 - keep serving the next line
            # Includes RecursionError/MemoryError raised outside the parse step.
            # Report the exception type only: the text may be attacker-shaped.
            _write(_error_response(None, INTERNAL_ERROR, f"Internal error: {type(exc).__name__}"))


if __name__ == "__main__":
    main()
