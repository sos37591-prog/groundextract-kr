"""Tests for the viewer fixture exporter (``groundextract.export``).

Fixtures must be produced by the real gate (not hand-written), share the
viewer schema ``{doc_text, summary, fields}``, and carry the demo storyline:
the clean invoice is fully verified while the injected one auto-discards the
hallucinated VAT and taints its rule siblings.
"""

from __future__ import annotations

import json

from groundextract.export import export_fixtures

FIXTURE_SCHEMA = {"doc_text", "summary", "fields"}
FIELD_SCHEMA = {"field", "value", "checks", "verdict", "confidence"}


def _export(tmp_path):
    paths = export_fixtures(tmp_path)

    def load(name: str) -> dict:
        return json.loads(paths[name].read_text(encoding="utf-8"))

    return load("clean"), load("injected"), paths


def test_export_writes_fixture_files(tmp_path):
    _, _, paths = _export(tmp_path)
    assert paths["clean"].name == "clean.json" and paths["clean"].exists()
    assert paths["injected"].name == "injected.json" and paths["injected"].exists()
    # standalone file:// fallback bundle (window.__GROUNDEXTRACT_FIXTURES__); the
    # image viewer (index.html) loads image_data.js, this is a separate JSON export.
    assert paths["fixtures_js"].name == "fixtures.js" and paths["fixtures_js"].exists()


def test_fixture_schema(tmp_path):
    clean, injected, _ = _export(tmp_path)
    for data in (clean, injected):
        assert set(data) == FIXTURE_SCHEMA
        assert isinstance(data["doc_text"], str) and data["doc_text"]
        assert data["summary"]["total"] == len(data["fields"]) > 0
        for f in data["fields"]:
            assert set(f) == FIELD_SCHEMA
            assert f["verdict"] in ("verified", "discarded")
            assert f["value"]["field"] == f["field"]
            # every field carries a grounding check the viewer can explain
            assert any(c["name"] == "grounding" for c in f["checks"])


def test_clean_fixture_fully_verified(tmp_path):
    clean, _, _ = _export(tmp_path)
    assert clean["summary"]["discarded"] == 0
    assert all(f["verdict"] == "verified" for f in clean["fields"])
    assert all(f["confidence"] > 0 for f in clean["fields"])


def test_injected_fixture_discards_vat_and_taints_siblings(tmp_path):
    _, injected, _ = _export(tmp_path)
    assert injected["summary"]["discarded"] >= 1
    by_field = {f["field"]: f for f in injected["fields"]}

    vat = by_field["vat"]
    assert vat["verdict"] == "discarded"
    assert vat["confidence"] == 0.0
    failed = [c["name"] for c in vat["checks"] if not c["passed"]]
    assert "grounding" in failed  # 250,000 has no verbatim support
    assert any(name != "grounding" for name in failed)  # arithmetic also broke

    # sibling contamination: the violated invariants taint supply/total too
    assert by_field["supply"]["verdict"] == "discarded"
    assert by_field["total"]["verdict"] == "discarded"
    # ...but fields the broken rules never reference stay verified
    assert by_field["item1_supply"]["verdict"] == "verified"
    assert by_field["item2_supply"]["verdict"] == "verified"


def test_injected_vat_quote_is_findable_for_the_viewer(tmp_path):
    # The viewer highlights grounding_quote by substring search; the injected
    # vat cites the real VAT line (with a wrong number), so the doc pane can
    # flip that exact span green -> red when the toggle is switched.
    _, injected, _ = _export(tmp_path)
    vat = next(f for f in injected["fields"] if f["field"] == "vat")
    assert vat["value"]["grounding_quote"] in injected["doc_text"]
