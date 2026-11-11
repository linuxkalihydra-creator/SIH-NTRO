"""Risk propagation: through the money flow, and through shared network infrastructure.

**Downstream taint** (``propagate_risk``) is a time-respecting, value-weighted
*haircut* taint from seed (known illicit) wallets:

* seeds start fully tainted; addresses that entity clustering puts in the same
  entity as a seed are co-owned and start at ``co_owned``;
* transactions are replayed in time order, so funds can only carry taint forward
  in time (unlike PageRank-style propagation on a static graph);
* a transaction's taint is the value-weighted taint of its inputs, and every
  output receives that share of tainted value, discounted by ``decay`` per hop to
  reflect the uncertainty of address-level linking;
* an address's taint is tainted value received / total value received, so a busy
  exchange that receives a little tainted money stays low, while a hop wallet that
  only ever moved seed funds stays close to the seed;
* services (exchanges, merchants, processors) commingle everybody's funds, so what
  they pay out is discounted by how service-like they are instead of being traced
  onward as the criminal's money;
* taint keeps its strength while it moves between wallets of an operation (seeds,
  wallets inside laundering structures, and the wallets owned or operated with
  them) and fades faster once it is being spent by anyone else. A seller or an
  unwitting recipient who was paid with dirty money is a lead; the shops *they*
  pay are not.

For every tainted address the strongest upstream contributor is kept, so the path
back to the seed can be shown as evidence.

**Upstream exposure** (``propagate_exposure``) runs the same idea backwards in
time from wallets that the pattern detectors place inside a laundering structure
(peeling-chain hops, layering legs, pass-through mules): an address that sent most
of its outgoing value into such a structure, directly or through one
intermediate, is exposed. The hop limit keeps ordinary payers (for example
ransomware victims, who pay into a collection address one hop further upstream)
from being scored as launderers.

**Network association** (``propagate_network_association``) carries risk across
the network layer. For every IP, the share of its attributed traffic that comes
from already-flagged wallets is its *dirtiness*; a wallet inherits the dirtiness
of the IPs it broadcast from, weighted by how much it used each one and leaving
its own traffic out. An IP that serves a whole office or a VPN exit stays clean
when one flagged wallet uses it, while infrastructure used almost only by a
laundering operation marks every other wallet that shares it. Two rounds let the
signal travel wallet -> IP -> wallet -> IP -> wallet across one operator's pool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import sparse

from backend.correlation.flows import FlowIndex
from backend.correlation.network import NetworkCorrelation
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


def _haircut(flows: FlowIndex, base: dict[str, tuple[float, str, str]], decay: float, min_taint: float,
             service: dict[str, float] | None, entity_of: dict[str, str] | None,
             launderers: set[str] | frozenset[str] | None = None, spent_decay: float | None = None) -> tuple[dict[str, TaintInfo], dict[str, float]]:
    """Replay the transactions in time order, carrying value-weighted taint forward from the ``base`` addresses.

    ``base`` maps a source address to (taint, origin shown as evidence, reason). The per-hop ``decay`` is the
    uncertainty of address-level linking; it is not applied when funds stay inside one entity, because
    moving money between your own addresses does not make it any less yours.

    ``launderers`` are the addresses known to move funds as part of an operation (the sources themselves,
    wallets inside laundering structures, and the wallets operated with them). Money they pass on keeps
    its taint (``decay``). Money passed on by anyone else has already changed hands to somebody who shows
    no laundering behaviour - a shop, a seller, an unwitting recipient - so it is discounted by the
    steeper ``spent_decay``: whoever *they* pay is two steps removed from the crime.
    """
    wallets: dict[str, TaintInfo] = {}
    tx_taint: dict[str, float] = {}
    if not base:
        return wallets, tx_taint
    service = service or {}
    received: dict[str, float] = {}
    tainted: dict[str, float] = {}
    hops: dict[str, int] = {address: 0 for address in base}
    origin: dict[str, str] = {address: seed for address, (_, seed, _) in base.items()}
    best: dict[str, tuple[float, str, str]] = {}

    for tx in flows.txs:
        dirty = 0.0
        source, strongest = "", 0.0
        for address, amount in tx.inputs:
            entry = base.get(address)
            if entry is not None:
                share = entry[0]
            else:
                total = received.get(address, 0.0)
                share = tainted.get(address, 0.0) / total if total > 0 else 0.0
                if share and service:
                    share *= 1.0 - service.get(address, 0.0)
            if share:
                value = amount * share
                dirty += value
                if value > strongest:
                    source, strongest = address, value
        total_in = tx.total_in
        share = dirty / total_in if total_in > 0 else 0.0
        tainted_tx = share >= min_taint
        if tainted_tx:
            tx_taint[tx.txid] = share
        owner = entity_of.get(source) if entity_of and source else None
        for address, amount in tx.outputs:
            received[address] = received.get(address, 0.0) + amount
            if not tainted_tx or address in base:
                continue
            if owner is not None and entity_of.get(address) == owner:
                kept = 1.0
            elif launderers is not None and source not in launderers and source not in base:
                kept = spent_decay if spent_decay is not None else decay
            else:
                kept = decay
            value = amount * share * kept
            tainted[address] = tainted.get(address, 0.0) + value
            if value > best.get(address, (0.0, "", ""))[0]:
                best[address] = (value, source, tx.txid)
                hops[address] = hops.get(source, 0) + 1
                origin[address] = origin.get(source, "")

    for address, (score, seed, reason) in base.items():
        wallets[address] = TaintInfo(score, received.get(address, 0.0) * score, received.get(address, 0.0), 0, seed, "" if reason == "seed" else seed, "", reason)
    for address, value in tainted.items():
        score = value / received[address] if received.get(address) else 0.0
        if score >= min_taint:
            _, parent, txid = best[address]
            wallets[address] = TaintInfo(min(score, 1.0), value, received[address], hops.get(address, 1), origin.get(address, ""), parent, txid, "downstream")
    return wallets, tx_taint


def propagate_risk(flows: FlowIndex, seeds: list[str], clustering: EntityClustering | None = None,
                   decay: float = 0.9, co_owned: float = 0.9, min_taint: float = 1e-4,
                   service: dict[str, float] | None = None, launderers: set[str] | frozenset[str] | None = None,
                   spent_decay: float | None = None) -> PropagationResult:
    """Haircut taint from ``seeds``. ``service`` maps addresses to a 0-1 service score that discounts what they pass on;
    ``launderers`` / ``spent_decay`` make taint fade faster once it is spent by uninvolved wallets (see ``_haircut``)."""
    known = [seed for seed in dict.fromkeys(seeds) if seed in flows.first_seen]
    base: dict[str, tuple[float, str, str]] = {seed: (1.0, seed, "seed") for seed in known}
    co_owned_addresses = []
    if clustering is not None:
        for seed in known:
            for address in clustering.members.get(clustering.entity_of.get(seed, ""), []):
                if address not in base:
                    base[address] = (co_owned, seed, "co_owned")
                    co_owned_addresses.append(address)
    wallets, tx_taint = _haircut(flows, base, decay, min_taint, service, clustering.entity_of if clustering is not None else None, launderers, spent_decay)
    return PropagationResult(wallets, tx_taint, known, co_owned_addresses)


def propagate_proceeds(flows: FlowIndex, sources: dict[str, float], clustering: EntityClustering | None = None,
                       decay: float = 0.9, min_taint: float = 1e-4, service: dict[str, float] | None = None,
                       launderers: set[str] | frozenset[str] | None = None, spent_decay: float | None = None) -> tuple[dict[str, TaintInfo], dict[str, float]]:
    """Follow funds that leave a detected laundering structure (no seed needed).

    ``sources`` maps the wallets inside a structure to their evidence strength. The result gives, for every
    other address, the share of what it received that came out of such a structure - which is how the
    cash-out points and onward recipients of an *unreported* operation are found.
    Returns (address -> taint, txid -> tainted share of its inputs); the source wallets are not included.
    """
    base = {address: (float(score), address, "structure") for address, score in sources.items() if address in flows.first_seen and score > 0}
    wallets, tx_taint = _haircut(flows, base, decay, min_taint, service, clustering.entity_of if clustering is not None else None, launderers, spent_decay)
    return {address: info for address, info in wallets.items() if address not in base}, tx_taint


def propagate_exposure(flows: FlowIndex, pattern_seeds: dict[str, float], clustering: EntityClustering | None = None,
                       decay: float = 0.85, max_hops: int = 2, min_fraction: float = 0.6,
                       service: dict[str, float] | None = None) -> dict[str, ExposureInfo]:
    """Reverse-time exposure of addresses whose outgoing value flows into laundering structures.

    ``clustering`` is accepted for backward compatibility; co-owned addresses now inherit through
    entity-level scoring instead of being expanded here. Service-like addresses never become exposed:
    everybody's money passes through them.
    """
    del clustering
    service = service or {}
    sent: dict[str, float] = {}
    exposed: dict[str, float] = {}     # weighted exposed value
    reached: dict[str, float] = {}     # unweighted value that reached a structure
    hops: dict[str, int] = {address: 0 for address in pattern_seeds}
    best: dict[str, tuple[float, str, str]] = {}
    if not pattern_seeds:
        return {}

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
        child = max(scored, key=lambda item: item[1] * item[2])[0]
        for address, amount in tx.inputs:
            sent[address] = sent.get(address, 0.0) + amount
            if weighted <= 0 or address in pattern_seeds:
                continue
            exposed[address] = exposed.get(address, 0.0) + amount * weighted * decay
            reached[address] = reached.get(address, 0.0) + amount * fraction
            contribution = amount * weighted
            if contribution > best.get(address, (0.0, "", ""))[0]:
                best[address] = (contribution, child, tx.txid)
                hops[address] = hops.get(child, 0) + 1

    def target_of(address: str) -> str:
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
            score = value / total * (1.0 - service.get(address, 0.0))
            if score > 0:
                _, child, txid = best[address]
                result[address] = ExposureInfo(score, fraction, hops[address], target_of(address), child, txid)
    return result


@dataclass
class NetworkAssociation:
    """Per-address risk inherited through shared IPs, and how dirty each IP is."""

    signal: dict[str, float]                   # address -> 0-1 association signal
    evidence: dict[str, dict[str, Any]]        # address -> the IP that contributes most
    ip_dirtiness: dict[str, float]             # ip -> share of its attributed traffic that comes from flagged wallets
    ip_flagged_wallets: dict[str, int]         # ip -> wallets on it with direct evidence


def propagate_network_association(network: NetworkCorrelation, direct: dict[str, float], rounds: int = 2, damping: float = 0.7,
                                  smoothing: float = 1.0, flagged_at: float = 0.5) -> NetworkAssociation:
    """Carry risk across shared IPs. ``direct`` maps addresses to 0-1 evidence from the blockchain layer."""
    addresses = list(network.wallets)
    if not addresses:
        return NetworkAssociation({}, {}, {}, {})
    ips = list(network.ip_wallets)
    address_index = {address: position for position, address in enumerate(addresses)}
    ip_index = {ip: position for position, ip in enumerate(ips)}
    rows, cols, data = [], [], []
    for address, footprint in network.wallets.items():
        for ip, count in footprint.ips.items():
            rows.append(address_index[address])
            cols.append(ip_index[ip])
            data.append(float(count))
    rows_a, cols_a, counts = np.array(rows), np.array(cols), np.array(data)
    usage = sparse.coo_matrix((counts, (rows_a, cols_a)), shape=(len(addresses), len(ips))).tocsr()
    address_total = np.asarray(usage.sum(axis=1)).ravel()
    ip_total = np.asarray(usage.sum(axis=0)).ravel()
    evidence = np.array([float(direct.get(address, 0.0)) for address in addresses])
    risk = evidence.copy()
    signal = np.zeros(len(addresses))
    contribution = np.zeros(counts.size)
    for _ in range(max(1, rounds)):
        ip_dirty_mass = usage.T @ risk
        # Leave-one-out: what the *other* traffic on an IP says, so a wallet is never associated with itself.
        others_mass = ip_dirty_mass[cols_a] - counts * risk[rows_a]
        others_total = ip_total[cols_a] - counts + smoothing
        contribution = counts / address_total[rows_a] * np.clip(others_mass / others_total, 0.0, 1.0)
        signal = np.bincount(rows_a, weights=contribution, minlength=len(addresses))
        risk = np.maximum(evidence, damping * signal)
    dirtiness = (usage.T @ risk) / (ip_total + smoothing)
    flagged = usage.T @ (evidence >= flagged_at).astype(float)
    # Which IP explains each address's association best.
    best: dict[str, dict[str, Any]] = {}
    order = np.argsort(-contribution)
    seen = set()
    for position in order:
        if contribution[position] <= 0:
            break
        row = int(rows_a[position])
        if row in seen:
            continue
        seen.add(row)
        ip = ips[int(cols_a[position])]
        column = int(cols_a[position])
        best[addresses[row]] = {"ip": ip, "share_of_spends": round(float(counts[position] / address_total[row]), 4),
                                "ip_flagged_share": round(float(np.clip(others_mass[position] / others_total[position], 0, 1)), 4),
                                "wallets_on_ip": len(network.ip_wallets.get(ip, ())), "flagged_wallets_on_ip": int(flagged[column])}
    return NetworkAssociation(
        signal={address: float(value) for address, value in zip(addresses, signal) if value > 0},
        evidence=best,
        ip_dirtiness={ip: float(value) for ip, value in zip(ips, dirtiness)},
        ip_flagged_wallets={ip: int(value) for ip, value in zip(ips, flagged)},
    )


__all__ = ["TaintInfo", "ExposureInfo", "PropagationResult", "NetworkAssociation", "propagate_risk", "propagate_proceeds",
           "propagate_exposure", "propagate_network_association"]
