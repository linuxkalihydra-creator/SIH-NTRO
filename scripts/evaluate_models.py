#!/usr/bin/env python3
"""Measure the models on one dataset against its synthetic ground truth and print a Markdown report.

The pipeline itself never reads ground_truth.json; this script runs the pipeline,
then compares its output with the labels.

    uv run python scripts/evaluate_models.py --input data/synthetic/transactions.csv \
        --truth data/synthetic/ground_truth.json --seeds data/synthetic/seed_wallets.txt

For the mean and worst case over many independently generated datasets, use
``scripts/benchmark.py`` instead.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.correlation.flows import build_flow_index  # noqa: E402
from backend.enrichment.service import enrich_dataset  # noqa: E402
from backend.ingestion.service import load_dataset  # noqa: E402
from backend.ml.entities import cluster_entities, common_input_ownership  # noqa: E402
from backend.ml.patterns import detect_coinjoins  # noqa: E402
from backend.pipeline.investigation import Investigation  # noqa: E402
from scripts.evaluation import evaluate  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", default="data/synthetic/transactions.csv")
    parser.add_argument("--truth", default="data/synthetic/ground_truth.json")
    parser.add_argument("--seeds", default="data/synthetic/seed_wallets.txt")
    parser.add_argument("--json", help="Also write the full metrics to this file.")
    args = parser.parse_args()

    truth = json.loads(Path(args.truth).read_text(encoding="utf-8"))
    seeds = Path(args.seeds).read_text(encoding="utf-8").split() if args.seeds else []
    records, summary = load_dataset(args.input, include_summary=True)
    enriched, ip_info = enrich_dataset(records)
    start = time.perf_counter()
    investigation = Investigation.build(enriched, seeds, ip_info=ip_info, ingestion=summary)
    seconds = time.perf_counter() - start
    metrics = evaluate(investigation, truth)
    wallets = metrics["wallets"]

    print(f"# Evaluation of `{args.input}`\n")
    print(f"{summary['rows']:,} capture rows, {summary['transactions']:,} transactions, {wallets['wallets']:,} addresses "
          f"({wallets['illicit']} owned by illicit actors, {wallets['cash_out_points']} cash-out points), {wallets['seeds']} seed wallets. "
          f"Models fitted in {seconds:.1f}s.\n")

    print("## Wallet leads (seed wallets excluded: they are given, not detected)\n")
    print("| Score | ROC-AUC | Average precision |\n|---|---|---|")
    print(f"| **Fused risk score, strict** (owned by an illicit actor) | **{wallets['strict']['roc_auc']:.3f}** | **{wallets['strict']['average_precision']:.3f}** |")
    print(f"| **Fused risk score, investigative** (also counts cash-out points) | **{wallets['investigative']['roc_auc']:.3f}** | **{wallets['investigative']['average_precision']:.3f}** |")
    for label, key in (("Anomaly signal alone", "anomaly_only"), ("Pattern signal alone", "pattern_only"), ("Taint signal alone", "taint_only"), ("Network signal alone", "network_only")):
        print(f"| {label} | {wallets[key]['roc_auc']:.3f} | {wallets[key]['average_precision']:.3f} |")

    investigative = wallets["investigative"]
    print("\n| Top-k leads | Precision |\n|---|---|")
    for k in (25, 50, 100, 200, 400):
        print(f"| {k} | {investigative[f'precision_at_{k}']:.2f} |")
    print("\n| Tier | Wallets | Precision | Recall |\n|---|---|---|---|")
    print(f"| HIGH or CRITICAL | {investigative['high_count']} | {investigative['high_precision']:.2f} | {investigative['high_recall']:.2f} |")
    print(f"| MEDIUM or above | {investigative['medium_count']} | {investigative['medium_precision']:.2f} | {investigative['medium_recall']:.2f} |")
    print(f"\nFalse positives at HIGH+ by true role: {wallets['false_positive_roles_high'] or 'none'}.")

    print("\n## Detection by true role (share at MEDIUM or above / at HIGH or above)\n")
    print("| Role | Addresses | >= MEDIUM | >= HIGH |\n|---|---|---|---|")
    for row in wallets["by_role"]:
        print(f"| {row['role']} | {row['addresses']} | {row['medium_or_above']:.2f} | {row['high_or_above']:.2f} |")

    print("\n## Entity clustering\n")
    flows = build_flow_index(records)
    coinjoins = {pattern.txids[0] for pattern in detect_coinjoins(flows)}
    labels = truth["wallets"]

    def mixed(clustering) -> tuple[int, int]:
        multi = [members for members in clustering.members.values() if len(members) > 1]
        return len(multi), sum(1 for members in multi if len({labels[address]["entity_id"] for address in members}) > 1)

    print("| Variant | Multi-address entities | Entities mixing two true owners |\n|---|---|---|")
    for name, clustering in (("Common-input ownership, CoinJoins included (naive)", common_input_ownership(flows, set())),
                             ("Common-input ownership, CoinJoins excluded", common_input_ownership(flows, coinjoins)),
                             ("+ unambiguous change addresses", cluster_entities(flows, coinjoins, use_elimination=False)),
                             ("+ change by elimination (other new outputs paid a service)", cluster_entities(flows, coinjoins))):
        multi, impure = mixed(clustering)
        print(f"| {name} | {multi} | {impure} |")
    entities, actors = metrics["entities"]["entities"], metrics["entities"]["actors"]
    print(f"| + laundering-structure continuity (used) | {entities['multi_address_groups']} | {entities['impure_groups']} |")
    print(f"\nEntities: pair precision {entities['pair_precision']:.4f}, pair recall {entities['pair_recall']:.3f} "
          f"({entities['misplaced_addresses']} misplaced addresses). Links used: {metrics['entities']['entity_links']}.")
    print(f"Actors (entities tied by two of: same IP, direct payment, same deposit address): pair precision {actors['pair_precision']:.4f}, pair recall {actors['pair_recall']:.3f} "
          f"against the true operations ({actors['misplaced_addresses']} misplaced addresses).\n")
    print("| Operation | Addresses | Actors it is split into | Share in its largest actor |\n|---|---|---|---|")
    for name, row in metrics["entities"]["operations"].items():
        print(f"| {name} | {row['addresses']} | {row['actors']} | {row['largest_actor_share']:.0%} |")

    origin = metrics["origin"]
    if origin:
        print("\n## Origin IP inference\n")
        print(f"- Taking the first-seen relay as the origin is right for {origin['first_seen_accuracy']:.1%} of {origin['transactions']:,} transactions.")
        print(f"- With lead-time confidence and relay-node discounting, {origin['attributed_share']:.1%} are attributed, and "
              f"{origin['attributed_precision']:.2%} of those attributions are correct ({origin['wrong_attributions']} wrong).")

    print("\n## Pattern detectors (transaction level, against the generated structures)\n")
    print("| Detector | Precision | Recall | Generated transactions |\n|---|---|---|---|")
    for row in metrics["patterns"]:
        print(f"| {row['detector']} | {row['precision']:.2f} | {row['recall']:.2f} | {row['generated_transactions']} |")

    transactions = metrics["transactions"]
    print("\n## Transaction leads (against the dataset's behaviour labels)\n")
    print(f"Fused transaction risk ROC-AUC {transactions.get('transaction_auc')}, anomaly ensemble alone {transactions.get('transaction_anomaly_only_auc')}; "
          f"{transactions.get('high_risk_transactions')} transactions at HIGH+ with precision {transactions.get('high_risk_precision')}.")
    if args.json:
        Path(args.json).write_text(json.dumps(metrics, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
