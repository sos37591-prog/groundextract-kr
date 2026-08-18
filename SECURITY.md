# Security Policy

## Known-vulnerable release on PyPI

> **0.1.3 is what `pip install groundextract` currently gives you, and it has a gate
> bypass.** A misassigned amount comes back `verified` at confidence 1.0 whenever the
> document spells it with a space or a dot as the thousands separator, or in 한글/한자
> 수사 — ordinary OCR output for Korean forms. It also has an unbounded fuzzy-matching
> path that lets one well-formed MCP request occupy the server for minutes.
>
> Install the fixed release until the upload lands:
>
> ```bash
> pip install "groundextract @ git+https://github.com/sos37591-prog/groundextract-kr@v0.1.8"
> ```
>
> **Mitigation if you must stay on 0.1.3:** treat as unverified any value that carries a
> quantity your parser cannot read as one number — regardless of whether a rule names the
> field. 0.1.3 required arithmetic only of values it could read as a single number, so
> everything else skipped it:
>
> ```python
> from groundextract.grounding import normalize_number_str, carries_number
> unchecked = normalize_number_str(v.raw) is None and carries_number(v.raw)
> ```
>
> `carries_number` does not exist in 0.1.3; use `any(c.isdigit() for c in v.raw)` there,
> and treat 한글/한자 수사 (`일백만원`, `壹百萬`) as unchecked too — they carry no digit at
> all and so were never even considered numeric.

## Supported versions

The project is pre-1.0. Only the latest release line receives fixes; there are no
backports to older tags.

| Version | Supported | Why |
| --- | --- | --- |
| 0.1.8 | ✅ | current |
| 0.1.7 | ❌ | a 상호 plainly on the page came back ungrounded on documents past ~20 KB |
| 0.1.6 | ❌ | fault localization certified misread values and discarded correct ones |
| 0.1.4 – 0.1.5 | ❌ | gate bypass: 한글/한자 수사 misassignment verified at confidence 1.0 |
| ≤ 0.1.3 | ❌ | gate bypass: space/dot thousands separators verified at confidence 1.0 |

## Known limitation: a coordinated error is not caught

The gate catches any **single** wrong value (recall 100% on the benchmark). It does not
catch values that are wrong **together** in a way that still satisfies the invariants
linking them — and in that case it reports them `verified` at confidence 1.0.

Read 공급가액 and 세액 both an order of magnitude high and `세액 = 공급가액 × 10%` still
holds, so both misread values verify while 합계금액, read correctly, is discarded as the
only remaining explanation. The archetype is a 당기/전기 two-column form read one column
off. Refusing to corroborate across an overlapping rule catches this and costs 17 points
of precision (57.8% → 40.9%), so it is disclosed rather than paid for silently.

**If your documents are multi-column, treat `verified` as "no invariant objected", not
as "a human need not look".** This is not accepted as a vulnerability report on its own;
a *single-value* error reaching `verified` is, and remains the most serious class here.

## What counts as a security issue here

GroundExtract-KR is a **trust boundary**. Downstream systems act on values it marks
`verified` and discard everything else. That makes correctness of the verdict a
security property, not just a quality one. We treat all of the following as security
reports:

### 1. Gate bypass (false negative) — the highest severity class

**An input where a number that is not supported by the document comes out
`verdict=verified`.** This is the failure this project exists to prevent, and a
report of one is more valuable to us than any feature. Examples:

- A fabricated number that the grounding matcher accepts (for instance because a
  normalization or fuzzy-matching step is too permissive, or because a substring of
  an unrelated number is treated as evidence).
- A crafted quote that makes an ungrounded value look grounded.
- Numbers that violate a rule pack but slip through unchecked — e.g. a document that
  causes an invariant to be skipped rather than evaluated, or a formatting form
  (negative amounts, unit scaling, full-width digits) that the parser reads as a
  different number than a human reader would.
- A rule pack that silently fails to load, leaving values verified with grounding
  only.

Please report these **privately** rather than opening a public issue, so the fix can
ship before the technique is public.

### 2. Code execution and injection

- Any way to get arbitrary code execution out of a YAML rule pack or a rule
  expression. Expressions are evaluated by an AST-restricted evaluator (`+ - * /`,
  parentheses, numeric literals, field names) and rule packs are loaded with
  `yaml.safe_load` — anything that escapes either boundary is a vulnerability.
- Path traversal in document-type or rule-pack resolution (for example, via the
  `doc_type` argument of the MCP tools).
- Untrusted input reaching the MCP server that causes crashes, hangs, unbounded
  memory use, or reads of files outside the repository.

### 3. Data leakage

- Any code path that writes document contents, extracted values, or file paths to a
  location the caller did not ask for (logs, temp files, network).
- Personal or corporate data that has been committed to this repository by mistake.
  Report it privately and it will be removed.

### Not security issues

- Low precision / over-discarding (false positives). Values are discarded
  conservatively by design; that is a known trade-off, tracked publicly, and belongs
  in a normal issue.
- The optional Ollama or Docling backends being unreachable or slow.
- Extraction quality of any particular LLM. The gate assumes the extractor is
  untrustworthy — that is the premise, not a bug.

## Reporting a vulnerability

**Use GitHub's private vulnerability reporting:**

<https://github.com/sos37591-prog/groundextract-kr/security/advisories/new>

This opens a private advisory visible only to you and the maintainers. Please do not
open a public issue, and please do not include real personal or corporate documents
in the report — reproduce with **synthetic** text (the golden-set generator in
`bench/generate_golden.py` produces realistic synthetic documents).

A good report includes:

- The document text (synthetic), the extracted values, and the `doc_type`
- The verdict you got and the verdict you expected
- The version or commit you tested
- Why the input is realistic, if it is not obvious

## What to expect

| Stage | Target |
| --- | --- |
| Acknowledgement | within 5 business days |
| Initial assessment (severity, is it a bypass?) | within 10 business days |
| Fix or documented mitigation for a confirmed bypass | as fast as we can; a regression test lands with the fix |

Every confirmed gate bypass gets a **regression test in the public test suite** so it
can never come back, and a `CHANGELOG.md` entry under `Security`. Reporters are
credited by name or handle in the advisory and changelog unless they ask otherwise.

We do not operate a bug bounty.

## Deployment notes

- The deterministic path (grounding, rules, gate, `verify_extraction`, benchmark,
  viewer) makes **no network calls** and needs no credentials. If you see outbound
  traffic from that path, treat it as a security issue.
- **The optional `extract_verified` path sends your document to a model.** It talks
  to the Ollama server named by the `OLLAMA_HOST` environment variable, defaulting
  to `http://localhost:11434`, and the **full document text** goes into the prompt.
  If `OLLAMA_HOST` is already set in your shell, container, or MCP host to a shared
  GPU box or a colleague's endpoint, that is where the document goes — "open-weight"
  and "local" are not the same guarantee, and the input here is real 세금계산서 and
  재무제표. Check the variable before pointing this tool at anything confidential.
- `host` is **deployment configuration, not a tool argument**. A caller-supplied
  host would make the server a confused deputy — an agent could name any endpoint
  and have the server post your documents to it — so a request carrying `host` is
  rejected with `-32602` rather than honoured. **Both** tools reject it, including
  the one that opens no socket: a client sending it believes it is choosing an
  endpoint, and answering that belief with silence is worse than an error. Protect
  `OLLAMA_HOST` on the machine running the server; that is the actual control point.
- The runtime dependency surface is intentionally one package (PyYAML). CI generates
  a CycloneDX SBOM and fails on strong-copyleft licenses in the dependency tree.
