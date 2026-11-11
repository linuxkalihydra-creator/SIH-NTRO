"""Address-level, entity-level and transaction-level feature engineering.

*Address features* combine the blockchain layer (amounts, counterparties, timing,
holding time, fees, script types, co-spending) with the network layer correlated
to each address (IPs, ASNs, countries and ports seen when it spent).

*Entity features* describe the same behaviour for a whole entity (all addresses
one party controls). Most addresses are used once or twice, so a single address
says little about how its owner behaves; an entity's spends, counterparties and
relay footprint are far more informative. Ratios estimated from very few events
are shrunk towards the dataset average so that one odd transaction does not look
like a habit.

*Transaction features* describe the shape of each transaction, the funds it
spends and how it travelled through the network.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from statistics import median
from typing import Any

import numpy as np
import pandas as pd

from backend.correlation.flows import FlowIndex, build_flow_index
from backend.correlation.network import NetworkCorrelation, build_network_correlation

QUICK_SPEND_SECONDS = 3600.0
SHRINKAGE = 4.0  # pseudo-observations pulling small-sample ratios towards the dataset average
# An entity dealing with this many distinct counterparties starts to look like a service (exchange,
# merchant, payment processor); at SERVICE_FULL it is treated as one.
SERVICE_START, SERVICE_FULL = 20, 200

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
    # entity features
    "address_count": "Addresses controlled",
    "spend_count": "Spending transactions",
    "receive_count": "Receiving transactions",
    "received_value": "Received from others (BTC)",
    "sent_value": "Sent to others (BTC)",
    "largest_transfer": "Largest transfer (BTC)",
    "flow_through_ratio": "Sent / received (entity)",
    "holding_hours": "Median holding time (h)",
    "quick_forward_ratio": "Receipts moved on within 1 h",
    "counterparty_entities": "Counterparty entities",
    "relay_ips": "IPs it broadcast from",
    "relay_asns": "Networks (ASNs) it broadcast from",
    "relay_countries": "Countries it broadcast from",
    "nonstandard_port_share": "Non-standard port share (entity)",
    "fee_rate": "Fee rate (sat/vB)",
    "peak_fee_rate": "Highest fee rate (sat/vB)",
    "lifetime_hours": "Active span (h)",
    "spends_per_day": "Spends per day",
    "single_use_share": "Single-use addresses",
    "widest_cospend": "Most inputs co-spent",
    "widest_payout": "Most outputs in one spend",
    # transaction features
    "input_count": "Inputs",
    "output_count": "Outputs",
    "total_input": "Input value (BTC)",
    "fee": "Fee (BTC)",
    "max_output_share": "Largest output share",
    "equal_output_count": "Equal-value outputs",
    "fresh_output_ratio": "Outputs to fresh addresses",
    "round_output_ratio": "Round-amount outputs",
    "input_age_hours": "Age of spent funds (h)",
    "input_prior_transactions": "Prior activity of inputs",
    "nonstandard_port": "Non-standard port",
    "ip_wallet_count": "Wallets on the relaying IP",
    "relay_count": "Times observed on the network",
    "propagation_seconds": "Propagation spread (s)",
    "origin_confidence": "Origin confidence",
}

# Columns that are identifiers or duplicates are left out of the anomaly models.
WALLET_MODEL_FEATURES = [
    "incoming_transaction_count", "outgoing_transaction_count", "total_received", "total_sent", "maximum_transaction",
    "pass_through_ratio", "median_holding_hours", "quick_spend_ratio", "unique_counterparties", "unique_ips", "unique_asns",
    "unique_countries", "nonstandard_port_ratio", "shared_ip_wallets", "average_fee_rate", "max_inputs_cospent",
    "max_outputs_created", "entity_size", "transactions_per_day", "minimum_time_between_transactions", "burstiness",
]
ENTITY_MODEL_FEATURES = [
    "address_count", "spend_count", "receive_count", "received_value", "sent_value", "largest_transfer", "flow_through_ratio",
    "holding_hours", "quick_forward_ratio", "counterparty_entities", "relay_ips", "relay_asns", "relay_countries",
    "nonstandard_port_share", "fee_rate", "peak_fee_rate", "lifetime_hours", "spends_per_day", "single_use_share",
    "widest_cospend", "widest_payout",
]
TRANSACTION_MODEL_FEATURES = [
    "input_count", "output_count", "total_input", "fee_rate", "max_output_share", "equal_output_count", "fresh_output_ratio",
    "round_output_ratio", "input_age_hours", "input_prior_transactions", "nonstandard_port", "ip_wallet_count",
]


def _is_round(amount: float) -> bool:
    return amount > 0 and abs(amount * 1000 - round(amount * 1000)) < 1e-6


def service_score(counterparties: np.ndarray | float) -> np.ndarray | float:
    """0 for an ordinary wallet, 1 for a hub with hundreds of distinct counterparties (log scale between)."""
    value = (np.log10(np.asarray(counterparties, dtype=float) + 1) - math.log10(SERVICE_START + 1)) / (math.log10(SERVICE_FULL + 1) - math.log10(SERVICE_START + 1))
    return np.clip(value, 0.0, 1.0)


# ── address level ───────────────────────────────────────────────────────────
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
        participants = set(tx.input_addresses)
        participants.update(tx.output_addresses)
        rate = tx.fee * 1e8 / max(tx.size, 1)
        inputs, outputs = len(tx.inputs), len(tx.outputs)
        for address, amount in tx.inputs:
            sent_amounts[address].append(amount)
            times[address].append(tx.ts)
            fee_rates[address].append(rate)
            if tx.script_type:
                scripts[address].add(tx.script_type)
            if inputs > max_inputs[address]:
                max_inputs[address] = inputs
            if outputs > max_outputs[address]:
                max_outputs[address] = outputs
        for address, amount in tx.outputs:
            received_amounts[address].append(amount)
            times[address].append(tx.ts)
        if len(participants) > 1:
            for address in participants:
                counterparties[address].update(participants)

    end_ts = flows.end_ts
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
        if address in flows.spent:
            for index in flows.received.get(address, ()):
                receipt = flows.txs[index]
                spend = flows.next_spend(address, receipt.ts)
                if spend is not None:
                    holding.append(spend.ts - receipt.ts)
        footprint = network.wallets.get(address)
        rates = fee_rates[address]
        rows.append({
            "wallet_id": address,
            "transaction_count": len(received) + len(sent),
            "incoming_transaction_count": len(received),
            "outgoing_transaction_count": len(sent),
            "total_received": round(total_received, 8),
            "total_sent": round(total_sent, 8),
            "average_received": round(total_received / len(received), 8) if received else 0.0,
            "average_sent": round(total_sent / len(sent), 8) if sent else 0.0,
            "maximum_transaction": round(max(max(received, default=0.0), max(sent, default=0.0)), 8),
            "pass_through_ratio": round(min(total_sent / total_received, 5.0), 6) if total_received > 0 else 0.0,
            # Funds never spent inside the window are "held" for the rest of the window.
            "median_holding_hours": round(median(holding) / 3600, 4) if holding else round((end_ts - stamps[0]) / 3600, 4),
            "quick_spend_ratio": round(sum(1 for value in holding if value <= QUICK_SPEND_SECONDS) / len(received), 4) if received else 0.0,
            "unique_counterparties": max(len(counterparties[address]) - 1, 0),
            "unique_ips": len(footprint.ips) if footprint else 0,
            "unique_asns": len(footprint.asns) if footprint else 0,
            "unique_countries": len(footprint.countries) if footprint else 0,
            "nonstandard_port_ratio": round(footprint.nonstandard_port_ratio, 4) if footprint else 0.0,
            "shared_ip_wallets": network.shared_ip_wallets(address),
            "average_fee_rate": round(sum(rates) / len(rates), 3) if rates else 0.0,
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


# ── entity level ────────────────────────────────────────────────────────────
def build_entity_features(flows: FlowIndex, network: NetworkCorrelation, entity_of: dict[str, str], members: dict[str, list[str]],
                          wallets: pd.DataFrame, exclude_txids: set[str] | frozenset[str] = frozenset()) -> pd.DataFrame:
    """One row per entity: what its addresses did together, and where it broadcast from.

    ``wallets`` is the address feature frame indexed by ``wallet_id``. ``exclude_txids`` (CoinJoins) do not
    count towards the relay footprint, because a coordinator broadcasts them, not the participants.
    """
    entities = list(members)
    position = {entity: index for index, entity in enumerate(entities)}
    count = len(entities)
    of = {address: position[entity] for address, entity in entity_of.items()}

    spends = np.zeros(count)
    receives = np.zeros(count)
    received_value = np.zeros(count)
    sent_value = np.zeros(count)
    largest = np.zeros(count)
    fee_sum = np.zeros(count)
    fee_peak = np.zeros(count)
    first_ts = np.full(count, np.inf)
    last_ts = np.full(count, -np.inf)
    widest_cospend = np.zeros(count)
    widest_payout = np.zeros(count)
    broadcasts = np.zeros(count)
    nonstandard = np.zeros(count)
    counterparties: list[set[int]] = [set() for _ in range(count)]
    ips: list[set[str]] = [set() for _ in range(count)]
    asns: list[set[int]] = [set() for _ in range(count)]
    countries: list[set[str]] = [set() for _ in range(count)]

    for tx in flows.txs:
        senders = {of[address] for address in tx.input_addresses}
        incoming: dict[int, float] = {}
        for address, amount in tx.outputs:
            owner = of[address]
            if owner not in senders:
                incoming[owner] = incoming.get(owner, 0.0) + amount
        outgoing = sum(incoming.values())
        rate = tx.fee * 1e8 / max(tx.size, 1)
        origin = network.origins[tx.index] if network.origins else None
        attributed = origin is not None and origin.attributed and tx.txid not in exclude_txids
        for sender in senders:
            spends[sender] += 1
            sent_value[sender] += outgoing / len(senders)
            fee_sum[sender] += rate
            if rate > fee_peak[sender]:
                fee_peak[sender] = rate
            if outgoing > largest[sender]:
                largest[sender] = outgoing
            if len(tx.inputs) > widest_cospend[sender]:
                widest_cospend[sender] = len(tx.inputs)
            if len(tx.outputs) > widest_payout[sender]:
                widest_payout[sender] = len(tx.outputs)
            if tx.ts < first_ts[sender]:
                first_ts[sender] = tx.ts
            if tx.ts > last_ts[sender]:
                last_ts[sender] = tx.ts
            counterparties[sender].update(incoming)
            if attributed:
                broadcasts[sender] += 1
                nonstandard[sender] += tx.nonstandard_port
                ips[sender].add(origin.ip)
                if tx.asn:
                    asns[sender].add(tx.asn)
                if tx.country:
                    countries[sender].add(tx.country)
        for owner, amount in incoming.items():
            receives[owner] += 1
            received_value[owner] += amount
            if amount > largest[owner]:
                largest[owner] = amount
            if tx.ts < first_ts[owner]:
                first_ts[owner] = tx.ts
            if tx.ts > last_ts[owner]:
                last_ts[owner] = tx.ts
            counterparties[owner].update(senders)

    index = np.fromiter((of[address] for address in wallets.index), dtype=np.int64, count=len(wallets))
    sizes = np.bincount(index, minlength=count).astype(float)
    incoming_tx = wallets["incoming_transaction_count"].to_numpy(dtype=float)
    outgoing_tx = wallets["outgoing_transaction_count"].to_numpy(dtype=float)
    spent_mask = outgoing_tx > 0
    # Holding time and quick forwarding are per address; an entity's value is the receipt-weighted mean over its spending addresses.
    weight = np.where(spent_mask, np.maximum(incoming_tx, 1.0), 0.0)
    weight_sum = np.bincount(index, weights=weight, minlength=count)
    holding = np.bincount(index, weights=weight * wallets["median_holding_hours"].to_numpy(dtype=float), minlength=count)
    quick = np.bincount(index, weights=weight * wallets["quick_spend_ratio"].to_numpy(dtype=float), minlength=count)
    unspent_hold = np.bincount(index, weights=wallets["median_holding_hours"].to_numpy(dtype=float), minlength=count) / np.maximum(sizes, 1)
    single_use = np.bincount(index, weights=((incoming_tx <= 1) & (outgoing_tx <= 1)).astype(float), minlength=count)

    lifetime = np.where(np.isfinite(first_ts), np.maximum(last_ts - first_ts, 0.0) / 3600, 0.0)
    global_nonstandard = float(nonstandard.sum() / broadcasts.sum()) if broadcasts.sum() else 0.0
    frame = pd.DataFrame({
        "entity_id": entities,
        "address_count": sizes,
        "spend_count": spends,
        "receive_count": receives,
        "received_value": np.round(received_value, 8),
        "sent_value": np.round(sent_value, 8),
        "largest_transfer": np.round(largest, 8),
        "flow_through_ratio": np.round(np.where(received_value > 0, np.minimum(sent_value / np.maximum(received_value, 1e-12), 5.0), 0.0), 6),
        "holding_hours": np.round(np.where(weight_sum > 0, holding / np.maximum(weight_sum, 1e-12), unspent_hold), 4),
        "quick_forward_ratio": np.round(np.where(weight_sum > 0, quick / np.maximum(weight_sum, 1e-12), 0.0), 4),
        "counterparty_entities": np.fromiter((len(item) for item in counterparties), dtype=float, count=count),
        "relay_ips": np.fromiter((len(item) for item in ips), dtype=float, count=count),
        "relay_asns": np.fromiter((len(item) for item in asns), dtype=float, count=count),
        "relay_countries": np.fromiter((len(item) for item in countries), dtype=float, count=count),
        # Shrunk towards the dataset average: one odd port in one spend is not a habit.
        "nonstandard_port_share": np.round((nonstandard + SHRINKAGE * global_nonstandard) / (broadcasts + SHRINKAGE), 4),
        "fee_rate": np.round(np.where(spends > 0, fee_sum / np.maximum(spends, 1), 0.0), 3),
        "peak_fee_rate": np.round(fee_peak, 3),
        "lifetime_hours": np.round(lifetime, 4),
        "spends_per_day": np.round(np.where(lifetime > 0, spends / np.maximum(lifetime / 24, 1 / 24), spends), 6),
        "single_use_share": np.round(single_use / np.maximum(sizes, 1), 4),
        "widest_cospend": widest_cospend,
        "widest_payout": widest_payout,
        "broadcasts": broadcasts,
        "nonstandard_broadcasts": nonstandard,
    })
    frame["service_score"] = np.round(service_score(frame["counterparty_entities"].to_numpy()), 4)
    return frame


# ── transaction level ───────────────────────────────────────────────────────
def build_transaction_features(flows: FlowIndex, network: NetworkCorrelation) -> pd.DataFrame:
    """One row per transaction describing its shape, the funds it spends and how it was relayed."""
    last_received: dict[str, float] = {}
    prior_activity: Counter = Counter()
    origins = network.origins
    rows = []
    for tx in flows.txs:
        outputs = [amount for _, amount in tx.outputs]
        total_out = tx.total_out
        equal = Counter(round(amount, 8) for amount in outputs).most_common(1)[0][1] if outputs else 0
        ages = [tx.ts - last_received[address] for address in tx.input_addresses if address in last_received]
        origin = origins[tx.index] if origins else None
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
            "fresh_output_ratio": round(sum(1 for address in tx.output_addresses if flows.first_seen.get(address) == tx.index) / max(len(tx.outputs), 1), 4),
            "round_output_ratio": round(sum(1 for amount in outputs if _is_round(amount)) / max(len(outputs), 1), 4),
            "input_age_hours": round(min(ages) / 3600, 4) if ages else 0.0,
            "input_prior_transactions": int(sum(prior_activity[address] for address in tx.input_addresses)),
            "nonstandard_port": int(tx.nonstandard_port),
            "ip_wallet_count": len(network.ip_wallets.get(tx.src_ip, ())),
            "relay_count": tx.relay_count,
            "propagation_seconds": round(tx.propagation_seconds, 3),
            "origin_confidence": round(origin.confidence, 3) if origin else 0.0,
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
    "FEATURE_LABELS", "WALLET_MODEL_FEATURES", "ENTITY_MODEL_FEATURES", "TRANSACTION_MODEL_FEATURES", "build_wallet_features",
    "build_entity_features", "build_transaction_features", "build_wallet_feature_frame", "log_transform", "service_score",
    "SERVICE_START", "SERVICE_FULL",
]
