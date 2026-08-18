# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Benchmark numbers are part of the public contract: any release that changes
`Auto-Discard Recall`, `Auto-Discard Precision`, `Grounded-Accuracy`, or the
`Numeric Hallucination Rate` reports the before/after values here.

## [Unreleased]

## [0.1.6] - 2026-08-18

### Security

- **A quantity written without digits still slipped into a field a rule names.**
  0.1.4 widened the "does this owe arithmetic?" predicate from "is this one number?"
  to "does this hold a number token", where a token is ASCII digits after NFKC. That
  is not the only way to write a quantity. 한글 and 한자 수사 — `일백만원`, `壹百萬` —
  carry no digit at all, and superscripts, fractions and Roman numerals are Unicode
  category `No`/`Nl`, which `_drop_enclosed_numerals` strips as notation before the
  matcher ever sees them. It strips them for a good reason: ①②③ number line items on
  Korean forms and are not amounts.

  Read as "text", such a value inherited a 상호's treatment — grounded verbatim,
  nothing else asked. So the supply amount spelled `일백만원`, dropped into the VAT
  slot, came back `verified` at confidence 1.0, the same failure the 0.1.4 fix closed
  for space-separated thousands.

  The predicate is no longer only about the value. A field the rule pack **names** is
  a field an invariant was supposed to decide; reaching the end of the gate with no
  arithmetic having run means it was not decided, whatever the value looks like. Such
  a field now fails closed. Fields no rule covers are still decided by grounding
  alone — otherwise every unmodelled field would become undiscardable noise — so 상호,
  품목 and free-text memos behave exactly as before.

  Nine notations are pinned as regression tests, along with the counterweight that the
  same string stays text where no rule claims it. Benchmark unchanged.

## [0.1.5] - 2026-08-18

### Security

- **The work budget added in 0.1.4 did not bound the work.** It predicted the span
  walk's cost as `source_tokens × value_tokens` and declined when the product was too
  large. That product does not track what the walk costs, and it under-charges by the
  most exactly where it matters. Measured on the 0.1.4 code with a 1024-character
  value against a 64 KiB source, varying only the tokenization:

  | source / value tokens | budget consumed | wall clock |
  | --- | --- | --- |
  | 64 / 1 | 0.096% | 0.55s |
  | 32 / 2 | 0.189% | 0.65s |
  | 8 / 1 | 0.694% | 5.78s |
  | 8 / 4 | 2.083% | 17.40s |
  | 16 / 8 | 2.573% | **17.47s** |

  The worst shape the guard let through cost 17.5 seconds for a *single* value while
  spending 2.6% of its budget — so the limit permitted roughly forty times the work
  its slowest observed input actually did, and a well-formed request respecting every
  declared cap still occupied the single-threaded MCP server for **~74 minutes** at
  `MAX_VALUES`. (Sharper shapes were reported against other hardware; the numbers here
  are the ones reproducible on the machine this was fixed on.) The 0.1.4 regression
  test passed because it pinned the one shape the guard did catch.

  `SequenceMatcher`'s cost is data-dependent — autojunk alone swings it by an order of
  magnitude — and is not worth predicting. So `MAX_FUZZY_COMPARISON_CHARS` is not a
  prediction: the walk decrements it by the actual size of each comparison and stops
  when it runs out. That cannot be gamed by input shape, and the walk order is fixed
  so the cut-off is deterministic. Stopping early returns what was found, which is the
  fail-closed direction — a discarded field the caller can rescue with a
  `grounding_quote`, never a false verification.

  Worst crafted shape now measures **0.13s** per value, down from 17.5s — and lands
  *below* what an honest miss on a full-size 64 KiB document costs (0.14s), so there
  is no amplification left to buy and tightening further would start costing real
  documents their grounding. Tests parametrize the whole family of source shapes
  rather than one point, and pin the no-amplification property directly. Benchmark
  unchanged.

## [0.1.4] - 2026-08-18

Never published to PyPI; superseded by 0.1.5 the same day. Wheel and sdist are
attached to the GitHub release.

### Security

- **A misassigned amount escaped the gate whenever the document spelled it with a
  space or a dot.** The gate decided whether a value still owed arithmetic
  verification by asking `normalize_number_str`, which returns `None` both for text
  and for a string holding several number tokens. So `"1 000 000원"` and
  `"1.000.000"` — ordinary OCR spellings of a Korean invoice figure — were filed as
  *text*, the `rules_applied` requirement fell away, and values nothing had checked
  came back `verified` at confidence 1.0. The identical misassignment spelled
  `"1,000,000원"` was discarded, so the gate's answer depended on typography rather
  than on the numbers. `carries_number` (any number token) is now the predicate, and
  a value holding digits owes a rule however it is written. SECURITY.md classifies
  this as the highest-severity class — "a formatting form that the parser reads as a
  different number than a human reader would". Benchmark unchanged: every golden
  field is a single token, so nothing in the published figures moves.

- **Dates and identifiers no rule covers are now discarded, not verified.** Same root
  cause, and the module docstring already promised this behaviour.

- **The fuzzy tier's cap counted the wrong thing.** `MAX_FUZZY_SOURCE_CHARS` bounds
  the source in characters, but that is not what the span search costs — so a
  token-dense 64 KiB source against a value near `MAX_VALUE_CHARS` ran ~34s for
  **one** value, and `MAX_VALUES` of them put a single well-formed request past two
  hours on a loop that answers one caller at a time. The size test never caught it
  because its source has no spaces, which collapses the search to one candidate.
  `MAX_FUZZY_SEARCH_TOKENS` budgets `source_tokens × value_tokens`.

  **This did not work** — that product turned out to be anti-correlated with the real
  cost, leaving the same request shape open. Fixed properly in 0.1.5; see above.

- **`extract_verified` enforced no document cap**, so everything `MAX_LINE_CHARS`
  allowed (4 MiB) went into the prompt, over the wire, and back through the gate —
  strictly more work than `verify_extraction`, which *did* enforce the cap. Both
  tools now share `MAX_FULL_TEXT_CHARS`, and a caller-supplied `timeout` is bounded
  by `MAX_TIMEOUT_SECONDS`.

### Fixed

- **`python -m groundextract.bench` measured nothing when run from an installed
  wheel.** It resolved rule packs as "two levels up from this file" — the repository
  root in a checkout, `site-packages` once installed, where no `rules/` exists
  because the packs ship *inside* the package. `_rule_pack_for` returns `None` rather
  than raising, so `pip install groundextract` followed by the documented command
  printed both headline numbers character-for-character identical to the repository
  run (`13.4% -> 0.0%`, recall `100.0%`) with zero arithmetic rules applied, exit
  code 0. Only precision moved: 57.8% → 13.4%. Anyone reproducing the published
  figures would have concluded they were inflated four-fold. The bench now uses
  `default_rules_dir()`, the resolver the library itself uses, and a document type
  with no pack is a loud exit 2.

- **The version was written in two files and 0.1.3 shipped disagreeing with itself**
  (`Version: 0.1.3` in the metadata, `__version__ = "0.1.2"` in the code). SECURITY.md
  asks a reporter for "the version you tested", so the package's own answer has to be
  the true one. `pyproject.toml` now declares the version dynamic and hatchling reads
  it from `groundextract/__init__.py`.

- **`CITATION.cff` and the PR template carried v0.1.0-era figures.** The citation
  metadata feeds Zenodo and GitHub's "Cite this repository", and the template's stale
  `before` line made a precision *regression* read as an improvement against it. Both
  are now covered by `test_published_numbers_match_the_measured_ones`.

### Documentation

- **Where your document actually goes.** `extract_verified` posts the full document
  text to the server named by `OLLAMA_HOST`, and both READMEs said only "local
  Ollama" — a licence property described as if it were a network one. If that
  variable is already set to a shared GPU box or a remote endpoint, real 세금계산서
  and 재무제표 go there. Now stated in both READMEs and SECURITY.md.

- **SECURITY.md described a `host` tool argument that no longer exists** (removed as
  a confused-deputy risk), which pointed readers away from `OLLAMA_HOST` — the actual
  control point.

### Added

- **Two rule packs covering the rest of a filed corporate return**: `income_statement`
  (표준손익계산서) and `corporate_tax_return` (법인세 과세표준 및 세액조정계산서). With
  `balance_sheet` they cover 재무상태표 · 손익계산서 · 세액조정계산서 — the three forms a
  Korean company actually files together.

  `corporate_tax_return` is the unusual one: 법인세법 시행규칙 [별지 제3호서식] prints its
  own arithmetic beside each line — `(101＋102－103)`, `(122-123+124)` — so the pack
  transcribes the document's formulas instead of encoding someone's domain knowledge.

  One invariant is deliberately **not** encoded: `115 산출세액 = 과세표준 × 세율`. 법인세 is
  a bracketed schedule (구간별 세율 + 누진공제), so the printed 세율(%) times 과세표준 only
  equals 산출세액 for a single-bracket filer. A rule that is wrong for large filers destroys
  their correct values as surely as a missing rule lets bad ones through, and fail-closed
  does not make a wrong rule safe. A test pins the omission.

  `income_statement` is a subtraction chain — each subtotal is a term of the next rule —
  which is what makes it the best case for fault localization in the suite: a misread
  판매비와관리비 is named **alone**, because every neighbour is corroborated by a rule that
  passed.

- **A regression test that the three doc_type surfaces agree.** A rule pack with no
  `FIELD_SPECS` entry cannot be extracted for, a `FIELD_SPECS` entry with no pack produces
  values the gate then discards for want of arithmetic, and either one missing from the MCP
  enum is unreachable by an agent. The three had to be updated by hand and could drift
  silently; now they cannot.

### Changed

- **The benchmark's synthetic 재무제표 documents now reproduce the filed layout** —
  계정과목 / 코드 / 금액 columns, roman-numeral section headers, the form's real code
  numbers, and no `원` in the amount column — instead of a prose approximation. The text
  under test is now the shape an OCR pass over a real return produces, which also means
  grounding is exercised at the PARTIAL_NUMERIC tier (the extractor emits `320,000,000원`
  against a document that prints `320,000,000`) rather than as a substring hit.

- **Benchmark numbers.** The suite grew from 58 documents / 218 fields to **72 / 388**:

  | | before | after |
  |---|---|---|
  | Auto-Discard Recall | 100.0% | **100.0%** |
  | Auto-Discard Precision | 59.2% | **57.8%** |
  | Grounded-Accuracy | 86.7% | **90.2%** |
  | Numeric Hallucination Rate (pre-gate) | 19.3% | **13.4%** |
  | Numeric Hallucination Rate (post-gate) | 0.0% | **0.0%** |
  | Confusion | TP=42 FP=29 FN=0 TN=147 | **TP=52 FP=38 FN=0 TN=298** |

  The pre-gate rate fell because it is a property of the suite, not of the world: at most
  one error is injected per document so a labeled fault is never ambiguous, and a 15-field
  세액조정계산서 contributes fifteen fields against one possible error. The old cap spent
  that shortfall by injecting into *every* document, which left no clean one — and a clean
  document is the only case exercising "the gate must not discard anything", the
  generator's own correctness check. `MAX_BAD_DOC_SHARE` now keeps a quarter of the
  documents clean and the realised rate is reported rather than assumed.

  Precision moved down 1.4pp: the wide forms add fields governed by a single rule, and a
  single rule cannot localize — exactly the limitation already documented for `statement`,
  now visible in the headline number. Grounded-accuracy rose because those same forms are
  mostly correct fields the gate correctly keeps.

## [0.1.3] - 2026-08-18

Published to PyPI (wheel uploaded 08:31 UTC) and registered with the MCP registry.
It was released without a tag or a changelog section of its own; both were added
retroactively on the same day. Its changes are the Added/Changed entries under
[0.1.4] above — the two rule packs covering a filed corporate return, and the
five-form documentation — which had accumulated under `[Unreleased]` and were never
promoted at release time.

### Known defects

Both fixed in [0.1.4]/[0.1.5]. If you installed this version, upgrade.

- The gate bypass described under [0.1.5] and [0.1.4] is present: a misassigned
  amount spelled with a space or dot separator comes back `verified` at confidence
  1.0 having been checked by nothing.
- The package declares `Version: 0.1.3` in its metadata but answers
  `__version__ == "0.1.2"`. Cite the tag, not the constant, when reporting against
  this release.
- `python -m groundextract.bench` finds no rule packs when run from the installed
  wheel and reports metrics computed without any arithmetic, exit code 0.

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

  Measured on its own, against the 146-field benchmark as it stood before
  `balance_sheet` joined it later in this release:

  | | before | after |
  |---|---|---|
  | Auto-Discard Recall | 100.0% | **100.0%** |
  | Auto-Discard Precision | 35.8% | **63.0%** |
  | Grounded-Accuracy | 64.4% | **88.4%** |
  | Confusion | TP=29 FP=52 FN=0 TN=65 | **TP=29 FP=17 FN=0 TN=100** |

  The figures this release *ships* with are the ones over the grown 218-field
  suite — recall 100.0%, precision 59.2%, grounded-accuracy 86.7% — see the
  `balance_sheet` entry below for why they differ.

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

[Unreleased]: https://github.com/sos37591-prog/groundextract-kr/compare/v0.1.6...HEAD
[0.1.6]: https://github.com/sos37591-prog/groundextract-kr/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/sos37591-prog/groundextract-kr/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/sos37591-prog/groundextract-kr/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/sos37591-prog/groundextract-kr/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/sos37591-prog/groundextract-kr/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/sos37591-prog/groundextract-kr/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/sos37591-prog/groundextract-kr/releases/tag/v0.1.0
