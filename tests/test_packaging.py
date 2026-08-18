"""Packaging invariants: what a `pip install` gets must be what this tree says.

Two of these went wrong at once in 0.1.3, and both were invisible from a source
checkout — the only place anyone ever runs the tests:

  * the built wheel declared ``Version: 0.1.3`` while the code inside it
    answered ``0.1.2``, because the number was written in two files and only one
    was bumped. SECURITY.md asks a vulnerability reporter for "the version you
    tested", so the package's own answer is the one that has to be true.
  * ``python -m groundextract.bench`` resolved its rule packs relative to the
    repository layout, which in an installed wheel is ``site-packages``. No
    packs were found, no arithmetic ran, and the two headline numbers printed
    *identically* to the repository run — with exit code 0.

Neither is a bug the library hits at runtime, which is exactly why they need
tests of their own.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import groundextract
from groundextract.bench import main
from groundextract.rules import default_rules_dir

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "bench" / "golden"


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_the_version_lives_in_exactly_one_file():
    project = _pyproject()["project"]
    assert "version" not in project, (
        "pyproject.toml declares a static version again; it must stay dynamic so "
        "the number cannot disagree with groundextract.__version__"
    )
    assert "version" in project.get("dynamic", [])


def test_the_build_backend_reads_the_version_from_the_package():
    hatch_version = _pyproject()["tool"]["hatch"]["version"]
    assert hatch_version["path"] == "groundextract/__init__.py"
    # ...and that file really is where the constant lives.
    assert isinstance(groundextract.__version__, str)
    assert groundextract.__version__.count(".") == 2


def test_every_file_that_states_a_version_states_the_same_one():
    """The version appears in four places; nothing was checking they agreed.

    They did not. 0.1.3 shipped with `Version: 0.1.3` in its metadata and
    `__version__ == "0.1.2"` in its code, `CITATION.cff` still said 0.1.0 two
    releases later, and `server.json` — the MCP registry entry that tells a
    third party's agent which PyPI version to install — kept pointing at a
    release with a known gate bypass. Each drifted silently because releasing is
    a manual edit in several files.
    """
    version = groundextract.__version__

    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    assert server["version"] == version, "server.json disagrees with the package"
    for package in server.get("packages", []):
        assert package["version"] == version, (
            f"server.json points the registry at {package['identifier']} "
            f"{package['version']} while this tree is {version}"
        )

    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    assert f'version: "{version}"' in citation, "CITATION.cff disagrees with the package"


def test_bench_resolves_rule_packs_the_way_the_library_does():
    """The bench must use the resolver that knows about installed layouts.

    ``default_rules_dir()`` prefers the copy inside the package (force-included
    into the wheel) and falls back to the repository root. Anything computing its
    own path from ``__file__`` is correct in a checkout and wrong once installed.
    """
    rules_dir = default_rules_dir()
    doc_types = {
        json.loads(p.read_text(encoding="utf-8"))["doc_type"] for p in GOLDEN.glob("*.json")
    }
    assert doc_types, "golden set carries no documents"
    for doc_type in sorted(doc_types):
        assert (rules_dir / f"{doc_type}.yaml").is_file(), (
            f"no rule pack for {doc_type} in {rules_dir}"
        )


def test_bench_refuses_to_report_when_a_rule_pack_is_missing(tmp_path, capsys):
    """A golden set the packs do not cover is a loud failure, not a scorecard.

    Without this the metrics still print — grounding alone decides every verdict —
    and only precision moves, which is the number a casual reader checks last.
    """
    (tmp_path / "doc.json").write_text(
        json.dumps(
            {
                "doc_id": "d",
                "doc_type": "no_such_doc_type",
                "full_text": "공급가액 1,000원\n",
                "fields": [
                    {
                        "field": "supply",
                        "raw": "1,000원",
                        "grounding_quote": "공급가액 1,000원",
                        "expected_verdict": "verified",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    code = main([str(tmp_path)])
    assert code == 2
    err = capsys.readouterr().err
    assert "no rule pack" in err
    assert "no_such_doc_type" in err
