<!--
Thanks for contributing to GroundExtract-KR.
One concern per PR. Delete sections that genuinely do not apply.
-->

## What and why

<!-- What does this change, and what problem does it solve? Link the issue: Fixes #123 -->

## Type of change

- [ ] Bug fix (a wrong verdict, a crash, or documented behavior that wasn't true)
- [ ] New rule pack / document type
- [ ] New feature or behavior change
- [ ] Docs only
- [ ] Build, CI, or packaging

## Verification

```text
python -m pytest -q      →
python -m ruff check .   →
```

<!-- Paste the actual result lines. Both must be green. -->

- [ ] `python -m pytest -q` passes
- [ ] `python -m ruff check .` passes
- [ ] New behavior has a test; a bug fix has a regression test that fails without the fix

## Benchmark impact

Run `python -m groundextract.bench` if this touches `grounding.py`, `rules.py`,
`gate.py`, the rule packs, or the golden set. Paste before/after — we track these
numbers in the open, including when they get worse.

Paste **your own** `before`, measured on the base branch — do not trust a number
typed into this template. A stale baseline left here once made a precision *drop*
read as an improvement, which is the one direction this section exists to catch.

```text
before:  (run `python -m groundextract.bench` on the base branch and paste it here)
after:
```

For reference, `main` currently measures: `NHR 13.4% -> 0.0% | recall 100.0% |
precision 57.8% | grounded-accuracy 90.2%` — if your `before` disagrees, say so
rather than adjusting it.

- [ ] No benchmark change (this PR cannot affect verdicts)
- [ ] Numbers changed and are pasted above, with `CHANGELOG.md` updated
- [ ] **`Auto-Discard Recall` is still 100.0%** — if not, explain below why letting a
      labeled bad value through is acceptable
- [ ] `Auto-Discard Precision` and `Grounded-Accuracy` did not drop — if they did,
      say why the trade is worth it

## Rule pack PRs only

- [ ] `rules/<doc_type>.yaml` added (`doc_type` matches the filename stem)
- [ ] `FIELD_SPECS["<doc_type>"]` added in `groundextract/llm/openweight.py`
- [ ] `<doc_type>` added to `_DOC_TYPE_SCHEMA["enum"]` in `groundextract/mcp_server.py`
- [ ] Test covering one clean document (all `verified`) and one violating document
      (`discarded`)

## Project invariants

- [ ] No new **runtime** dependency (PyYAML is the whole list; heavier things go in an
      optional extra and are imported lazily)
- [ ] The gate path stays deterministic and offline — no randomness, no wall-clock
      dependence, no network in `grounding.py` / `rules.py` / `gate.py`
- [ ] No real documents, personal data, registration numbers, or company-identifying
      information anywhere in the diff — fixtures are synthetic
- [ ] `CHANGELOG.md` updated under `## [Unreleased]` for user-visible changes
- [ ] Docs updated if behavior or commands changed (`README.md` **and**
      `README.ko.md` stay in sync)

## Notes for reviewers

<!-- Trade-offs you made, things you are unsure about, what to look at first. -->
