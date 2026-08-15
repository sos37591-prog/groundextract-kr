"""Tiny CLI demo: `python -m groundextract` runs the gate on a canned invoice.

Shows the core value proposition in one command with no keys: a hallucinated
VAT is auto-discarded (confidence 0) while grounded, arithmetic-consistent
numbers stay verified.

This entry point *only* runs that built-in demo — it is deliberately not a
document processor, and says so rather than ignoring a file argument. To verify
your own numbers, use the library (`run_gate`), the MCP tool
`verify_extraction`, or the optional real-document path
(`adapters.DoclingAdapter` + `llm.OllamaExtractor`).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__, load_pack
from .gate import run_gate, summarize
from .llm import MockExtractor
from .models import ExtractedValue, VerifiedField
from .rules import RulePack

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
  library : from groundextract import ExtractedValue, load_pack, run_gate
  agents  : python -m groundextract.mcp_server   (MCP tool `verify_extraction`)
  bench   : python -m groundextract.bench        (NumHall-KR regression suite)

Docs: https://github.com/sos37591-prog/groundextract-kr
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


def _print_report(fields: list[VerifiedField], pack: RulePack) -> None:
    print("=== GroundExtract-KR demo ===")
    for f in fields:
        mark = "OK " if f.verdict.value == "verified" else "XX "
        print(
            f"{mark}{f.field:6} value={f.value.raw:12} "
            f"verdict={f.verdict.value} conf={f.confidence}"
        )
        for c in f.failed_checks:
            print(f"       ! {c.name}: {c.detail}")
    print("summary:", json.dumps(summarize(fields, pack), ensure_ascii=False))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the demo. Returns the process exit code (0 = the demo ran)."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.args:
        parser.error(
            f"this command runs the built-in demo only and cannot process {args.args[0]!r}. "
            "To verify your own document, call the library (groundextract.run_gate) or the "
            "MCP tool `verify_extraction`; see --help."
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
