# GroundExtract-KR

**Numbers your LLM invented never reach your database.**

[![CI](https://github.com/sos37591-prog/groundextract-kr/actions/workflows/ci.yml/badge.svg)](https://github.com/sos37591-prog/groundextract-kr/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Runtime deps: 1](https://img.shields.io/badge/runtime%20deps-1%20%28PyYAML%29-brightgreen.svg)](pyproject.toml)

<!-- PyPI badge — uncomment after the first release is published to PyPI:
[![PyPI](https://img.shields.io/pypi/v/groundextract.svg)](https://pypi.org/project/groundextract/)
-->

> **[▶ Live demo](https://sos37591-prog.github.io/groundextract-kr/?doc=balance_sheet&injected=1)**
> — opens with the *환각 주입* (hallucination injected) toggle already on: one digit of
> 비유동자산 is misread, the wrong number is present in the document text so grounding passes,
> and only the balance-sheet identity catches it. Toggle it off to see the same document come
> back all-green. Nothing to install; the page is static and runs entirely in your browser.
>
> 🇰🇷 한국어 문서: **[README.ko.md](README.ko.md)**

GroundExtract-KR is a **value-level truth gate** for numbers extracted from Korean
regulatory documents (tax invoices, financial statements, trial balances). It sits
*after* your OCR / LLM extractor and forces every extracted number to earn its place:

1. **Verbatim grounding** — the number must actually exist in the source text, at the
   span the model cited.
2. **Arithmetic invariants** — the number must satisfy the domain's rules
   (`VAT = supply × 10%`, `total = supply + VAT`, `assets = liabilities + equity`, …).
3. **Auto-discard** — anything ungrounded *or* rule-violating is stamped
   `verdict=discarded, confidence=0.0` and never reaches downstream.

The trust decision is made by **deterministic code**, not by another model call. No API
key, no network, no randomness — the same input always produces the same verdict.

---

## 30-second quick start

```bash
git clone https://github.com/sos37591-prog/groundextract-kr.git
cd groundextract-kr
python -m pip install -e .     # Python 3.11+, exactly one runtime dep (PyYAML)
python -m groundextract        # key-free demo, no network
python -m groundextract --help # what the demo shows, and where to go next
```

Prefer a container? `docker build -t groundextract-kr . && docker run --rm groundextract-kr`
runs the same demo — see [Run in Docker](#run-in-docker).

The demo feeds a canned Korean tax invoice to a mock extractor that hallucinates the VAT
(`250,000원`, a number that appears nowhere in the document):

```text
=== GroundExtract-KR demo ===
XX supply value=1,000,000원   verdict=discarded conf=0.0
       ! vat_equals_supply_x_10pct: 250,000 vs 100,000 (tol 1) -> diff 150,000
       ! total_equals_supply_plus_vat: 1,100,000 vs 1,250,000 (tol 1) -> diff 150,000
XX vat    value=250,000원     verdict=discarded conf=0.0
       ! grounding: cited quote not found verbatim in document: '세액  250,000원'
       ! vat_equals_supply_x_10pct: 250,000 vs 100,000 (tol 1) -> diff 150,000
       ! total_equals_supply_plus_vat: 1,100,000 vs 1,250,000 (tol 1) -> diff 150,000
OK total  value=1,100,000원   verdict=verified conf=1.0
summary: {"total": 3, "verified": 1, "discarded": 2, "ungrounded": 1, "rule_pack": "tax_invoice", "rules_applied": 2}
```

`rule_pack` and `rules_applied` in the summary record which invariants actually ran:
`rules_applied: 0` would mean the gate had no arithmetic to check with at all, which is a
very different situation from "everything passed".

Note that `supply` goes too, and `total` does not. Both invariants break, and `vat` and
`supply` each appear in both — arithmetic alone cannot say which of the two is lying, so
both are discarded. `total` appears in only one of them, so it cannot be the single
explanation for what went wrong and is spared. That narrowing is
[fault localization](#benchmark-numhall-kr); what it cannot narrow is discussed under
[Limitations](#limitations).

`--json` prints the same result as machine-readable JSON, and installing the package puts
an equivalent `groundextract` command on your PATH.

### Verify your own document

```bash
python -m groundextract verify --doc invoice.txt --values values.json --doc-type tax_invoice
```

`--doc` is the plain text your OCR/LLM read the numbers out of; `--values` is what it
claims to have found — the same shape the MCP tool takes, with `number` and
`grounding_quote` optional:

```json
[
  {"field": "supply", "raw": "1,000,000원", "number": 1000000,
   "grounding_quote": "공급가액  1,000,000원"},
  {"field": "vat",    "raw": "100,000원"},
  {"field": "total",  "raw": "1,100,000원"}
]
```

Use `--rules my_pack.yaml` instead of `--doc-type` for a pack of your own (see
[rules/README.md](rules/README.md)). **It exits 1 if any field was discarded**, so a
pipeline can gate on it directly:

```bash
python -m groundextract verify --doc doc.txt --values values.json --doc-type tax_invoice   && ./ingest.sh          # only runs when every value survived
```

This is not an extractor — it verifies what some other tool produced. For the full
document path see [Real-document pipeline](#real-document-pipeline-optional), and for
agents the [MCP server](#mcp-server--agents-consume-only-verified-values).

---

## Why two checks, not one

Grounding and arithmetic catch **disjoint classes of error**. Neither is sufficient alone:

| Error the extractor makes | Caught by grounding? | Caught by arithmetic? |
| --- | :---: | :---: |
| Invents a number that is nowhere in the document | ✅ | sometimes |
| Copies a number from the wrong line of the document | ❌ | ✅ |
| Misreads one digit into a value that exists elsewhere on the page | ❌ | ✅ |
| Reads a correct number but the document itself is internally inconsistent | ❌ | ✅ |

### The signature case: only arithmetic sees it

This is an actual document from the benchmark suite
(`bench/golden/gen_tax_001.json` — synthetic, fixed seed). The extractor filled `vat`
with `5,230,000원` — a number that **is** printed in the document, on a line-item row,
and it cited that exact span:

```text
품목: 전산장비 납품    공급가액 5,230,000원   세액 523,000원    ← the cited span, verbatim
품목: 시스템 유지보수  공급가액 31,048,000원  세액 3,104,800원

공급가액  36,278,000원
세액  3,627,800원          ← the correct value the extractor should have taken
합계금액  39,905,800원
```

Verbatim grounding **passes** — the quote is genuinely in the text, character for
character. Only `vat = supply × 10%` exposes it:

```text
! vat_equals_supply_x_10pct: 5,230,000 vs 3,627,800 (tol 1) -> diff 1,602,200
```

A grounding-only tool (bbox / span citation) would ship this number to your ledger with a
green checkmark and a highlighted box pointing at the "evidence".

The mirror case holds too: a fabricated number in a field that no invariant constrains —
or where the invariant's other operands were never extracted, so it is skipped rather
than evaluated — is invisible to arithmetic, and only grounding kills it. That is why the
gate demands **both**.

```text
extracted values (LLM) ──▶ verbatim grounding ─┐
                                               ├──▶ gate ──▶ VerifiedField[]
rule pack (YAML)      ──▶ arithmetic engine ───┘        (fail either → confidence 0)
```

- `groundextract/grounding.py` — three deterministic tiers: `exact` (verbatim substring)
  → `partial_numeric` (same number, different formatting: `1,000,000원` / `₩1000000` /
  `△500` / `(500)`) → `fuzzy` (bounded edit distance for OCR noise). None of them →
  `none` = hallucination. **The fuzzy tier never accepts a numeric value**: an
  edit-distance ratio cannot tell a smudged glyph from a different amount
  (`1,900,000원` scores 0.90 against `1,000,000원`), so numbers must match exactly or
  numerically, and fuzziness is reserved for genuinely textual fields.
- `groundextract/rules.py` — loads YAML rule packs and evaluates `equals` / `sum`
  invariants through an **AST-restricted evaluator** (`+ - * /` only; no `eval()`).
  Rules that cannot be evaluated (missing field, malformed expression) fail safe.
- `groundextract/gate.py` — `run_gate()`: grounding failure **or** a violated rule that
  references the field ⇒ `verdict=DISCARDED`, `confidence=0.0`.

---

## Use it as a library

```python
from groundextract import ExtractedValue, load_pack, run_gate

doc = "공급가액 1,000,000원\n세액 100,000원\n합계금액 1,100,000원"
pack = load_pack("tax_invoice")   # bundled rule pack, resolved from the installed package

values = [
    ExtractedValue("supply", "1,000,000원", 1_000_000, "공급가액 1,000,000원"),
    ExtractedValue("vat",    "100,000원",   100_000,   "세액 100,000원"),
    ExtractedValue("total",  "1,100,000원", 1_100_000, "합계금액 1,100,000원"),
]

for f in run_gate(values, doc, pack):
    print(f.field, f.verdict.value, f.confidence)
    # supply verified 1.0 / vat verified 1.0 / total verified 1.0
```

`load_pack(doc_type)` finds the packs that ship *inside* the package, so it works from any
working directory and from a `pip install`ed wheel — no repository checkout required.
`available_doc_types()` lists what ships (`tax_invoice`, `statement`, `balance_sheet`) and
`default_rules_dir()` returns where they live. For a pack of your own, keep using
`load_rule_pack("path/to/my_doc_type.yaml")`.

Every consumer (core, MCP server, benchmark, viewer) shares one output contract:

```python
VerifiedField(field, value, checks[], verdict, confidence)
#   value  -> raw / number / grounding_quote / page / bbox
#   checks -> one Check per verification performed (name, passed, detail, kind)
```

## MCP server — agents consume only verified values

**MCP (Model Context Protocol) is the standard way an AI agent calls an external tool.**
Attach this server to an agent and the agent can no longer reach a number that failed the
gate — every value arrives with its verdict, and the discarded ones arrive as discarded.

`groundextract.mcp_server` is a dependency-free implementation (protocol `2024-11-05`,
newline-delimited JSON-RPC 2.0 over stdio, standard library only):

```bash
python -m groundextract.mcp_server

# smoke test without an agent — the server answers on stdin/stdout:
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python -m groundextract.mcp_server
```

Client config (Claude Desktop / Claude Code / any MCP client). Use the **absolute path of
the interpreter that has `groundextract` installed** — the client launches the server with
its own environment, where a bare `python` usually resolves somewhere else:

```json
{
  "mcpServers": {
    "groundextract": {
      "command": "/absolute/path/to/venv/bin/python",
      "args": ["-m", "groundextract.mcp_server"]
    }
  }
}
```

On Windows that is `C:\\path\\to\\venv\\Scripts\\python.exe` (escaped backslashes in JSON).
No `cwd` is needed once the package is installed — the rule packs ship inside it. Running
straight from a source checkout instead? Add `"cwd": "/absolute/path/to/groundextract-kr"`.

| Tool | Needs a model? | What it does |
| --- | --- | --- |
| `verify_extraction` | ❌ deterministic, key-free, offline | Takes `full_text`, `doc_type`, and already-extracted `values`; returns per-field verdicts + summary. |
| `extract_verified` | ✅ local Ollama | Extracts with a local open-weight model, then runs the same gate. Returns an `isError` result with guidance when Ollama is unreachable. |

`doc_type` is one of `tax_invoice`, `statement`, `balance_sheet`. A `verify_extraction`
call returns the full audit trail — here, the same hallucinated VAT:

```json
{
  "fields": [
    {
      "field": "vat",
      "value": {"raw": "250,000원", "number": 250000.0, "grounding_quote": "세액  250,000원"},
      "checks": [
        {"name": "grounding", "passed": false,
         "detail": "cited quote not found verbatim in document: '세액  250,000원'", "kind": "none"},
        {"name": "vat_equals_supply_x_10pct", "passed": false,
         "detail": "250,000 vs 100,000 (tol 1) -> diff 150,000"}
      ],
      "verdict": "discarded",
      "confidence": 0.0
    }
  ],
  "summary": {"total": 3, "verified": 0, "discarded": 3, "ungrounded": 1,
              "rule_pack": "tax_invoice", "rules_applied": 2}
}
```

An agent's rule becomes trivial: **act on `verdict == "verified"`, escalate the rest to a
human.**

---

## Rule packs are one YAML file

`rules/tax_invoice.yaml`:

```yaml
doc_type: tax_invoice
rules:
  - name: vat_equals_supply_x_10pct       # VAT = supply × 10%
    type: equals
    lhs: {field: vat}
    rhs: {expr: "supply * 0.10"}
    tol: 1.0
  - name: total_equals_supply_plus_vat    # total = supply + VAT
    type: equals
    lhs: {field: total}
    rhs: {expr: "supply + vat"}
    tol: 1.0
```

Shipped packs: `tax_invoice` (VAT / total / line-item sum), `statement`
(debit total = credit total), `balance_sheet` (assets = liabilities + equity, and the two
subtotal identities). `tol` is expressed in the value's own unit (KRW), so a 1-won
rounding slack is normal.

Adding a new document type means writing one YAML file and registering the type in three
places — see [CONTRIBUTING.md](CONTRIBUTING.md#adding-a-rule-pack-new-document-type).

---

## Benchmark: NumHall-KR

```bash
python -m groundextract.bench
```

```text
=== NumHall-KR benchmark ===
fields: 218  (ground-truth bad: 42)
confusion: TP=42 FP=29 FN=0 TN=147
Numeric Hallucination Rate: 19.3% (before gate)  ->  0.0% (after gate)
Grounded-Accuracy:          86.7%
Auto-Discard Precision:     59.2%
Auto-Discard Recall:        100.0%
```

| Metric | Value | Reading |
| --- | --- | --- |
| Numeric Hallucination Rate | **19.3% → 0.0%** | Zero bad numbers survive the gate. |
| Auto-Discard Recall | **100.0%** | All 42 labeled bad fields were discarded (FN = 0). |
| Auto-Discard Precision | **59.2%** | 29 good fields were discarded alongside them. |
| Grounded-Accuracy | **86.7%** | Share of all 218 fields that end up correctly verified. |

**We publish the unflattering numbers on purpose.** The objective function here is *not*
accuracy — it is **zero leakage**: no fabricated number may reach a downstream ledger,
and the price we knowingly pay is precision.

**Fault localization** buys most of that price back. A broken rule names several fields but
indicts only some of them, so the gate sets aside any field a *passing* rule corroborates,
then blames the smallest sets of the rest that account for every violation. Because a
single wrong value must appear in every rule that broke, `{that value}` is always among
those minimum explanations — so blaming their union cannot lose it. Recall stays at 100%
while precision went 35.8% → **59.2%** and grounded-accuracy 64.4% → **86.7%** on a
benchmark that also grew by a third. What remains is genuine ambiguity: when two fields
explain the failures equally well, both go.

### What NumHall-KR is, and what it is not

- **It is** a deterministic, reproducible **regression suite**: 58 documents / 218 labeled
  fields = **50 synthetic** (30 tax invoices + 10 trial balances + 10 balance sheets,
  fixed-seed generator) + **8 hand-written** documents. Every shipped rule pack is
  exercised — a pack the benchmark never runs is a pack whose regressions nobody notices,
  and a test asserts the two sets stay in step. Every injected error is labeled by kind
  (`ungrounded` — the number exists nowhere; `field-swap` — the number exists but belongs
  to another field), so you can see which check did the catching.
- **It is not** a real-world golden set. The synthetic documents are generated by
  `bench/generate_golden.py`, which ships in this repository — regenerate, extend, or
  disagree with it:

  ```bash
  python bench/generate_golden.py --count-tax 30 --count-stmt 10 --seed 42
  ```

  Every candidate document is validated through the real gate before being written, and a
  given `(seed, counts)` always reproduces byte-identical files.
- **Real-document golden set is v0.2 roadmap.** Until then, treat these numbers as a
  regression signal for the gate's behavior, not as a claim about your production corpus.

An end-to-end run on a real PDF (Docling → local open-weight LLM → gate) is available at
`bench/verify_real.py`: it converts the committed sample PDF, extracts with the local
model, and checks both directions — a clean document must come out fully `verified`, and
a tampered VAT must come out `discarded`. It needs a running Ollama server.

---

## Demo viewer

A build-step-free static viewer overlays gate verdicts as bounding boxes on document
images (green = verified, red = discarded; hover a red box for the failed check):

```bash
python -m http.server 8931 --directory viewer   # → http://localhost:8931/index.html
```

Fixtures are committed, so it works offline. See [`viewer/README.md`](viewer/README.md).

The scene is deep-linkable — useful for sharing a specific failure or capturing a
screenshot (the query string works on the hosted demo and on localhost alike):

| Parameter | Meaning |
| --- | --- |
| `?doc=<id>` | which document: `tax_invoice` or `balance_sheet` |
| `&injected=1` | start with the *환각 주입* (hallucination injected) toggle on |
| `&tip=<field>` | pin that field's discard reason open, e.g. `noncurrent_assets` |

Example:
[`?doc=balance_sheet&injected=1&tip=noncurrent_assets`](https://sos37591-prog.github.io/groundextract-kr/?doc=balance_sheet&injected=1&tip=noncurrent_assets)

## Real-document pipeline (optional)

| Stage | Module | Install |
| --- | --- | --- |
| PDF → text / tables / bbox | `groundextract.adapters.DoclingAdapter` | `pip install -e ".[docling]"` |
| Extraction with a local open-weight model | `groundextract.llm.OllamaExtractor` | standard library; needs a running [Ollama](https://ollama.com) server (default model `qwen2.5:7b`, Apache-2.0) |
| Gate | `groundextract.gate` | always available |

Both are **optional adapters**. The entire verification path — demo, benchmark, MCP
`verify_extraction`, viewer, tests — runs with no model, no key, and no network.
Commercial APIs are deliberately not a dependency; the default engine is open-weight.

---

## Limitations

Read these before trusting the gate in production:

- **Single-voucher assumption.** Each document is treated as one self-contained voucher
  with one set of fields. Multi-invoice pages, consolidated statements, and cross-document
  aggregates are out of scope.
- **Ambiguity still over-discards.** Fault localization narrows a violation to the smallest
  sets of fields that explain it, but when two fields explain it equally well — 세액 vs
  공급가액 with no line items to break the tie — both are discarded (Precision 59.2%).
  More overlapping invariants in a rule pack means sharper localization; a pack with a
  single rule, like `statement`, cannot localize at all. Expect verified-only pipelines to
  send some correct values to human review.
- **Table-cell bbox mapping is coarse.** Bounding boxes come from the PDF adapter at
  line/region granularity; individual cells inside a table are not precisely mapped, so
  viewer overlays on dense tables can be approximate.
- **Rules are arithmetic, not semantic.** The gate verifies that numbers are consistent
  and present. It does not verify that the counterparty, date, or account classification
  is correct.
- **Grounding proves the value is in the *text layer*, not that a human can see it.**
  Evidence is checked against the text the PDF adapter extracts. Text that is invisible on
  screen — white-on-white, 0pt, positioned off-page, or in an annotation layer — is part of
  that text and grounds normally. A sender who plants an arithmetically consistent set of
  invisible figures can therefore obtain `verified` for amounts a reader never sees. Treat
  documents from untrusted senders as out of scope until visibility checking lands
  (roadmap), or pre-flight them with a tool that flags hidden text.
- **A cited quote is verified for existence, not for relevance.** The gate requires the
  quote to occur verbatim and to contain the value, but it does not check that the quote is
  the *right* line — a figure that legitimately appears elsewhere in the document (a
  line-item unit price, a subtotal, a prior-period column) can ground a field it does not
  belong to. Arithmetic rules catch most such mix-ups; a field no rule reaches is discarded
  rather than trusted. Per [SECURITY.md](SECURITY.md) a gate bypass is our highest-severity
  class, so this is disclosed here rather than left to be discovered.
- **Unit scaling is not handled yet.** Statements printed in 천원/백만원 units are not
  rescaled before rule evaluation.
- **Korean regulatory documents only.** Nothing in the core is language-locked, but the
  shipped rule packs and field specs are Korean.

## Roadmap

**v0.2**

- ~~**Fault localization**~~ — shipped. Precision 35.8% → 59.2%, grounded-accuracy
  64.4% → 86.7%, recall unchanged at 100%. What is left is genuine ambiguity, which
  needs *more invariants* per document rather than a smarter search.
- **Real-document golden set** — labeled real (de-identified) documents alongside the
  synthetic suite, reported separately so the two are never conflated.
- **Unit scaling** — detect 천원/백만원 headers and normalize before rule evaluation.
- **Table-cell bbox** — per-cell mapping for table fields.

**Later**

- More document types (등기부 / registry extracts); a public leaderboard;
  production serving.

Ideas and disagreements are welcome — see
[good first issues](CONTRIBUTING.md#good-first-issues).

---

## Contributing

```bash
python -m pip install -e ".[dev]"
python -m pytest -q        # full suite: deterministic, offline, no API key
python -m ruff check .
```

[CONTRIBUTING.md](CONTRIBUTING.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) ·
[SECURITY.md](SECURITY.md) · [CHANGELOG.md](CHANGELOG.md)

**A gate bypass is a security issue.** If you find an input where a hallucinated number
comes out `verified`, please report it under [SECURITY.md](SECURITY.md) — a false negative
is the one bug class this project exists to prevent.

## Run in Docker

```bash
docker build -t groundextract-kr .
docker run --rm groundextract-kr          # the key-free demo
docker run --rm groundextract-kr python -m groundextract.bench
```

## Citation

If you use GroundExtract-KR in academic work, see [CITATION.cff](CITATION.cff) (GitHub
renders a "Cite this repository" button from it).

## License

Apache-2.0 — see [LICENSE](LICENSE).
