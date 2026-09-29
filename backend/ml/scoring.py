"""Fuse detector outputs into a risk score, a confidence and ranked reasons.

Four independent signal families, each in [0, 1], feed the score:

* ``anomaly`` — the anomaly ensemble's percentile, rescaled so only the top 10 % count;
* ``pattern`` — the strongest laundering pattern the entity takes part in, weighted
  by its role (a hop wallet counts fully, a CoinJoin participant only partly);
* ``taint`` — value-weighted taint propagated from the seed wallets;
* ``network`` — non-standard relay ports and ASN diversity of the spending side.

They are combined with a noisy-OR, ``risk = 100 * (1 - prod(1 - w_k * s_k))``: any
single strong signal raises the risk, and agreeing signals push it towards 100
without ever exceeding it. Because there are only four signals, the exact Shapley
value of each one is cheap to compute, so ``contributions`` always sums to the risk
score and says how many points each signal family is responsible for.

Confidence measures corroboration rather than magnitude: how many signal families
independently support the flag and how many anomaly detectors agree.
"""

from __future__ import annotations

from itertools import combinations
from math import factorial
from typing import Any

import numpy as np

SIGNALS = ("anomaly", "pattern", "taint", "network")
WALLET_WEIGHTS = np.array([0.55, 0.7, 0.9, 0.35])
TRANSACTION_WEIGHTS = np.array([0.5, 0.75, 0.85, 0.3])
LEVELS = (("CRITICAL", 80.0), ("HIGH", 60.0), ("MEDIUM", 35.0))


def level_for(score: float) -> str:
    for level, threshold in LEVELS:
        if score >= threshold:
            return level
    return "LOW"


def anomaly_signal(ensemble_percentile: np.ndarray) -> np.ndarray:
    return np.clip((np.asarray(ensemble_percentile, dtype=float) - 0.90) / 0.10, 0.0, 1.0)


def wallet_network_signal(nonstandard_ratio: np.ndarray, broadcasts: np.ndarray, unique_asns: np.ndarray) -> np.ndarray:
    ports = np.clip((np.asarray(nonstandard_ratio, float) - 0.1) / 0.5, 0, 1) * np.clip(np.asarray(broadcasts, float) / 3, 0, 1)
    diversity = np.clip((np.asarray(unique_asns, float) - 3) / 7, 0, 1)
    return 1 - (1 - ports) * (1 - diversity)


def transaction_network_signal(nonstandard_port: np.ndarray, ip_wallet_count: np.ndarray) -> np.ndarray:
    # A relaying IP shared by many unrelated addresses is typical of anonymising infrastructure.
    return 1 - (1 - 0.6 * np.asarray(nonstandard_port, float)) * (1 - 0.4 * np.clip((np.asarray(ip_wallet_count, float) - 8) / 30, 0, 1))


def fuse(signals: np.ndarray, weights: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Noisy-OR risk (0-100) and the exact Shapley contribution of each signal (rows x 4)."""
    signals = np.clip(np.asarray(signals, dtype=float), 0, 1)
    weights = np.broadcast_to(weights, signals.shape)
    keep = 1 - weights * signals  # probability that signal k does *not* raise the risk
    n = signals.shape[1]

    def value(members: tuple[int, ...]) -> np.ndarray:
        if not members:
            return np.zeros(signals.shape[0])
        return 100 * (1 - np.prod(keep[:, list(members)], axis=1))

    contributions = np.zeros_like(signals)
    for k in range(n):
        others = [index for index in range(n) if index != k]
        for size in range(n):
            weight = factorial(size) * factorial(n - size - 1) / factorial(n)
            for subset in combinations(others, size):
                contributions[:, k] += weight * (value(tuple(sorted(subset + (k,)))) - value(subset))
    return value(tuple(range(n))), contributions


def confidence(weighted: np.ndarray, agreement: np.ndarray, is_seed: np.ndarray) -> np.ndarray:
    """0.2 base, +0.2 per corroborating signal family, +0.2 for full detector agreement."""
    corroborating = (np.asarray(weighted) >= 0.2).sum(axis=1)
    value = np.minimum(0.99, 0.2 + 0.2 * corroborating + 0.2 * np.asarray(agreement, float))
    return np.where(np.asarray(is_seed, bool), 1.0, value)


def _short(value: str) -> str:
    return value if len(value) <= 14 else f"{value[:8]}…{value[-4:]}"


def _top_percent(percentile: float) -> str:
    share = max(0.0, 1 - percentile) * 100
    return "the highest value" if share < 0.05 else f"top {share:.1f}%"


PATTERN_ROLE_TEXT = {
    "chain_hop": "Hop wallet in a {hops}-hop peeling chain",
    "peel_recipient": "Received a peel from a {hops}-hop peeling chain",
    "layering_source": "Source of a fan-out/fan-in layering structure",
    "layering_intermediate": "Intermediate in a layering structure ({converged} branches reconverge)",
    "layering_sink": "Sink where {converged} layering branches reconverge",
    "pass_through": "Rapid pass-through wallet (forwards {ratio:.0%} after ~{minutes:.0f} min)",
    "pass_through_recipient": "Receives funds forwarded by a rapid pass-through wallet",
    "coinjoin_input": "Joined a CoinJoin ({equal} equal outputs of {denomination:g} BTC)",
    "coinjoin_output": "Received an equal-value CoinJoin output ({denomination:g} BTC)",
    "coinjoin_change": "Received CoinJoin change",
}


def _pattern_text(role: str, pattern: dict[str, Any]) -> str:
    details = pattern.get("details", {})
    values = {
        "hops": details.get("hops", len(pattern.get("txids", []))),
        "converged": details.get("converged_branches", 0),
        "ratio": details.get("median_forward_ratio", 0.0),
        "minutes": details.get("median_minutes", 0.0),
        "equal": details.get("equal_outputs", 0),
        "denomination": details.get("denomination", 0.0),
    }
    return PATTERN_ROLE_TEXT.get(role, f"Part of a {pattern.get('type', 'pattern')}").format(**values)


def build_reasons(row: dict[str, Any], patterns: dict[str, dict[str, Any]], attributions: list[dict[str, Any]],
                  taint: dict[str, Any] | None, feature_labels: dict[str, str], kind: str = "wallet") -> list[dict[str, Any]]:
    """Human-readable reasons, each carrying the risk points it accounts for."""
    contributions = row["contributions"]
    signals = row["signals"]
    reasons: list[dict[str, Any]] = []
    if row.get("is_seed"):
        reasons.append({"category": "seed", "label": "Known illicit seed wallet", "detail": "Supplied by the investigator as a known illicit address.", "contribution": round(contributions["taint"], 2)})
    elif taint and signals["taint"] > 0:
        if taint.get("reason") == "co_owned":
            label = f"Co-owned with seed wallet {_short(taint['seed'])}"
            detail = "Spent together with the seed as inputs of one transaction (common-input ownership)."
        elif taint.get("reason") == "upstream":
            label = f"Sent {taint['fraction']:.0%} of its outgoing value into a laundering structure"
            detail = f"Funds reach pattern wallet {_short(taint['target'])} within {taint['hops']} hop(s) (upstream exposure {taint['score']:.2f})."
        else:
            label = f"{signals['taint']:.0%} of received value traces back to seed {_short(taint['seed'])}"
            detail = f"{taint['tainted_value']:.4f} BTC of {taint['received_value']:.4f} BTC received is downstream of the seed, {taint['hops']} hop(s) away."
        reasons.append({"category": "taint", "label": label, "detail": detail, "contribution": round(contributions["taint"], 2)})
    refs = sorted(row.get("pattern_refs", []), key=lambda ref: -ref[2])
    if refs and contributions["pattern"] > 0:
        total_weight = sum(ref[2] for ref in refs[:3]) or 1.0
        for pattern_id, role, weight in refs[:3]:
            pattern = patterns.get(pattern_id, {})
            label = _pattern_text(role, pattern) if kind == "wallet" else pattern.get("summary", pattern_id)
            reasons.append({"category": "pattern", "label": label, "detail": pattern.get("summary", ""), "pattern_id": pattern_id,
                            "contribution": round(contributions["pattern"] * weight / total_weight, 2)})
    if signals["anomaly"] > 0:
        flagged = int(round(row.get("anomaly_agreement", 0) * 3))
        drivers = ", ".join(f"{feature_labels.get(item['feature'], item['feature'])} = {item['value']:g} ({_top_percent(item['percentile'])})" for item in attributions[:3])
        reasons.append({"category": "anomaly", "label": f"Statistical outlier ({flagged} of 3 detectors, {_top_percent(row['anomaly_percentile'])})",
                        "detail": f"Main drivers: {drivers}." if drivers else "Unusual combination of features.", "contribution": round(contributions["anomaly"], 2)})
    if signals["network"] > 0:
        if kind == "wallet":
            parts = []
            if row.get("nonstandard_port_ratio", 0) > 0.1:
                parts.append(f"{row['nonstandard_port_ratio']:.0%} of its spends relayed on non-standard ports")
            if row.get("unique_asns", 0) > 3:
                parts.append(f"spent from {row['unique_asns']} ASNs in {row.get('unique_countries', 0)} countries")
            label = "; ".join(parts).capitalize() or "Unusual network footprint"
        else:
            parts = []
            if row.get("nonstandard_port"):
                parts.append(f"relayed to non-standard port {row.get('dst_port')}")
            if row.get("ip_wallet_count", 0) > 8:
                parts.append(f"relaying IP used by {row['ip_wallet_count']} addresses")
            label = "; ".join(parts).capitalize() or "Unusual relay"
        reasons.append({"category": "network", "label": label, "detail": "Network-layer observations correlated with this activity.", "contribution": round(contributions["network"], 2)})
    if kind == "wallet" and row.get("entity_size", 1) > 1:
        reasons.append({"category": "entity", "label": f"One of {row['entity_size']} addresses controlled by entity {row['entity_id']}",
                        "detail": "Linked by common-input ownership (CoinJoins excluded).", "contribution": 0.0})
    reasons.sort(key=lambda reason: -reason["contribution"])
    return reasons


__all__ = ["SIGNALS", "WALLET_WEIGHTS", "TRANSACTION_WEIGHTS", "level_for", "anomaly_signal", "wallet_network_signal",
           "transaction_network_signal", "fuse", "confidence", "build_reasons"]
