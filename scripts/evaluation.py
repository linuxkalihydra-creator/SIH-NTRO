"""Measure one analysed dataset against the synthetic ground truth.

Shared by ``scripts/evaluate_models.py`` (one dataset, full report) and
``scripts/benchmark.py`` (many independently generated worlds). The pipeline
itself never reads the ground truth; only this module does.

Two definitions of a "true lead" are reported side by side:

* **strict** - the address is owned by an illicit actor;
* **investigative** - the same, plus exchange deposit addresses an illicit actor
  cashes out through. The exchange owns those addresses, so they are "licit" in
  the strict sense, but they are exactly what an investigator takes to the
  exchange with a legal request.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

HIGH, MEDIUM = 60.0, 35.0


def _pairs(count: int) -> int:
    return count * (count - 1) // 2


def wallet_metrics(investigation: Any, truth: dict[str, Any]) -> dict[str, Any]:
    """Ranking and threshold metrics for wallet leads (seed wallets are given, so they are excluded)."""
    wallets = investigation.wallets
    labels = truth["wallets"]
    addresses = list(wallets.index)
    risk = wallets["risk_score"].to_numpy()
    evaluated = ~wallets["is_seed"].to_numpy()
    strict = np.array([labels[address]["illicit"] for address in addresses])
    cashout = np.array([bool(labels[address].get("cashout_for")) for address in addresses])
    result: dict[str, Any] = {"wallets": len(addresses), "illicit": int(strict[evaluated].sum()), "cash_out_points": int(cashout[evaluated].sum()),
                              "seeds": int((~evaluated).sum())}
    for name, target in (("strict", strict), ("investigative", strict | cashout)):
        y, scores = target[evaluated], risk[evaluated]
        order = np.argsort(-scores, kind="stable")
        high, medium = scores >= HIGH, scores >= MEDIUM
        result[name] = {
            "roc_auc": round(float(roc_auc_score(y, scores)), 4), "average_precision": round(float(average_precision_score(y, scores)), 4),
            **{f"precision_at_{k}": round(float(y[order][:k].mean()), 4) for k in (25, 50, 100, 200, 400)},
            "high_count": int(high.sum()), "high_precision": round(float(y[high].mean()), 4) if high.any() else 0.0,
            "high_recall": round(float(y[high].sum() / max(y.sum(), 1)), 4),
            "medium_count": int(medium.sum()), "medium_precision": round(float(y[medium].mean()), 4) if medium.any() else 0.0,
            "medium_recall": round(float(y[medium].sum() / max(y.sum(), 1)), 4),
        }
    for name, column in (("anomaly_only", "signal_anomaly"), ("pattern_only", "signal_pattern"), ("taint_only", "signal_taint"), ("network_only", "signal_network")):
        values = wallets[column].to_numpy()[evaluated]
        result[name] = {"roc_auc": round(float(roc_auc_score(strict[evaluated], values)), 4),
                        "average_precision": round(float(average_precision_score(strict[evaluated], values)), 4)}

    roles = np.array([f"{labels[address]['entity_kind']} / {labels[address]['role']}" for address in addresses])
    by_role = []
    for role, _ in Counter(roles[evaluated & strict]).most_common(16):
        mask = (roles == role) & evaluated
        by_role.append({"role": role, "addresses": int(mask.sum()), "medium_or_above": round(float((risk[mask] >= MEDIUM).mean()), 3),
                        "high_or_above": round(float((risk[mask] >= HIGH).mean()), 3)})
    mask = cashout & evaluated
    if mask.any():
        by_role.append({"role": "exchange deposit used by an illicit actor (cash-out point)", "addresses": int(mask.sum()),
                        "medium_or_above": round(float((risk[mask] >= MEDIUM).mean()), 3), "high_or_above": round(float((risk[mask] >= HIGH).mean()), 3)})
    victims = {address for record in investigation.records if record.get("behavior_type") == "RANSOMWARE" for address in record["input_addresses"]
               if labels[address]["role"] == "user_wallet"}
    mask = np.array([address in victims for address in addresses]) & evaluated
    if mask.any():
        by_role.append({"role": "ransomware victims (licit users who paid)", "addresses": int(mask.sum()),
                        "medium_or_above": round(float((risk[mask] >= MEDIUM).mean()), 3), "high_or_above": round(float((risk[mask] >= HIGH).mean()), 3)})
    result["by_role"] = by_role
    invest = (strict | cashout)
    false_medium = evaluated & ~invest & (risk >= MEDIUM)
    false_high = evaluated & ~invest & (risk >= HIGH)
    result["false_positive_roles_medium"] = {str(role): count for role, count in Counter(roles[false_medium]).most_common(6)}
    result["false_positive_roles_high"] = {str(role): count for role, count in Counter(roles[false_high]).most_common(6)}
    return result


def entity_metrics(investigation: Any, truth: dict[str, Any]) -> dict[str, Any]:
    """How pure and how complete the ownership clusters (entities) and operation clusters (actors) are."""
    labels = truth["wallets"]
    clustering = investigation.clustering
    result: dict[str, Any] = {}

    def score(groups: list[list[str]], label_of) -> dict[str, Any]:
        merged = [group for group in groups if len(group) > 1]
        impure = [group for group in merged if len({label_of(address) for address in group}) > 1]
        misplaced = sum(len(group) - Counter(label_of(address) for address in group).most_common(1)[0][1] for group in impure)
        linked = sum(_pairs(len(group)) for group in groups)
        correct = sum(sum(_pairs(count) for count in Counter(label_of(address) for address in group).values()) for group in groups)
        actual = sum(_pairs(count) for count in Counter(label_of(address) for group in groups for address in group).values())
        return {"groups": len(groups), "multi_address_groups": len(merged), "impure_groups": len(impure), "misplaced_addresses": int(misplaced),
                "pair_precision": round(correct / max(linked, 1), 5), "pair_recall": round(correct / max(actual, 1), 4)}

    result["entities"] = score(list(clustering.members.values()), lambda address: labels[address]["entity_id"])
    actor_groups: dict[str, list[str]] = {}
    for address, actor in zip(investigation.wallets.index, investigation.wallets["actor_id"]):
        actor_groups.setdefault(actor, []).append(address)
    # An operator and the mules it runs from its own infrastructure are one operation.
    result["actors"] = score(list(actor_groups.values()), lambda address: labels[address].get("group") or labels[address]["entity_id"])
    result["entity_links"] = dict(clustering.link_counts)
    operations = {}
    for name in sorted({item["group"] for item in labels.values() if item.get("group")}):
        members = [address for address in investigation.wallets.index if labels[address].get("group") == name]
        largest = Counter(investigation.wallets.loc[members, "actor_id"]).most_common(1)[0][1] if members else 0
        operations[name] = {"addresses": len(members), "actors": int(investigation.wallets.loc[members, "actor_id"].nunique()) if members else 0,
                            "largest_actor_share": round(largest / max(len(members), 1), 3)}
    result["operations"] = operations
    return result


def origin_metrics(investigation: Any, truth: dict[str, Any]) -> dict[str, Any]:
    """How often the inferred origin IP is the node that really created the transaction."""
    actual = truth.get("origin_ips") or {}
    if not actual:
        return {}
    flows, origins = investigation.flows, investigation.network.origins
    total = attributed = first_seen_right = attributed_right = 0
    for tx in flows.txs:
        if tx.txid not in actual:
            continue
        total += 1
        origin = origins[tx.index]
        right = origin.ip == actual[tx.txid]
        first_seen_right += right
        if origin.attributed:
            attributed += 1
            attributed_right += right
    return {"transactions": total, "first_seen_accuracy": round(first_seen_right / max(total, 1), 4),
            "attributed_share": round(attributed / max(total, 1), 4), "attributed_precision": round(attributed_right / max(attributed, 1), 4),
            "wrong_attributions": attributed - attributed_right, "withheld_wrong": (total - attributed) - (first_seen_right - attributed_right)}


def pattern_metrics(investigation: Any, truth: dict[str, Any]) -> list[dict[str, Any]]:
    """Transaction-level precision and recall of each pattern detector against the generated structures."""
    actual: dict[str, set[str]] = {}
    for pattern in truth.get("patterns", []):
        actual.setdefault(pattern["type"], set()).update(pattern["txids"])
    rows = []
    for kind in ("peeling_chain", "layering", "rapid_passthrough", "coinjoin"):
        predicted: set[str] = set()
        for pattern in investigation.patterns.values():
            if pattern["type"] == kind:
                predicted.update(pattern["details"].get("forward_txids") or pattern["txids"])
        wanted = actual.get(kind, set())
        hits = len(predicted & wanted)
        rows.append({"detector": kind, "detected_transactions": len(predicted), "generated_transactions": len(wanted),
                     "precision": round(hits / max(len(predicted), 1), 4), "recall": round(hits / max(len(wanted), 1), 4)})
    return rows


def evaluate(investigation: Any, truth: dict[str, Any]) -> dict[str, Any]:
    return {"wallets": wallet_metrics(investigation, truth), "entities": entity_metrics(investigation, truth),
            "origin": origin_metrics(investigation, truth), "patterns": pattern_metrics(investigation, truth),
            "transactions": {key: value for key, value in investigation.overview["evaluation"].items() if key != "notes"}}


__all__ = ["evaluate", "wallet_metrics", "entity_metrics", "origin_metrics", "pattern_metrics", "HIGH", "MEDIUM"]
