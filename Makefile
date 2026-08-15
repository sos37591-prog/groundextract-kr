.PHONY: install test lint demo bench sbom

install:
	python -m pip install -e ".[dev]"

test:
	python -m pytest -q

lint:
	ruff check .

demo:
	python -m groundextract

bench:
	python -m groundextract.bench

sbom:
	python -m pip install cyclonedx-bom pip-licenses
	mkdir -p sbom
	cyclonedx-py environment -o sbom/bom.cdx.json
	pip-licenses --format=markdown --output-file=sbom/licenses.md
