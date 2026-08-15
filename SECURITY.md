# Security Policy

## Supported versions

The project is pre-1.0. Only the latest release line receives fixes; there are no
backports to older tags.

| Version | Supported |
| --- | --- |
| 0.1.x | ✅ |
| < 0.1 | ❌ (pre-release, never published) |

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
- The optional `extract_verified` MCP tool talks to an Ollama server you configure
  (`host` argument). Point it only at an endpoint you trust; document text is sent
  to it.
- The runtime dependency surface is intentionally one package (PyYAML). CI generates
  a CycloneDX SBOM and fails on strong-copyleft licenses in the dependency tree.
