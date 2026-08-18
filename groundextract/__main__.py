"""CLI: `python -m groundextract` demos the gate, `... verify` runs it on your files.

The bare command shows the core value proposition with no keys and no inputs: a
hallucinated VAT is auto-discarded (confidence 0) while grounded,
arithmetic-consistent numbers stay verified.

`verify` is the same gate over a document and an extraction you supply. It is
deliberately *not* an extractor — this project verifies what some other OCR/LLM
produced, so it takes the text and the values as files rather than pretending to
read a PDF. For the full real-document path see `adapters.DoclingAdapter` +
`llm.OllamaExtractor`, and for agents the MCP tool `verify_extraction`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__, available_doc_types, load_pack
from .gate import run_gate, summarize
from .llm import MockExtractor
from .models import ExtractedValue, Verdict, VerifiedField
from .rules import RulePack, RulePackError, load_rule_pack

_DOC = (
    "전자세금계산서\n"
    "공급가액  1,000,000원\n"
    "세액        100,000원\n"
    "합계금액  1,100,000원\n"
)

_DESCRIPTION = (
    "Run the built-in GroundExtract-KR demo: a canned Korean tax invoice is fed to a "
    "mock extractor that hallucinates the VAT, and the gate auto-discards every value "
    "it cannot ground in the text or reconcile with the rule pack. No API key, no "
    "network, no randomness."
)

_EPILOG = """\
This command takes no document arguments; it always runs the same demo.

To verify your own numbers:
  CLI     : ... verify --doc doc.txt --values values.json --doc-type tax_invoice
  library : from groundextract import ExtractedValue, load_pack, run_gate
  agents  : python -m groundextract.mcp_server   (MCP tool `verify_extraction`)
  bench   : python -m groundextract.bench        (NumHall-KR regression suite)

Docs: https://github.com/sos37591-prog/groundextract-kr
"""

_VERIFY_DESCRIPTION = (
    "Run the gate over a document and an extraction you already have. This is not an "
    "extractor: --doc is the plain text your OCR/LLM read the numbers out of, and "
    "--values is what it claims to have found."
)

_VERIFY_EPILOG = """\
values.json is a list of objects — the same shape the MCP tool accepts:

  [
    {"field": "supply", "raw": "1,000,000원", "number": 1000000,
     "grounding_quote": "공급가액  1,000,000원"},
    {"field": "vat",    "raw": "100,000원",   "number": 100000},
    {"field": "total",  "raw": "1,100,000원", "number": 1100000}
  ]

`number` and `grounding_quote` are optional. `number` is cross-checked against
`raw` and must agree; omit it and it is parsed from `raw`.

Exit code: 0 if every field was verified, 1 if any was discarded — so a pipeline
can gate on it directly. Usage errors exit 2.
"""


def _prog() -> str:
    """Name to print in usage: the console script if that is how we were called."""
    if Path(sys.argv[0] if sys.argv else "").stem == "groundextract":
        return "groundextract"
    return "python -m groundextract"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=_prog(),
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="emit the demo result as JSON (fields + summary) instead of the text report",
    )
    parser.add_argument("--version", action="version", version=f"groundextract {__version__}")
    # Accepted only so a file argument gets an explanatory error instead of
    # being silently ignored (which would look like the file was processed).
    parser.add_argument("args", nargs="*", metavar="", help=argparse.SUPPRESS)
    return parser


def _build_verify_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{_prog()} verify",
        description=_VERIFY_DESCRIPTION,
        epilog=_VERIFY_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--doc", required=True, type=Path, metavar="PATH", help="document text file (UTF-8)"
    )
    parser.add_argument(
        "--values", required=True, type=Path, metavar="PATH", help="extracted values, JSON"
    )
    pack = parser.add_mutually_exclusive_group(required=True)
    pack.add_argument(
        "--doc-type",
        choices=available_doc_types(),
        help="use a bundled rule pack",
    )
    pack.add_argument(
        "--rules", type=Path, metavar="PATH", help="use your own rule pack (YAML)"
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true", help="emit JSON instead of a text report"
    )
    return parser


def _read_values(path: Path, parser: argparse.ArgumentParser) -> list[ExtractedValue]:
    """Parse values.json, reusing the exact contract the MCP tool enforces.

    The CLI and the MCP tool must accept the same value shape — two parsers would
    drift, and the difference would show up as a document that verifies through
    one path and not the other.
    """
    from .mcp_server import InvalidParamsError, _parse_values

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        parser.error(f"cannot read {path}: {e}")
    except json.JSONDecodeError as e:
        parser.error(f"{path}: invalid JSON ({e})")
    try:
        return _parse_values(raw)
    except InvalidParamsError as e:
        parser.error(f"{path}: {e}")


def _verify_main(argv: Sequence[str]) -> int:
    parser = _build_verify_parser()
    args = parser.parse_args(argv)

    try:
        text = args.doc.read_text(encoding="utf-8")
    except OSError as e:
        parser.error(f"cannot read {args.doc}: {e}")
    if not text.strip():
        parser.error(f"{args.doc} is empty; there is nothing to ground against")

    values = _read_values(args.values, parser)
    try:
        pack = load_pack(args.doc_type) if args.doc_type else load_rule_pack(args.rules)
    except RulePackError as e:
        parser.error(str(e))

    fields = run_gate(values, text, pack)
    if args.as_json:
        payload = {"fields": [f.to_dict() for f in fields], "summary": summarize(fields, pack)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_report(fields, pack, title=f"=== {args.doc.name} ===")
    # Non-zero when anything was discarded, so `... verify && ingest` is safe.
    return 1 if any(f.verdict is Verdict.DISCARDED for f in fields) else 0


def _demo_fields() -> tuple[list[VerifiedField], RulePack]:
    pack = load_pack("tax_invoice")

    # Two grounded/consistent values + one hallucinated VAT (250,000 is nowhere
    # in the document and breaks vat = supply * 10%).
    extractor = MockExtractor(
        [
            ExtractedValue("supply", "1,000,000원", 1_000_000, "공급가액  1,000,000원"),
            ExtractedValue("vat", "250,000원", 250_000, "세액  250,000원"),  # hallucinated
            ExtractedValue("total", "1,100,000원", 1_100_000, "합계금액  1,100,000원"),
        ]
    )
    return run_gate(extractor.extract(_DOC, doc_type="tax_invoice"), _DOC, pack), pack


def _print_report(
    fields: list[VerifiedField], pack: RulePack, title: str = "=== GroundExtract-KR demo ==="
) -> None:
    print(title)
    for f in fields:
        mark = "OK " if f.verdict.value == "verified" else "XX "
        print(
            f"{mark}{f.field:6} value={f.value.raw:12} "
            f"verdict={f.verdict.value} conf={f.confidence}"
        )
        for c in f.failed_checks:
            print(f"       ! {c.name}: {c.detail}")
    print("summary:", json.dumps(summarize(fields, pack), ensure_ascii=False))


def _survive_a_narrow_console() -> None:
    """Never let an unencodable character turn a verdict into a traceback.

    A check detail is free-form text — rule names, quoted document spans, the
    gate's own prose — and the console it lands on is not. On a Korean Windows
    console (cp949) the em dash in "this verifies nothing — the rule leans on …"
    raised UnicodeEncodeError *while printing the report*, so `verify` crashed on
    exactly the case it exists to catch: an extraction whose arithmetic passes
    only because it leans on ungrounded values. The exit code was still 1, which
    made it look like an ordinary failed verification rather than a crash.

    Replacing unencodable characters degrades one glyph; raising loses the whole
    report. `errors` is set without touching `encoding`, so console output stays
    in the terminal's own codepage and Korean still renders.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):  # pragma: no cover - detached/odd stream
                pass


def main(argv: Sequence[str] | None = None) -> int:
    """Run the demo, or dispatch to `verify`. Returns the process exit code."""
    _survive_a_narrow_console()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args and raw_args[0] == "verify":
        return _verify_main(raw_args[1:])

    parser = _build_parser()
    args = parser.parse_args(raw_args)
    if args.args:
        parser.error(
            f"this command runs the built-in demo only and cannot process {args.args[0]!r}. "
            f"To verify your own document use `{_prog()} verify --doc ... --values ...` "
            "(see `verify --help`), the library (groundextract.run_gate), or the MCP tool "
            "`verify_extraction`."
        )

    fields, pack = _demo_fields()
    if args.as_json:
        payload = {"fields": [f.to_dict() for f in fields], "summary": summarize(fields, pack)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_report(fields, pack)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
