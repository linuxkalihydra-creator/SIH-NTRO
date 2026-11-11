"""Read-only queries over an analysed dataset: ranked leads, evidence, link-analysis graphs, search, exports.

Mixed into :class:`backend.pipeline.investigation.Investigation`. Nothing here refits a model or
changes a score; every method turns stored results into what an investigator reads: why something
was flagged, what the evidence is, and where to look next.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from backend.correlation.flows import service_port
from backend.ml.anomaly import DETECTORS
from backend.ml.entities import ACTOR_LINK_TEXT
from backend.ml.features import FEATURE_LABELS
from backend.ml.patterns import HOP_ROLES, TYPE_WEIGHTS
from backend.ml.scoring import SIGNALS, build_reasons, count_of, pattern_text, top_percent

RECIPIENT_ROLES = frozenset({"peel_recipient", "pass_through_recipient", "layering_exit", "chain_exit"})
LEVEL_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
GRAPH_NODE_LIMIT = 160


def _iso(ts: float | None) -> str | None:
    if ts is None or ts != ts or ts in (float("inf"), float("-inf")):
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _short(value: Any) -> str:
    value = str(value)
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
    if isinstance(value, float) and value != value:
        return 0.0
    return value


class _Row:
    """One row of a frame, read straight from the column arrays (pandas ``.loc`` is ~100x slower per lookup)."""

    __slots__ = ("_columns", "_position")

    def __init__(self, columns: dict[str, np.ndarray], position: int) -> None:
        self._columns = columns
        self._position = position

    def __getitem__(self, name: str) -> Any:
        return self._columns[name][self._position]

    @property
    def index(self) -> Any:
        return self._columns.keys()


class _Rows:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.columns = {name: frame[name].to_numpy() for name in frame.columns}
        self.position = {key: index for index, key in enumerate(frame.index)}

    def __getitem__(self, key: Any) -> _Row:
        return _Row(self.columns, self.position[key])


class InvestigationViews:
    """Query methods; the attributes they read are set by ``Investigation.build`` / ``rescore``."""

    def _row(self, table: str, key: Any) -> _Row:
        return self._lookups()["rows"][table][key]

    def _has(self, table: str, key: Any) -> bool:
        return key in self._lookups()["rows"][table].position

    # ── cached lookups (rebuilt lazily after every rescore) ─────────────────
    def _lookups(self) -> dict[str, Any]:
        cache = getattr(self, "_view_cache", None)
        if cache is not None and cache["seeds"] is self.seeds:
            return cache
        wallets = self.wallets
        ordered = wallets.sort_values("rank")
        actor_wallets: dict[str, list[str]] = defaultdict(list)
        entity_wallets: dict[str, list[str]] = defaultdict(list)
        for address, actor, entity in zip(ordered.index, ordered["actor_id"], ordered["entity_id"]):
            actor_wallets[actor].append(address)
            entity_wallets[entity].append(address)
        tainted = wallets["taint_own"] >= 0.3
        structured = wallets["structural"] >= 0.3
        cache = {
            "seeds": self.seeds,
            "rows": {"wallets": _Rows(wallets), "transactions": _Rows(self.transactions), "actors": _Rows(self.actor_frame),
                     "entities": _Rows(self.entities), "ips": _Rows(self.ips)},
            "actor_wallets": dict(actor_wallets),
            "entity_wallets": dict(entity_wallets),
            "actor_of": dict(zip(wallets.index, wallets["actor_id"])),
            "risk": dict(zip(wallets.index, wallets["risk_score"])),
            "level": dict(zip(wallets.index, wallets["risk_level"])),
            "tainted": {"entity": tainted.groupby(wallets["entity_id"]).sum().to_dict(), "actor": tainted.groupby(wallets["actor_id"]).sum().to_dict()},
            "structured": {"entity": structured.groupby(wallets["entity_id"]).sum().to_dict(), "actor": structured.groupby(wallets["actor_id"]).sum().to_dict()},
        }
        self._view_cache = cache
        return cache

    # ── wallets ─────────────────────────────────────────────────────────────
    def _signals(self, row: Any) -> tuple[dict[str, float], dict[str, float]]:
        return ({name: float(row[f"signal_{name}"]) for name in SIGNALS}, {name: float(row[f"contrib_{name}"]) for name in SIGNALS})

    def _group(self, kind: str, row: Any) -> tuple[str, int]:
        """(group id, number of wallets) of the entity or actor a wallet belongs to."""
        lookups = self._lookups()
        if kind == "entity":
            return row["entity_id"], len(lookups["entity_wallets"].get(row["entity_id"], ()))
        return row["actor_id"], len(lookups["actor_wallets"].get(row["actor_id"], ()))

    def _taint_evidence(self, wallet_id: str, row: Any) -> dict[str, Any] | None:
        kind = row["taint_kind"]
        if not kind:
            return None
        if kind in ("seed", "co_owned", "downstream"):
            info = self.taint.get(wallet_id)
            return {**info, "kind": kind} if info else None
        if kind == "proceeds":
            info = self.proceeds.get(wallet_id)
            return {**info, "kind": kind} if info else None
        if kind == "upstream":
            info = self.exposure.get(wallet_id)
            return {**info, "kind": kind} if info else None
        group, size = self._group(kind, row)
        return {"kind": kind, "source": row["taint_source"], "size": size, "affected": int(self._lookups()["tainted"][kind].get(group, 0))}

    def _pattern_evidence(self, wallet_id: str, row: Any) -> dict[str, Any] | None:
        kind = row["pattern_kind"]
        if kind == "cash_out":
            return {"kind": kind, "share": float(row["received_dirty"]), "counterparties": int(self._row("entities", row["entity_id"])["counterparty_entities"])}
        if kind in ("entity", "actor"):
            source = row["pattern_source"]
            refs = [ref for ref in self._row("wallets", source)["pattern_refs"] if ref[1] not in RECIPIENT_ROLES] if source else []
            best = max(refs, key=lambda ref: ref[2], default=None)
            group, size = self._group(kind, row)
            return {"kind": kind, "source": source, "pattern_id": best[0] if best else None, "role": best[1] if best else "",
                    "size": size, "affected": int(self._lookups()["structured"][kind].get(group, 0))}
        return {"kind": kind} if kind else None

    def _wallet_row(self, wallet_id: str) -> dict[str, Any]:
        row = self._row("wallets", wallet_id)
        signals, contributions = self._signals(row)
        actor = self._row("actors", row["actor_id"])
        link = self.clustering.evidence.get(wallet_id)
        refs = [ref for ref in row["pattern_refs"] if ref[1] not in RECIPIENT_ROLES]
        association = self.association.evidence.get(wallet_id)
        return {
            "signals": signals, "contributions": contributions, "is_seed": bool(row["is_seed"]), "pattern_refs": refs,
            "anomaly_percentile": float(row["anomaly_percentile"]), "anomaly_agreement": float(row["anomaly_agreement"]), "detectors_total": len(DETECTORS),
            "anomaly_scope": f"{row['actor_id']}, {int(actor['address_count'])} wallets" if actor["address_count"] > 1 else None,
            "pattern_evidence": self._pattern_evidence(wallet_id, row),
            "network_evidence": {"nonstandard_port_share": float(actor["nonstandard_port_share"]), "broadcasts": int(actor["broadcasts"]), "asns": int(actor["relay_asns"]),
                                 "countries": int(actor["relay_countries"]), "ips": int(actor["relay_ips"]), "wallets": int(actor["address_count"])},
            "association_evidence": {**association, "signal": float(row["association"])} if association and row["association"] > 0 else None,
            "entity_size": int(row["entity_size"]), "entity_id": row["entity_id"],
            "entity_link": f"Attached to the entity by {link[0]} ({_short(link[1])})." if link else None,
            "service_score": float(row["service_score"]), "counterparties": int(self._row("entities", row["entity_id"])["counterparty_entities"]),
        }

    def wallet_alert(self, wallet_id: str) -> dict[str, Any]:
        row = self._row("wallets", wallet_id)
        base = self._wallet_row(wallet_id)
        reasons = build_reasons(base, self.patterns, self.attributions.get(row["actor_id"], []), self._taint_evidence(wallet_id, row), FEATURE_LABELS, "wallet")
        listed = {reason.get("pattern_id") for reason in reasons}
        for pattern_id, role, _ in sorted((ref for ref in row["pattern_refs"] if ref[1] in RECIPIENT_ROLES), key=lambda ref: -ref[2])[:2]:
            if pattern_id in self.patterns and pattern_id not in listed:  # context: it is scored through the funds it received
                reasons.append({"category": "pattern", "label": pattern_text(role, self.patterns[pattern_id]), "detail": self.patterns[pattern_id]["summary"],
                                "pattern_id": pattern_id, "contribution": 0.0})
        pattern_types = sorted({self.patterns[ref[0]]["type"] for ref in row["pattern_refs"] if ref[0] in self.patterns})
        tags = [tag for tag, present in (("seed", bool(row["is_seed"])), ("cash-out", row["pattern_kind"] == "cash_out"),
                                         ("service", row["service_score"] >= 0.5), ("structure", row["structural"] >= 0.5)) if present]
        return _native({
            "rank": int(row["rank"]), "wallet_id": wallet_id, "risk_score": float(row["risk_score"]), "risk_level": row["risk_level"],
            "confidence": float(row["confidence"]), "is_seed": bool(row["is_seed"]), "entity_id": row["entity_id"],
            "entity_size": int(row["entity_size"]), "actor_id": row["actor_id"], "actor_size": int(self._row("actors", row["actor_id"])["address_count"]),
            "cluster_id": int(row["cluster_id"]), "signals": base["signals"], "contributions": base["contributions"], "patterns": pattern_types, "tags": tags,
            "top_reason": reasons[0]["label"] if reasons else "No significant signals", "reasons": reasons,
        })

    def wallet_alerts(self, limit: int = 200, level: str | None = None) -> list[dict[str, Any]]:
        frame = self.wallets if not level else self.wallets[self.wallets["risk_level"] == level.upper()]
        return [self.wallet_alert(wallet_id) for wallet_id in frame.sort_values("rank").index[:limit]]

    def wallet_detail(self, wallet_id: str) -> dict[str, Any]:
        alert = self.wallet_alert(wallet_id)
        row = self._row("wallets", wallet_id)
        lookups = self._lookups()
        footprint = self.network.wallets.get(wallet_id)
        entity, actor = row["entity_id"], row["actor_id"]
        entity_members = lookups["entity_wallets"].get(entity, [wallet_id])
        actor_members = lookups["actor_wallets"].get(actor, [wallet_id])
        taint = self.taint.get(wallet_id)
        proceeds = self.proceeds.get(wallet_id)
        transactions = []
        indices = sorted(set(self.flows.received.get(wallet_id, [])) | set(self.flows.spent.get(wallet_id, [])))
        for index in indices[-200:]:
            tx = self.flows.txs[index]
            incoming = wallet_id in tx.output_addresses
            outgoing = wallet_id in tx.input_addresses
            amount = sum(value for address, value in (tx.inputs if outgoing else tx.outputs) if address == wallet_id)
            others = [address for address in (tx.output_addresses if outgoing else tx.input_addresses) if address != wallet_id]
            tx_row = self._row("transactions", tx.txid)
            transactions.append({"txid": tx.txid, "timestamp": tx.time.isoformat(), "direction": "self" if incoming and outgoing else ("out" if outgoing else "in"),
                                 "amount": round(amount, 8), "counterparties": others[:5], "src_ip": tx.src_ip, "dst_port": service_port(tx.src_port, tx.dst_port),
                                 "risk_level": tx_row["risk_level"], "risk_score": float(tx_row["risk_score"]),
                                 "patterns": sorted({ref[1] for ref in tx_row["pattern_refs"]})})
        ips = []
        for item in self.network.ip_associations(wallet_id, limit=20):
            info = self.ip_info.get(item["ip"], {})
            known = self._row("ips", item["ip"]) if self._has("ips", item["ip"]) else None
            ips.append({**item, "country": info.get("country") or item["country"], "asn": info.get("asn") or item["asn"], "as_org": info.get("as_org", ""),
                        "risk_score": float(known["risk_score"]) if known is not None else 0.0, "risk_level": known["risk_level"] if known is not None else "LOW"})
        link = self.clustering.evidence.get(wallet_id)
        actor_row = self._row("actors", actor)
        return _native({
            **alert,
            "detectors": [{"name": name, "percentile": round(float(row[f"det_{name}"]), 4), "flagged": bool(row[f"det_{name}"] >= 0.95)} for name in DETECTORS],
            "feature_attributions": [{**item, "label": FEATURE_LABELS.get(item["feature"], item["feature"])} for item in self.attributions.get(actor, [])],
            "features": {column: _native(row[column]) for column in FEATURE_LABELS if column in row.index},
            "feature_labels": {column: label for column, label in FEATURE_LABELS.items() if column in row.index},
            "entity": {"entity_id": entity, "size": len(entity_members), "wallets": entity_members[:50],
                       "link": {"heuristic": link[0], "txid": link[1]} if link else None,
                       "service_score": float(row["service_score"]), "counterparties": int(self._row("entities", entity)["counterparty_entities"])},
            "actor": {"actor_id": actor, "wallets": len(actor_members), "entities": int(actor_row["entity_count"]), "risk_score": float(actor_row["risk_score"]),
                      "risk_level": actor_row["risk_level"], "top_wallets": [{"wallet_id": address, "risk_score": float(lookups["risk"][address]),
                                                                              "risk_level": lookups["level"][address]} for address in actor_members[:12]],
                      "link": self._actor_link(entity)},
            "cluster": {"cluster_id": int(row["cluster_id"]), "label": f"Community {int(row['cluster_id'])}" if row["cluster_id"] >= 0 else "Unassigned"},
            "taint": ({**taint, "path": self.propagation.path(wallet_id) if self.propagation else []} if taint else None),
            "proceeds": ({**proceeds, "path": self._proceeds_path(wallet_id)} if proceeds else None),
            "upstream_exposure": self.exposure.get(wallet_id),
            "patterns": [{**self.pattern_view(self.patterns[ref[0]]), "role": ref[1]} for ref in row["pattern_refs"] if ref[0] in self.patterns],
            "network": {
                "ips": ips,
                "countries": [{"country": key, "count": value} for key, value in (footprint.countries.most_common() if footprint else [])],
                "asns": [{"asn": key, "count": value} for key, value in (footprint.asns.most_common() if footprint else [])],
                "ports": [{"port": key, "count": value} for key, value in (footprint.ports.most_common() if footprint else [])],
                "nonstandard_port_ratio": round(footprint.nonstandard_port_ratio, 4) if footprint else 0.0,
                "shared_ip_wallets": self.network.shared_ip_wallets(wallet_id),
                "association": {**self.association.evidence[wallet_id], "signal": float(row["association"])} if wallet_id in self.association.evidence else None,
                "actor": {"ips": int(actor_row["relay_ips"]), "asns": int(actor_row["relay_asns"]), "countries": int(actor_row["relay_countries"]),
                          "broadcasts": int(actor_row["broadcasts"]), "nonstandard_port_share": float(actor_row["nonstandard_port_share"])},
            },
            "transactions": transactions,
        })

    def _proceeds_path(self, wallet_id: str, limit: int = 12) -> list[dict[str, str]]:
        steps, seen, current = [], set(), wallet_id
        while current in self.proceeds and current not in seen and len(steps) < limit:
            seen.add(current)
            info = self.proceeds[current]
            steps.append({"wallet_id": current, "txid": info["parent_txid"]})
            current = info["parent"]
        if current and current not in seen:
            steps.append({"wallet_id": current, "txid": ""})
        return steps

    # ── transactions ────────────────────────────────────────────────────────
    def transaction_alert(self, txid: str) -> dict[str, Any]:
        row = self._row("transactions", txid)
        signals, contributions = self._signals(row)
        tx = self.flows.txs[self.flows.by_txid[txid]]
        origin = self.network.origins[tx.index]
        base = {key: _native(row[key]) for key in ("anomaly_percentile", "anomaly_agreement", "nonstandard_port", "dst_port", "ip_wallet_count", "pattern_refs",
                                                    "src_ip", "ip_flagged_share")}
        base.update(signals=signals, contributions=contributions, is_seed=False, detectors_total=len(DETECTORS), service_port=service_port(tx.src_port, tx.dst_port),
                    origin_confidence=origin.confidence)
        reasons = build_reasons(base, self.patterns, self.tx_attributions.get(txid, []), None, FEATURE_LABELS, "transaction")
        if signals["taint"] > 0:
            seed_share = self.propagation.transactions.get(txid, 0.0) if self.propagation else 0.0
            label = (f"Spends funds {seed_share:.0%} tainted by seed wallets" if seed_share >= signals["taint"] - 1e-6
                     else f"Spends funds from flagged wallets ({signals['taint']:.0%} of the input value carries taint)")
            reasons.append({"category": "taint", "label": label, "detail": "Value-weighted taint of its inputs at the time of the transaction.",
                            "contribution": round(contributions["taint"], 2)})
            reasons.sort(key=lambda reason: -reason["contribution"])
        return _native({
            "rank": int(row["rank"]), "txid": txid, "timestamp": row["timestamp"], "risk_score": float(row["risk_score"]), "risk_level": row["risk_level"],
            "confidence": float(row["confidence"]), "signals": signals, "contributions": contributions,
            "patterns": sorted({ref[1] for ref in base["pattern_refs"]}), "input_count": int(row["input_count"]), "output_count": int(row["output_count"]),
            "total_input": float(row["total_input"]), "total_output": float(row["total_output"]), "fee": float(row["fee"]),
            "src_ip": row["src_ip"], "dst_ip": row["dst_ip"], "dst_port": int(row["dst_port"]), "country": row["country"],
            "origin_confidence": round(origin.confidence, 3), "relay_count": tx.relay_count,
            "top_reason": reasons[0]["label"] if reasons else "No significant signals", "reasons": reasons,
        })

    def transaction_alerts(self, limit: int = 200, level: str | None = None) -> list[dict[str, Any]]:
        frame = self.transactions if not level else self.transactions[self.transactions["risk_level"] == level.upper()]
        return [self.transaction_alert(txid) for txid in frame.sort_values("rank").index[:limit]]

    def transaction_detail(self, txid: str) -> dict[str, Any]:
        alert = self.transaction_alert(txid)
        row = self._row("transactions", txid)
        tx = self.flows.txs[self.flows.by_txid[txid]]
        origin = self.network.origins[tx.index]
        lookups = self._lookups()

        def side(items: list[tuple[str, float]]) -> list[dict[str, Any]]:
            return [{"wallet_id": address, "amount": round(amount, 8), "risk_level": lookups["level"][address], "risk_score": float(lookups["risk"][address])}
                    for address, amount in items]

        observations = []
        for position, observation in enumerate(tx.observations):
            info = self.ip_info.get(observation.src_ip, {})
            profile = self.network.ips.get(observation.src_ip)
            observations.append({
                "timestamp": _iso(observation.ts), "seconds_after_first": round(observation.ts - tx.observations[0].ts, 3),
                "src_ip": observation.src_ip, "src_port": observation.src_port, "dst_ip": observation.dst_ip, "dst_port": observation.dst_port,
                "country": info.get("country", ""), "asn": info.get("asn", 0), "as_org": info.get("as_org", ""),
                "role": "first seen" if position == 0 else ("origin announcing again" if observation.src_ip == tx.src_ip else "relay"),
                "relay_ratio": round(profile.relay_ratio, 3) if profile else 0.0,
            })
        info = self.ip_info.get(tx.src_ip, {})
        return _native({
            **alert,
            "inputs": side(tx.inputs), "outputs": side(tx.outputs),
            "detectors": [{"name": name, "percentile": round(float(row[f"det_{name}"]), 4), "flagged": bool(row[f"det_{name}"] >= 0.95)} for name in DETECTORS],
            "feature_attributions": [{**item, "label": FEATURE_LABELS.get(item["feature"], item["feature"])} for item in self.tx_attributions.get(txid, [])],
            "patterns": [self.pattern_view(self.patterns[ref[0]]) for ref in row["pattern_refs"] if ref[0] in self.patterns],
            "origin": {**origin.to_dict(), "country": info.get("country") or tx.country, "asn": info.get("asn") or tx.asn, "as_org": info.get("as_org", ""),
                       "explanation": self._origin_text(origin)},
            "observations": observations,
            "script_type": tx.script_type, "size": tx.size, "fee_rate": float(row["fee_rate"]), "src_port": tx.src_port,
        })

    def _origin_text(self, origin: Any) -> str:
        if not origin.ip:
            return "No source IP was recorded for this transaction."
        profile = self.network.ips.get(origin.ip)
        relay = profile.relay_ratio if profile else 0.0
        if origin.observations <= 1:
            text = "Observed once, so there is no later relay to compare its timing against."
        elif origin.lead_seconds is None:
            text = f"All {origin.observations} sightings came from this IP."
        else:
            text = f"First of {origin.observations} sightings, {origin.lead_seconds:.2f} s ahead of the next relay."
        if relay >= 0.5:
            text += f" This IP relays other parties' transactions in {relay:.0%} of its sightings, so it is treated as a relay node, not the creator."
        return text + (" Attributed to the spending wallets." if origin.attributed else " Not attributed: the creator was probably not observed.")

    # ── actors ──────────────────────────────────────────────────────────────
    def actor_alert(self, actor_id: str) -> dict[str, Any]:
        row = self._row("actors", actor_id)
        members = self._lookups()["actor_wallets"].get(actor_id, [])
        top = self.wallet_alert(members[0]) if members else None
        kinds = Counter()
        for address in members[:400]:
            for ref in self._row("wallets", address)["pattern_refs"]:
                if ref[1] in HOP_ROLES and ref[0] in self.patterns:
                    kinds[self.patterns[ref[0]]["type"]] += 1
        return _native({
            "rank": int(row["rank"]), "actor_id": actor_id, "risk_score": float(row["risk_score"]), "risk_level": row["risk_level"], "mean_risk": float(row["mean_risk"]),
            "max_wallet_risk": float(row["max_wallet_risk"]),
            "wallets": int(row["address_count"]), "entities": int(row["entity_count"]), "flagged_wallets": int(row["flagged_wallets"]),
            "seed_wallets": int(row["seed_wallets"]), "received_value": float(row["received_value"]), "sent_value": float(row["sent_value"]),
            "ips": int(row["relay_ips"]), "asns": int(row["relay_asns"]), "countries": int(row["relay_countries"]),
            "nonstandard_port_share": float(row["nonstandard_port_share"]), "anomaly_percentile": float(row["anomaly_percentile"]),
            "service_score": float(row["service_score"]), "patterns": dict(kinds),
            "top_wallet": members[0] if members else None, "top_reason": top["top_reason"] if top else "No significant signals",
        })

    def _actor_link(self, entity: str) -> dict[str, Any] | None:
        """Why an entity was put in its actor: the two clues that agreed, in words and as identifiers."""
        link = self.actors.evidence.get(entity)
        if not link:
            return None
        kind = link.get("kind", "payment")
        return {**link, "kind": kind, "description": ACTOR_LINK_TEXT.get(kind, kind)}

    def actor_alerts(self, limit: int = 200, level: str | None = None) -> list[dict[str, Any]]:
        frame = self.actor_frame if not level else self.actor_frame[self.actor_frame["risk_level"] == level.upper()]
        return [self.actor_alert(actor_id) for actor_id in frame.sort_values("rank").index[:limit]]

    def actor_detail(self, actor_id: str) -> dict[str, Any]:
        alert = self.actor_alert(actor_id)
        row = self._row("actors", actor_id)
        lookups = self._lookups()
        members = lookups["actor_wallets"].get(actor_id, [])
        entities = []
        for entity in self.actors.members.get(actor_id, []):
            wallets = lookups["entity_wallets"].get(entity, [])
            link = self._actor_link(entity)
            entities.append({"entity_id": entity, "wallets": len(wallets), "max_risk": float(lookups["risk"][wallets[0]]) if wallets else 0.0,
                             "top_wallet": wallets[0] if wallets else None, "link": link})
        entities.sort(key=lambda item: -item["max_risk"])
        ip_uses: Counter = Counter()
        for address in members:
            footprint = self.network.wallets.get(address)
            if footprint:
                ip_uses.update(footprint.ips)
        ips = []
        for ip, uses in ip_uses.most_common(40):
            info = self.ip_info.get(ip, {})
            known = self._row("ips", ip) if self._has("ips", ip) else None
            ips.append({"ip": ip, "count": uses, "country": info.get("country") or self.network.ip_country.get(ip, ""), "asn": info.get("asn") or self.network.ip_asn.get(ip, 0),
                        "as_org": info.get("as_org", ""), "wallets_on_ip": len(self.network.ip_wallets.get(ip, ())),
                        "risk_score": float(known["risk_score"]) if known is not None else 0.0, "risk_level": known["risk_level"] if known is not None else "LOW"})
        reasons = []
        if alert["seed_wallets"]:
            reasons.append({"category": "seed", "label": f"{alert['seed_wallets']} of its wallets are known illicit seeds", "detail": "", "contribution": 0.0})
        if alert["patterns"]:
            listed = ", ".join(f"{count} in a {kind.replace('_', ' ')}" for kind, count in sorted(alert["patterns"].items(), key=lambda item: -item[1]))
            reasons.append({"category": "pattern", "label": f"Wallets inside laundering structures: {listed}", "detail": "", "contribution": 0.0})
        if row["relay_asns"] > 3:
            reasons.append({"category": "network", "label": f"Broadcasts from {int(row['relay_asns'])} networks in {int(row['relay_countries'])} countries ({int(row['relay_ips'])} IPs)",
                            "detail": f"{int(row['broadcasts'])} attributed broadcasts.", "contribution": 0.0})
        if row["nonstandard_port_share"] > 0.1 and row["broadcasts"] >= 3:
            reasons.append({"category": "network", "label": f"{row['nonstandard_port_share']:.0%} of its broadcasts used non-standard ports", "detail": "", "contribution": 0.0})
        if row["anomaly_percentile"] >= 0.95:
            reasons.append({"category": "anomaly", "label": f"Behaviour is a statistical outlier ({top_percent(row['anomaly_percentile'])} of actors)",
                            "detail": "", "contribution": 0.0})
        if row["entity_count"] > 1:
            reasons.append({"category": "entity", "label": f"{int(row['entity_count'])} entities operated together",
                            "detail": "Each one is attached by two agreeing clues out of: the same broadcast IP, a direct payment, deposits into the same "
                                      "service address. The clues used are listed per entity.", "contribution": 0.0})
        return _native({
            **alert, "reasons": reasons,
            "detectors": [{"name": name, "percentile": round(float(row[f"det_{name}"]), 4), "flagged": bool(row[f"det_{name}"] >= 0.95)} for name in DETECTORS],
            "feature_attributions": [{**item, "label": FEATURE_LABELS.get(item["feature"], item["feature"])} for item in self.attributions.get(actor_id, [])],
            "features": {column: _native(row[column]) for column in FEATURE_LABELS if column in row.index},
            "feature_labels": {column: label for column, label in FEATURE_LABELS.items() if column in row.index},
            "top_wallets": [{"wallet_id": address, "risk_score": float(lookups["risk"][address]), "risk_level": lookups["level"][address],
                             "entity_id": self.clustering.entity_of[address]} for address in members[:60]],
            "entity_list": entities[:60], "ip_list": ips,
        })

    # ── IPs ─────────────────────────────────────────────────────────────────
    def _ip_role(self, row: Any, actors: int) -> str:
        if row["relayed_count"] >= 3 and row["relay_ratio"] >= 0.5:
            return "relay node"
        if row["originated"] == 0:
            return "seen only as a later relay" if row["relayed_count"] else "no attributed traffic"
        return "single actor" if actors <= 1 else f"shared by {actors} actors"

    def ip_alert(self, ip: str) -> dict[str, Any]:
        row = self._row("ips", ip)
        lookups = self._lookups()
        users = self.network.ip_wallets.get(ip, ())
        actors = {lookups["actor_of"][address] for address in users}
        role = self._ip_role(row, len(actors))
        if row["flagged_wallets"]:
            reason = f"{int(row['flagged_wallets'])} of {int(row['wallets'])} wallets broadcasting from it are HIGH risk or above"
        elif row["originated"]:
            reason = f"Originated {int(row['originated'])} transactions for {int(row['wallets'])} wallets"
        else:
            reason = f"Relayed {int(row['relayed_count'])} transactions first seen elsewhere"
        return _native({
            "rank": int(row["rank"]), "ip": ip, "risk_score": float(row["risk_score"]), "risk_level": row["risk_level"], "confidence": float(row["confidence"]),
            "country": row["country"], "asn": int(row["asn"] or 0), "as_org": row["as_org"], "scope": row["scope"], "role": role,
            "originated": int(row["originated"]), "relayed": int(row["relayed_count"]), "relay_ratio": float(row["relay_ratio"]),
            "wallets": int(row["wallets"]), "flagged_wallets": int(row["flagged_wallets"]), "actors": len(actors), "flagged_share": float(row["flagged_share"]),
            "value": float(row["value"]), "nonstandard_port_share": float(row["nonstandard_port_share"]),
            "first_seen": _iso(row["first_ts"]), "last_seen": _iso(row["last_ts"]), "top_reason": reason,
        })

    def ip_alerts(self, limit: int = 200, level: str | None = None, include_relays: bool = False) -> list[dict[str, Any]]:
        frame = self.ips if include_relays else self.ips[self.ips["originated"] > 0]
        if level:
            frame = frame[frame["risk_level"] == level.upper()]
        return [self.ip_alert(ip) for ip in frame.sort_values("rank").index[:limit]]

    def ip_detail(self, ip: str) -> dict[str, Any]:
        alert = self.ip_alert(ip)
        profile = self.network.ips[ip]
        lookups = self._lookups()
        users = sorted(self.network.ip_wallets.get(ip, ()), key=lambda address: -lookups["risk"][address])
        actor_counts: Counter = Counter(lookups["actor_of"][address] for address in users)
        transactions = []
        for index in sorted(profile.originated, key=lambda item: -self._row("transactions", self.flows.txs[item].txid)["risk_score"])[:150]:
            tx = self.flows.txs[index]
            tx_row = self._row("transactions", tx.txid)
            transactions.append({"txid": tx.txid, "timestamp": tx.time.isoformat(), "risk_score": float(tx_row["risk_score"]), "risk_level": tx_row["risk_level"],
                                 "total_output": round(tx.total_out, 8), "port": service_port(tx.src_port, tx.dst_port),
                                 "origin_confidence": round(self.network.origins[index].confidence, 3), "inputs": len(tx.inputs), "outputs": len(tx.outputs)})
        reasons = []
        if alert["flagged_wallets"]:
            reasons.append({"category": "network", "label": alert["top_reason"],
                            "detail": f"Risk-weighted share of its traffic: {alert['risk_score']:.0f}/100. {alert['flagged_share']:.0%} of its broadcasts come from wallets with direct evidence.",
                            "contribution": 0.0})
        if alert["nonstandard_port_share"] > 0.1:
            reasons.append({"category": "network", "label": f"{alert['nonstandard_port_share']:.0%} of the transactions it originated used non-standard ports", "detail": "", "contribution": 0.0})
        if alert["role"] == "relay node":
            reasons.append({"category": "network", "label": f"Behaves as a relay node ({alert['relay_ratio']:.0%} of its sightings pass on other parties' transactions)",
                            "detail": "Transactions first seen from it are not attributed to their spenders.", "contribution": 0.0})
        if alert["actors"] > 1:
            reasons.append({"category": "entity", "label": f"Used by {alert['actors']} actors ({alert['wallets']} wallets)", "detail": "", "contribution": 0.0})
        return _native({
            **alert, "reasons": reasons,
            "wallet_list": [{"wallet_id": address, "risk_score": float(lookups["risk"][address]), "risk_level": lookups["level"][address],
                             "broadcasts": int(self.network.wallets[address].ips[ip]), "actor_id": lookups["actor_of"][address]} for address in users[:150]],
            "actor_list": [{"actor_id": actor, "wallets_on_ip": uses, "risk_score": float(self._row("actors", actor)["risk_score"]),
                            "risk_level": self._row("actors", actor)["risk_level"], "wallets": int(self._row("actors", actor)["address_count"])}
                           for actor, uses in actor_counts.most_common(40)],
            "ports": [{"port": port, "count": uses} for port, uses in profile.ports.most_common()],
            "transactions": transactions,
        })

    # ── patterns & communities ──────────────────────────────────────────────
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

    # ── link analysis ───────────────────────────────────────────────────────
    def graph(self, focus_type: str, focus_id: str, depth: int = 1, max_nodes: int = GRAPH_NODE_LIMIT, mode: str = "neighbourhood") -> dict[str, Any]:
        """Link-analysis graph around a wallet, transaction, pattern, community, actor or IP.

        ``mode`` applies to wallets: ``neighbourhood`` (every transaction it took part in), or ``downstream`` /
        ``upstream`` to follow the money forward or backward in time through the largest flows.
        """
        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[str, dict[str, Any]] = {}
        limited = False
        lookups = self._lookups()
        stops: set[str] = set()

        def add_wallet(address: str) -> bool:
            nonlocal limited
            if address in nodes:
                return True
            if len(nodes) >= max_nodes:
                limited = True
                return False
            row = self._row("wallets", address)
            nodes[address] = {"id": address, "type": "Wallet", "label": _short(address), "risk_score": float(row["risk_score"]),
                              "risk_level": row["risk_level"], "is_seed": bool(row["is_seed"]), "is_focus": address == focus_id,
                              "entity_id": row["entity_id"], "actor_id": row["actor_id"], "service": bool(row["service_score"] >= 0.5)}
            return True

        def add_ip(ip: str, focus: bool = False) -> bool:
            nonlocal limited
            if not ip:
                return False
            if ip in nodes:
                return True
            if len(nodes) >= max_nodes:
                limited = True
                return False
            info = self.ip_info.get(ip, {})
            known = self._row("ips", ip) if self._has("ips", ip) else None
            nodes[ip] = {"id": ip, "type": "IP", "label": ip, "is_focus": focus, "country": info.get("country") or self.network.ip_country.get(ip, ""),
                         "asn": info.get("asn") or self.network.ip_asn.get(ip, 0), "as_org": info.get("as_org", ""),
                         "risk_score": float(known["risk_score"]) if known is not None else 0.0, "risk_level": known["risk_level"] if known is not None else "LOW",
                         "is_relay": bool(known is not None and known["relayed_count"] >= 3 and known["relay_ratio"] >= 0.5)}
            return True

        def add_tx_node(index: int) -> bool:
            nonlocal limited
            tx = self.flows.txs[index]
            if tx.txid in nodes:
                return True
            if len(nodes) >= max_nodes:
                limited = True
                return False
            row = self._row("transactions", tx.txid)
            nodes[tx.txid] = {"id": tx.txid, "type": "Transaction", "label": _short(tx.txid), "risk_score": float(row["risk_score"]),
                              "risk_level": row["risk_level"], "is_seed": False, "is_focus": tx.txid == focus_id, "timestamp": tx.time.isoformat()}
            return True

        def link_input(address: str, index: int, amount: float) -> None:
            txid = self.flows.txs[index].txid
            edges[f"{address}>{txid}"] = {"id": f"{address}>{txid}", "source": address, "target": txid, "type": "INPUT_FROM", "amount": round(amount, 8)}

        def link_output(index: int, address: str, amount: float) -> None:
            txid = self.flows.txs[index].txid
            edges[f"{txid}>{address}"] = {"id": f"{txid}>{address}", "source": txid, "target": address, "type": "OUTPUT_TO", "amount": round(amount, 8)}

        def link_ip(index: int, relays: bool = False) -> None:
            tx = self.flows.txs[index]
            if tx.src_ip and add_ip(tx.src_ip):
                origin = self.network.origins[index]
                edges[f"{tx.txid}@{tx.src_ip}"] = {"id": f"{tx.txid}@{tx.src_ip}", "source": tx.txid, "target": tx.src_ip, "type": "OBSERVED_IN",
                                                   "confidence": round(origin.confidence, 3), "attributed": origin.attributed}
            if relays:
                for observation in tx.observations[1:]:
                    if observation.src_ip != tx.src_ip and add_ip(observation.src_ip):
                        key = f"{tx.txid}~{observation.src_ip}"
                        edges.setdefault(key, {"id": key, "source": tx.txid, "target": observation.src_ip, "type": "RELAYED_BY",
                                               "delay": round(observation.ts - tx.observations[0].ts, 3)})

        def add_tx(index: int, with_ip: bool = False, relays: bool = False) -> None:
            if not add_tx_node(index):
                return
            tx = self.flows.txs[index]
            for address, amount in tx.inputs:
                if add_wallet(address):
                    link_input(address, index, amount)
            for address, amount in tx.outputs:
                if add_wallet(address):
                    link_output(index, address, amount)
            if with_ip:
                link_ip(index, relays)

        def wallet_txs(address: str, limit: int = 25) -> list[int]:
            indices = sorted(set(self.flows.received.get(address, [])) | set(self.flows.spent.get(address, [])))
            if len(indices) > limit:  # hubs: keep the riskiest transactions
                indices = sorted(indices, key=lambda index: -self._row("transactions", self.flows.txs[index].txid)["risk_score"])[:limit]
            return indices

        def trace(start: str, forward: bool, hops: int, fan: int = 3) -> None:
            """Follow the largest flows forward (where the money went) or backward (where it came from)."""
            add_wallet(start)
            frontier = [(start, float("-inf") if forward else float("inf"))]
            seen = {start}
            for _ in range(hops):
                following = []
                for address, moment in frontier:
                    if address != start and self._row("wallets", address)["service_score"] >= 0.5:
                        stops.add(address)  # a service pools everyone's funds: following it further says nothing
                        continue
                    if forward:
                        indices = [index for index in self.flows.spent.get(address, []) if self.flows.txs[index].ts >= moment][:fan]
                    else:
                        indices = [index for index in self.flows.received.get(address, []) if self.flows.txs[index].ts <= moment][-fan:]
                    for index in indices:
                        tx = self.flows.txs[index]
                        if not add_tx_node(index):
                            return
                        own = sum(amount for other, amount in (tx.inputs if forward else tx.outputs) if other == address)
                        (link_input if forward else lambda a, i, v: link_output(i, a, v))(address, index, own)
                        link_ip(index)
                        others = sorted(tx.outputs if forward else tx.inputs, key=lambda item: -item[1])[:fan]
                        for other, amount in others:
                            if other == address or not add_wallet(other):
                                continue
                            if forward:
                                link_output(index, other, amount)
                            else:
                                link_input(other, index, amount)
                            if other not in seen:
                                seen.add(other)
                                following.append((other, tx.ts))
                frontier = following

        if focus_type == "wallet":
            if mode in ("downstream", "upstream"):
                trace(focus_id, mode == "downstream", hops=max(2, min(depth * 3, 8)))
            else:
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
            add_tx(index, with_ip=True, relays=True)
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
        elif focus_type == "actor":
            members = lookups["actor_wallets"].get(focus_id, [])[:40]
            chosen = set(lookups["actor_wallets"].get(focus_id, []))
            for address in members:
                add_wallet(address)
            for address in members:
                for index in wallet_txs(address, 5):
                    tx = self.flows.txs[index]
                    if not add_tx_node(index):
                        break
                    for other, amount in tx.inputs:  # only the actor's own wallets and its direct counterparties in these transactions
                        if (other in chosen or len(tx.inputs) <= 3) and add_wallet(other):
                            link_input(other, index, amount)
                    for other, amount in sorted(tx.outputs, key=lambda item: -item[1])[:4]:
                        if add_wallet(other):
                            link_output(index, other, amount)
                    if set(tx.input_addresses) & chosen:
                        link_ip(index)
        elif focus_type == "ip":
            add_ip(focus_id, focus=True)
            profile = self.network.ips.get(focus_id)
            originated = sorted(profile.originated, key=lambda item: -self._row("transactions", self.flows.txs[item].txid)["risk_score"])[:30] if profile else []
            for index in originated:
                tx = self.flows.txs[index]
                if not add_tx_node(index):
                    break
                edges[f"{tx.txid}@{focus_id}"] = {"id": f"{tx.txid}@{focus_id}", "source": tx.txid, "target": focus_id, "type": "OBSERVED_IN",
                                                 "confidence": round(self.network.origins[index].confidence, 3), "attributed": True}
                for address, amount in tx.inputs[:4]:
                    if add_wallet(address):
                        link_input(address, index, amount)
                for address, amount in sorted(tx.outputs, key=lambda item: -item[1])[:2]:
                    if add_wallet(address):
                        link_output(index, address, amount)
        else:
            raise ValueError(f"Unsupported focus_type: {focus_type}")
        for address in stops:
            if address in nodes:
                nodes[address]["trace_stopped"] = True
        valid = [edge for edge in edges.values() if edge["source"] in nodes and edge["target"] in nodes]
        return _native({"graph_available": True, "source": "local", "mode": mode, "limited": limited, "nodes": list(nodes.values()), "edges": valid})

    # ── search ──────────────────────────────────────────────────────────────
    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Find wallets, transactions, IPs, entities, actors and patterns by (part of) their identifier."""
        needle = str(query or "").strip().lower()
        if len(needle) < 2:
            return []
        hits: list[tuple[int, dict[str, Any]]] = []

        def consider(kind: str, identifier: str, label: str, score: float, level: str | None) -> None:
            text = identifier.lower()
            place = text.find(needle)
            if place < 0:
                return
            order = 0 if text == needle else 1 if place == 0 else 2
            hits.append((order, {"type": kind, "id": identifier, "label": label, "risk_score": score, "risk_level": level}))

        lookups = self._lookups()
        for address in self.wallets.index:
            if needle in address.lower():
                consider("wallet", address, "Wallet", float(lookups["risk"][address]), lookups["level"][address])
        for txid, score, level in zip(self.transactions.index, self.transactions["risk_score"], self.transactions["risk_level"]):
            if needle in txid.lower():
                consider("transaction", txid, "Transaction", float(score), level)
        for ip, score, level in zip(self.ips.index, self.ips["risk_score"], self.ips["risk_level"]):
            consider("ip", ip, "IP", float(score), level)
        for actor, score, level in zip(self.actor_frame.index, self.actor_frame["risk_score"], self.actor_frame["risk_level"]):
            consider("actor", actor, "Actor", float(score), level)
        for entity, wallets in lookups["entity_wallets"].items():
            if needle == entity.lower():
                consider("wallet", wallets[0], f"Top wallet of entity {entity}", float(lookups["risk"][wallets[0]]), lookups["level"][wallets[0]])
        for pattern_id, pattern in self.patterns.items():
            consider("pattern", pattern_id, pattern["summary"], round(100 * pattern["strength"], 1), None)
        hits.sort(key=lambda item: (item[0], -item[1]["risk_score"]))
        return _native([hit for _, hit in hits[:limit]])

    # ── exports ─────────────────────────────────────────────────────────────
    def export_rows(self, kind: str, limit: int = 5000) -> tuple[list[str], list[list[Any]]]:
        """Flat (header, rows) of ranked leads for CSV export. ``kind``: wallets | transactions | actors | ips | patterns."""
        if kind == "wallets":
            header = ["rank", "wallet_id", "risk_score", "risk_level", "confidence", "is_seed", "entity_id", "actor_id", "top_reason",
                      *[f"points_{name}" for name in SIGNALS], "patterns", "tags"]
            rows = [[a["rank"], a["wallet_id"], a["risk_score"], a["risk_level"], a["confidence"], a["is_seed"], a["entity_id"], a["actor_id"], a["top_reason"],
                     *[a["contributions"][name] for name in SIGNALS], " ".join(a["patterns"]), " ".join(a["tags"])] for a in self.wallet_alerts(limit)]
        elif kind == "transactions":
            header = ["rank", "txid", "timestamp", "risk_score", "risk_level", "confidence", "top_reason", *[f"points_{name}" for name in SIGNALS],
                      "src_ip", "dst_port", "country", "origin_confidence", "relay_count", "total_input", "total_output", "fee", "patterns"]
            rows = [[a["rank"], a["txid"], a["timestamp"], a["risk_score"], a["risk_level"], a["confidence"], a["top_reason"],
                     *[a["contributions"][name] for name in SIGNALS], a["src_ip"], a["dst_port"], a["country"], a["origin_confidence"], a["relay_count"],
                     a["total_input"], a["total_output"], a["fee"], " ".join(a["patterns"])] for a in self.transaction_alerts(limit)]
        elif kind == "actors":
            header = ["rank", "actor_id", "risk_score", "risk_level", "wallets", "entities", "flagged_wallets", "seed_wallets", "received_value", "sent_value",
                      "ips", "asns", "countries", "nonstandard_port_share", "top_wallet", "top_reason"]
            rows = [[a[key] for key in header] for a in self.actor_alerts(limit)]
        elif kind == "ips":
            header = ["rank", "ip", "risk_score", "risk_level", "confidence", "country", "asn", "as_org", "role", "originated", "relayed", "wallets",
                      "flagged_wallets", "actors", "flagged_share", "value", "nonstandard_port_share", "first_seen", "last_seen", "top_reason"]
            rows = [[a[key] for key in header] for a in self.ip_alerts(limit, include_relays=True)]
        elif kind == "patterns":
            header = ["pattern_id", "type", "strength", "wallets", "transactions", "start_time", "end_time", "total_value", "summary"]
            rows = [[p["pattern_id"], p["type"], p["strength"], len(p["wallets"]), len(p["transactions"]), p["start_time"], p["end_time"], p["total_value"], p["summary"]]
                    for p in self.pattern_list()[:limit]]
        else:
            raise ValueError(f"Unsupported export kind: {kind}")
        return header, rows

    def report_markdown(self, kind: str, identifier: str) -> str:
        """A self-contained evidence sheet for one lead, ready to paste into a case file."""
        detail = {"wallet": self.wallet_detail, "transaction": self.transaction_detail, "actor": self.actor_detail, "ip": self.ip_detail}[kind](identifier)
        lines = [f"# {kind.capitalize()} lead: `{identifier}`", "",
                 f"- **Risk:** {detail['risk_score']:.1f} / 100 ({detail['risk_level']})" + (f", confidence {detail['confidence']:.0%}" if "confidence" in detail else ""),
                 f"- **Rank:** #{detail['rank']}", f"- **Summary:** {detail['top_reason']}", ""]
        if detail.get("contributions"):
            lines += ["## Why flagged", "", "| Signal | Points | Signal strength |", "|---|---|---|"]
            lines += [f"| {name} | {detail['contributions'][name]:.1f} | {detail['signals'][name]:.2f} |" for name in SIGNALS]
            lines.append("")
        if detail.get("reasons"):
            lines += ["## Evidence", ""]
            for reason in detail["reasons"]:
                points = f" (+{reason['contribution']:.1f})" if reason.get("contribution") else ""
                lines.append(f"- **{reason['label']}**{points}" + (f" - {reason['detail']}" if reason.get("detail") else ""))
            lines.append("")
        if kind == "wallet":
            lines += [f"## Ownership", "", f"- Entity `{detail['entity']['entity_id']}` with {count_of(detail['entity']['size'], 'address', 'addresses')}; "
                      f"actor `{detail['actor']['actor_id']}` with {count_of(detail['actor']['wallets'], 'wallet')} in "
                      f"{count_of(detail['actor']['entities'], 'entity', 'entities')}."]
            if detail["entity"].get("link"):
                lines.append(f"- Attached to the entity by {detail['entity']['link']['heuristic']} (`{_short(detail['entity']['link']['txid'])}`).")
            link = detail["actor"].get("link")
            if link:
                where = "".join([f", IP {link['ip']}" if link.get("ip") else "", f", address `{_short(link['address'])}`" if link.get("address") else ""])
                lines.append(f"- Joined to the actor because this entity and `{link['with']}` {link['description']} (transaction `{_short(link['txid'])}`{where}).")
            lines.append("")
            for title, key in (("Taint path back to the seed", "taint"), ("Path back to the laundering structure", "proceeds")):
                path = (detail.get(key) or {}).get("path") or []
                if path:
                    lines += [f"## {title}", "", " -> ".join(f"`{_short(step['wallet_id'])}`" for step in path), ""]
            if detail["network"]["ips"]:
                lines += ["## Network footprint", "", "| IP | Country | ASN | Broadcasts | Share | Wallets on IP | IP risk |", "|---|---|---|---|---|---|---|"]
                lines += [f"| {item['ip']} | {item['country']} | AS{item['asn']} {item.get('as_org', '')} | {item['count']} | {item['share']:.0%} | {item['wallets_on_ip']} | {item['risk_score']:.0f} |"
                          for item in detail["network"]["ips"][:10]]
                lines.append("")
            if detail["transactions"]:
                lines += ["## Transactions (most recent)", "", "| Time | Direction | Amount (BTC) | Risk | Txid |", "|---|---|---|---|---|"]
                lines += [f"| {item['timestamp']} | {item['direction']} | {item['amount']:.8f} | {item['risk_level']} | `{item['txid']}` |" for item in detail["transactions"][-15:]]
                lines.append("")
        elif kind == "transaction":
            lines += ["## Network observations", "", f"{detail['origin']['explanation']}", "", "| Time | From | To | Country | ASN | Role |", "|---|---|---|---|---|---|"]
            lines += [f"| {item['timestamp']} | {item['src_ip']}:{item['src_port']} | {item['dst_ip']}:{item['dst_port']} | {item['country']} | AS{item['asn']} | {item['role']} |"
                      for item in detail["observations"][:12]]
            lines += ["", "## Inputs and outputs", "", "| Side | Wallet | Amount (BTC) | Risk |", "|---|---|---|---|"]
            lines += [f"| in | `{item['wallet_id']}` | {item['amount']:.8f} | {item['risk_level']} |" for item in detail["inputs"][:20]]
            lines += [f"| out | `{item['wallet_id']}` | {item['amount']:.8f} | {item['risk_level']} |" for item in detail["outputs"][:20]]
            lines.append("")
        elif kind == "actor":
            lines += ["## Wallets (highest risk first)", "", "| Wallet | Entity | Risk |", "|---|---|---|"]
            lines += [f"| `{item['wallet_id']}` | {item['entity_id']} | {item['risk_score']:.1f} {item['risk_level']} |" for item in detail["top_wallets"][:25]]
            lines += ["", "## Infrastructure", "", "| IP | Country | ASN | Broadcasts | Wallets on IP | IP risk |", "|---|---|---|---|---|---|"]
            lines += [f"| {item['ip']} | {item['country']} | AS{item['asn']} {item['as_org']} | {item['count']} | {item['wallets_on_ip']} | {item['risk_score']:.0f} |" for item in detail["ip_list"][:25]]
            lines.append("")
        elif kind == "ip":
            lines += [f"- **Location:** {detail['country'] or 'unknown'}, AS{detail['asn']} {detail['as_org']}", f"- **Role:** {detail['role']}", "",
                      "## Wallets broadcasting from this IP", "", "| Wallet | Actor | Broadcasts | Risk |", "|---|---|---|---|"]
            lines += [f"| `{item['wallet_id']}` | {item['actor_id']} | {item['broadcasts']} | {item['risk_score']:.1f} {item['risk_level']} |" for item in detail["wallet_list"][:25]]
            lines.append("")
        lines += ["---", "_Investigative lead generated offline from the supplied dataset. Not proof of identity, ownership or criminal activity._", ""]
        return "\n".join(lines)


__all__ = ["InvestigationViews", "_iso", "_native", "_short", "GRAPH_NODE_LIMIT"]
