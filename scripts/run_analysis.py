#!/usr/bin/env python3
"""Run the complete synthetic analysis pipeline for the offline investigation prototype."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.pipeline.orchestrator import AnalysisOrchestrator

logging.basicConfig(level=logging.INFO, format="%(message)s")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the offline synthetic investigation pipeline.")
    parser.add_argument("--input", required=True, help="Path to a local synthetic dataset (.csv, .json, .xml).")
    parser.add_argument("--output-dir", default="data/processed", help="Directory for generated analysis artifacts.")
    parser.add_argument("--seeds", help="Optional file of known illicit wallet addresses (one per line) to propagate risk from.")
    parser.add_argument("--contamination", type=float, default=0.05, help="Share of rows each anomaly detector flags.")
    parser.add_argument("--random-state", type=int, default=42, help="Random seed used by ML routines.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    seeds = [line.strip() for line in Path(args.seeds).read_text(encoding="utf-8").splitlines() if line.strip()] if args.seeds else []
    orchestrator = AnalysisOrchestrator(contamination=args.contamination, random_state=args.random_state)
    result = orchestrator.run(args.input, output_dir=args.output_dir, seed_wallets=seeds)
    overview = result.overview
    stats = overview["stats"]
    risk = overview["risk_distribution"]
    print(f"Analysis completed in {result.processing_duration:.1f}s: {stats['transactions']} transactions, {stats['wallets']} wallets, "
          f"{stats['entities']} entities ({stats['multi_address_entities']} multi-address), {stats['communities']} communities.")
    print(f"Patterns: {overview['patterns']} | Seeds: {len(overview['seeds'])} | Wallet risk: {risk}")
    evaluation = overview["evaluation"]
    if evaluation.get("labels_available"):
        print(f"Evaluation vs synthetic labels: transaction AUC {evaluation.get('transaction_auc')}, HIGH+ precision {evaluation.get('high_risk_precision')}")
    print(f"Outputs written to {result.output_dir}")


if __name__ == "__main__":
    main()
