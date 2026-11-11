"""Fuse detector outputs into a risk score, a confidence and ranked reasons.

Four signal families, each in [0, 1], feed the score:

* ``anomaly`` - how unusual the wallet's actor behaves (anomaly-ensemble percentile, top 10 % only);
* ``pattern`` - the strongest laundering structure it takes part in, weighted by its role, or
  inherited from the wallets it is operated with, or the fact that it is where dirty funds
  entered a service (a cash-out point);
* ``taint``   - value-weighted taint from seed wallets, proceeds of a detected structure, or
  exposure of funds it sent into one;
* ``network`` - non-standard relay ports and rotation across networks, and IPs it shares with
  wallets that are already flagged.

They are combined with a noisy-OR, ``risk = 100 * (1 - prod(1 - w_k * s_k))``: any single strong
signal raises the risk, and agreeing signals push it towards 100 without ever exceeding it.

**Anomaly needs corroboration.** Being statistically unusual is weak evidence of crime: exchanges,
merchants and privacy-minded users are all outliers. The anomaly weight therefore starts at
``ANOMALY_ALONE`` and rises to its full value only as far as the strongest *other* signal supports
it. An odd wallet with nothing else against it stays a low-priority lead; the same oddness on a
wallet tied to a laundering structure counts in full.

Because there are only four signals, the exact Shapley value of each one is cheap to compute, so
``contributions`` always sums to the risk score and says how many points each family is responsible
for (the synergy between anomaly and its corroborating signal is split between them).

Confidence measures corroboration rather than magnitude: how many families independently support
the flag, whether the evidence is the wallet's own or inherited, and how many detectors agree.
"""

from __future__ import annotations

from itertools import combinations
from math import factorial
from typing import Any

import numpy as np

SIGNALS = ("anomaly", "pattern", "taint", "network")
ANOMALY = SIGNALS.index("anomaly")
# Network oddities are the weakest evidence on their own: privacy tools are legal and common. They count for more as
# corroboration (they open the anomaly gate) and through IPs shared with flagged wallets than through this weight.
WALLET_WEIGHTS = np.array([0.6, 0.7, 0.9, 0.25])
TRANSACTION_WEIGHTS = np.array([0.55, 0.75, 0.85, 0.35])
# Weight of the anomaly signal when nothing else supports it.
ANOMALY_ALONE = 0.25
LEVELS = (("CRITICAL", 80.0), ("HIGH", 60.0), ("MEDIUM", 35.0))

# How much evidence passes between wallets that are operated together.
ENTITY_INHERITANCE = 0.8   # same owner (co-spend, change, laundering structure)
ACTOR_INHERITANCE = 0.6    # operated together (shared IP / direct payment / same deposit account)
UPSTREAM_WEIGHT = 0.9      # funds sent into a structure, relative to funds received from a seed
PROCEEDS_WEIGHT = 0.7      # funds received from a detected structure, relative to seed taint
CASH_OUT_WEIGHT = 0.6      # dirty funds arriving at a service-like entity
SERVICE_DISCOUNT = 0.7     # how strongly service-likeness discounts behaviour-based signals
NETWORK_SEED = 0.6         # how much rotation/port evidence counts as "flagged" for IP association


def level_for(score: float) -> str:
    for level, threshold in LEVELS:
        if score >= threshold:
            return level
    return "LOW"


def anomaly_signal(ensemble_percentile: np.ndarray) -> np.ndarray:
    return np.clip((np.asarray(ensemble_percentile, dtype=float) - 0.90) / 0.10, 0.0, 1.0)


def wallet_network_signal(nonstandard_ratio: np.ndarray, broadcasts: np.ndarray, unique_asns: np.ndarray) -> np.ndarray:
    """Non-standard relay ports (needs a few broadcasts to count) or rotation across more than three networks."""
    ports = np.clip((np.asarray(nonstandard_ratio, float) - 0.1) / 0.5, 0, 1) * np.clip(np.asarray(broadcasts, float) / 3, 0, 1)
    diversity = np.clip((np.asarray(unique_asns, float) - 3) / 7, 0, 1)
    return 1 - (1 - ports) * (1 - diversity)


def transaction_network_signal(nonstandard_port: np.ndarray, ip_wallet_count: np.ndarray, ip_flagged_share: np.ndarray | None = None) -> np.ndarray:
    # A relaying IP shared by many unrelated addresses is typical of anonymising infrastructure;
    # an IP whose traffic mostly comes from flagged wallets is the operator's.
    value = 1 - (1 - 0.6 * np.asarray(nonstandard_port, float)) * (1 - 0.4 * np.clip((np.asarray(ip_wallet_count, float) - 8) / 30, 0, 1))
    if ip_flagged_share is not None:
        value = 1 - (1 - value) * (1 - 0.8 * np.clip(np.asarray(ip_flagged_share, float), 0, 1))
    return value


def group_inherit(values: np.ndarray, codes: np.ndarray, threshold: float = 0.3, full_coverage: float = 0.25) -> tuple[np.ndarray, np.ndarray]:
    """Evidence a group of wallets shares, per row, and the row that supplies it.

    The group's evidence is its strongest member's value scaled by *coverage*: the share of members
    with evidence of their own, reaching 1 when a quarter of the group is affected. A handful of
    tainted deposits therefore never condemns an exchange's thousands of addresses, while a small
    operator cluster in which several wallets are implicated passes the evidence to the rest.
    """
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return values, np.zeros(0, dtype=np.int64)
    groups = int(codes.max()) + 1
    size = np.bincount(codes, minlength=groups)
    order = np.lexsort((-values, codes))
    first = order[np.searchsorted(codes[order], np.arange(groups))]
    affected = np.bincount(codes, weights=(values >= threshold).astype(float), minlength=groups)
    coverage = np.clip(affected / np.maximum(full_coverage * size, 1.0), 0.0, 1.0)
    return (values[first] * coverage)[codes], first[codes]


def fuse(signals: np.ndarray, weights: np.ndarray, anomaly_alone: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Noisy-OR risk (0-100) and the exact Shapley contribution of each signal (rows x 4).

    With ``anomaly_alone`` set, the anomaly weight inside any coalition is
    ``anomaly_alone + (w_anomaly - anomaly_alone) * max(other signals in the coalition)``.
    """
    signals = np.clip(np.asarray(signals, dtype=float), 0, 1)
    weights = np.broadcast_to(np.asarray(weights, dtype=float), signals.shape)
    rows, n = signals.shape
    cache: dict[tuple[int, ...], np.ndarray] = {(): np.zeros(rows)}

    def value(members: tuple[int, ...]) -> np.ndarray:
        found = cache.get(members)
        if found is not None:
            return found
        keep = np.ones(rows)
        for k in members:
            weight = weights[:, k]
            if k == ANOMALY and anomaly_alone is not None:
                others = [signals[:, j] for j in members if j != ANOMALY]
                support = np.max(others, axis=0) if others else 0.0
                weight = anomaly_alone + np.maximum(weight - anomaly_alone, 0.0) * support
            keep = keep * (1 - weight * signals[:, k])
        found = cache[members] = 100 * (1 - keep)
        return found

    contributions = np.zeros_like(signals)
    for k in range(n):
        others = [index for index in range(n) if index != k]
        for size in range(n):
            share = factorial(size) * factorial(n - size - 1) / factorial(n)
            for subset in combinations(others, size):
                contributions[:, k] += share * (value(tuple(sorted(subset + (k,)))) - value(subset))
    return value(tuple(range(n))), contributions


def confidence(weighted: np.ndarray, agreement: np.ndarray, is_seed: np.ndarray, inherited: np.ndarray | None = None) -> np.ndarray:
    """0.2 base, +0.2 per corroborating signal family, +0.2 for full detector agreement, -0.1 when the main evidence is inherited."""
    corroborating = (np.asarray(weighted) >= 0.2).sum(axis=1)
    value = 0.2 + 0.2 * corroborating + 0.2 * np.asarray(agreement, float)
    if inherited is not None:
        value = value - 0.1 * np.asarray(inherited, float)
    return np.where(np.asarray(is_seed, bool), 1.0, np.clip(value, 0.05, 0.99))


def _short(value: str) -> str:
    value = str(value)
    return value if len(value) <= 14 else f"{value[:8]}…{value[-4:]}"


def count_of(number: int | float, singular: str, plural: str | None = None) -> str:
    """``1 hop`` / ``3 hops`` / ``2 entities``."""
    number = int(number)
    return f"{number} {singular if number == 1 else (plural or singular + 's')}"


def top_percent(percentile: float) -> str:
    """``top 2.4%`` for a 0-1 percentile rank; ``top <0.1%`` at the very top."""
    share = max(0.0, 1 - percentile) * 100
    return "top <0.1%" if share < 0.1 else f"top {share:.1f}%"


PATTERN_ROLE_TEXT = {
    "chain_hop": "Hop wallet in a {hops}-hop peeling chain",
    "chain_exit": "Holds the remainder of a {hops}-hop peeling chain",
    "peel_recipient": "Received a peel from a {hops}-hop peeling chain",
    "layering_source": "Source of a fan-out/fan-in layering structure",
    "layering_intermediate": "Intermediate in a layering structure ({converged} branches reconverge)",
    "layering_sink": "Sink where {converged} layering branches reconverge",
    "layering_change": "Kept the leftover of a layering hop",
    "layering_exit": "Received the funds a layering structure reassembled",
    "pass_through": "Rapid pass-through wallet (forwards {ratio:.0%} after ~{minutes:.0f} min)",
    "pass_through_change": "Kept the dust left by a rapid pass-through wallet",
    "pass_through_recipient": "Receives funds forwarded by a rapid pass-through wallet",
    "coinjoin_input": "Joined a CoinJoin ({equal} equal outputs of {denomination:g} BTC)",
    "coinjoin_output": "Received an equal-value CoinJoin output ({denomination:g} BTC)",
    "coinjoin_change": "Received CoinJoin change",
}


def pattern_text(role: str, pattern: dict[str, Any]) -> str:
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


def _taint_reason(taint: dict[str, Any], share: float) -> tuple[str, str]:
    kind = taint.get("kind")
    if kind == "co_owned":
        return (f"Co-owned with seed wallet {_short(taint['seed'])}",
                "Belongs to the same entity as the seed (linked by co-spending, change or a laundering structure).")
    if kind == "downstream":
        return (f"{share:.0%} of received value traces back to seed {_short(taint['seed'])}",
                f"{taint['tainted_value']:.4f} BTC of {taint['received_value']:.4f} BTC received is downstream of the seed, {count_of(taint['hops'], 'hop')} away.")
    if kind == "proceeds":
        return (f"{taint['score']:.0%} of received value came out of a laundering structure",
                f"{taint['tainted_value']:.4f} BTC of {taint['received_value']:.4f} BTC received was paid on from structure wallet {_short(taint['seed'])}, "
                f"{count_of(taint['hops'], 'hop')} away. No seed was needed to find this.")
    if kind == "upstream":
        return (f"Sent {taint['fraction']:.0%} of its outgoing value into a laundering structure",
                f"Funds reach pattern wallet {_short(taint['target'])} within {count_of(taint['hops'], 'hop')} (upstream exposure {taint['score']:.2f}).")
    if kind in ("entity", "actor"):
        how = "the same entity (same owner)" if kind == "entity" else "the same actor (operated together)"
        return (f"Operated with tainted wallet {_short(taint['source'])}",
                f"Part of {how} as {_short(taint['source'])}, whose funds are tainted; {taint.get('affected', 0)} of {taint.get('size', 0)} wallets in the group carry such evidence.")
    return "Linked to tainted funds", ""


def build_reasons(row: dict[str, Any], patterns: dict[str, dict[str, Any]], attributions: list[dict[str, Any]],
                  taint: dict[str, Any] | None, feature_labels: dict[str, str], kind: str = "wallet") -> list[dict[str, Any]]:
    """Human-readable reasons, each carrying the risk points it accounts for."""
    contributions = row["contributions"]
    signals = row["signals"]
    reasons: list[dict[str, Any]] = []

    # ── taint ───────────────────────────────────────────────────────────────
    if row.get("is_seed"):
        reasons.append({"category": "seed", "label": "Known illicit seed wallet", "detail": "Supplied by the investigator as a known illicit address.",
                        "contribution": round(contributions["taint"], 2)})
    elif taint and signals["taint"] > 0:
        label, detail = _taint_reason(taint, signals["taint"])
        reasons.append({"category": "taint", "label": label, "detail": detail, "contribution": round(contributions["taint"], 2)})

    # ── pattern ─────────────────────────────────────────────────────────────
    if contributions["pattern"] > 0 and signals["pattern"] > 0:
        source = row.get("pattern_evidence") or {}
        refs = sorted(row.get("pattern_refs", []), key=lambda ref: -ref[2])
        if source.get("kind") == "cash_out":
            reasons.append({"category": "pattern", "label": "Cash-out point: dirty funds entering a service",
                            "detail": f"Belongs to a service-like entity ({source.get('counterparties', 0)} counterparties) and {source.get('share', 0):.0%} of what it received is "
                                      "seed-tainted or came out of a laundering structure. The service behind it can identify the depositor.",
                            "contribution": round(contributions["pattern"], 2)})
        elif source.get("kind") in ("entity", "actor"):
            how = "Same entity (same owner)" if source["kind"] == "entity" else "Same actor (operated together)"
            pattern = patterns.get(source.get("pattern_id", ""), {})
            reasons.append({"category": "pattern", "label": f"Operated with {pattern_text(source.get('role', ''), pattern).lower()} {_short(source['source'])}",
                            "detail": f"{how} as {_short(source['source'])}; {source.get('affected', 0)} of {source.get('size', 0)} wallets in the group are inside laundering structures.",
                            "pattern_id": source.get("pattern_id"), "contribution": round(contributions["pattern"], 2)})
        elif refs:
            total_weight = sum(ref[2] for ref in refs[:3]) or 1.0
            for pattern_id, role, weight in refs[:3]:
                pattern = patterns.get(pattern_id, {})
                if kind == "wallet":
                    label, detail = pattern_text(role, pattern), pattern.get("summary", "")
                else:  # a transaction is part of the structure; the summary is the headline
                    label = pattern.get("summary", pattern_id)
                    detail = (f"One of {count_of(len(pattern.get('txids', [])), 'transaction')} of {pattern_id}, which involves "
                              f"{count_of(len(pattern.get('roles', {})), 'wallet')} (strength {pattern.get('strength', 0):.2f}).")
                reasons.append({"category": "pattern", "label": label, "detail": detail, "pattern_id": pattern_id,
                                "contribution": round(contributions["pattern"] * weight / total_weight, 2)})

    # ── anomaly ─────────────────────────────────────────────────────────────
    if signals["anomaly"] > 0:
        detectors = row.get("detectors_total", 3)
        flagged = int(round(row.get("anomaly_agreement", 0) * detectors))
        drivers = ", ".join(f"{feature_labels.get(item['feature'], item['feature'])} = {item['value']:g} ({top_percent(item['percentile'])})" for item in attributions[:3])
        scope = row.get("anomaly_scope")
        subject = f"Behaviour of its actor ({scope}) is a statistical outlier" if scope else "Statistical outlier"
        detail = f"Main drivers: {drivers}." if drivers else "Unusual combination of features."
        if contributions["anomaly"] < 0.5 * signals["anomaly"] * 100 * 0.6 and not any(signals[name] > 0.2 for name in ("pattern", "taint", "network")):
            detail += " Counted at a reduced weight because no other evidence corroborates it."
        reasons.append({"category": "anomaly", "label": f"{subject} ({flagged} of {detectors} detectors, {top_percent(row['anomaly_percentile'])})",
                        "detail": detail, "contribution": round(contributions["anomaly"], 2)})

    # ── network ─────────────────────────────────────────────────────────────
    if signals["network"] > 0:
        if kind == "wallet":
            own = row.get("network_evidence") or {}
            association = row.get("association_evidence") or {}
            parts = []
            if own.get("nonstandard_port_share", 0) > 0.1 and own.get("broadcasts", 0) >= 1:
                parts.append(f"{own['nonstandard_port_share']:.0%} of its actor's broadcasts used non-standard ports")
            if own.get("asns", 0) > 3:
                parts.append(f"broadcast from {own['asns']} networks in {own.get('countries', 0)} countries ({own.get('ips', 0)} IPs)")
            labels, details = [], []
            if parts:
                labels.append("; ".join(parts))
                details.append(f"Measured over {own.get('broadcasts', 0)} broadcasts by the {count_of(own.get('wallets', 1), 'wallet')} operated together.")
            if association.get("signal", 0) > 0.05:
                labels.append(f"shares IP {association['ip']} with flagged wallets")
                details.append(f"{association['ip_flagged_share']:.0%} of the other traffic from {association['ip']} comes from flagged wallets "
                               f"({association['flagged_wallets_on_ip']} of {association['wallets_on_ip']} wallets on it); "
                               f"{association['share_of_spends']:.0%} of this wallet's spends used it.")
            label = "; ".join(labels) or "Unusual network footprint"
            detail = " ".join(details) or "Network-layer observations correlated with this activity."
            reasons.append({"category": "network", "label": label[0].upper() + label[1:], "detail": detail, "contribution": round(contributions["network"], 2)})
        else:
            parts = []
            if row.get("nonstandard_port"):
                parts.append(f"relayed on non-standard port {row.get('service_port') or row.get('dst_port')}")
            if row.get("ip_flagged_share", 0) > 0.2:
                parts.append(f"origin IP {row.get('src_ip')} mostly serves flagged wallets ({row['ip_flagged_share']:.0%} of its traffic)")
            if row.get("ip_wallet_count", 0) > 8:
                parts.append(f"relaying IP used by {row['ip_wallet_count']} addresses")
            label = "; ".join(parts) or "Unusual relay"
            reasons.append({"category": "network", "label": label[0].upper() + label[1:],
                            "detail": f"First seen from {row.get('src_ip') or 'an unknown IP'} (origin confidence {row.get('origin_confidence', 0):.0%}).",
                            "contribution": round(contributions["network"], 2)})

    # ── context (no points) ─────────────────────────────────────────────────
    if kind == "wallet":
        if row.get("entity_size", 1) > 1:
            reasons.append({"category": "entity", "label": f"One of {row['entity_size']} addresses controlled by entity {row['entity_id']}",
                            "detail": row.get("entity_link") or "Linked by ownership heuristics (CoinJoins excluded).", "contribution": 0.0})
        if row.get("service_score", 0) >= 0.5:
            reasons.append({"category": "entity", "label": "Belongs to a service-like entity (exchange, merchant or processor)",
                            "detail": f"Its entity deals with {row.get('counterparties', 0)} distinct counterparties, so behaviour-based signals are discounted "
                                      "and taint is not traced through it.", "contribution": 0.0})
    reasons.sort(key=lambda reason: -reason["contribution"])
    return reasons


__all__ = ["SIGNALS", "WALLET_WEIGHTS", "TRANSACTION_WEIGHTS", "ANOMALY_ALONE", "ENTITY_INHERITANCE", "ACTOR_INHERITANCE", "UPSTREAM_WEIGHT",
           "PROCEEDS_WEIGHT", "CASH_OUT_WEIGHT", "SERVICE_DISCOUNT", "NETWORK_SEED", "level_for", "anomaly_signal", "wallet_network_signal",
           "transaction_network_signal", "group_inherit", "fuse", "confidence", "build_reasons", "pattern_text", "count_of", "top_percent"]
