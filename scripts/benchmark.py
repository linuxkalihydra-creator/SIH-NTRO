#!/usr/bin/env python3
"""Benchmark the pipeline on independently generated synthetic worlds.

One committed dataset says little: detectors can be tuned to it by accident. This
script generates a fresh economy per random seed, runs the full pipeline on it and
reports the mean and the *worst* world for every metric, plus how run time grows
with dataset size. Nothing is fitted to the ground truth; it is only used to score.

    uv run python scripts/benchmark.py                       # 10 worlds x 10,000 transactions
    uv run python scripts/benchmark.py --seeds 1 2 3 --records 5000
    uv run python scripts/benchmark.py --scale 10000 25000 50000 100000
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

import numpy as np  # noqa: E402

from backend.enrichment.service import enrich_dataset  # noqa: E402
from backend.pipeline.investigation import Investigation  # noqa: E402
from scripts.evaluation import evaluate  # noqa: E402
from scripts.generate_dataset import generate_world, observation_rows  # noqa: E402

DEFAULT_SEEDS = (42, 7, 99, 2024, 5, 11, 3, 8, 13, 21)
SUMMARY = (
    ("Wallet leads, strict: average precision", lambda r: r["wallets"]["strict"]["average_precision"]),
    ("Wallet leads, strict: ROC-AUC", lambda r: r["wallets"]["strict"]["roc_auc"]),
    ("Wallet leads, investigative: average precision", lambda r: r["wallets"]["investigative"]["average_precision"]),
    ("Precision of the top 100 leads", lambda r: r["wallets"]["investigative"]["precision_at_100"]),
    ("Precision of the top 400 leads", lambda r: r["wallets"]["investigative"]["precision_at_400"]),
    ("HIGH or above: precision", lambda r: r["wallets"]["investigative"]["high_precision"]),
    ("HIGH or above: recall", lambda r: r["wallets"]["investigative"]["high_recall"]),
    ("MEDIUM or above: precision", lambda r: r["wallets"]["investigative"]["medium_precision"]),
    ("MEDIUM or above: recall", lambda r: r["wallets"]["investigative"]["medium_recall"]),
    ("Anomaly signal alone: average precision", lambda r: r["wallets"]["anomaly_only"]["average_precision"]),
    ("Pattern signal alone: average precision", lambda r: r["wallets"]["pattern_only"]["average_precision"]),
    ("Taint signal alone: average precision", lambda r: r["wallets"]["taint_only"]["average_precision"]),
    ("Network signal alone: average precision", lambda r: r["wallets"]["network_only"]["average_precision"]),
    ("Entity clustering: pair precision", lambda r: r["entities"]["entities"]["pair_precision"]),
    ("Entity clustering: pair recall", lambda r: r["entities"]["entities"]["pair_recall"]),
    ("Actor grouping: pair precision", lambda r: r["entities"]["actors"]["pair_precision"]),
    ("Actor grouping: pair recall", lambda r: r["entities"]["actors"]["pair_recall"]),
    ("Origin IP: attributed share", lambda r: r["origin"]["attributed_share"]),
    ("Origin IP: precision of attributions", lambda r: r["origin"]["attributed_precision"]),
    ("Origin IP: plain first-seen accuracy", lambda r: r["origin"]["first_seen_accuracy"]),
    ("Transaction risk ROC-AUC", lambda r: r["transactions"].get("transaction_auc", float("nan"))),
    ("Analysis seconds", lambda r: r["seconds"]),
)


def run_world(records: int, seed: int) -> dict:
    transactions, _, truth = generate_world(records, seed)
    enriched, ip_info = enrich_dataset(transactions)
    start = time.perf_counter()
    investigation = Investigation.build(enriched, truth["seed_wallets"], ip_info=ip_info)
    seconds = time.perf_counter() - start
    result = evaluate(investigation, truth)
    result.update(seed=seed, records=records, rows=len(observation_rows(transactions)), seconds=round(seconds, 2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, nargs="*", default=list(DEFAULT_SEEDS), help="Random seeds; each generates an independent world.")
    parser.add_argument("--records", type=int, default=10000, help="Transactions per world.")
    parser.add_argument("--scale", type=int, nargs="*", default=[], help="Also time the pipeline at these transaction counts (seed 42).")
    parser.add_argument("--json", help="Write every world's full metrics to this file.")
    args = parser.parse_args()

    results = []
    for seed in args.seeds:
        result = run_world(args.records, seed)
        results.append(result)
        wallets = result["wallets"]["investigative"]
        print(f"seed {seed:5d}: AP {wallets['average_precision']:.3f} | HIGH+ precision {wallets['high_precision']:.3f} recall {wallets['high_recall']:.3f} | "
              f"MEDIUM+ precision {wallets['medium_precision']:.3f} recall {wallets['medium_recall']:.3f} | {result['seconds']:.1f}s", flush=True)

    if results:
        print(f"\n## {len(results)} independent worlds x {args.records:,} transactions\n")
        print("| Metric | Mean | Worst world |\n|---|---|---|")
        for label, getter in SUMMARY:
            values = np.array([getter(result) for result in results], dtype=float)
            worst = values.max() if label == "Analysis seconds" else values.min()
            print(f"| {label} | {np.nanmean(values):.3f} | {worst:.3f} |")
        print("\n### Pattern detectors (pooled over all worlds)\n")
        print("| Detector | Precision | Recall | Generated transactions |\n|---|---|---|---|")
        for index, row in enumerate(results[0]["patterns"]):
            detected = sum(result["patterns"][index]["detected_transactions"] for result in results)
            generated = sum(result["patterns"][index]["generated_transactions"] for result in results)
            hits = sum(result["patterns"][index]["precision"] * result["patterns"][index]["detected_transactions"] for result in results)
            print(f"| {row['detector']} | {hits / max(detected, 1):.3f} | {hits / max(generated, 1):.3f} | {generated} |")
        print("\n### Detection by true role (mean share of addresses at MEDIUM or above / HIGH or above)\n")
        print("| Role | Addresses per world | >= MEDIUM | >= HIGH |\n|---|---|---|---|")
        roles: dict[str, list[dict]] = {}
        for result in results:
            for row in result["wallets"]["by_role"]:
                roles.setdefault(row["role"], []).append(row)
        for role, rows in sorted(roles.items(), key=lambda item: -np.mean([row["addresses"] for row in item[1]])):
            print(f"| {role} | {np.mean([row['addresses'] for row in rows]):.0f} | {np.mean([row['medium_or_above'] for row in rows]):.2f} | "
                  f"{np.mean([row['high_or_above'] for row in rows]):.2f} |")

    if args.scale:
        print("\n## Run time by dataset size (seed 42)\n")
        print("| Transactions | Capture rows | Wallets | Analysis seconds | Investigative AP |\n|---|---|---|---|---|")
        for count in args.scale:
            result = run_world(count, 42)
            results.append(result)
            print(f"| {count:,} | {result['rows']:,} | {result['wallets']['wallets']:,} | {result['seconds']:.1f} | "
                  f"{result['wallets']['investigative']['average_precision']:.3f} |", flush=True)

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1), encoding="utf-8")
        print(f"\nFull metrics written to {args.json}")


if __name__ == "__main__":
    main()
