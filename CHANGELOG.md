# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Benchmark numbers are part of the public contract: any release that changes
`Auto-Discard Recall`, `Auto-Discard Precision`, `Grounded-Accuracy`, or the
`Numeric Hallucination Rate` reports the before/after values here.

## [Unreleased]

_Nothing yet. See the roadmap in [README.md](README.md#roadmap)._

## [0.1.2] - 2026-08-16

Precision, coverage and reach: the gate now says *which* value broke a rule instead
of blaming every field the rule mentions, the benchmark exercises every shipped
rule pack, the AGPL dependency is gone, and you can run the gate on your own
files without writing code.

### Added

- **Fault localization.** A violated invariant names several fields but indicts
  only some of them, and discarding all of them was the deliberate price of
  recall. The gate now sets aside any field a *passing* rule corroborates, then
  blames the smallest sets of the remainder that account for every violation.

  | | before | after |
  |---|---|---|
  | Auto-Discard Recall | 100.0% | **100.0%** |
  | Auto-Discard Precision | 35.8% | **63.0%** |
  | Grounded-Accuracy | 64.4% | **88.4%** |
  | Confusion | TP=29 FP=52 FN=0 TN=65 | **TP=29 FP=17 FN=0 TN=100** |

  Recall is untouched by construction, not by luck: a single wrong value appears
  in *every* rule that broke, so `{that value}` is itself a minimum explanation
  and is always among the sets blamed — which is why ambiguity widens the blame
  instead of narrowing it. Two assumptions come with that and are documented as
  limits: several fields wrong at once may admit an explanation smaller than the
  truth, and a wrong value that happens to satisfy some other invariant is
  cleared by it.

  A rule downgraded for leaning on ungrounded values verified nothing, so it is
  **not** a violation to localize and exonerates nobody; treating it as one would
  hand a field exactly the verdict the downgrade exists to deny. Exonerated
  fields carry an informational `fault_localization` check naming who was blamed.

  What is left is genuine ambiguity — 세액 vs 공급가액 with no line items to break
  the tie — which needs more overlapping invariants per document, not a smarter
  search. A single-rule pack like `statement` cannot localize at all.

- **`groundextract verify` — the gate over your own files.** The library and the
  MCP server could already verify an arbitrary extraction, but both asked you to
  write code first, so trying this on a real document meant more work than the
  demo suggested:

  ```bash
  python -m groundextract verify --doc doc.txt --values values.json --doc-type tax_invoice
  ```

  `--values` takes the same shape the MCP tool accepts (and literally the same
  parser, so the two cannot drift). `--rules my_pack.yaml` uses a pack of your
  own. **Exit code 1 when any field was discarded**, 0 when all survived, 2 on
  usage errors — so `verify && ingest` is a safe pipeline gate. The bare command
  still runs the demo.

- **[`rules/README.md`](rules/README.md) — how to write a rule pack.** The one
  part of this project users are expected to author had no guide; the field-name
  contract, `expr` grammar and `tol` semantics had to be reverse-engineered from
  the three shipped packs. It also documents the design choice that matters most
  in practice: **write overlapping rules.** A field two invariants cover can be
  cleared by the one that still holds when the other breaks; a field only one
  covers cannot, which is why `statement` cannot localize a fault at all.

- **`balance_sheet` is in NumHall-KR.** Its rule pack, viewer fixture and unit
  tests already existed, but the benchmark ran only `tax_invoice` and
  `statement` — a pack the benchmark never exercises is a pack whose regressions
  nobody notices. The generator now builds 표준재무상태표 documents (three
  overlapping invariants over six totals), two hand-written goldens were added,
  and a test asserts the benchmark's document types stay in step with the packs
  in `rules/`.

  The suite grew from 46 documents / 146 fields to **58 / 218**, so the headline
  numbers move with it — recall stays 100.0%, precision lands at 59.2% and
  grounded-accuracy at 86.7% (both measured over a third more fields, and both
  well above the 35.8% / 64.4% this release started from).

### Changed

- **The viewer fixture builder uses `pypdfium2` (BSD-3-Clause / Apache-2.0)
  instead of PyMuPDF (AGPL-3.0).** PyMuPDF sat in the optional, maintainer-only
  `fixture` extra, so it never shipped to a user — but an AGPL package anywhere
  in the declared dependency graph is a licence-compatibility question an
  Apache-2.0 project should not have to answer, and automated licence scans do
  not care that the extra is optional. pdfium provides the same two things the
  script needs (a rasterized page and word boxes), so nothing was traded away.
  The whole dependency surface, runtime and extras alike, is now MIT / BSD-3 /
  Apache-2.0.
- CI's licence gate now scans the `fixture` extra as well as the runtime
  dependency, because the optional surface is where the question actually lived.
- `viewer/assets/balance_sheet_demo.png` and `viewer/fixtures/image_data.js`
  were regenerated with the new renderer. Gate results are identical; box
  coordinates shift by up to ~2px from glyph-metric differences between the two
  engines.

## [0.1.1] - 2026-08-16

A security release. Every item below is a **gate bypass** — an extraction the
gate reported as `verified` at `confidence 1.0` although the document did not
support it. That is the failure class [SECURITY.md](SECURITY.md) ranks highest,
so 0.1.0 should be treated as superseded rather than merely outdated.

Benchmark numbers are unchanged (`TP=29 FP=52 FN=0 TN=65`, NHR 19.9% -> 0.0%,
recall 100.0%, precision 35.8%, grounded-accuracy 64.4%): the bypasses needed
hand-built adversarial extractions that NumHall-KR does not contain. New
regression tests cover each one.

### Security

- **Fabricated grounding quotes are rejected.** A cited span was checked with the
  same tiered matcher used for values, so a quote holding two numbers passed as
  long as the *first* one existed in the document. An extractor (or a document
  that prompt-injects one) could staple a real figure onto an invented one —
  `"1,000,000 공급가액 2,000,000원"` — and have the invented half count as
  verbatim evidence. A quote must now occur verbatim, and the value must ground
  against the full document as well as inside the quote.
- **A supplied `number` must agree with `raw`.** Grounding proved what the
  document said about `raw` while the rule engine did its arithmetic on the
  optional caller-supplied `number`, so quoting the document exactly and
  inventing the number sent both halves green on different figures. `number` is
  now cross-checked against `raw`; a mismatch discards the field
  (`raw_number_agreement`).
- **Numbers are matched on token boundaries, not substrings.** `EXACT` was a
  substring test, and `"1,000,000원"` is a substring of `"11,000,000원"` — so a
  10x digit shift, the most common OCR/LLM corruption, was also the one that
  passed. A whole 10x-shifted invoice now discards in full.
- **Duplicate field names fail closed.** Arithmetic runs over a field-name
  environment, so a repeated name let one instance supply the number while every
  same-named instance inherited the resulting check (`duplicate_field`).
- **A rule cannot vouch using an ungrounded value.** An invariant holding across
  the extractor's own numbers proves nothing unless those numbers came from the
  document. A passing check whose inputs are not all grounded is downgraded;
  a *failing* check still reports the violation, which is the useful diagnostic.
- **`normalize_number_str` refuses ambiguous readings.** It kept only the first
  number token, so space-separated thousands (`"1 234 567원"`, common in OCR
  output) reduced to the key `"1"` and matched any stray 1 in the document.
  Enclosed numerals (`①②③`, which NFKC rewrites to digits, and which Korean
  forms use for line-item numbering) no longer supply grounding tokens.
  Full-width digits still do.
- **Bounded work per request.** The fuzzy tier walked the source for every value
  with no size cap, so one well-formed MCP request could occupy the
  single-threaded stdio server for minutes and starve every other caller. The
  tier now declines oversized sources (fail-closed), document normalization and
  number indexing are computed once per source instead of once per field, and
  the MCP server caps `values` count, string lengths, and `full_text` size. The
  reported worst case drops from ~12.5s to <0.1s.

### Fixed

- **A typo in a hand-written rule pack no longer crashes the gate.**
  `load_rule_pack` validated nothing, so a malformed `expr` surfaced much later
  as a bare `SyntaxError: invalid syntax (<unknown>, line 1)` raised out of
  `ast.parse` inside `run_gate` — naming neither the file, nor the rule, nor the
  expression. Packs are now validated at load time (rule type, required sides,
  `tol`, `items`/`target`, duplicate names, unknown keys, and expressions
  restricted to the arithmetic the evaluator can actually run), and failures
  raise `RulePackError` naming the file and the rule. A `RulePack` built in code
  bypasses that door, so an unreadable rule there fails closed with a visible
  check rather than raising. New `tests/test_rules.py`.
- **A fragment of the page no longer grounds a textual field.** 상호·품목 carry
  no arithmetic invariant, so grounding is their only check — and `EXACT` was a
  substring test, so any slice of the document passed. Against
  "상호 주식회사 지티소프트", even "주" came back verified at confidence 1.0. A
  textual value must now occur as a whole token (bounded by non-alphanumeric
  characters, so Korean text abutting punctuation still grounds).
- **The fuzzy tier is scored against token-aligned spans**, not character windows
  sized from the value. The old windows made the tier incoherent: a truncated
  value got a correspondingly short window that matched its own surviving prefix,
  so the six-character truncation "주식회사 지티" passed at 0.88 while the
  likelier one-character slip "주식회사 지티소프" failed at 0.80. Tolerance is now
  symmetric between dropped and misread characters, and a near-miss is reported
  as `FUZZY` rather than as verbatim evidence.
- **`groundextract.bench` refuses to report metrics over zero labels.** Every
  rate is a ratio whose denominator is the label count, so a missing or mistyped
  golden directory printed 100% recall, 100% grounded-accuracy and a 0% post-gate
  hallucination rate, then exited 0 — a flawless scorecard produced by measuring
  nothing, which CI would have accepted. It now errors and exits 2; `main()`
  returns an exit code.

### Changed

- `Dockerfile` copies an allowlist of paths instead of `COPY . .`, and runs as
  `nobody`. A denylist silently admits each new private file; the working tree
  legitimately holds unmasked source documents that `.gitignore` keeps out of the
  repository, and those must not reach an image layer either.
- `.gitignore` and `.dockerignore` moved from named files to patterns
  (`.env.*`, `*.key`, `*.pem`, `*.pfx`, `*token*`, `bench/sample_pdf/real_*`).
- CI pins third-party actions to commit SHAs and bounds each job with
  `timeout-minutes`. A tag is a mutable pointer, so whoever can move it can run
  their own code in the workflow.
- `viewer/build_combined_fixture.py` no longer renders a caller-supplied
  `GEK_BALANCE_PDF` over the **tracked** demo asset. There is no masking step on
  that path — the committed sample is synthetic and has nothing to mask — so a
  real 재무상태표 was one `git add .` from a public repository. Non-sample
  sources now render to gitignored `local/` with a warning.
  `viewer/README.md` no longer claims that path produces a masked PNG.

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
