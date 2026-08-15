# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Benchmark numbers are part of the public contract: any release that changes
`Auto-Discard Recall`, `Auto-Discard Precision`, `Grounded-Accuracy`, or the
`Numeric Hallucination Rate` reports the before/after values here.

## [Unreleased]

_Nothing yet. See the roadmap in [README.md](README.md#roadmap)._

## [0.1.0] - 2026-08-15

First public release. The complete verification path — grounding, rules, gate,
benchmark, MCP `verify_extraction`, viewer, and the full test suite — runs
deterministically, offline, with no API key and one runtime dependency (PyYAML).

### Added

- **Verbatim grounding** (`groundextract/grounding.py`) — three deterministic match
  tiers: `exact` (verbatim substring) → `partial_numeric` (same number, different
  formatting: thousands separators, currency units, full-width digits, and the Korean
  negative notations △/▲ and accounting parentheses) → `fuzzy` (bounded edit distance
  for OCR noise). No match ⇒ `none`, i.e. hallucination. The fuzzy tier is refused for
  numeric values: an edit-distance ratio cannot separate a smudged glyph from a
  different amount, so every single-digit corruption of a 7–8 digit KRW figure would
  otherwise have been reported as grounded.
- **Arithmetic rule engine** (`groundextract/rules.py`) — YAML rule packs with
  `equals` and `sum` rule types, evaluated by an AST-restricted evaluator
  (`+ - * /`, parentheses, numeric literals, field names; no `eval()` of arbitrary
  Python). Rules apply only when every referenced field is present, and unevaluable
  rules fail safe instead of raising.
- **The gate** (`groundextract/gate.py`) — `run_gate()` / `summarize()`: a grounding
  failure *or* a violated rule that references the field yields
  `verdict=DISCARDED, confidence=0.0`.
- **Shared output contract** (`groundextract/models.py`) — `ExtractedValue`,
  `VerifiedField`, `Verdict`, `Check`, `MatchKind`, `BBox`, used identically by the
  core, MCP server, benchmark, and viewer.
- **Rule packs** for three document types: `tax_invoice` (VAT = supply × 10%,
  total = supply + VAT, supply = Σ line items), `statement` (debit total = credit
  total), `balance_sheet` (assets = liabilities + equity, plus both subtotal
  identities).
- **NumHall-KR benchmark** (`groundextract/bench.py`, `bench/golden/`) — 46 documents
  / 146 labeled fields: 40 synthetic (30 tax invoices + 10 trial balances) from the
  fixed-seed generator `bench/generate_golden.py`, plus 6 hand-written documents.
  Injected errors are labeled `ungrounded` (the number exists nowhere) or
  `field-swap` (the number exists but belongs to another field — the case grounding
  alone cannot see). Every generated candidate is validated through the real gate
  before it is written, and a given `(seed, counts)` always reproduces identical
  files.
- **MCP server** (`groundextract/mcp_server.py`) — protocol `2024-11-05`,
  newline-delimited JSON-RPC 2.0 over stdio, standard library only. Tools:
  `verify_extraction` (deterministic, key-free) and `extract_verified` (local Ollama
  extraction + gate, degrading to an `isError` result with guidance when the backend
  is unavailable).
- **Open-weight extractor** (`groundextract/llm/openweight.py`) — `OllamaExtractor`
  over plain `urllib`, prompting per field for `{value, quote}` where the quote must
  be copied verbatim. The gate then verifies that claim; the model is never trusted
  on its own.
- **Docling adapter** (`groundextract/adapters/docling_adapter.py`) — PDF → text /
  tables / bounding boxes, behind the optional `[docling]` extra.
- **Static demo viewer** (`viewer/`) — no build step, works offline: gate verdicts
  overlaid as bounding boxes on document images, document tabs, hallucination-injection
  toggle, failed-check tooltips, and a benchmark card.
- **Key-free CLI demo** (`python -m groundextract`) — a hallucinated VAT is discarded
  along with the siblings its broken invariants reference.
- **CI** (`.github/workflows/ci.yml`) — ruff + pytest with coverage on Python 3.11,
  plus a CycloneDX SBOM job that fails the build on strong-copyleft (GPL/AGPL)
  licenses in the dependency tree.

### Benchmark (NumHall-KR, 146 labeled fields)

| Metric | Value |
| --- | --- |
| Numeric Hallucination Rate | 19.9% → **0.0%** |
| Auto-Discard Recall | **100.0%** (TP 29 / FN 0) |
| Auto-Discard Precision | 35.8% (FP 52) |
| Grounded-Accuracy | 64.4% |

The objective is zero leakage, not accuracy: precision is the price paid for
recall 100%. Fault localization to recover precision is the first v0.2 item.

### Known limitations

Single-voucher assumption; sibling over-discard when a rule breaks; coarse
table-cell bounding boxes; no unit scaling for 천원/백만원 statements;
`balance_sheet` not yet represented in the benchmark. See
[README.md](README.md#limitations).

[Unreleased]: https://github.com/sos37591-prog/groundextract-kr/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/sos37591-prog/groundextract-kr/releases/tag/v0.1.0
