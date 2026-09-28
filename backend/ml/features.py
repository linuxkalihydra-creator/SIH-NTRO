"""Wallet-level behavioral feature engineering for the synthetic SIH prototype.

Each row in the output DataFrame represents a wallet entity. The feature set is
built from transaction and network metadata without using `behavior_type` as a
feature. This is appropriate for later anomaly detection and clustering tasks.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

import pandas as pd


def _safe_float(value: float | int | None) -> float:
    """Return a float while safely handling null or missing values."""
    if value in (None, "", "nan"):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def build_wallet_feature_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Construct a wallet-level feature DataFrame from the synthetic dataset."""
    wallet_data: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "transaction_count": 0,
        "incoming_transaction_count": 0,
        "outgoing_transaction_count": 0,
        "total_received": 0.0,
        "total_sent": 0.0,
        "average_received": 0.0,
        "average_sent": 0.0,
        "maximum_transaction": 0.0,
        "amount_variance": 0.0,
        "unique_counterparties": set(),
        "unique_ips": set(),
        "unique_asns": set(),
        "unique_countries": set(),
        "timestamps": [],
        "incoming_amounts": [],
        "outgoing_amounts": [],
        "graph_degree": 0,
        "graph_in_degree": 0,
        "graph_out_degree": 0,
    })

    for record in records:
        txid = str(record.get("txid", ""))
        input_wallets = [str(wallet) for wallet in record.get("input_addresses", [])]
        output_wallets = [str(wallet) for wallet in record.get("output_addresses", [])]
        all_wallets = sorted(set(input_wallets + output_wallets))
        input_amounts = [float(amount) for amount in record.get("input_amounts", [])]
        output_amounts = [float(amount) for amount in record.get("output_amounts", [])]

        # A wallet in input_addresses spends funds (outgoing, observed at src_ip).
        for wallet in input_wallets:
            stats = wallet_data[wallet]
            stats["transaction_count"] += 1
            stats["outgoing_transaction_count"] += 1
            stats["graph_out_degree"] += 1
            stats["outgoing_amounts"].extend(input_amounts)
            stats["total_sent"] += sum(input_amounts)
            stats["unique_ips"].add(str(record.get("src_ip", "")))
            stats["unique_asns"].add(int(record.get("asn", 0) or 0))
            stats["unique_countries"].add(str(record.get("geo_country", "")))
            stats["timestamps"].append(datetime.fromisoformat(str(record.get("timestamp"))))
            stats["graph_degree"] += 1

        # A wallet in output_addresses receives funds (incoming, observed at dst_ip).
        for wallet in output_wallets:
            stats = wallet_data[wallet]
            stats["transaction_count"] += 1
            stats["incoming_transaction_count"] += 1
            stats["graph_in_degree"] += 1
            stats["incoming_amounts"].extend(output_amounts)
            stats["total_received"] += sum(output_amounts)
            stats["unique_ips"].add(str(record.get("dst_ip", "")))
            stats["unique_asns"].add(int(record.get("asn", 0) or 0))
            stats["unique_countries"].add(str(record.get("geo_country", "")))
            stats["timestamps"].append(datetime.fromisoformat(str(record.get("timestamp"))))
            stats["graph_degree"] += 1

        for wallet in all_wallets:
            wallet_data[wallet]["unique_counterparties"].update(other for other in all_wallets if other != wallet)

    rows: list[dict[str, Any]] = []
    for wallet_id, stats in wallet_data.items():
        incoming_amounts = stats["incoming_amounts"]
        outgoing_amounts = stats["outgoing_amounts"]
        incoming_total = sum(incoming_amounts)
        outgoing_total = sum(outgoing_amounts)

        incoming_count = max(len(incoming_amounts), 1)
        outgoing_count = max(len(outgoing_amounts), 1)
        max_tx = max(incoming_amounts + outgoing_amounts, default=0.0)
        variance = float(pd.Series(incoming_amounts + outgoing_amounts).var(ddof=0)) if incoming_amounts or outgoing_amounts else 0.0

        timestamps = sorted(stats["timestamps"])
        gaps = []
        for previous, current in zip(timestamps, timestamps[1:]):
            gaps.append((current - previous).total_seconds())
        # Activity rate over the wallet's observed time span: (n - 1) intervals per hour.
        # The span is floored at one minute so simultaneous transactions stay finite.
        if len(timestamps) > 1:
            span_hours = max((timestamps[-1] - timestamps[0]).total_seconds(), 60.0) / 3600
            transactions_per_hour = (len(timestamps) - 1) / span_hours
        else:
            transactions_per_hour = 0.0

        rows.append({
            "wallet_id": wallet_id,
            "transaction_count": int(stats["transaction_count"]),
            "incoming_transaction_count": int(stats["incoming_transaction_count"]),
            "outgoing_transaction_count": int(stats["outgoing_transaction_count"]),
            "total_received": round(incoming_total, 8),
            "total_sent": round(outgoing_total, 8),
            "average_received": round(incoming_total / incoming_count, 8),
            "average_sent": round(outgoing_total / outgoing_count, 8),
            "maximum_transaction": round(max_tx, 8),
            "amount_variance": round(variance, 8),
            "unique_counterparties": len(stats["unique_counterparties"]),
            "unique_ips": len(stats["unique_ips"]),
            "unique_asns": len(stats["unique_asns"]),
            "unique_countries": len(stats["unique_countries"]),
            "transactions_per_hour": round(transactions_per_hour, 8),
            "transactions_per_day": round(transactions_per_hour * 24, 8),
            "average_time_between_transactions": round(sum(gaps) / len(gaps), 8) if gaps else 0.0,
            "minimum_time_between_transactions": round(min(gaps, default=0.0), 8),
            "fan_in_ratio": round(incoming_total / max(outgoing_total, 1e-9), 8) if incoming_total > 0 else 0.0,
            "fan_out_ratio": round(outgoing_total / max(incoming_total, 1e-9), 8) if outgoing_total > 0 else 0.0,
            "graph_degree": int(stats["graph_degree"]),
            "graph_in_degree": int(stats["graph_in_degree"]),
            "graph_out_degree": int(stats["graph_out_degree"]),
        })

    return pd.DataFrame(rows)


__all__ = ["build_wallet_feature_frame"]
