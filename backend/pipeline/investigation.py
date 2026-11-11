"""One analysed dataset: fitted models, scores, and the queries the API answers.

``Investigation.build`` runs every model on the records. The result can be saved
as a JSON snapshot and restored without refitting (``to_snapshot`` /
``from_snapshot``), and ``rescore`` re-runs only risk propagation and fusion when
the investigator changes the seed wallets.

Three levels of "who" are kept side by side:

* **address** - one wallet address, the unit alerts are raised on;
* **entity**  - addresses one party controls, by ownership heuristics (co-spend,
  unambiguous change, laundering-structure continuity);
* **actor**   - entities operated together: two of three clues agree (same broadcast
  IP, a direct payment between them, deposits into the same service address).
  Behaviour (the anomaly model, port habits, rotation across networks) is measured
  per actor, because a single address or a small entity fragment shows too little.

The query methods (alerts, details, link-analysis graphs, search, exports) live in
``backend.pipeline.views`` and are mixed into this class.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Callable

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from backend.correlation.flows import build_flow_index, FlowIndex, service_port, STANDARD_PORTS
from backend.correlation.network import NetworkCorrelation, build_network_correlation
from backend.ml.anomaly import DETECTOR_DESCRIPTIONS, DETECTORS, AnomalyEnsemble
from backend.ml.entities import ACTOR_LINK_TEXT, ActorGroups, EntityClustering, cluster_entities, detect_communities, link_actors
from backend.ml.features import (ENTITY_MODEL_FEATURES, FEATURE_LABELS, TRANSACTION_MODEL_FEATURES, build_entity_features,
                                 build_transaction_features, build_wallet_features)
from backend.ml.patterns import HOP_ROLES, ROLE_WEIGHTS, TYPE_WEIGHTS, detect_all
from backend.ml.propagation import NetworkAssociation, PropagationResult, propagate_exposure, propagate_network_association, propagate_proceeds, propagate_risk
from backend.ml.scoring import (ACTOR_INHERITANCE, ANOMALY_ALONE, CASH_OUT_WEIGHT, ENTITY_INHERITANCE, NETWORK_SEED, PROCEEDS_WEIGHT, SERVICE_DISCOUNT,
                                SIGNALS, TRANSACTION_WEIGHTS, UPSTREAM_WEIGHT, WALLET_WEIGHTS, anomaly_signal, confidence, fuse, group_inherit, level_for,
                                transaction_network_signal, wallet_network_signal)
from backend.pipeline.views import InvestigationViews, _iso, _native

SNAPSHOT_VERSION = 3
# Taint kept per change of hands once funds are being spent by a wallet that is not part of any operation
# (0.9 while they are still being laundered): the payees of an unwitting recipient are two steps from the crime.
SPENT_DECAY = 0.6
ATTRIBUTION_LIMIT = 1500
MIN_MODEL_ROWS = 8
# A structure must be this strong before its wallets are merged into one entity.
STRUCTURE_OWNERSHIP_STRENGTH = 0.5
# A wallet inside a structure counts as a source for exposure/proceeds from this evidence strength.
STRUCTURE_SOURCE_STRENGTH = 0.5
# Pattern roles of wallets that keep what a structure wallet did not pass on (probably its owner's change).
LEFTOVER_ROLES = frozenset({"pass_through_change", "layering_change"})
# Roles of pure recipients: scored through the funds they received (proceeds), not through the role itself.
RECIPIENT_ROLES = frozenset({"peel_recipient", "pass_through_recipient", "layering_exit", "chain_exit"})
ILLICIT_LABELS = ("RANSOMWARE", "PEELING_CHAIN", "LAYERING_LIKE", "RAPID_TRANSFER", "HIGH_NETWORK_DIVERSITY")
LICIT_LABELS = ("NORMAL", "EXCHANGE_LIKE")
PATTERN_LABELS = {"peeling_chain": "PEELING_CHAIN", "layering": "LAYERING_LIKE", "rapid_passthrough": "RAPID_TRANSFER", "coinjoin": "MIXING_LIKE"}
PATTERN_NAMES = {"peeling_chain": "Peeling-chain detector", "layering": "Layering (fan-out/fan-in) detector",
                 "rapid_passthrough": "Rapid pass-through detector", "coinjoin": "CoinJoin detector"}
PATTERN_DESCRIPTIONS = {"peeling_chain": "Chains of small peels with the remainder moving to fresh addresses; strength grows with length and speed.",
                        "layering": "Fan-out to fresh addresses that each forward their own funds and reconverge on one address with most of the value.",
                        "rapid_passthrough": "Wallets forwarding nearly everything they receive within minutes, funded by that receipt alone.",
                        "coinjoin": "Many-input transactions with several equal-value outputs."}
LEVEL_NAMES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
Progress = Callable[[str, float], None]


def _levels(scores: np.ndarray) -> dict[str, int]:
    scores = np.asarray(scores, dtype=float)
    critical, high, medium = int((scores >= 80).sum()), int((scores >= 60).sum()), int((scores >= 35).sum())
    return {"CRITICAL": critical, "HIGH": high - critical, "MEDIUM": medium - high, "LOW": int(scores.size) - medium}


class Investigation(InvestigationViews):
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.ip_info: dict[str, dict[str, Any]] = {}
        self.ingestion: dict[str, Any] = {}
        self.flows: FlowIndex
        self.network: NetworkCorrelation
        self.clustering: EntityClustering
        self.actors: ActorGroups
        self.community_of: dict[str, int] = {}
        self.patterns: dict[str, dict[str, Any]] = {}
        self.wallets: pd.DataFrame = pd.DataFrame()
        self.entities: pd.DataFrame = pd.DataFrame()
        self.actor_frame: pd.DataFrame = pd.DataFrame()
        self.transactions: pd.DataFrame = pd.DataFrame()
        self.ips: pd.DataFrame = pd.DataFrame()
        self.attributions: dict[str, list[dict[str, Any]]] = {}
        self.tx_attributions: dict[str, list[dict[str, Any]]] = {}
        self.taint: dict[str, dict[str, Any]] = {}
        self.proceeds: dict[str, dict[str, Any]] = {}
        self.exposure: dict[str, dict[str, Any]] = {}
        self.association: NetworkAssociation = NetworkAssociation({}, {}, {}, {})
        self.propagation: PropagationResult | None = None
        self.seeds: list[str] = []
        self.overview: dict[str, Any] = {}
        self._coinjoins: set[str] = set()

    # ── building ────────────────────────────────────────────────────────────
    @classmethod
    def build(cls, records: list[dict[str, Any]], seeds: list[str] | None = None, contamination: float = 0.05, random_state: int = 42,
              ip_info: dict[str, dict[str, Any]] | None = None, ingestion: dict[str, Any] | None = None,
              progress: Progress | None = None) -> "Investigation":
        def step(label: str, fraction: float) -> None:
            if progress is not None:
                progress(label, fraction)

        self = cls()
        self.records = records
        self.ip_info = ip_info or {}
        self.ingestion = ingestion or {}
        step("Indexing transaction flows", 0.05)
        self.flows = build_flow_index(records)
        step("Detecting laundering patterns", 0.12)
        found = detect_all(self.flows)
        self._coinjoins = {pattern.txids[0] for pattern in found if pattern.type == "coinjoin"}
        step("Correlating network observations", 0.2)
        self.network = build_network_correlation(self.flows, self._coinjoins)
        step("Clustering entities", 0.28)
        structures = [(pattern.pattern_id, group) for pattern in found if pattern.strength >= STRUCTURE_OWNERSHIP_STRENGTH for group in pattern.owner_groups]
        self.clustering = cluster_entities(self.flows, self._coinjoins, structures=structures)
        sizes = {address: self.clustering.size(address) for address in self.clustering.entity_of}

        step("Engineering features", 0.36)
        wallets = build_wallet_features(self.flows, self.network, sizes).set_index("wallet_id", drop=False)
        entities = build_entity_features(self.flows, self.network, self.clustering.entity_of, self.clustering.members, wallets, self._coinjoins)
        self.entities = entities.set_index("entity_id", drop=False)
        step("Linking actors across shared infrastructure", 0.44)
        self.actors = link_actors(self.flows, self.network.origins, self.clustering.entity_of, self._coinjoins,
                                  service=dict(zip(self.entities.index, self.entities["service_score"])))
        wallets["entity_id"] = wallets["wallet_id"].map(self.clustering.entity_of)
        wallets["actor_id"] = wallets["entity_id"].map(self.actors.actor_of)
        actor_addresses: dict[str, list[str]] = defaultdict(list)
        for address, actor in zip(wallets.index, wallets["actor_id"]):
            actor_addresses[actor].append(address)
        actor_frame = build_entity_features(self.flows, self.network, dict(zip(wallets.index, wallets["actor_id"])), dict(actor_addresses), wallets, self._coinjoins)
        actor_frame = actor_frame.rename(columns={"entity_id": "actor_id"}).set_index("actor_id", drop=False)
        actor_frame["entity_count"] = [len(self.actors.members[actor]) for actor in actor_frame.index]

        step("Embedding the entity graph and finding communities", 0.52)
        communities = detect_communities(self.flows, self.clustering, random_state=random_state)
        self.community_of = {str(key): int(value) for key, value in communities.community_of.items()}
        wallets["cluster_id"] = wallets["entity_id"].map(communities.community_of).fillna(-1).astype(int)
        transactions = build_transaction_features(self.flows, self.network).set_index("txid", drop=False)

        step("Fitting the anomaly ensemble on actor behaviour", 0.62)
        self.attributions = self._fit_anomaly(actor_frame, actor_frame["spend_count"] > 0, ENTITY_MODEL_FEATURES, contamination, random_state)
        step("Fitting the anomaly ensemble on transactions", 0.76)
        self.tx_attributions = self._fit_anomaly(transactions, np.ones(len(transactions), bool), TRANSACTION_MODEL_FEATURES, contamination, random_state)
        for column in ("anomaly_percentile", "anomaly_agreement", *(f"det_{name}" for name in DETECTORS)):
            wallets[column] = wallets["actor_id"].map(actor_frame[column]).to_numpy()

        step("Scoring", 0.88)
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
        tx_view = self.flows.txs
        by_txid = self.flows.by_txid
        ordered = [tx_view[by_txid[txid]] for txid in transactions.index]
        transactions["timestamp"] = [tx.time.isoformat() for tx in ordered]
        transactions["src_ip"] = [tx.src_ip for tx in ordered]
        transactions["dst_ip"] = [tx.dst_ip for tx in ordered]
        transactions["src_port"] = [tx.src_port for tx in ordered]
        transactions["dst_port"] = [tx.dst_port for tx in ordered]
        transactions["country"] = [tx.country for tx in ordered]
        transactions["asn"] = [tx.asn for tx in ordered]
        transactions["label"] = [tx.label for tx in ordered]
        self.wallets, self.actor_frame, self.transactions = wallets, actor_frame, transactions
        self.rescore(seeds or [])
        step("Done", 1.0)
        return self

    @staticmethod
    def _fit_anomaly(frame: pd.DataFrame, mask: Any, columns: list[str], contamination: float, random_state: int) -> dict[str, list[dict[str, Any]]]:
        """Fit the ensemble on the rows in ``mask``, write percentile/agreement/detector columns, return attributions for the top rows."""
        mask = np.asarray(mask, dtype=bool)
        for name in DETECTORS:
            frame[f"det_{name}"] = 0.0
        frame["anomaly_percentile"] = 0.0
        frame["anomaly_agreement"] = 0.0
        active = frame.loc[mask, columns]
        if len(active) < MIN_MODEL_ROWS:
            return {}
        model = AnomalyEnsemble(contamination, random_state).fit(active)
        scores = model.score(active)
        for name in DETECTORS:
            frame.loc[mask, f"det_{name}"] = scores[f"{name} percentile"].to_numpy()
        frame.loc[mask, "anomaly_percentile"] = scores["ensemble_percentile"].to_numpy()
        frame.loc[mask, "anomaly_agreement"] = scores["agreement"].to_numpy()
        top = scores["ensemble_percentile"].sort_values(ascending=False).index[: min(ATTRIBUTION_LIMIT, max(1, int(len(active) * 0.1)))]
        return {str(key): value for key, value in zip(top, model.attributions(active.loc[top]))}

    # ── scoring (re-run when seeds change) ──────────────────────────────────
    def _codes(self) -> None:
        """Integer codes and per-wallet lookups that never change after ``build``."""
        wallets = self.wallets
        self._position = {address: index for index, address in enumerate(wallets.index)}
        self._entity_codes = pd.factorize(wallets["entity_id"])[0]
        self._actor_codes = pd.factorize(wallets["actor_id"])[0]
        self._service = wallets["entity_id"].map(self.entities["service_score"]).to_numpy(dtype=float)
        hop = np.zeros(len(wallets))
        left = np.zeros(len(wallets))
        mix = np.zeros(len(wallets))
        for index, refs in enumerate(wallets["pattern_refs"]):
            for _, role, weight in refs:
                if role in HOP_ROLES:
                    hop[index] = max(hop[index], weight)
                elif role in LEFTOVER_ROLES:
                    left[index] = max(left[index], weight)
                elif role not in RECIPIENT_ROLES:
                    mix[index] = max(mix[index], weight)
        self._pattern_hop, self._pattern_left, self._pattern_mix = hop, left, mix
        actors = self.actor_frame
        self._network_base = wallet_network_signal(
            wallets["actor_id"].map(actors["nonstandard_port_share"]).to_numpy(dtype=float),
            wallets["actor_id"].map(actors["broadcasts"]).to_numpy(dtype=float),
            wallets["actor_id"].map(actors["relay_asns"]).to_numpy(dtype=float),
        )

    def rescore(self, seeds: list[str]) -> list[str]:
        """Propagate risk from ``seeds`` and fuse all signals; returns seeds not present in the data."""
        if not hasattr(self, "_position"):
            self._codes()
        wallets, transactions, flows = self.wallets, self.transactions, self.flows
        addresses = list(wallets.index)
        position = self._position
        count = len(addresses)
        service = self._service
        service_map = {address: value for address, value in zip(addresses, service) if value > 0}

        # 1. Downstream taint from the seeds (and the addresses co-owned with them).
        requested = [str(seed).strip() for seed in seeds if str(seed).strip()]
        unknown = [seed for seed in dict.fromkeys(requested) if seed not in flows.first_seen]
        hop_own = self._pattern_hop * (1 - SERVICE_DISCOUNT * service)  # a service is not a mule
        # Wallets that move funds as part of an operation: seeds, wallets inside structures, and the wallets
        # owned or operated with them. Taint they pass on stays strong; taint spent by anyone else fades fast.
        implicated = hop_own >= STRUCTURE_SOURCE_STRENGTH
        for seed in requested:
            if seed in position:
                implicated[position[seed]] = True
        involved = np.zeros(count, dtype=bool)
        for codes in (self._entity_codes, self._actor_codes):
            involved |= (np.bincount(codes, weights=implicated, minlength=int(codes.max()) + 1) > 0)[codes]
        launderers = frozenset(addresses[index] for index in np.flatnonzero(involved & (service < 0.5)))
        self.propagation = propagate_risk(flows, requested, self.clustering, service=service_map, launderers=launderers, spent_decay=SPENT_DECAY)
        self.seeds = self.propagation.seeds
        self.taint = {address: info.to_dict() for address, info in self.propagation.wallets.items()}
        is_seed = np.zeros(count, dtype=bool)
        for seed in self.seeds:
            is_seed[position[seed]] = True
        down = np.zeros(count)
        for address, info in self.taint.items():
            down[position[address]] = info["score"]

        # 2. Wallets inside laundering structures: exposure of what was sent in, proceeds of what came out.
        sources = {address: float(hop_own[index]) for index, address in enumerate(addresses)
                   if hop_own[index] >= STRUCTURE_SOURCE_STRENGTH and service[index] < 0.5}
        exposure = propagate_exposure(flows, sources, service=service_map)
        self.exposure = {address: info.to_dict() for address, info in exposure.items()}
        up = np.zeros(count)
        for address, info in exposure.items():
            up[position[address]] = info.score
        proceed_sources = dict(sources)
        for address, info in exposure.items():  # a wallet that mostly funds a structure taints its other outputs too
            if info.score >= STRUCTURE_SOURCE_STRENGTH:
                proceed_sources.setdefault(address, float(info.score))
        proceeds_wallets, proceeds_txs = propagate_proceeds(flows, proceed_sources, self.clustering, service=service_map,
                                                            launderers=launderers, spent_decay=SPENT_DECAY)
        self.proceeds = {address: info.to_dict() for address, info in proceeds_wallets.items()}
        proceeds = np.zeros(count)
        for address, info in proceeds_wallets.items():
            proceeds[position[address]] = info.score

        # 3. Each wallet's own evidence.
        taint_parts = np.vstack([down, UPSTREAM_WEIGHT * up, PROCEEDS_WEIGHT * proceeds])
        taint_own = taint_parts.max(axis=0)
        taint_kind = np.array(["downstream", "upstream", "proceeds"], dtype=object)[taint_parts.argmax(axis=0)]
        taint_kind[taint_own <= 0] = ""
        structural = np.maximum(hop_own, self._pattern_left)
        pattern_own = np.maximum(structural, self._pattern_mix)
        pattern_kind = np.where(pattern_own > 0, "role", "").astype(object)
        cash_out = CASH_OUT_WEIGHT * np.maximum(down, proceeds) * np.clip(service / 0.5, 0, 1)  # dirty funds arriving at a service
        better = cash_out > pattern_own
        pattern_own = np.where(better, cash_out, pattern_own)
        pattern_kind[better] = "cash_out"

        # 4. Evidence shared with the wallets it is operated with (same owner, then same actor).
        pattern, taint = pattern_own.copy(), taint_own.copy()
        pattern_source = np.full(count, -1, dtype=np.int64)
        taint_source = np.full(count, -1, dtype=np.int64)
        damp = 1 - service  # nothing is inherited across a service's address pool
        for codes, factor, name in ((self._entity_codes, ENTITY_INHERITANCE, "entity"), (self._actor_codes, ACTOR_INHERITANCE, "actor")):
            shared, source = group_inherit(structural, codes)
            shared = factor * damp * shared
            better = shared > pattern + 1e-9
            pattern = np.where(better, shared, pattern)
            pattern_kind[better] = name
            pattern_source[better] = source[better]
            shared, source = group_inherit(taint_own, codes)
            shared = factor * damp * shared
            better = shared > taint + 1e-9
            taint = np.where(better, shared, taint)
            taint_kind[better] = name
            taint_source[better] = source[better]

        # 5. Network layer: the actor's own port/rotation habits, then risk shared through IPs.
        network_own = self._network_base * (1 - SERVICE_DISCOUNT * service)
        direct = np.where(is_seed, 1.0, np.maximum.reduce([pattern, taint, NETWORK_SEED * network_own]))
        self.association = propagate_network_association(self.network, dict(zip(addresses, direct)))
        association = np.zeros(count)
        for address, value in self.association.signal.items():
            association[position[address]] = value
        association = association * (1 - SERVICE_DISCOUNT * service)
        network = 1 - (1 - network_own) * (1 - association)

        # 6. Fuse.
        anomaly = anomaly_signal(wallets["anomaly_percentile"]) * (1 - SERVICE_DISCOUNT * service)
        signals = np.column_stack([anomaly, pattern, taint, network])
        weights = np.tile(WALLET_WEIGHTS, (count, 1))
        taint_column = SIGNALS.index("taint")
        weights[is_seed, taint_column] = 1.0  # a known illicit wallet is maximal risk by definition
        signals[is_seed, taint_column] = 1.0
        taint_kind[is_seed] = "seed"
        for address in self.propagation.co_owned:
            taint_kind[position[address]] = "co_owned"
        inherited = ((pattern > pattern_own + 1e-9) | (taint > taint_own + 1e-9)) & (np.maximum(pattern_own, taint_own) < 0.2)
        self._apply(wallets, signals, weights, is_seed, inherited)
        wallets["taint_kind"], wallets["pattern_kind"] = taint_kind, pattern_kind
        wallets["taint_source"] = [addresses[index] if index >= 0 else "" for index in taint_source]
        wallets["pattern_source"] = [addresses[index] if index >= 0 else "" for index in pattern_source]
        wallets["taint_own"], wallets["structural"] = np.round(taint_own, 4), np.round(structural, 4)
        wallets["received_dirty"] = np.round(np.maximum(down, proceeds), 4)
        wallets["network_own"], wallets["association"] = np.round(network_own, 4), np.round(association, 4)
        wallets["service_score"] = np.round(service, 4)

        # Transactions: their own shape, the structures they belong to, the funds they spend and how they were relayed.
        wallet_taint = dict(zip(addresses, signals[:, taint_column]))
        origins = self.network.origins
        dirtiness = self.association.ip_dirtiness
        tx_taint = np.zeros(len(transactions))
        ip_share = np.zeros(len(transactions))
        seed_taint, structure_taint = self.propagation.transactions, proceeds_txs
        for index, txid in enumerate(transactions.index):
            tx = flows.txs[flows.by_txid[txid]]
            spent = sum(amount * wallet_taint.get(address, 0.0) for address, amount in tx.inputs) / tx.total_in if tx.total_in > 0 else 0.0
            tx_taint[index] = max(seed_taint.get(txid, 0.0), PROCEEDS_WEIGHT * structure_taint.get(txid, 0.0), spent)
            origin = origins[tx.index]
            if origin.attributed:
                ip_share[index] = dirtiness.get(origin.ip, 0.0)
        tx_pattern = np.array([max((ref[2] for ref in refs), default=0.0) for refs in transactions["pattern_refs"]])
        tx_signals = np.column_stack([
            anomaly_signal(transactions["anomaly_percentile"]), tx_pattern, tx_taint,
            transaction_network_signal(transactions["nonstandard_port"], transactions["ip_wallet_count"], ip_share),
        ])
        self._apply(transactions, tx_signals, np.tile(TRANSACTION_WEIGHTS, (len(transactions), 1)), np.zeros(len(transactions), bool))
        transactions["ip_flagged_share"] = np.round(ip_share, 4)
        self._score_groups()
        self.overview = self._build_overview()
        return unknown

    @staticmethod
    def _apply(frame: pd.DataFrame, signals: np.ndarray, weights: np.ndarray, is_seed: np.ndarray, inherited: np.ndarray | None = None) -> None:
        risk, contributions = fuse(signals, weights, ANOMALY_ALONE)
        for index, name in enumerate(SIGNALS):
            frame[f"signal_{name}"] = np.round(signals[:, index], 4)
            frame[f"contrib_{name}"] = np.round(contributions[:, index], 3)
        frame["risk_score"] = np.round(risk, 2)
        frame["risk_level"] = [level_for(score) for score in risk]
        frame["confidence"] = np.round(confidence(weights * signals, frame["anomaly_agreement"], is_seed, inherited), 3)
        frame["is_seed"] = is_seed
        order = np.argsort(-risk, kind="stable")
        ranks = np.empty(len(frame), dtype=int)
        ranks[order] = np.arange(1, len(frame) + 1)
        frame["rank"] = ranks

    def _score_groups(self) -> None:
        """Roll wallet risk up to actors and IPs so they can be ranked as leads of their own."""
        wallets, actors = self.wallets, self.actor_frame
        grouped = wallets.groupby("actor_id", sort=False)["risk_score"]
        strongest = grouped.max().reindex(actors.index).to_numpy()
        # An actor is as risky as its riskiest wallet only when the evidence is widespread: full weight once a
        # quarter of its wallets are at MEDIUM or above, half weight when a single wallet of a large actor is
        # (an exchange whose deposit address received dirty funds is a cash-out venue, not a criminal operation).
        implicated = (wallets["risk_score"] >= 35).groupby(wallets["actor_id"], sort=False).sum().reindex(actors.index).to_numpy()
        coverage = np.clip(implicated / np.maximum(0.25 * actors["address_count"].to_numpy(dtype=float), 1.0), 0.0, 1.0)
        actors["max_wallet_risk"] = strongest
        actors["risk_score"] = np.round(strongest * (0.5 + 0.5 * coverage), 2)
        actors["mean_risk"] = grouped.mean().reindex(actors.index).round(2).to_numpy()
        flagged = wallets["risk_score"] >= 60
        actors["flagged_wallets"] = flagged.groupby(wallets["actor_id"], sort=False).sum().reindex(actors.index).to_numpy()
        actors["seed_wallets"] = wallets["is_seed"].groupby(wallets["actor_id"], sort=False).sum().reindex(actors.index).to_numpy()
        actors["risk_level"] = [level_for(score) for score in actors["risk_score"]]
        order = np.lexsort((-actors["received_value"].to_numpy(), -actors["flagged_wallets"].to_numpy(), -actors["risk_score"].to_numpy()))
        ranks = np.empty(len(actors), dtype=int)
        ranks[order] = np.arange(1, len(actors) + 1)
        actors["rank"] = ranks

        # An IP's risk is the risk of the traffic it originated: wallet risk weighted by how often each wallet broadcast from it.
        risk = dict(zip(wallets.index, wallets["risk_score"]))
        rows = []
        for ip, profile in self.network.ips.items():
            users = self.network.ip_wallets.get(ip, ())
            total = weighted = 0.0
            high = 0
            for address in users:
                uses = self.network.wallets[address].ips[ip]
                total += uses
                weighted += uses * risk[address]
                high += risk[address] >= 60
            info = self.ip_info.get(ip, {})
            originated = len(profile.originated)
            confidences = [self.network.origins[index].confidence for index in profile.originated]
            rows.append({
                "ip": ip, "country": info.get("country") or self.network.ip_country.get(ip, ""), "asn": info.get("asn") or self.network.ip_asn.get(ip, 0),
                "as_org": info.get("as_org", ""), "scope": info.get("scope", ""),
                "first_seen_count": profile.first_seen, "relayed_count": profile.later_seen, "originated": originated,
                "relay_ratio": round(profile.relay_ratio, 4), "wallets": len(users), "flagged_wallets": int(high),
                "value": round(profile.value, 8), "nonstandard_port_share": round(profile.nonstandard_port_ratio, 4),
                "first_ts": profile.first_ts if originated else None, "last_ts": profile.last_ts if originated else None,
                # Smoothed with one clean pseudo-broadcast, so a single flagged spend does not condemn an IP.
                "risk_score": round(weighted / (total + 1.0), 2) if total else 0.0,
                "flagged_share": round(self.association.ip_dirtiness.get(ip, 0.0), 4),
                "confidence": round(float(np.mean(confidences)) * (1 - float(np.exp(-originated / 3))), 3) if confidences else 0.0,
            })
        ips = pd.DataFrame(rows, columns=["ip", "country", "asn", "as_org", "scope", "first_seen_count", "relayed_count", "originated", "relay_ratio", "wallets",
                                          "flagged_wallets", "value", "nonstandard_port_share", "first_ts", "last_ts", "risk_score", "flagged_share", "confidence"])
        ips = ips.set_index("ip", drop=False)
        ips["risk_level"] = [level_for(score) for score in ips["risk_score"]]
        order = np.lexsort((-ips["originated"].to_numpy(), -ips["risk_score"].to_numpy())) if len(ips) else np.zeros(0, dtype=int)
        ranks = np.empty(len(ips), dtype=int)
        ranks[order] = np.arange(1, len(ips) + 1)
        ips["rank"] = ranks
        self.ips = ips

    # ── overview & evaluation ───────────────────────────────────────────────
    def _build_overview(self) -> dict[str, Any]:
        wallets, transactions, actors, ips = self.wallets, self.transactions, self.actor_frame, self.ips
        txs = self.flows.txs
        members = self.clustering.members
        communities = {value for value in self.community_of.values() if value >= 0}
        pattern_counts = Counter(pattern["type"] for pattern in self.patterns.values())
        high = int((wallets["risk_score"] >= 60).sum())
        active_actors = actors["spend_count"] > 0
        origins = self.network.origins
        observed = sum(1 for tx in txs for observation in tx.observations if observation.src_ip)  # sightings that name a peer
        attributed = sum(1 for origin in origins if origin.attributed)
        relay_nodes = int(((ips["relay_ratio"] >= 0.5) & (ips["relayed_count"] >= 3)).sum()) if len(ips) else 0
        links = self.clustering.link_counts
        models = [
            *({"name": name, "family": "anomaly detection", "level": "actor behaviour", "description": DETECTOR_DESCRIPTIONS[name],
               "flagged": int((actors.loc[active_actors, f"det_{name}"] >= 0.95).sum())} for name in DETECTORS),
            *({"name": name, "family": "anomaly detection", "level": "transaction", "description": DETECTOR_DESCRIPTIONS[name],
               "flagged": int((transactions[f"det_{name}"] >= 0.95).sum())} for name in DETECTORS),
            {"name": "Ownership heuristics", "family": "entity clustering", "level": "address -> entity",
             "description": "Union-find over co-spent inputs (CoinJoins excluded), change addresses (unambiguous, or the only new output not paid to a service) "
                            "and laundering-structure continuity.",
             "flagged": sum(1 for items in members.values() if len(items) > 1)},
            {"name": "Actor linking (two agreeing clues)", "family": "entity clustering", "level": "entity -> actor",
             "description": "Entities are operated together when two of three clues agree: same broadcast IP, a direct payment between them, "
                            "deposits into the same service address. Services and widely shared IPs are excluded.",
             "flagged": int((actors["entity_count"] > 1).sum())},
            {"name": "DeepWalk embeddings + HDBSCAN", "family": "entity clustering", "level": "entity -> community",
             "description": "Random-walk (PPMI + SVD) embeddings of the entity payment graph, density-clustered into communities.",
             "flagged": len(communities)},
            *({"name": PATTERN_NAMES[kind], "family": "pattern detection", "level": "transaction flow", "description": PATTERN_DESCRIPTIONS[kind],
               "flagged": pattern_counts.get(kind, 0)} for kind in PATTERN_NAMES),
            {"name": "First-seen origin inference", "family": "network correlation", "level": "transaction -> IP",
             "description": "The earliest relay of each transaction is its origin; confidence rises with its lead time and falls for IPs that behave as relay nodes.",
             "flagged": attributed},
            {"name": "Haircut taint propagation", "family": "risk propagation", "level": "wallet",
             "description": "Time-ordered, value-weighted taint from seed wallets and their co-owned addresses; decays per hop between entities, not within one.",
             "flagged": sum(1 for info in self.taint.values() if info["score"] > 0.05)},
            {"name": "Structure proceeds propagation", "family": "risk propagation", "level": "wallet",
             "description": "The same haircut from wallets inside detected laundering structures: finds cash-out points and recipients without any seed.",
             "flagged": sum(1 for info in self.proceeds.values() if info["score"] > 0.05)},
            {"name": "Upstream exposure propagation", "family": "risk propagation", "level": "wallet",
             "description": "Reverse-time pass from structure wallets: sources that sent most of their value into a laundering structure (max 2 hops).",
             "flagged": len(self.exposure)},
            {"name": "Network association propagation", "family": "risk propagation", "level": "wallet <-> IP",
             "description": "Wallets inherit the share of flagged traffic on the IPs they broadcast from (leave-one-out, two rounds).",
             "flagged": sum(1 for value in self.association.signal.values() if value > 0.2)},
            {"name": "Gated noisy-OR fusion with Shapley attribution", "family": "risk scoring", "level": "wallet + transaction",
             "description": "Combines anomaly, pattern, taint and network signals; anomaly counts in full only when corroborated; exact Shapley values split the score by signal.",
             "flagged": high},
        ]
        return _native({
            "stats": {
                "transactions": len(txs), "observations": observed, "wallets": len(wallets), "ips": len(self.ip_info) or len(ips),
                "origin_ips": int((ips["originated"] > 0).sum()) if len(ips) else 0,
                "entities": len(members), "multi_address_entities": sum(1 for items in members.values() if len(items) > 1),
                "actors": len(actors), "multi_entity_actors": int((actors["entity_count"] > 1).sum()),
                "communities": len(communities), "countries": len({tx.country for tx in txs if tx.country}),
                "asns": len({tx.asn for tx in txs if tx.asn}),
                "start_time": txs[0].time.isoformat() if txs else None, "end_time": txs[-1].time.isoformat() if txs else None,
                "coinjoins_excluded_from_cio": self.clustering.excluded_coinjoins,
                "total_value": round(sum(tx.total_out for tx in txs), 8),
            },
            "network": {
                "observations": observed, "multi_observed_transactions": sum(1 for tx in txs if tx.relay_count > 1),
                "attributed_transactions": attributed, "attributed_share": round(attributed / len(txs), 4) if txs else 0.0,
                "mean_origin_confidence": round(float(np.mean([origin.confidence for origin in origins])), 4) if origins else 0.0,
                "relay_nodes": relay_nodes, "skipped_shared_ips": len(self.actors.skipped_ips),
                "standard_ports": sorted(STANDARD_PORTS),
            },
            "entity_links": {name: int(value) for name, value in links.items()},
            "actor_links": {ACTOR_LINK_TEXT.get(kind, kind): int(value) for kind, value in self.actors.link_counts.items()},
            "geo_sources": dict(Counter(str(record.get("geo_country_source", "dataset")) for record in self.records)),
            "risk_distribution": _levels(wallets["risk_score"]),
            "transaction_risk_distribution": _levels(transactions["risk_score"]),
            "actor_risk_distribution": _levels(actors["risk_score"]),
            "ip_risk_distribution": _levels(ips.loc[ips["originated"] > 0, "risk_score"]) if len(ips) else _levels(np.zeros(0)),
            "patterns": {kind: pattern_counts.get(kind, 0) for kind in PATTERN_NAMES},
            "seeds": self.seeds,
            "models": models,
            "evaluation": self._evaluation(),
            "ingestion": self.ingestion,
            "timeline": self._timeline(),
            "geography": self._geography(),
        })

    def _timeline(self, buckets: int = 60) -> list[dict[str, Any]]:
        """Transactions, value and flagged transactions per time bucket (for the activity chart)."""
        txs = self.flows.txs
        if not txs:
            return []
        start, end = txs[0].ts, txs[-1].ts
        span = max(end - start, 1.0)
        width = max(3600.0, span / buckets)
        width = 86400.0 if width > 43200 else 3600.0 * max(1, round(width / 3600))
        origin = (start // width) * width  # buckets begin on the hour, daily buckets at 00:00 UTC
        risk = self.transactions["risk_score"].to_numpy()
        index = {txid: position for position, txid in enumerate(self.transactions.index)}
        rows: dict[int, list[float]] = defaultdict(lambda: [0, 0.0, 0, 0.0])
        for tx in txs:
            row = rows[int((tx.ts - origin) // width)]
            score = risk[index[tx.txid]]
            row[0] += 1
            row[1] += tx.total_out
            if score >= 60:
                row[2] += 1
                row[3] += tx.total_out
        return [{"start": _iso(origin + bucket * width), "seconds": int(width), "transactions": int(values[0]), "value": round(values[1], 4),
                 "flagged": int(values[2]), "flagged_value": round(values[3], 4)} for bucket, values in sorted(rows.items())]

    def _geography(self, limit: int = 20) -> dict[str, Any]:
        """Where transactions originate: by country and by network, with the share that is flagged.

        The lists hold the ``limit`` origins with the most flagged transactions; the ``*_count`` fields say how many
        countries and networks there are in all, and ``attributed`` how many transactions the figures cover.
        """
        risk = dict(zip(self.transactions.index, self.transactions["risk_score"]))
        countries: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        asns: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        for tx in self.flows.txs:
            if not self.network.origins[tx.index].attributed:
                continue
            flagged = risk[tx.txid] >= 60
            if tx.country:
                countries[tx.country][0] += 1
                countries[tx.country][1] += flagged
            if tx.asn:
                asns[tx.asn][0] += 1
                asns[tx.asn][1] += flagged
        org = {info.get("asn"): info.get("as_org", "") for info in self.ip_info.values() if info.get("asn")}
        return {
            "country_count": len(countries), "asn_count": len(asns),
            "attributed": sum(1 for origin in self.network.origins if origin.attributed),
            "countries": [{"country": key, "transactions": value[0], "flagged": int(value[1])}
                          for key, value in sorted(countries.items(), key=lambda item: (-item[1][1], -item[1][0]))[:limit]],
            "asns": [{"asn": key, "as_org": org.get(key, ""), "transactions": value[0], "flagged": int(value[1])}
                     for key, value in sorted(asns.items(), key=lambda item: (-item[1][1], -item[1][0]))[:limit]],
        }

    def _evaluation(self) -> dict[str, Any]:
        labels = self.transactions["label"]
        if not labels.astype(bool).any():
            return {"labels_available": False, "notes": "The dataset carries no behaviour labels, so detection quality cannot be measured."}
        mask = labels.isin(ILLICIT_LABELS + LICIT_LABELS)
        target = labels[mask].isin(ILLICIT_LABELS)
        result: dict[str, Any] = {"labels_available": True,
                                  "notes": "Measured against the dataset's behaviour labels, which the models never see. CoinJoin-labelled transactions are excluded from the AUC because mixing is not illicit by itself."}
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
    @staticmethod
    def _columns(frame: pd.DataFrame) -> dict[str, list[Any]]:
        """A frame as plain-Python column lists (``tolist`` converts NumPy scalars far faster than walking the cells)."""
        columns: dict[str, list[Any]] = {}
        for name in frame.columns:
            series = frame[name]
            if series.dtype.kind == "f":
                series = series.fillna(0.0)
            columns[str(name)] = series.tolist()
        return columns

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "version": SNAPSHOT_VERSION,
            "records": self.records,
            "ip_info": self.ip_info,
            "ingestion": self.ingestion,
            "seeds": self.seeds,
            "wallets": self._columns(self.wallets),
            "entities": self._columns(self.entities),
            "actors": self._columns(self.actor_frame),
            "transactions": self._columns(self.transactions),
            "patterns": self.patterns,
            "coinjoins": sorted(self._coinjoins),
            "entity_of": self.clustering.entity_of,
            "entity_evidence": self.clustering.evidence,
            "entity_links": self.clustering.link_counts,
            "excluded_coinjoins": self.clustering.excluded_coinjoins,
            "actor_of": self.actors.actor_of,
            "actor_evidence": self.actors.evidence,
            "actor_links": self.actors.link_counts,
            "skipped_ips": self.actors.skipped_ips,
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
        self.ip_info = snapshot.get("ip_info", {})
        self.ingestion = snapshot.get("ingestion", {})
        self.flows = build_flow_index(self.records)
        self._coinjoins = set(snapshot.get("coinjoins", []))
        self.network = build_network_correlation(self.flows, self._coinjoins)
        members: dict[str, list[str]] = defaultdict(list)
        for address, entity in snapshot["entity_of"].items():
            members[entity].append(address)
        self.clustering = EntityClustering(snapshot["entity_of"], {entity: sorted(items) for entity, items in members.items()},
                                           snapshot.get("excluded_coinjoins", 0), snapshot.get("entity_evidence", {}), snapshot.get("entity_links", {}))
        actor_members: dict[str, list[str]] = defaultdict(list)
        for entity, actor in snapshot["actor_of"].items():
            actor_members[actor].append(entity)
        self.actors = ActorGroups(snapshot["actor_of"], dict(actor_members), snapshot.get("actor_evidence", {}), snapshot.get("skipped_ips", []),
                                  snapshot.get("actor_links", {}))
        self.community_of = {key: int(value) for key, value in snapshot["community_of"].items()}
        self.patterns = snapshot["patterns"]
        self.wallets = pd.DataFrame(snapshot["wallets"]).set_index("wallet_id", drop=False)
        self.entities = pd.DataFrame(snapshot["entities"]).set_index("entity_id", drop=False)
        self.actor_frame = pd.DataFrame(snapshot["actors"]).set_index("actor_id", drop=False)
        self.transactions = pd.DataFrame(snapshot["transactions"]).set_index("txid", drop=False)
        self.attributions = snapshot["attributions"]
        self.tx_attributions = snapshot["tx_attributions"]
        self.rescore(snapshot.get("seeds", []))
        return self

    # ── tabular exports (compatibility with the CLI outputs) ────────────────
    @property
    def wallet_features(self) -> pd.DataFrame:
        return self.wallets[["wallet_id", *[column for column in FEATURE_LABELS if column in self.wallets.columns]]].reset_index(drop=True)

    @property
    def risk_scores(self) -> pd.DataFrame:
        columns = ["wallet_id", "risk_score", "risk_level", "confidence", "entity_id", "actor_id", "cluster_id",
                   *[f"signal_{name}" for name in SIGNALS], *[f"contrib_{name}" for name in SIGNALS]]
        return self.wallets[columns].sort_values("risk_score", ascending=False).reset_index(drop=True)


__all__ = ["Investigation", "SNAPSHOT_VERSION", "ILLICIT_LABELS", "service_port"]
