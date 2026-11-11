#!/usr/bin/env python3
"""Run the complete offline analysis pipeline on one dataset and write the leads.

    uv run python scripts/run_analysis.py --input data/synthetic/transactions.csv \
        --seeds data/synthetic/seed_wallets.txt --output-dir data/processed

Input may be CSV/TSV, JSON/JSON Lines or XML, optionally gzip-compressed. Outputs
(ranked leads as JSON and CSV, evidence sheets, scores, features, patterns) are
described in docs/pipeline.md.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.pipeline.orchestrator import AnalysisOrchestrator  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the offline Bitcoin transaction-traffic investigation pipeline.")
    parser.add_argument("--input", required=True, help="Dataset file (.csv, .tsv, .json, .jsonl, .xml; optionally .gz).")
    parser.add_argument("--output-dir", default="data/processed", help="Directory for the generated leads and reports.")
    parser.add_argument("--seeds", help="File of known illicit wallet addresses (one per line, or comma/space separated) to propagate risk from.")
    parser.add_argument("--contamination", type=float, default=0.05, help="Share of rows each anomaly detector flags.")
    parser.add_argument("--random-state", type=int, default=42, help="Random seed used by ML routines.")
    parser.add_argument("--neo4j", action="store_true", help="Also write the graph to Neo4j (NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD).")
    parser.add_argument("--dataset-id", default="cli", help="Label the graph is stored under in Neo4j.")
    parser.add_argument("--top", type=int, default=10, help="How many leads to print.")
    return parser.parse_args()


def read_seeds(path: str | None) -> list[str]:
    if not path:
        return []
    text = Path(path).read_text(encoding="utf-8")
    return list(dict.fromkeys(part.strip().strip("\"'") for part in text.replace(",", " ").replace(";", " ").split() if part.strip()))


def main() -> None:
    args = parse_args()
    seeds = read_seeds(args.seeds)
    orchestrator = AnalysisOrchestrator(contamination=args.contamination, random_state=args.random_state)
    result = orchestrator.run(args.input, output_dir=args.output_dir, seed_wallets=seeds, persist_graph=args.neo4j, dataset_id=args.dataset_id)
    investigation = result.investigation
    overview = result.overview
    stats, network, ingestion = overview["stats"], overview["network"], result.ingestion_statistics

    print(f"\nAnalysis completed in {result.processing_duration:.1f}s  (stages: {result.stage_seconds})")
    print(f"Ingested {ingestion['rows']:,} rows -> {stats['transactions']:,} transactions ({stats['observations']:,} network observations); "
          f"{ingestion['invalid_records']} rejected, {ingestion['duplicates']} duplicates, amounts in {ingestion['amount_unit']}.")
    print(f"{stats['wallets']:,} wallets -> {stats['entities']:,} entities ({stats['multi_address_entities']:,} multi-address) -> "
          f"{stats['actors']:,} actors ({stats['multi_entity_actors']:,} joining several entities); {stats['communities']} communities.")
    print(f"Origin inference: {network['attributed_share']:.1%} of transactions attributed to an origin IP "
          f"(mean confidence {network['mean_origin_confidence']:.2f}); {network['relay_nodes']} relay nodes recognised.")
    print(f"Patterns: {overview['patterns']} | Seeds: {len(overview['seeds'])}")
    print(f"Wallet risk: {overview['risk_distribution']} | Actor risk: {overview['actor_risk_distribution']} | IP risk: {overview['ip_risk_distribution']}")
    evaluation = overview["evaluation"]
    if evaluation.get("labels_available"):
        print(f"Against the dataset's behaviour labels: transaction ROC-AUC {evaluation.get('transaction_auc')}, "
              f"HIGH+ precision {evaluation.get('high_risk_precision')} (wallet-level metrics: scripts/evaluate_models.py).")

    leads = [alert for alert in investigation.wallet_alerts(args.top + len(seeds)) if not alert["is_seed"]][: args.top]
    if leads:
        print(f"\nTop {len(leads)} wallet leads (seeds excluded):")
        for alert in leads:
            print(f"  #{alert['rank']:<4d} {alert['risk_score']:5.1f} {alert['risk_level']:8s} conf {alert['confidence']:.2f}  {alert['wallet_id']}  - {alert['top_reason']}")
    ips = investigation.ip_alerts(min(args.top, 5))
    if ips:
        print("\nTop IP leads:")
        for alert in ips:
            where = ", ".join(part for part in (alert["country"], f"AS{alert['asn']}" if alert["asn"] else "", alert["as_org"]) if part)
            print(f"  #{alert['rank']:<4d} {alert['risk_score']:5.1f} {alert['risk_level']:8s} {alert['ip']:<39s} {where}  - {alert['top_reason']}")
    for warning in result.warnings:
        if args.neo4j:
            print(f"\nNote: {warning}")
    print(f"\nOutputs written to {result.output_dir}")


if __name__ == "__main__":
    main()
