#!/bin/bash

# Bitcoin Investigation Platform - Complete Demo Script
# This script demonstrates the full end-to-end workflow

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$PROJECT_DIR/data/synthetic"
OUTPUT_DIR="$PROJECT_DIR/data/processed"
# generate_dataset.py writes to data/synthetic relative to the working directory.
cd "$PROJECT_DIR"

echo "========================================="
echo "Bitcoin Investigation Platform - Demo"
echo "========================================="
echo ""

# Step 1: Generate synthetic data (the committed dataset is regenerated identically with --seed 42)
echo "[1/5] Generating synthetic transaction data..."
mkdir -p "$DATA_DIR"
uv run python scripts/generate_dataset.py --records 10000 --seed 42 --output-dir "$DATA_DIR"
echo "✓ Generated 10000 synthetic transactions, ground truth and seed list"
echo ""

# Offline GeoIP: download the open DB-IP Lite databases once if no .mmdb is present yet.
if ! ls "$PROJECT_DIR"/data/geoip/*.mmdb >/dev/null 2>&1; then
  echo "Downloading DB-IP Lite GeoIP databases (one-time, needs internet)..."
  uv run python scripts/download_geoip.py || echo "GeoIP download failed; continuing with the dataset's own country/ASN fields"
fi

# Step 2: Run analysis pipeline
echo "[2/5] Running analysis pipeline..."
mkdir -p "$OUTPUT_DIR"
uv run python scripts/run_analysis.py --input "$DATA_DIR/transactions.csv" --seeds "$DATA_DIR/seed_wallets.txt" --output-dir "$OUTPUT_DIR"
echo "✓ Pipeline analysis complete"
echo ""

# Step 3: Run backend tests
echo "[3/5] Running backend tests..."
uv run pytest tests/ -q
echo "✓ Test suite passed"
echo ""

# Step 4: Build frontend
echo "[4/5] Building frontend..."
cd "$PROJECT_DIR/frontend"
[ -d node_modules ] || npm ci
npm run build
echo "✓ Production build complete (dist/ directory ready)"
cd "$PROJECT_DIR"
echo ""

# Step 5: Display startup instructions
echo "[5/5] Ready to launch!"
echo ""
echo "========================================="
echo "To start the investigation platform:"
echo "========================================="
echo ""
echo "Terminal 1 - Backend API Server:"
echo "  cd $PROJECT_DIR"
echo "  uv run uvicorn backend.api.main:app --reload"
echo ""
echo "Terminal 2 - Frontend Development Server:"
echo "  cd $PROJECT_DIR/frontend"
echo "  npm run dev"
echo ""
echo "Open in browser:"
echo "  http://localhost:5173"
echo ""
echo "Backend API docs:"
echo "  http://localhost:8000/docs"
echo ""
echo "========================================="
