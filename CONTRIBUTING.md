# Contributing to GroundExtract-KR

Thanks for taking the time. This project has a narrow, testable mission — **no
number reaches downstream unless it is grounded in the source text and consistent
with the domain's arithmetic** — and contributions are easiest to merge when they
serve that mission.

The two most valuable contributions are:

1. **A gate bypass.** An input where a wrong number comes out `verified`. That is a
   false negative, the one bug class this project exists to prevent. Report it via
   [SECURITY.md](SECURITY.md).
2. **A rule pack for a document type we don't cover yet.** See
   [Adding a rule pack](#adding-a-rule-pack-new-document-type) — it is one YAML file
   plus three registrations.

By contributing you agree that your contribution is licensed under Apache-2.0, the
same license as the project.

---

## Development setup

Requires **Python 3.11+**. The runtime dependency is PyYAML and nothing else —
please keep it that way (see [Dependency policy](#dependency-policy)).

```bash
git clone https://github.com/sos37591-prog/groundextract-kr.git
cd groundextract-kr
python -m venv .venv
# Linux/macOS: source .venv/bin/activate     Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
```

### Everyday commands

| Command | What it does |
| --- | --- |
| `python -m pytest -q` | Full suite. Deterministic and offline — no network, no API key, no Ollama. |
| `python -m ruff check .` | Lint (line length 100, rules `E,F,I,UP,B`, `UP042` intentionally ignored). |
| `python -m mypy groundextract` | Optional type check (config in `pyproject.toml`). |
| `python -m groundextract` | Key-free demo. |
| `python -m groundextract.bench` | NumHall-KR benchmark. |
| `python bench/generate_golden.py` | Regenerate the synthetic golden set (fixed seed). |
| `python -m groundextract.mcp_server` | MCP stdio server. |
| `python -m http.server 8931 --directory viewer` | Demo viewer. |
| `python viewer/build_combined_fixture.py` | Rebuild the viewer fixture from the sample documents. Needs the optional `[fixture]` extra (`pip install -e ".[fixture]"`). |

`make test`, `make lint`, `make demo`, `make bench` wrap the same commands.

**Both `pytest -q` and `ruff check .` must be green before you open a PR.** CI runs
exactly these two, plus a CycloneDX SBOM job with a copyleft-license denylist.

> **Windows note:** the demo prints Korean text. If your console is not UTF-8, run
> `set PYTHONIOENCODING=utf-8` (cmd) or `$env:PYTHONIOENCODING="utf-8"` (PowerShell)
> first, otherwise the output is mojibake. This is a console issue, not a gate issue.

---

## Adding a rule pack (new document type)

This is the main extension point, and it is the highest-signal contribution for a
newcomer. A new document type must be registered in **three places** — miss one and
the type is half-supported.

### 1. `rules/<doc_type>.yaml` — the invariants

```yaml
doc_type: my_doc_type          # must equal the filename stem
rules:
  - name: total_equals_a_plus_b   # unique, snake_case; shows up verbatim in check output
    type: equals                  # equals | sum
    lhs: {field: total}
    rhs: {expr: "a + b"}
    tol: 1.0                      # absolute tolerance, in the value's own unit (KRW)

  - name: total_equals_sum_of_items
    type: sum
    target: total
    items: [item1, item2]
    tol: 1.0
```

Rule semantics (`groundextract/rules.py`):

- `equals` — `|lhs - rhs| <= tol`. Each side is `{field: name}`, `{expr: "..."}`, or
  `{const: 1000}`.
- `sum` — `|target - sum(items)| <= tol`.
- `expr` is evaluated by an **AST-restricted evaluator**: `+ - * /`, parentheses,
  unary sign, numeric literals, and field names only. There is no `eval()` of
  arbitrary Python — do not add one.
- A rule is **applied only when every field it references is present**, so optional
  invariants (like a line-item sum) are skipped rather than failed when those fields
  weren't extracted.
- A rule that cannot be evaluated (missing field, malformed expression, division by
  zero) **fails safe**: it produces a failed check, never an exception.
- `tol` is absolute, not relative. For KRW amounts `1.0` (a one-won rounding slack)
  is the norm.

### 2. `groundextract/llm/openweight.py` — `FIELD_SPECS`

Add the field names and short Korean descriptions used to build the extraction
prompt. The keys **must** match the field names your rule pack references:

```python
FIELD_SPECS: dict[str, dict[str, str]] = {
    ...
    "my_doc_type": {
        "total": "합계금액 (total amount)",
        "a": "...",
        "b": "...",
    },
}
```

### 3. `groundextract/mcp_server.py` — `_DOC_TYPE_SCHEMA`

Add the type to the enum so MCP clients can select it:

```python
_DOC_TYPE_SCHEMA: dict[str, Any] = {
    "type": "string",
    "enum": ["tax_invoice", "statement", "balance_sheet", "my_doc_type"],
    ...
}
```

### 4. Tests

Add a test that exercises the pack through the real gate — at minimum one clean
document that comes out fully `verified` and one with a wrong number that comes out
`discarded`. `tests/test_balance_sheet.py` is the model to copy.

If you also want your type in the benchmark, add generator support in
`bench/generate_golden.py` and regenerate; keep the fixed-seed reproducibility
property intact (a given `(seed, counts)` must always produce identical files).

**Checklist for a rule-pack PR**

- [ ] `rules/<doc_type>.yaml` added, `doc_type` matches the filename
- [ ] `FIELD_SPECS["<doc_type>"]` added in `groundextract/llm/openweight.py`
- [ ] `<doc_type>` added to `_DOC_TYPE_SCHEMA["enum"]` in `groundextract/mcp_server.py`
- [ ] Test with at least one clean and one violating document
- [ ] `pytest -q` and `ruff check .` green
- [ ] Sample text in tests/fixtures is **synthetic** — see [Data policy](#data-policy)

---

## Data policy

**No real documents, ever.**

This repository handles tax invoices and financial statements. **Never commit real
regulatory documents, personal data, business registration numbers, barcodes, or
company-identifying information**, not even in a test fixture, not even "just this
once".

- Test and benchmark documents must be **synthetic** (the golden-set generator makes
  realistic ones for you).
- The committed sample documents are synthetic. If you point the fixture builder at a
  document of your own, pass it by environment variable (`GEK_BALANCE_PDF`) — real
  sources stay git-ignored and are never inlined into code or fixtures.
- Demo images must be masked before they are committed.

A PR that adds real document data will be asked to rewrite it, no matter how good
the code is.

## Dependency policy

**The runtime dependency list is PyYAML, full stop.** "One `pip install`, no
network, no keys" is a core property of this project: it is why the gate can run
inside an air-gapped review process and why the deterministic path can never be
blamed on a model provider.

- New runtime dependencies will almost certainly be rejected. Use the standard
  library (the MCP server and the Ollama client are both plain `urllib`/`json`).
- Anything heavier belongs in an **optional extra** in `pyproject.toml`
  (`[docling]`, `[fixture]`, `[dev]`) and must be imported lazily, inside the
  function that needs it, with a clear error message when it is missing.
- CI fails the build if a strong-copyleft (GPL/AGPL) license appears in the
  dependency tree.

## Code style

- Ruff is the authority (`python -m ruff check .`). Line length 100.
- Type hints on public functions; `from __future__ import annotations` at the top.
- Docstrings explain **why**, not what the next line already says. Module docstrings
  are expected on new modules.
- English for identifiers and code comments. Korean is fine in user-facing strings,
  field descriptions, rule-pack comments, and Korean documentation.
- Determinism is not negotiable in the gate path: no randomness, no wall-clock
  dependence, no network calls in `grounding.py`, `rules.py`, or `gate.py`.

## Pull requests

- **One concern per PR.** A rule pack, a bug fix, or a docs change — not all three.
- Branch from `main`; keep the branch rebased rather than merge-committed.
- Commit subjects use `type(scope): summary`, e.g. `feat(rules): payslip rule pack`,
  `fix(bench): report recall per injected-error kind`. Body explains the reasoning.
- Fill in the PR template. If your change moves a benchmark number, **paste the
  before/after `python -m groundextract.bench` output in the PR description** — we
  track precision and recall in the open, including when they get worse.
- A change that lowers `Auto-Discard Recall` below 100% needs an explicit
  justification. Recall is the product; precision is the thing we are willing to pay.
- New behavior needs a test. Bug fixes need a regression test that fails before the
  fix.
- Add a `CHANGELOG.md` entry under `## [Unreleased]` for anything user-visible.

## Good first issues

Small, self-contained, and genuinely useful — no invented busywork:

1. **New rule pack** for a Korean document type we don't cover: 급여명세서 (payslip:
   gross = sum of allowances, net = gross − deductions), 영수증/현금영수증, 견적서,
   거래명세서. Follow the three-registration checklist above.
2. **Document the `const` rule side.** `{const: 1000}` is supported by
   `_side_value()` but appears in no rule pack, no test, and no docs. Add a test and
   an example.
3. **CI matrix.** `.github/workflows/ci.yml` tests Python 3.11 only while the project
   claims 3.11+. Add 3.12 and 3.13 to the matrix.
4. **Per-error-kind benchmark breakdown.** The golden set labels every injected error
   as `ungrounded` or `field-swap`. Report recall separately for each so the
   "grounding alone is not enough" claim is measured, not asserted.
5. **`balance_sheet` golden documents.** The rule pack, viewer fixture, and unit tests
   exist, but the type is absent from the 146-field benchmark. Extend
   `bench/generate_golden.py`.
6. **Unit-scale detection.** Statements printed in 천원/백만원 are not rescaled before
   rule evaluation, which can produce spurious violations. Detect the header and
   normalize (start with a failing test that demonstrates the problem).
7. **Viewer accessibility.** `viewer/index.html` is keyboard-unfriendly: the bbox
   overlays are hover-only, so failed-check details are unreachable without a mouse.

Comment on the issue (or open one) before starting anything large, so two people
don't build the same thing.

## Reporting bugs

Use the issue templates: **bug report**, **feature request**, or **rule pack
request** for a new document type. For a suspected gate bypass, use
[SECURITY.md](SECURITY.md) instead of a public issue.

A bug report about a wrong verdict is only actionable with the **exact input** —
document text, extracted values, `doc_type` — so please include a minimal
reproduction with synthetic text.

## Code of Conduct

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).
