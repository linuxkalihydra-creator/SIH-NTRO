# Convenience targets. Everything here also works as the plain commands shown in the README.
.PHONY: help setup ui run demo test benchmark evaluate dataset geoip neo4j neo4j-down bundle clean

PY ?= uv run python

help:
	@echo "make setup      install Python (uv) and dashboard (npm) dependencies"
	@echo "make ui         build the dashboard into frontend/dist"
	@echo "make run        start the platform on http://127.0.0.1:8000"
	@echo "make demo       analyse the bundled capture, then start the platform"
	@echo "make test       run the test suite"
	@echo "make evaluate   metrics on the committed capture vs its ground truth"
	@echo "make benchmark  metrics over 10 independently generated captures"
	@echo "make dataset    regenerate data/synthetic (seed 42)"
	@echo "make geoip      download the DB-IP Lite GeoIP databases (the one online step)"
	@echo "make neo4j      start the optional Neo4j container;  make neo4j-down stops it"
	@echo "make bundle     build the air-gap bundle (wheels + dashboard + GeoIP + source)"

setup:
	uv sync
	cd frontend && npm ci --no-audit --no-fund

ui:
	cd frontend && npm run build

run:
	bash run.sh

demo:
	bash run.sh --demo

test:
	uv run pytest -q

evaluate:
	$(PY) scripts/evaluate_models.py

benchmark:
	$(PY) scripts/benchmark.py

dataset:
	$(PY) scripts/generate_dataset.py --records 10000 --seed 42 --output-dir data/synthetic

geoip:
	$(PY) scripts/download_geoip.py

neo4j:
	docker compose up -d neo4j

neo4j-down:
	docker compose stop neo4j

bundle:
	bash scripts/make_offline_bundle.sh

clean:
	rm -rf data/processed data/raw/uploads offline-bundle wheelhouse .deps .pytest_cache
