#!/usr/bin/env python3
"""Measure the models against the synthetic ground truth (wallet roles, illicit flags, patterns).

The pipeline itself never reads ground_truth.json; this script runs the pipeline,
then compares its output with the labels and prints a Markdown report.

    uv run python scripts/evaluate_models.py --input data/synthetic/transactions.csv \
        --truth data/synthetic/ground_truth.json --seeds data/synthetic/seed_wallets.txt
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sklearn.metrics import average_precision_score, roc_auc_score  # noqa: E402

from backend.correlation.flows import build_flow_index  # noqa: E402
from backend.enrichment.service import enrich_records  # noqa: E402
from backend.ingestion.service import load_dataset  # noqa: E402
from backend.ml.entities import common_input_ownership  # noqa: E402
from backend.ml.patterns import detect_coinjoins  # noqa: E402
from backend.pipeline.investigation import Investigation  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", default="data/synthetic/transactions.csv")
    parser.add_argument("--truth", default="data/synthetic/ground_truth.json")
    parser.add_argument("--seeds", default="data/synthetic/seed_wallets.txt")
    args = parser.parse_args()

    truth = json.loads(Path(args.truth).read_text(encoding="utf-8"))["wallets"]
    seeds = Path(args.seeds).read_text(encoding="utf-8").split() if args.seeds else []
    records = enrich_records(load_dataset(args.input))
    investigation = Investigation.build(records, seeds)
    wallets = investigation.wallets
    illicit = wallets["wallet_id"].map(lambda address: truth[address]["illicit"])
    evaluated = ~wallets["is_seed"]  # seeds are given, not detected
    scores = wallets["risk_score"][evaluated]
    target = illicit[evaluated]

    print("## Wallet-level detection (seeds excluded)\n")
    print("| Score | ROC-AUC | Average precision |\n|---|---|---|")
    for name, column in (("Fused risk score", "risk_score"), ("Anomaly ensemble only", "anomaly_percentile"), ("Pattern signal only", "signal_pattern"), ("Taint signal only", "signal_taint")):
        values = wallets[column][evaluated]
        print(f"| {name} | {roc_auc_score(target, values):.3f} | {average_precision_score(target, values):.3f} |")

    ranked = wallets[evaluated].sort_values("risk_score", ascending=False)
    ranked_truth = ranked["wallet_id"].map(lambda address: truth[address]["illicit"])
    print("\n| Top-k leads | Precision |\n|---|---|")
    for k in (25, 50, 100, 200):
        print(f"| {k} | {ranked_truth.head(k).mean():.2f} |")
    flagged = wallets["risk_level"].isin(["HIGH", "CRITICAL"]) & evaluated
    print(f"\nHIGH or CRITICAL: {int(flagged.sum())} wallets, precision {illicit[flagged].mean():.2f}, "
          f"recall {illicit[flagged].sum() / target.sum():.2f} of {int(target.sum())} illicit addresses.")

    print("\n## Detection by role (share at MEDIUM or above / at HIGH or above)\n")
    print("| Role | Addresses | >= MEDIUM | >= HIGH |\n|---|---|---|---|")
    roles = wallets["wallet_id"].map(lambda address: f"{truth[address]['entity_kind']} / {truth[address]['role']}")
    level = wallets["risk_level"]
    victims = {address for record in records if record.get("behavior_type") == "RANSOMWARE" for address in record["input_addresses"] if truth[address]["role"] == "user_wallet"}
    groups = dict(Counter(roles[evaluated & illicit]).most_common(14))
    rows = [(role, roles == role) for role in groups] + [("victims (licit users who paid a ransom)", wallets["wallet_id"].isin(victims))]
    for role, mask in rows:
        mask = mask & evaluated
        if mask.sum():
            print(f"| {role} | {int(mask.sum())} | {level[mask].isin(['MEDIUM', 'HIGH', 'CRITICAL']).mean():.2f} | {level[mask].isin(['HIGH', 'CRITICAL']).mean():.2f} |")

    print("\n## Common-input ownership purity\n")
    flows = build_flow_index(records)
    coinjoins = {pattern.txids[0] for pattern in detect_coinjoins(flows)}
    print("| Variant | Multi-address entities | Entities mixing two true owners |\n|---|---|---|")
    for name, excluded in (("CoinJoins excluded (used)", coinjoins), ("Naive (CoinJoins included)", set())):
        clustering = common_input_ownership(flows, excluded)
        multi = [members for members in clustering.members.values() if len(members) > 1]
        impure = sum(1 for members in multi if len({truth[address]["entity_id"] for address in members}) > 1)
        print(f"| {name} | {len(multi)} | {impure} |")

    evaluation = investigation.overview["evaluation"]
    print("\n## Transaction level (from the in-app evaluation)\n")
    print(f"Fused transaction risk ROC-AUC {evaluation.get('transaction_auc')}, anomaly ensemble alone {evaluation.get('transaction_anomaly_only_auc')}; "
          f"HIGH+ precision {evaluation.get('high_risk_precision')}.\n")
    print("| Detector | Precision | Recall | Support |\n|---|---|---|---|")
    for item in evaluation.get("detectors", []):
        print(f"| {item['detector']} | {item['precision']:.2f} | {item['recall']:.2f} | {item['support']} |")


if __name__ == "__main__":
    main()
