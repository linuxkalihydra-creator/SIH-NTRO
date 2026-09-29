"""Risk propagation from seed (known illicit) wallets through the money flow.

The algorithm is a time-respecting, value-weighted *haircut* taint:

* seeds start fully tainted; addresses the common-input-ownership heuristic puts
  in the same entity as a seed are treated as co-owned and start at ``co_owned``;
* transactions are replayed in time order, so funds can only carry taint forward
  in time (unlike PageRank-style propagation on a static graph);
* a transaction's taint is the value-weighted taint of its inputs, and every
  output receives that share of tainted value, discounted by ``decay`` per hop to
  reflect the uncertainty of address-level linking;
* an address's taint is tainted value received / total value received, so a busy
  exchange that receives a little tainted money stays low, while a hop wallet that
  only ever moved seed funds stays close to the seed.

For every tainted address the strongest upstream contributor is kept, so the path
back to the seed can be shown as evidence.

``propagate_exposure`` runs the same idea backwards in time from wallets that the
pattern detectors place inside a laundering structure (peeling-chain hops,
layering nodes, pass-through mules): an address that sent most of its outgoing
value into such a structure, directly or through one intermediate, is exposed.
The hop limit keeps ordinary payers (for example ransomware victims, who pay into
a collection address one hop further upstream) from being scored as launderers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.correlation.flows import FlowIndex
from backend.ml.entities import EntityClustering


@dataclass
class TaintInfo:
    score: float
    tainted_value: float
    received_value: float
    hops: int
    seed: str
    parent: str
    parent_txid: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4), "tainted_value": round(self.tainted_value, 8), "received_value": round(self.received_value, 8),
            "hops": self.hops, "seed": self.seed, "parent": self.parent, "parent_txid": self.parent_txid, "reason": self.reason,
        }


@dataclass
class ExposureInfo:
    score: float
    fraction: float
    hops: int
    target: str
    child: str
    child_txid: str

    def to_dict(self) -> dict[str, Any]:
        return {"score": round(self.score, 4), "fraction": round(self.fraction, 4), "hops": self.hops, "target": self.target,
                "child": self.child, "child_txid": self.child_txid}


@dataclass
class PropagationResult:
    wallets: dict[str, TaintInfo]
    transactions: dict[str, float]
    seeds: list[str]
    co_owned: list[str]
    upstream: dict[str, ExposureInfo] | None = None

    def path(self, address: str, limit: int = 12) -> list[dict[str, str]]:
        """Walk the strongest-contributor links back to the seed."""
        steps, seen = [], set()
        current = address
        while current in self.wallets and current not in seen and len(steps) < limit:
            seen.add(current)
            info = self.wallets[current]
            steps.append({"wallet_id": current, "txid": info.parent_txid})
            if not info.parent:
                break
            current = info.parent
        return steps


def propagate_risk(flows: FlowIndex, seeds: list[str], clustering: EntityClustering | None = None,
                   decay: float = 0.9, co_owned: float = 0.9, min_taint: float = 1e-4) -> PropagationResult:
    known = [seed for seed in dict.fromkeys(seeds) if seed in flows.first_seen]
    base: dict[str, tuple[float, str, str]] = {seed: (1.0, seed, "seed") for seed in known}
    co_owned_addresses = []
    if clustering is not None:
        for seed in known:
            for address in clustering.members.get(clustering.entity_of.get(seed, ""), []):
                if address not in base:
                    base[address] = (co_owned, seed, "co_owned")
                    co_owned_addresses.append(address)

    received: dict[str, float] = {}
    tainted: dict[str, float] = {}
    hops: dict[str, int] = {address: 0 for address in base}
    origin: dict[str, str] = {address: seed for address, (_, seed, _) in base.items()}
    best: dict[str, tuple[float, str, str]] = {}
    tx_taint: dict[str, float] = {}

    def taint_of(address: str) -> float:
        if address in base:
            return base[address][0]
        total = received.get(address, 0.0)
        return tainted.get(address, 0.0) / total if total > 0 else 0.0

    for tx in flows.txs:
        total_in = tx.total_in
        contributions = [(address, amount * taint_of(address)) for address, amount in tx.inputs]
        dirty = sum(value for _, value in contributions)
        share = dirty / total_in if total_in > 0 else 0.0
        if share >= min_taint:
            tx_taint[tx.txid] = share
            source, _ = max(contributions, key=lambda item: item[1])
        for address, amount in tx.outputs:
            received[address] = received.get(address, 0.0) + amount
            if share < min_taint or address in base:
                continue
            value = amount * share * decay
            tainted[address] = tainted.get(address, 0.0) + value
            if value > best.get(address, (0.0, "", ""))[0]:
                best[address] = (value, source, tx.txid)
                hops[address] = hops.get(source, 0) + 1
                origin[address] = origin.get(source, "")

    wallets: dict[str, TaintInfo] = {}
    for address, (score, seed, reason) in base.items():
        wallets[address] = TaintInfo(score, received.get(address, 0.0) * score, received.get(address, 0.0), 0, seed, "" if reason == "seed" else seed, "", reason)
    for address, value in tainted.items():
        score = value / received[address] if received.get(address) else 0.0
        if score >= min_taint:
            _, parent, txid = best[address]
            wallets[address] = TaintInfo(score, value, received[address], hops.get(address, 1), origin.get(address, ""), parent, txid, "downstream")
    return PropagationResult(wallets, tx_taint, known, co_owned_addresses)


def propagate_exposure(flows: FlowIndex, pattern_seeds: dict[str, float], clustering: EntityClustering | None = None,
                       decay: float = 0.85, max_hops: int = 2, min_fraction: float = 0.6) -> dict[str, ExposureInfo]:
    """Reverse-time exposure of addresses whose outgoing value flows into laundering structures."""
    sent: dict[str, float] = {}
    exposed: dict[str, float] = {}     # weighted exposed value
    reached: dict[str, float] = {}     # unweighted value that reached a structure
    hops: dict[str, int] = {address: 0 for address in pattern_seeds}
    best: dict[str, tuple[float, str, str, str]] = {}

    def score_of(address: str) -> tuple[float, float]:
        if address in pattern_seeds:
            return pattern_seeds[address], 1.0
        total = sent.get(address, 0.0)
        if total <= 0 or hops.get(address, max_hops + 1) >= max_hops:
            return 0.0, 0.0
        return exposed.get(address, 0.0) / total, reached.get(address, 0.0) / total

    for tx in reversed(flows.txs):
        inputs = set(tx.input_addresses)
        onward = [(address, amount) for address, amount in tx.outputs if address not in inputs]
        total_out = sum(amount for _, amount in onward)
        if total_out <= 0:
            continue
        scored = [(address, amount, *score_of(address)) for address, amount in onward]
        weighted = sum(amount * score for _, amount, score, _ in scored) / total_out
        fraction = sum(amount * share for _, amount, _, share in scored) / total_out
        child, _, _, _ = max(scored, key=lambda item: item[1] * item[2])
        for address, amount in tx.inputs:
            sent[address] = sent.get(address, 0.0) + amount
            if weighted <= 0 or address in pattern_seeds:
                continue
            exposed[address] = exposed.get(address, 0.0) + amount * weighted * decay
            reached[address] = reached.get(address, 0.0) + amount * fraction
            contribution = amount * weighted
            if contribution > best.get(address, (0.0, "", "", ""))[0]:
                best[address] = (contribution, child, tx.txid, "")
                hops[address] = hops.get(child, 0) + 1

    def targets(address: str) -> str:
        seen = set()
        while address in best and address not in seen:
            seen.add(address)
            address = best[address][1]
        return address

    result: dict[str, ExposureInfo] = {}
    for address, value in exposed.items():
        total = sent.get(address, 0.0)
        if total <= 0 or hops.get(address, 99) > max_hops:
            continue
        fraction = reached.get(address, 0.0) / total
        if fraction >= min_fraction:
            _, child, txid, _ = best[address]
            result[address] = ExposureInfo(value / total, fraction, hops[address], targets(address), child, txid)
    if clustering is not None:  # co-owned addresses share the entity's exposure
        for address, info in list(result.items()):
            for member in clustering.members.get(clustering.entity_of.get(address, ""), []):
                if member not in result and member not in pattern_seeds:
                    result[member] = ExposureInfo(info.score * 0.9, info.fraction, info.hops, info.target, address, "")
    return result


__all__ = ["TaintInfo", "ExposureInfo", "PropagationResult", "propagate_risk", "propagate_exposure"]
