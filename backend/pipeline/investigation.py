"""One analysed dataset: fitted models, scores, and the queries the API answers.

``Investigation.build`` runs every model on the records. The result can be saved
as a JSON snapshot and restored without refitting (``to_snapshot`` /
``from_snapshot``), and ``rescore`` re-runs only risk propagation and fusion when
the investigator changes the seed wallets.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from backend.correlation.flows import FlowIndex, build_flow_index
from backend.correlation.network import NetworkCorrelation, build_network_correlation
from backend.ml.anomaly import DETECTOR_DESCRIPTIONS, DETECTORS, AnomalyEnsemble
from backend.ml.entities import EntityClustering, common_input_ownership, detect_communities
from backend.ml.features import FEATURE_LABELS, TRANSACTION_MODEL_FEATURES, WALLET_MODEL_FEATURES, build_transaction_features, build_wallet_features
from backend.ml.patterns import ROLE_WEIGHTS, TYPE_WEIGHTS, detect_all
from backend.ml.propagation import PropagationResult, propagate_exposure, propagate_risk
from backend.ml.scoring import (SIGNALS, TRANSACTION_WEIGHTS, WALLET_WEIGHTS, anomaly_signal, build_reasons, confidence, fuse, level_for,
                                transaction_network_signal, wallet_network_signal)

SNAPSHOT_VERSION = 2
ATTRIBUTION_LIMIT = 1500
# Pattern roles that move the money; wallets in these roles seed upstream exposure.
HOP_ROLES = {"chain_hop", "layering_source", "layering_intermediate", "layering_sink", "pass_through"}
UPSTREAM_WEIGHT = 0.9
ILLICIT_LABELS = ("RANSOMWARE", "PEELING_CHAIN", "LAYERING_LIKE", "RAPID_TRANSFER", "HIGH_NETWORK_DIVERSITY")
LICIT_LABELS = ("NORMAL", "EXCHANGE_LIKE")
PATTERN_LABELS = {"peeling_chain": "PEELING_CHAIN", "layering": "LAYERING_LIKE", "rapid_passthrough": "RAPID_TRANSFER", "coinjoin": "MIXING_LIKE"}
PATTERN_NAMES = {"peeling_chain": "Peeling-chain detector", "layering": "Layering (fan-out/fan-in) detector",
                 "rapid_passthrough": "Rapid pass-through detector", "coinjoin": "CoinJoin detector"}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _short(value: str) -> str:
    return value if len(value) <= 14 else f"{value[:8]}…{value[-4:]}"


def _native(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return 0.0 if np.isnan(value) else float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


class Investigation:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.flows: FlowIndex
        self.network: NetworkCorrelation
        self.clustering: EntityClustering
        self.community_of: dict[str, int] = {}
        self.patterns: dict[str, dict[str, Any]] = {}
        self.wallets: pd.DataFrame = pd.DataFrame()
        self.transactions: pd.DataFrame = pd.DataFrame()
        self.attributions: dict[str, list[dict[str, Any]]] = {}
        self.tx_attributions: dict[str, list[dict[str, Any]]] = {}
        self.taint: dict[str, dict[str, Any]] = {}
        self.exposure: dict[str, dict[str, Any]] = {}
        self.propagation: PropagationResult | None = None
        self.seeds: list[str] = []
        self.overview: dict[str, Any] = {}

    # ── building ────────────────────────────────────────────────────────────
    @classmethod
    def build(cls, records: list[dict[str, Any]], seeds: list[str] | None = None, contamination: float = 0.05, random_state: int = 42) -> "Investigation":
        self = cls()
        self.records = records
        self.flows = build_flow_index(records)
        self.network = build_network_correlation(self.flows)
        found = detect_all(self.flows)
        coinjoin_txids = {pattern.txids[0] for pattern in found if pattern.type == "coinjoin"}
        self.clustering = common_input_ownership(self.flows, coinjoin_txids)
        communities = detect_communities(self.flows, self.clustering, random_state=random_state)
        sizes = {address: self.clustering.size(address) for address in self.clustering.entity_of}

        wallets = build_wallet_features(self.flows, self.network, sizes).set_index("wallet_id", drop=False)
        transactions = build_transaction_features(self.flows, self.network).set_index("txid", drop=False)
        wallets["entity_id"] = wallets["wallet_id"].map(self.clustering.entity_of)
        wallets["cluster_id"] = wallets["entity_id"].map(communities.community_of).fillna(-1).astype(int)
        self.community_of = {str(key): int(value) for key, value in communities.community_of.items()}

        wallet_model = AnomalyEnsemble(contamination, random_state).fit(wallets[WALLET_MODEL_FEATURES])
        tx_model = AnomalyEnsemble(contamination, random_state).fit(transactions[TRANSACTION_MODEL_FEATURES])
        for frame, model, columns, store in ((wallets, wallet_model, WALLET_MODEL_FEATURES, "attributions"), (transactions, tx_model, TRANSACTION_MODEL_FEATURES, "tx_attributions")):
            scores = model.score(frame[columns])
            for name in DETECTORS:
                frame[f"det_{name}"] = scores[f"{name} percentile"].to_numpy()
            frame["anomaly_percentile"] = scores["ensemble_percentile"].to_numpy()
            frame["anomaly_agreement"] = scores["agreement"].to_numpy()
            top = frame["anomaly_percentile"].sort_values(ascending=False).index[: min(ATTRIBUTION_LIMIT, max(1, int(len(frame) * 0.1)))]
            explained = model.attributions(frame.loc[top, columns])
            setattr(self, store, {str(key): value for key, value in zip(top, explained)})

        wallet_refs: dict[str, list[list[Any]]] = defaultdict(list)
        tx_refs: dict[str, list[list[Any]]] = defaultdict(list)
        for pattern in found:
            self.patterns[pattern.pattern_id] = _native(pattern.to_dict())
            for address, role in pattern.roles.items():
                wallet_refs[address].append([pattern.pattern_id, role, round(pattern.strength * ROLE_WEIGHTS.get(role, 0.3), 4)])
            for txid in pattern.txids:
                tx_refs[txid].append([pattern.pattern_id, pattern.type, round(pattern.strength * TYPE_WEIGHTS.get(pattern.type, 0.5), 4)])
        wallets["pattern_refs"] = [wallet_refs.get(address, []) for address in wallets.index]
        transactions["pattern_refs"] = [tx_refs.get(txid, []) for txid in transactions.index]
        tx_view = {tx.txid: tx for tx in self.flows.txs}
        transactions["timestamp"] = [tx_view[txid].time.isoformat() for txid in transactions.index]
        transactions["src_ip"] = [tx_view[txid].src_ip for txid in transactions.index]
        transactions["dst_ip"] = [tx_view[txid].dst_ip for txid in transactions.index]
        transactions["dst_port"] = [tx_view[txid].dst_port for txid in transactions.index]
        transactions["country"] = [tx_view[txid].country for txid in transactions.index]
        transactions["label"] = [tx_view[txid].label for txid in transactions.index]
        self.wallets, self.transactions = wallets, transactions
        self.rescore(seeds or [])
        return self

    # ── scoring (re-run when seeds change) ──────────────────────────────────
    def rescore(self, seeds: list[str]) -> list[str]:
        """Propagate risk from ``seeds`` and fuse all signals; returns seeds not present in the data."""
        requested = [str(seed).strip() for seed in seeds if str(seed).strip()]
        unknown = [seed for seed in dict.fromkeys(requested) if seed not in self.flows.first_seen]
        self.propagation = propagate_risk(self.flows, requested, self.clustering)
        self.seeds = self.propagation.seeds
        self.taint = {address: info.to_dict() for address, info in self.propagation.wallets.items()}
        wallets, transactions = self.wallets, self.transactions
        pattern_seeds = {address: max(ref[2] for ref in refs) for address, refs in zip(wallets.index, wallets["pattern_refs"])
                         if any(ref[1] in HOP_ROLES and ref[2] >= 0.5 for ref in refs)}
        self.exposure = {address: info.to_dict() for address, info in propagate_exposure(self.flows, pattern_seeds, self.clustering).items()}

        is_seed = wallets.index.isin(self.seeds)
        downstream = np.array([self.taint.get(address, {}).get("score", 0.0) for address in wallets.index])
        upstream = np.array([self.exposure.get(address, {}).get("score", 0.0) for address in wallets.index]) * UPSTREAM_WEIGHT
        taint = np.maximum(downstream, upstream)
        pattern = np.array([max((ref[2] for ref in refs), default=0.0) for refs in wallets["pattern_refs"]])
        signals = np.column_stack([
            anomaly_signal(wallets["anomaly_percentile"]), pattern, taint,
            wallet_network_signal(wallets["nonstandard_port_ratio"], wallets["outgoing_transaction_count"], wallets["unique_asns"]),
        ])
        weights = np.tile(WALLET_WEIGHTS, (len(wallets), 1))
        weights[is_seed, SIGNALS.index("taint")] = 1.0  # a known illicit wallet is maximal risk by definition
        self._apply(wallets, signals, weights, is_seed)

        tx_taint = np.array([self.propagation.transactions.get(txid, 0.0) for txid in transactions.index])
        tx_pattern = np.array([max((ref[2] for ref in refs), default=0.0) for refs in transactions["pattern_refs"]])
        tx_signals = np.column_stack([
            anomaly_signal(transactions["anomaly_percentile"]), tx_pattern, tx_taint,
            transaction_network_signal(transactions["nonstandard_port"], transactions["ip_wallet_count"]),
        ])
        self._apply(transactions, tx_signals, np.tile(TRANSACTION_WEIGHTS, (len(transactions), 1)), np.zeros(len(transactions), bool))
        self.overview = self._build_overview()
        return unknown

    @staticmethod
    def _apply(frame: pd.DataFrame, signals: np.ndarray, weights: np.ndarray, is_seed: np.ndarray) -> None:
        risk, contributions = fuse(signals, weights)
        for index, name in enumerate(SIGNALS):
            frame[f"signal_{name}"] = np.round(signals[:, index], 4)
            frame[f"contrib_{name}"] = np.round(contributions[:, index], 3)
        frame["risk_score"] = np.round(risk, 2)
        frame["risk_level"] = [level_for(score) for score in risk]
        frame["confidence"] = np.round(confidence(weights * signals, frame["anomaly_agreement"], is_seed), 3)
        frame["is_seed"] = is_seed
        order = np.argsort(-risk, kind="stable")
        ranks = np.empty(len(frame), dtype=int)
        ranks[order] = np.arange(1, len(frame) + 1)
        frame["rank"] = ranks

    # ── overview & evaluation ───────────────────────────────────────────────
    def _build_overview(self) -> dict[str, Any]:
        wallets, transactions = self.wallets, self.transactions
        txs = self.flows.txs
        ips = {tx.src_ip for tx in txs} | {tx.dst_ip for tx in txs}
        members = self.clustering.members
        communities = {value for value in self.community_of.values() if value >= 0}
        pattern_counts = Counter(pattern["type"] for pattern in self.patterns.values())
        high = int(wallets["risk_level"].isin(["HIGH", "CRITICAL"]).sum())
        models = [
            *({"name": name, "family": "anomaly detection", "level": "wallet", "description": DETECTOR_DESCRIPTIONS[name],
               "flagged": int((wallets[f"det_{name}"] >= 0.95).sum())} for name in DETECTORS),
            *({"name": name, "family": "anomaly detection", "level": "transaction", "description": DETECTOR_DESCRIPTIONS[name],
               "flagged": int((transactions[f"det_{name}"] >= 0.95).sum())} for name in DETECTORS),
            {"name": "Common-input ownership", "family": "entity clustering", "level": "wallet",
             "description": "Union-find over co-spent inputs, CoinJoins excluded, groups addresses controlled by one entity.",
             "flagged": sum(1 for items in members.values() if len(items) > 1)},
            {"name": "DeepWalk embeddings + HDBSCAN", "family": "entity clustering", "level": "entity",
             "description": "Random-walk (PPMI + SVD) embeddings of the entity transaction graph, density-clustered into communities.",
             "flagged": len(communities)},
            *({"name": PATTERN_NAMES[kind], "family": "pattern detection", "level": "transaction flow",
               "description": {"peeling_chain": "Chains of small peels with the remainder moving to fresh addresses.",
                               "layering": "Fan-out to fresh intermediates that reconverge on one address.",
                               "rapid_passthrough": "Wallets forwarding nearly everything they receive within minutes.",
                               "coinjoin": "Many-input transactions with several equal-value outputs."}[kind],
               "flagged": pattern_counts.get(kind, 0)} for kind in PATTERN_NAMES),
            {"name": "Haircut taint propagation", "family": "risk propagation", "level": "wallet",
             "description": "Time-ordered, value-weighted taint from seed wallets (and their co-owned addresses) with 0.9 decay per hop.",
             "flagged": sum(1 for info in self.taint.values() if info["score"] > 0.05)},
            {"name": "Upstream exposure propagation", "family": "risk propagation", "level": "wallet",
             "description": "Reverse-time pass from pattern-confirmed hop wallets: sources that sent most of their value into a laundering structure (max 2 hops).",
             "flagged": len(self.exposure)},
            {"name": "Noisy-OR fusion with Shapley attribution", "family": "risk scoring", "level": "wallet + transaction",
             "description": "Combines anomaly, pattern, taint and network signals; exact Shapley values split the score by signal.",
             "flagged": high},
        ]
        return _native({
            "stats": {
                "transactions": len(txs), "wallets": len(wallets), "ips": len({ip for ip in ips if ip}),
                "entities": len(members), "multi_address_entities": sum(1 for items in members.values() if len(items) > 1),
                "communities": len(communities), "countries": len({tx.country for tx in txs if tx.country}),
                "asns": len({tx.asn for tx in txs if tx.asn}),
                "start_time": txs[0].time.isoformat() if txs else None, "end_time": txs[-1].time.isoformat() if txs else None,
                "coinjoins_excluded_from_cio": self.clustering.excluded_coinjoins,
            },
            "geo_sources": dict(Counter(str(record.get("geo_country_source", "dataset")) for record in self.records)),
            "risk_distribution": {level: int((wallets["risk_level"] == level).sum()) for level in ("CRITICAL", "HIGH", "MEDIUM", "LOW")},
            "transaction_risk_distribution": {level: int((transactions["risk_level"] == level).sum()) for level in ("CRITICAL", "HIGH", "MEDIUM", "LOW")},
            "patterns": {kind: pattern_counts.get(kind, 0) for kind in PATTERN_NAMES},
            "seeds": self.seeds,
            "models": models,
            "evaluation": self._evaluation(),
        })

    def _evaluation(self) -> dict[str, Any]:
        labels = self.transactions["label"]
        if not labels.astype(bool).any():
            return {"labels_available": False, "notes": "The dataset carries no behaviour labels, so detection quality cannot be measured."}
        mask = labels.isin(ILLICIT_LABELS + LICIT_LABELS)
        target = labels[mask].isin(ILLICIT_LABELS)
        result: dict[str, Any] = {"labels_available": True,
                                  "notes": "Measured against the synthetic behaviour labels, which the models never see. CoinJoin-labelled transactions are excluded from the AUC because mixing is not illicit by itself."}
        if target.nunique() == 2:
            result["transaction_auc"] = round(float(roc_auc_score(target, self.transactions.loc[mask, "risk_score"])), 4)
            result["transaction_anomaly_only_auc"] = round(float(roc_auc_score(target, self.transactions.loc[mask, "anomaly_percentile"])), 4)
        detectors = []
        for kind, label in PATTERN_LABELS.items():
            actual = set(labels.index[labels == label])
            predicted: set[str] = set()
            for pattern in self.patterns.values():
                if pattern["type"] == kind:
                    predicted.update(pattern["details"].get("forward_txids") or pattern["txids"])
            if not actual and not predicted:
                continue
            hits = len(actual & predicted)
            detectors.append({"label": label, "detector": kind, "precision": round(hits / len(predicted), 4) if predicted else 0.0,
                              "recall": round(hits / len(actual), 4) if actual else 0.0, "support": len(actual)})
        result["detectors"] = detectors
        flagged = self.transactions["risk_level"].isin(["HIGH", "CRITICAL"])
        illicit = labels.isin(ILLICIT_LABELS)
        result["high_risk_transactions"] = int(flagged.sum())
        result["high_risk_precision"] = round(float((flagged & illicit).sum() / flagged.sum()), 4) if flagged.any() else 0.0
        result["illicit_recall_at_high"] = round(float((flagged & illicit).sum() / illicit.sum()), 4) if illicit.any() else 0.0
        return result

    # ── snapshot ────────────────────────────────────────────────────────────
    def to_snapshot(self) -> dict[str, Any]:
        return {
            "version": SNAPSHOT_VERSION,
            "records": self.records,
            "seeds": self.seeds,
            "wallets": _native(self.wallets.reset_index(drop=True).to_dict(orient="list")),
            "transactions": _native(self.transactions.reset_index(drop=True).to_dict(orient="list")),
            "patterns": self.patterns,
            "entity_of": self.clustering.entity_of,
            "excluded_coinjoins": self.clustering.excluded_coinjoins,
            "community_of": self.community_of,
            "attributions": self.attributions,
            "tx_attributions": self.tx_attributions,
            "overview": self.overview,
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> "Investigation":
        if snapshot.get("version") != SNAPSHOT_VERSION:
            raise ValueError("Snapshot was produced by an older version; re-run the analysis.")
        self = cls()
        self.records = snapshot["records"]
        self.flows = build_flow_index(self.records)
        self.network = build_network_correlation(self.flows)
        members: dict[str, list[str]] = defaultdict(list)
        for address, entity in snapshot["entity_of"].items():
            members[entity].append(address)
        self.clustering = EntityClustering(snapshot["entity_of"], dict(members), snapshot.get("excluded_coinjoins", 0))
        self.community_of = {key: int(value) for key, value in snapshot["community_of"].items()}
        self.patterns = snapshot["patterns"]
        self.wallets = pd.DataFrame(snapshot["wallets"]).set_index("wallet_id", drop=False)
        self.transactions = pd.DataFrame(snapshot["transactions"]).set_index("txid", drop=False)
        self.attributions = snapshot["attributions"]
        self.tx_attributions = snapshot["tx_attributions"]
        self.rescore(snapshot.get("seeds", []))
        return self

    # ── queries ─────────────────────────────────────────────────────────────
    def _signals(self, row: pd.Series) -> tuple[dict[str, float], dict[str, float]]:
        return ({name: float(row[f"signal_{name}"]) for name in SIGNALS}, {name: float(row[f"contrib_{name}"]) for name in SIGNALS})

    def _wallet_row(self, wallet_id: str) -> dict[str, Any]:
        row = self.wallets.loc[wallet_id]
        signals, contributions = self._signals(row)
        record = {key: _native(row[key]) for key in ("wallet_id", "entity_id", "entity_size", "cluster_id", "anomaly_percentile", "anomaly_agreement",
                                                      "nonstandard_port_ratio", "unique_asns", "unique_countries", "pattern_refs", "is_seed")}
        record.update(signals=signals, contributions=contributions)
        return record

    def _risk_path(self, wallet_id: str) -> dict[str, Any] | None:
        """Downstream seed taint or upstream exposure, whichever sets the wallet's taint signal."""
        downstream = self.taint.get(wallet_id)
        upstream = self.exposure.get(wallet_id)
        if upstream and upstream["score"] * UPSTREAM_WEIGHT > (downstream or {}).get("score", 0.0):
            return {**upstream, "reason": "upstream"}
        return downstream

    def wallet_alert(self, wallet_id: str) -> dict[str, Any]:
        row = self.wallets.loc[wallet_id]
        base = self._wallet_row(wallet_id)
        reasons = build_reasons(base, self.patterns, self.attributions.get(wallet_id, []), self._risk_path(wallet_id), FEATURE_LABELS, "wallet")
        pattern_types = sorted({self.patterns[ref[0]]["type"] for ref in base["pattern_refs"] if ref[0] in self.patterns})
        return _native({
            "rank": int(row["rank"]), "wallet_id": wallet_id, "risk_score": float(row["risk_score"]), "risk_level": row["risk_level"],
            "confidence": float(row["confidence"]), "is_seed": bool(row["is_seed"]), "entity_id": row["entity_id"],
            "entity_size": int(row["entity_size"]), "cluster_id": int(row["cluster_id"]),
            "signals": base["signals"], "contributions": base["contributions"], "patterns": pattern_types,
            "top_reason": reasons[0]["label"] if reasons else "No significant signals", "reasons": reasons,
        })

    def wallet_alerts(self, limit: int = 200, level: str | None = None) -> list[dict[str, Any]]:
        frame = self.wallets if not level else self.wallets[self.wallets["risk_level"] == level.upper()]
        return [self.wallet_alert(wallet_id) for wallet_id in frame.sort_values("rank").index[:limit]]

    def transaction_alert(self, txid: str) -> dict[str, Any]:
        row = self.transactions.loc[txid]
        signals, contributions = self._signals(row)
        base = {key: _native(row[key]) for key in ("anomaly_percentile", "anomaly_agreement", "nonstandard_port", "dst_port", "ip_wallet_count", "pattern_refs")}
        base.update(signals=signals, contributions=contributions, is_seed=False)
        reasons = build_reasons(base, self.patterns, self.tx_attributions.get(txid, []), None, FEATURE_LABELS, "transaction")
        if signals["taint"] > 0:
            reasons.append({"category": "taint", "label": f"Spends funds {signals['taint']:.0%} tainted by seed wallets",
                            "detail": "Value-weighted taint of its inputs at the time of the transaction.", "contribution": round(contributions["taint"], 2)})
            reasons.sort(key=lambda reason: -reason["contribution"])
        return _native({
            "rank": int(row["rank"]), "txid": txid, "timestamp": row["timestamp"], "risk_score": float(row["risk_score"]), "risk_level": row["risk_level"],
            "confidence": float(row["confidence"]), "signals": signals, "contributions": contributions,
            "patterns": sorted({ref[1] for ref in base["pattern_refs"]}), "input_count": int(row["input_count"]), "output_count": int(row["output_count"]),
            "total_input": float(row["total_input"]), "total_output": float(row["total_output"]), "fee": float(row["fee"]),
            "src_ip": row["src_ip"], "dst_ip": row["dst_ip"], "dst_port": int(row["dst_port"]), "country": row["country"],
            "top_reason": reasons[0]["label"] if reasons else "No significant signals", "reasons": reasons,
        })

    def transaction_alerts(self, limit: int = 200, level: str | None = None) -> list[dict[str, Any]]:
        frame = self.transactions if not level else self.transactions[self.transactions["risk_level"] == level.upper()]
        return [self.transaction_alert(txid) for txid in frame.sort_values("rank").index[:limit]]

    def pattern_view(self, pattern: dict[str, Any]) -> dict[str, Any]:
        return {
            "pattern_id": pattern["pattern_id"], "type": pattern["type"], "strength": pattern["strength"], "summary": pattern["summary"],
            "wallets": list(pattern["roles"]), "roles": pattern["roles"], "transactions": pattern["txids"],
            "start_time": _iso(pattern["start_ts"]), "end_time": _iso(pattern["end_ts"]), "total_value": pattern["total_value"],
            "details": {key: value for key, value in pattern["details"].items() if key != "forward_txids"},
        }

    def pattern_list(self, kind: str | None = None) -> list[dict[str, Any]]:
        items = [pattern for pattern in self.patterns.values() if not kind or pattern["type"] == kind]
        items.sort(key=lambda pattern: (-TYPE_WEIGHTS.get(pattern["type"], 0.5) * pattern["strength"], pattern["start_ts"]))
        return [self.pattern_view(pattern) for pattern in items]

    def clusters(self) -> list[dict[str, Any]]:
        output = []
        for cluster_id, frame in self.wallets.groupby("cluster_id"):
            top = frame.sort_values("rank").head(8)
            kinds: Counter = Counter()
            for refs in frame["pattern_refs"]:
                for pattern_id in {ref[0] for ref in refs}:
                    kinds[self.patterns[pattern_id]["type"]] += 1
            flagged = int(frame["risk_level"].isin(["HIGH", "CRITICAL"]).sum())
            dominant = kinds.most_common(1)[0][0].replace("_", " ") if kinds else None
            label = "Unassigned entities" if cluster_id == -1 else f"Community {cluster_id}: {len(frame)} wallets" + (f", {dominant}" if dominant else "")
            output.append(_native({
                "cluster_id": int(cluster_id), "label": label, "wallet_count": len(frame), "entity_count": int(frame["entity_id"].nunique()),
                "avg_risk": round(float(frame["risk_score"].mean()), 2), "max_risk": float(frame["risk_score"].max()), "flagged_count": flagged,
                "top_wallets": [{"wallet_id": wallet_id, "risk_score": float(row["risk_score"]), "risk_level": row["risk_level"]} for wallet_id, row in top.iterrows()],
                "pattern_counts": dict(kinds),
            }))
        output.sort(key=lambda item: (item["cluster_id"] == -1, -item["avg_risk"]))
        return output

    def wallet_detail(self, wallet_id: str) -> dict[str, Any]:
        alert = self.wallet_alert(wallet_id)
        row = self.wallets.loc[wallet_id]
        footprint = self.network.wallets.get(wallet_id)
        entity = self.clustering.entity_of.get(wallet_id, "")
        members = self.clustering.members.get(entity, [wallet_id])
        taint = self.taint.get(wallet_id)
        transactions = []
        indices = sorted(set(self.flows.received.get(wallet_id, [])) | set(self.flows.spent.get(wallet_id, [])))
        for index in indices[-200:]:
            tx = self.flows.txs[index]
            incoming = wallet_id in tx.output_addresses
            outgoing = wallet_id in tx.input_addresses
            amount = sum(value for address, value in (tx.inputs if outgoing else tx.outputs) if address == wallet_id)
            others = [address for address in (tx.output_addresses if outgoing else tx.input_addresses) if address != wallet_id]
            tx_row = self.transactions.loc[tx.txid]
            transactions.append({"txid": tx.txid, "timestamp": tx.time.isoformat(), "direction": "self" if incoming and outgoing else ("out" if outgoing else "in"),
                                 "amount": round(amount, 8), "counterparties": others[:5], "src_ip": tx.src_ip, "dst_port": tx.dst_port,
                                 "risk_level": tx_row["risk_level"], "patterns": sorted({ref[1] for ref in tx_row["pattern_refs"]})})
        return _native({
            **alert,
            "detectors": [{"name": name, "percentile": round(float(row[f"det_{name}"]), 4), "flagged": bool(row[f"det_{name}"] >= 0.95)} for name in DETECTORS],
            "feature_attributions": [{**item, "label": FEATURE_LABELS.get(item["feature"], item["feature"])} for item in self.attributions.get(wallet_id, [])],
            "features": {column: _native(row[column]) for column in FEATURE_LABELS if column in row.index},
            "entity": {"entity_id": entity, "size": len(members), "wallets": sorted(members, key=lambda address: self.wallets.at[address, "rank"])[:50]},
            "cluster": {"cluster_id": int(row["cluster_id"]), "label": f"Community {int(row['cluster_id'])}" if row["cluster_id"] >= 0 else "Unassigned"},
            "taint": ({**taint, "path": self.propagation.path(wallet_id) if self.propagation else []} if taint else None),
            "upstream_exposure": self.exposure.get(wallet_id),
            "patterns": [self.pattern_view(self.patterns[ref[0]]) for ref in row["pattern_refs"] if ref[0] in self.patterns],
            "network": {
                "ips": self.network.ip_associations(wallet_id, limit=20),
                "countries": [{"country": key, "count": value} for key, value in (footprint.countries.most_common() if footprint else [])],
                "asns": [{"asn": key, "count": value} for key, value in (footprint.asns.most_common() if footprint else [])],
                "ports": [{"port": key, "count": value} for key, value in (footprint.ports.most_common() if footprint else [])],
                "nonstandard_port_ratio": round(footprint.nonstandard_port_ratio, 4) if footprint else 0.0,
                "shared_ip_wallets": self.network.shared_ip_wallets(wallet_id),
            },
            "transactions": transactions,
        })

    def transaction_detail(self, txid: str) -> dict[str, Any]:
        alert = self.transaction_alert(txid)
        row = self.transactions.loc[txid]
        tx = self.flows.txs[self.flows.by_txid[txid]]

        def side(items: list[tuple[str, float]]) -> list[dict[str, Any]]:
            return [{"wallet_id": address, "amount": round(amount, 8), "risk_level": self.wallets.at[address, "risk_level"]} for address, amount in items]

        return _native({
            **alert,
            "inputs": side(tx.inputs), "outputs": side(tx.outputs),
            "detectors": [{"name": name, "percentile": round(float(row[f"det_{name}"]), 4), "flagged": bool(row[f"det_{name}"] >= 0.95)} for name in DETECTORS],
            "feature_attributions": [{**item, "label": FEATURE_LABELS.get(item["feature"], item["feature"])} for item in self.tx_attributions.get(txid, [])],
            "patterns": [self.pattern_view(self.patterns[ref[0]]) for ref in row["pattern_refs"] if ref[0] in self.patterns],
        })

    # ── local link-analysis graph ───────────────────────────────────────────
    def graph(self, focus_type: str, focus_id: str, depth: int = 1, max_nodes: int = 160) -> dict[str, Any]:
        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[str, dict[str, Any]] = {}
        limited = False

        def add_wallet(address: str) -> bool:
            nonlocal limited
            if address in nodes:
                return True
            if len(nodes) >= max_nodes:
                limited = True
                return False
            row = self.wallets.loc[address]
            nodes[address] = {"id": address, "type": "Wallet", "label": _short(address), "risk_score": float(row["risk_score"]),
                              "risk_level": row["risk_level"], "is_seed": bool(row["is_seed"]), "is_focus": address == focus_id}
            return True

        def add_tx(index: int, with_ip: bool = False) -> None:
            nonlocal limited
            tx = self.flows.txs[index]
            if tx.txid not in nodes:
                if len(nodes) >= max_nodes:
                    limited = True
                    return
                row = self.transactions.loc[tx.txid]
                nodes[tx.txid] = {"id": tx.txid, "type": "Transaction", "label": _short(tx.txid), "risk_score": float(row["risk_score"]),
                                  "risk_level": row["risk_level"], "is_seed": False, "is_focus": tx.txid == focus_id}
            for address, amount in tx.inputs:
                if add_wallet(address):
                    edges[f"{address}>{tx.txid}"] = {"id": f"{address}>{tx.txid}", "source": address, "target": tx.txid, "type": "INPUT_FROM", "amount": round(amount, 8)}
            for address, amount in tx.outputs:
                if add_wallet(address):
                    edges[f"{tx.txid}>{address}"] = {"id": f"{tx.txid}>{address}", "source": tx.txid, "target": address, "type": "OUTPUT_TO", "amount": round(amount, 8)}
            if with_ip and tx.src_ip and (tx.src_ip in nodes or len(nodes) < max_nodes):
                nodes.setdefault(tx.src_ip, {"id": tx.src_ip, "type": "IP", "label": tx.src_ip, "is_focus": False, "country": tx.country, "asn": tx.asn})
                edges[f"{tx.txid}@{tx.src_ip}"] = {"id": f"{tx.txid}@{tx.src_ip}", "source": tx.txid, "target": tx.src_ip, "type": "OBSERVED_IN"}

        def wallet_txs(address: str, limit: int = 25) -> list[int]:
            indices = sorted(set(self.flows.received.get(address, [])) | set(self.flows.spent.get(address, [])))
            if len(indices) > limit:  # hubs: keep the riskiest transactions
                indices = sorted(indices, key=lambda index: -self.transactions.at[self.flows.txs[index].txid, "risk_score"])[:limit]
            return indices

        if focus_type == "wallet":
            add_wallet(focus_id)
            frontier = [focus_id]
            for level in range(max(1, min(depth, 3))):
                next_frontier = []
                for address in frontier:
                    for index in wallet_txs(address, 25 if level == 0 else 6):
                        before = set(nodes)
                        add_tx(index, with_ip=level == 0)
                        next_frontier.extend(node for node in set(nodes) - before if nodes[node]["type"] == "Wallet")
                frontier = next_frontier
        elif focus_type == "transaction":
            index = self.flows.by_txid[focus_id]
            add_tx(index, with_ip=True)
            if depth >= 2:
                tx = self.flows.txs[index]
                for address in tx.input_addresses:
                    earlier = [item for item in self.flows.received.get(address, []) if item < index]
                    if earlier:
                        add_tx(earlier[-1])
                for address in tx.output_addresses:
                    spend = self.flows.next_spend(address, tx.ts)
                    if spend is not None:
                        add_tx(spend.index)
        elif focus_type == "pattern":
            for txid in self.patterns[focus_id]["txids"]:
                add_tx(self.flows.by_txid[txid], with_ip=True)
        elif focus_type == "cluster":
            members = self.wallets[self.wallets["cluster_id"] == int(focus_id)].sort_values("rank").index[:30]
            chosen = set(members)
            for address in members:
                add_wallet(address)
            for address in members:
                for index in wallet_txs(address, 4):
                    tx = self.flows.txs[index]
                    if chosen & (set(tx.input_addresses) | set(tx.output_addresses)):
                        add_tx(index)
        else:
            raise ValueError(f"Unsupported focus_type: {focus_type}")
        return _native({"graph_available": True, "source": "local", "limited": limited, "nodes": list(nodes.values()), "edges": list(edges.values())})

    def annotate_graph(self, stored: dict[str, Any], focus_id: str) -> dict[str, Any]:
        """Add risk and seed information to a graph read from Neo4j."""
        nodes = []
        for node in stored["nodes"]:
            item = {"id": node["id"], "type": node["type"], "label": _short(str(node["id"])) if node["type"] != "IP" else node["id"], "is_focus": node["id"] == focus_id}
            frame = self.wallets if node["type"] == "Wallet" else self.transactions if node["type"] == "Transaction" else None
            if frame is not None and node["id"] in frame.index:
                item.update(risk_score=float(frame.at[node["id"], "risk_score"]), risk_level=frame.at[node["id"], "risk_level"],
                            is_seed=bool(frame.at[node["id"], "is_seed"]) if node["type"] == "Wallet" else False)
            nodes.append(item)
        amounts = {}
        for tx in (self.flows.txs[self.flows.by_txid[node["id"]]] for node in stored["nodes"] if node["type"] == "Transaction" and node["id"] in self.flows.by_txid):
            amounts.update({(address, tx.txid): amount for address, amount in tx.inputs})
            amounts.update({(tx.txid, address): amount for address, amount in tx.outputs})
        edges = [{**edge, **({"amount": round(amounts[(edge["source"], edge["target"])], 8)} if (edge["source"], edge["target"]) in amounts else {})}
                 for edge in stored["edges"]]
        return _native({"graph_available": True, "source": "neo4j", "limited": stored.get("limited", False), "nodes": nodes, "edges": edges})

    # ── tabular exports (compatibility with the CLI outputs) ────────────────
    @property
    def wallet_features(self) -> pd.DataFrame:
        return self.wallets[["wallet_id", *[column for column in FEATURE_LABELS if column in self.wallets.columns]]].reset_index(drop=True)

    @property
    def risk_scores(self) -> pd.DataFrame:
        columns = ["wallet_id", "risk_score", "risk_level", "confidence", "entity_id", "cluster_id", *[f"signal_{name}" for name in SIGNALS], *[f"contrib_{name}" for name in SIGNALS]]
        return self.wallets[columns].sort_values("risk_score", ascending=False).reset_index(drop=True)


__all__ = ["Investigation", "SNAPSHOT_VERSION", "ILLICIT_LABELS"]
