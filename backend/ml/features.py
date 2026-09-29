"""Wallet-level and transaction-level feature engineering.

Wallet features combine the blockchain layer (amounts, counterparties, timing,
holding time, fees, script types, co-spending) with the network layer correlated
to each address (IPs, ASNs, countries and ports seen when it spent). Transaction
features describe the shape of each transaction and how it was relayed.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from typing import Any

import numpy as np
import pandas as pd

from backend.correlation.flows import FlowIndex, build_flow_index
from backend.correlation.network import NetworkCorrelation, build_network_correlation

QUICK_SPEND_SECONDS = 3600.0

# Human-readable names used in explanations.
FEATURE_LABELS = {
    "transaction_count": "Transactions",
    "incoming_transaction_count": "Incoming transactions",
    "outgoing_transaction_count": "Outgoing transactions",
    "total_received": "Total received (BTC)",
    "total_sent": "Total sent (BTC)",
    "average_received": "Average received (BTC)",
    "average_sent": "Average sent (BTC)",
    "maximum_transaction": "Largest amount (BTC)",
    "pass_through_ratio": "Sent / received",
    "median_holding_hours": "Median holding time (h)",
    "quick_spend_ratio": "Receipts spent within 1 h",
    "unique_counterparties": "Unique counterparties",
    "unique_ips": "Unique IPs",
    "unique_asns": "Unique ASNs",
    "unique_countries": "Unique countries",
    "nonstandard_port_ratio": "Non-standard port share",
    "shared_ip_wallets": "Other wallets on its IPs",
    "average_fee_rate": "Average fee rate (sat/vB)",
    "unique_script_types": "Script types used",
    "max_inputs_cospent": "Most inputs co-spent",
    "max_outputs_created": "Most outputs in one spend",
    "entity_size": "Addresses in its entity",
    "active_hours": "Active span (h)",
    "transactions_per_hour": "Transactions per hour",
    "transactions_per_day": "Transactions per day",
    "average_time_between_transactions": "Mean gap between transactions (s)",
    "minimum_time_between_transactions": "Shortest gap between transactions (s)",
    "burstiness": "Burstiness",
    "fan_in_ratio": "Fan-in (received / sent)",
    "fan_out_ratio": "Fan-out (sent / received)",
    "graph_degree": "Graph degree",
    "graph_in_degree": "Graph in-degree",
    "graph_out_degree": "Graph out-degree",
    # transaction features
    "input_count": "Inputs",
    "output_count": "Outputs",
    "total_input": "Input value (BTC)",
    "fee": "Fee (BTC)",
    "fee_rate": "Fee rate (sat/vB)",
    "max_output_share": "Largest output share",
    "equal_output_count": "Equal-value outputs",
    "fresh_output_ratio": "Outputs to fresh addresses",
    "round_output_ratio": "Round-amount outputs",
    "input_age_hours": "Age of spent funds (h)",
    "input_prior_transactions": "Prior activity of inputs",
    "nonstandard_port": "Non-standard port",
    "ip_wallet_count": "Wallets on the relaying IP",
}

# Columns that are identifiers or duplicates are left out of the anomaly models.
WALLET_MODEL_FEATURES = [
    "incoming_transaction_count", "outgoing_transaction_count", "total_received", "total_sent", "maximum_transaction",
    "pass_through_ratio", "median_holding_hours", "quick_spend_ratio", "unique_counterparties", "unique_ips", "unique_asns",
    "unique_countries", "nonstandard_port_ratio", "shared_ip_wallets", "average_fee_rate", "max_inputs_cospent",
    "max_outputs_created", "entity_size", "transactions_per_day", "minimum_time_between_transactions", "burstiness",
]
TRANSACTION_MODEL_FEATURES = [
    "input_count", "output_count", "total_input", "fee_rate", "max_output_share", "equal_output_count", "fresh_output_ratio",
    "round_output_ratio", "input_age_hours", "input_prior_transactions", "nonstandard_port", "ip_wallet_count",
]


def _is_round(amount: float) -> bool:
    return amount > 0 and abs(amount * 1000 - round(amount * 1000)) < 1e-6


def build_wallet_features(flows: FlowIndex, network: NetworkCorrelation, entity_sizes: dict[str, int] | None = None) -> pd.DataFrame:
    """One row per address with blockchain, timing and network-layer features."""
    received_amounts: dict[str, list[float]] = defaultdict(list)
    sent_amounts: dict[str, list[float]] = defaultdict(list)
    times: dict[str, list[float]] = defaultdict(list)
    counterparties: dict[str, set[str]] = defaultdict(set)
    fee_rates: dict[str, list[float]] = defaultdict(list)
    scripts: dict[str, set[str]] = defaultdict(set)
    max_inputs: Counter = Counter()
    max_outputs: Counter = Counter()
    for tx in flows.txs:
        participants = set(tx.input_addresses) | set(tx.output_addresses)
        rate = tx.fee * 1e8 / max(tx.size, 1)
        for address, amount in tx.inputs:
            sent_amounts[address].append(amount)
            times[address].append(tx.ts)
            fee_rates[address].append(rate)
            if tx.script_type:
                scripts[address].add(tx.script_type)
            max_inputs[address] = max(max_inputs[address], len(tx.inputs))
            max_outputs[address] = max(max_outputs[address], len(tx.outputs))
        for address, amount in tx.outputs:
            received_amounts[address].append(amount)
            times[address].append(tx.ts)
        for address in participants:
            counterparties[address].update(participants - {address})

    rows: list[dict[str, Any]] = []
    for address in flows.addresses:
        received, sent = received_amounts[address], sent_amounts[address]
        total_received, total_sent = sum(received), sum(sent)
        stamps = sorted(times[address])
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        span_hours = max(stamps[-1] - stamps[0], 60.0) / 3600 if len(stamps) > 1 else 0.0
        per_hour = (len(stamps) - 1) / span_hours if len(stamps) > 1 else 0.0
        if len(gaps) >= 2 and (mean := sum(gaps) / len(gaps)) > 0:
            deviation = float(np.std(gaps))
            burstiness = (deviation - mean) / (deviation + mean)
        else:
            burstiness = 0.0
        holding = []
        for index in flows.received.get(address, []):
            receipt = flows.txs[index]
            spend = flows.next_spend(address, receipt.ts)
            if spend is not None:
                holding.append(spend.ts - receipt.ts)
        footprint = network.wallets.get(address)
        rows.append({
            "wallet_id": address,
            "transaction_count": len(received) + len(sent),
            "incoming_transaction_count": len(received),
            "outgoing_transaction_count": len(sent),
            "total_received": round(total_received, 8),
            "total_sent": round(total_sent, 8),
            "average_received": round(total_received / len(received), 8) if received else 0.0,
            "average_sent": round(total_sent / len(sent), 8) if sent else 0.0,
            "maximum_transaction": round(max(received + sent, default=0.0), 8),
            "pass_through_ratio": round(min(total_sent / total_received, 5.0), 6) if total_received > 0 else 0.0,
            # Funds never spent inside the window are "held" for the rest of the window.
            "median_holding_hours": round(median(holding) / 3600, 4) if holding else round((flows.txs[-1].ts - stamps[0]) / 3600, 4),
            "quick_spend_ratio": round(sum(1 for value in holding if value <= QUICK_SPEND_SECONDS) / len(received), 4) if received else 0.0,
            "unique_counterparties": len(counterparties[address]),
            "unique_ips": len(footprint.ips) if footprint else 0,
            "unique_asns": len(footprint.asns) if footprint else 0,
            "unique_countries": len(footprint.countries) if footprint else 0,
            "nonstandard_port_ratio": round(footprint.nonstandard_port_ratio, 4) if footprint else 0.0,
            "shared_ip_wallets": network.shared_ip_wallets(address),
            "average_fee_rate": round(sum(fee_rates[address]) / len(fee_rates[address]), 3) if fee_rates[address] else 0.0,
            "unique_script_types": len(scripts[address]),
            "max_inputs_cospent": int(max_inputs[address]),
            "max_outputs_created": int(max_outputs[address]),
            "entity_size": int((entity_sizes or {}).get(address, 1)),
            "active_hours": round(span_hours, 4),
            "transactions_per_hour": round(per_hour, 8),
            "transactions_per_day": round(per_hour * 24, 8),
            "average_time_between_transactions": round(sum(gaps) / len(gaps), 4) if gaps else 0.0,
            "minimum_time_between_transactions": round(min(gaps), 4) if gaps else 0.0,
            "burstiness": round(burstiness, 6),
            "fan_in_ratio": round(total_received / max(total_sent, 1e-9), 6) if total_received > 0 else 0.0,
            "fan_out_ratio": round(total_sent / max(total_received, 1e-9), 6) if total_sent > 0 else 0.0,
            "graph_degree": len(received) + len(sent),
            "graph_in_degree": len(received),
            "graph_out_degree": len(sent),
        })
    return pd.DataFrame(rows)


def build_transaction_features(flows: FlowIndex, network: NetworkCorrelation) -> pd.DataFrame:
    """One row per transaction describing its shape, the funds it spends and how it was relayed."""
    last_received: dict[str, float] = {}
    prior_activity: Counter = Counter()
    rows = []
    for tx in flows.txs:
        outputs = [amount for _, amount in tx.outputs]
        total_out = sum(outputs)
        equal = Counter(round(amount, 8) for amount in outputs).most_common(1)[0][1] if outputs else 0
        ages = [tx.ts - last_received[address] for address in tx.input_addresses if address in last_received]
        rows.append({
            "txid": tx.txid,
            "input_count": len(tx.inputs),
            "output_count": len(tx.outputs),
            "total_input": round(tx.total_in, 8),
            "total_output": round(total_out, 8),
            "fee": round(tx.fee, 8),
            "fee_rate": round(tx.fee * 1e8 / max(tx.size, 1), 4),
            "max_output_share": round(max(outputs) / total_out, 6) if total_out > 0 else 0.0,
            "equal_output_count": int(equal) if equal > 1 else 0,
            "fresh_output_ratio": round(sum(1 for address in tx.output_addresses if flows.is_fresh_output(address, tx)) / max(len(tx.outputs), 1), 4),
            "round_output_ratio": round(sum(1 for amount in outputs if _is_round(amount)) / max(len(outputs), 1), 4),
            "input_age_hours": round(min(ages) / 3600, 4) if ages else 0.0,
            "input_prior_transactions": int(sum(prior_activity[address] for address in tx.input_addresses)),
            "nonstandard_port": int(tx.nonstandard_port),
            "ip_wallet_count": len(network.ip_wallets.get(tx.src_ip, ())),
        })
        for address in tx.input_addresses:
            prior_activity[address] += 1
        for address in tx.output_addresses:
            prior_activity[address] += 1
            last_received[address] = tx.ts
    return pd.DataFrame(rows)


def build_wallet_feature_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Convenience wrapper: wallet features straight from raw records (no entity sizes)."""
    flows = build_flow_index(records)
    return build_wallet_features(flows, build_network_correlation(flows))


def log_transform(frame: pd.DataFrame) -> pd.DataFrame:
    """Compress heavy-tailed non-negative features; keep signed ones (e.g. burstiness) as they are."""
    transformed = frame.astype(float).copy()
    for column in transformed.columns:
        if (transformed[column] >= 0).all():
            transformed[column] = np.log1p(transformed[column])
    return transformed


__all__ = [
    "FEATURE_LABELS", "WALLET_MODEL_FEATURES", "TRANSACTION_MODEL_FEATURES", "build_wallet_features",
    "build_transaction_features", "build_wallet_feature_frame", "log_transform",
]
