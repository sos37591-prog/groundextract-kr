# Writing a rule pack

A rule pack is one YAML file describing the arithmetic a document type must
satisfy. It is the only part of this project you are expected to write yourself,
and it needs no Python: the gate loads the file, evaluates the invariants over
whatever fields your extractor produced, and discards the values that break them.

The packs shipped here — [`tax_invoice.yaml`](tax_invoice.yaml),
[`statement.yaml`](statement.yaml), [`balance_sheet.yaml`](balance_sheet.yaml) —
are working examples; copy the closest one.

```bash
python -m groundextract verify --doc doc.txt --values values.json --rules my_pack.yaml
```

---

## The file

```yaml
doc_type: payroll          # names the pack; appears in every report

rules:
  - name: net_equals_gross_minus_deductions   # appears on the field that fails
    type: equals                              # equals | sum
    lhs: {field: net_pay}
    rhs: {expr: "gross_pay - deductions"}
    tol: 1.0                                  # in the value's unit (원)

  - name: deductions_equals_sum
    type: sum
    target: deductions
    items: [income_tax, pension, health_ins]
    tol: 1.0
```

Save it as `<doc_type>.yaml`. Dropping it into this directory makes it loadable
by name (`load_pack("payroll")`, `--doc-type payroll`); anywhere else, pass the
path (`load_rule_pack("...")`, `--rules ...`).

### `equals`

`|lhs - rhs| <= tol`. Each side is exactly one of:

| side | meaning |
| --- | --- |
| `{field: supply}` | the number extracted for that field |
| `{expr: "supply * 0.10"}` | arithmetic over field names |
| `{const: 0}` | a literal |

`expr` runs through an AST-restricted evaluator: `+ - * /`, parentheses, numeric
literals and field names, nothing else. There is no `eval()`, so a rule pack
cannot execute code — a pack from an untrusted source can produce wrong verdicts,
never side effects.

### `sum`

`|target - sum(items)| <= tol`. Shorthand for the common
`total = a + b + c` shape:

```yaml
  - name: supply_equals_sum_of_items
    type: sum
    target: supply
    items: [item1_supply, item2_supply]
```

### `tol`

Absolute, in the value's own unit. `1.0` on 원 amounts absorbs one-won rounding
without letting a real error through — the discrepancies this gate exists to
catch are orders of magnitude larger. Set it to `0` if the domain is exact.

---

## Field names

Rules reference `ExtractedValue.field`, so the names in the pack must be exactly
the names your extractor emits. Nothing infers or aliases them.

A field a rule *needs* but that was not extracted makes the rule **inapplicable**
— it is skipped rather than failed, because an invariant you did not supply the
operands for has not been violated, it just has not been checked. That is not a
free pass: the gate discards any numeric field that no applicable rule reached,
with a `rules_applied` check saying so. Skipping is not passing.

---

## Write overlapping rules

This is the one design choice that changes how the pack behaves in practice.

When a rule breaks, the gate asks *which* field is responsible. It sets aside any
field another rule still corroborates, then blames the smallest sets of the rest
that explain every violation. So a field covered by **two** rules can be cleared
by the one that still holds — and a field covered by only one cannot.

`balance_sheet` is the good case. Every total appears in two invariants:

```
자산총계 = 부채와자본총계          ← covers total_assets, total_liab_equity
자산총계 = 유동자산 + 비유동자산     ← covers total_assets, current, noncurrent
부채와자본총계 = 부채총계 + 자본총계  ← covers total_liab_equity, liabilities, equity
```

Misread 비유동자산 and only the second rule breaks. 자산총계 appears in it too,
but the first rule vouches for 자산총계, so the blame lands on the two addends
instead of all three fields.

`statement` is the bad case: one rule, `차변 = 대변`. When it breaks, both fields
are implicated and nothing can clear either. The pack is correct — it just cannot
localize, and its documents pay for that in precision.

**If you can express the same total two ways, write both rules.**

---

## Check your pack

`load_rule_pack` validates at load time and names what is wrong:

```
my_pack.yaml: rule 'net_equals_gross' .rhs: cannot parse expr 'gross - - *' (invalid syntax)
my_pack.yaml: rule 'r': unknown rule type 'magic'; expected one of equals, sum
my_pack.yaml: rule 'r': a 'sum' rule needs a non-empty 'items' list
my_pack.yaml: duplicate rule name 'r'
```

Then run it against a document you already know the answer for:

```bash
python -m groundextract verify --doc known_good.txt --values known_good.json \
    --rules my_pack.yaml            # expect every field verified, exit 0
```

A clean document coming back with discards usually means a rule references a
field name your extractor does not emit, or `tol` is too tight for the rounding
in the source.

---

## Contributing a pack

Rule packs for other Korean regulatory documents are welcome — see
[CONTRIBUTING.md](../CONTRIBUTING.md). A pack lands with a golden document or two
in `bench/golden/` so the benchmark exercises it; a pack the benchmark never runs
is a pack whose regressions nobody notices.
