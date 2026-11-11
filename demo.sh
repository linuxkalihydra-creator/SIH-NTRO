#!/usr/bin/env bash
# Bitcoin Investigation Platform - end-to-end demonstration.
#
# Regenerates the synthetic capture, analyses it, measures the models against the
# ground truth, runs the test suite and (when Node.js is present) builds the
# dashboard. Finish with `bash run.sh --demo` to explore the results in the browser.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$PROJECT_DIR/data/synthetic"
OUTPUT_DIR="$PROJECT_DIR/data/processed"
cd "$PROJECT_DIR"

if command -v uv >/dev/null 2>&1; then
  RUN=(uv run)
elif [ -x .venv/bin/python ]; then
  RUN=(env "PATH=$PROJECT_DIR/.venv/bin:$PATH")
else
  echo "Install uv (https://docs.astral.sh/uv/), or create .venv first (bash run.sh does it)." >&2
  exit 1
fi

step() { printf '\n\033[1m[%s] %s\033[0m\n' "$1" "$2"; }

echo "========================================="
echo " Bitcoin Investigation Platform - Demo"
echo "========================================="

step 1/6 "Generating the synthetic capture (seed 42: identical to the committed files)"
"${RUN[@]}" python scripts/generate_dataset.py --records 10000 --seed 42 --output-dir "$DATA_DIR"

step 2/6 "Offline GeoIP"
if ls "$PROJECT_DIR"/data/geoip/*.mmdb >/dev/null 2>&1; then
  echo "Using $(ls "$PROJECT_DIR"/data/geoip/*.mmdb | tr '\n' ' ')"
else
  echo "No database yet: downloading DB-IP Lite once (needs internet)..."
  "${RUN[@]}" python scripts/download_geoip.py || echo "Download failed; continuing with the dataset's own country/ASN columns."
fi

step 3/6 "Running the analysis pipeline"
"${RUN[@]}" python scripts/run_analysis.py --input "$DATA_DIR/transactions.csv" --seeds "$DATA_DIR/seed_wallets.txt" --output-dir "$OUTPUT_DIR"

step 4/6 "Measuring the models against the ground truth"
"${RUN[@]}" python scripts/evaluate_models.py --input "$DATA_DIR/transactions.csv" --truth "$DATA_DIR/ground_truth.json" --seeds "$DATA_DIR/seed_wallets.txt"

step 5/6 "Running the test suite"
if "${RUN[@]}" python -c "import pytest" >/dev/null 2>&1; then
  "${RUN[@]}" python -m pytest tests/ -q
else
  echo "pytest is not installed in this environment (runtime dependencies only): skipping. Install requirements-dev.txt to run the tests."
fi

step 6/6 "Dashboard"
if [ -f frontend/dist/index.html ]; then
  echo "frontend/dist is already built."
elif command -v npm >/dev/null 2>&1; then
  (cd frontend && { [ -d node_modules ] || npm ci --no-audit --no-fund; } && npm run build)
else
  echo "Node.js is not installed and frontend/dist is missing: the API will run without the dashboard."
fi

cat <<EOF

=========================================
 Done. Leads and evidence sheets: $OUTPUT_DIR

 Explore them in the dashboard:
   bash run.sh --demo            # http://127.0.0.1:8000
 With the optional Neo4j graph database:
   bash run.sh --demo --neo4j
 Across many independent captures:
   ${RUN[*]} python scripts/benchmark.py
=========================================
EOF
