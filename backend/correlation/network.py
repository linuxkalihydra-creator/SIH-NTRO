"""Correlate network-layer observations (IP, port, ASN, country, timing) with addresses.

**Who broadcast a transaction?** A transaction enters the peer-to-peer network at
its creator's node and is then relayed peer to peer, so a monitor that sees the
same transaction from several peers sees the creator's node *first* (the
first-seen heuristic of Koshy et al. 2014 and Biryukov et al. 2014). Each
transaction's earliest observation is therefore taken as its origin, with a
confidence that reflects how much that can be trusted:

* **lead time** - how far ahead of the next relay the first one was. Bitcoin
  nodes delay announcements by seconds per hop, so a clear lead points at the
  creator while a near tie means two relays were heard and the creator was not;
* **relay behaviour** - an IP that keeps turning up as a *later* relay of other
  transactions is a relay node, not a wallet. When such an IP happens to be first
  (the creator was simply not observed) the confidence drops towards zero;
* a transaction observed only once has nothing to compare against and gets a
  moderate, fixed confidence.

Network evidence is attributed only to the addresses that *spend* in a
transaction (its inputs) and only when the origin is confident enough. Receiving
an output reveals nothing about the receiver's network position, and CoinJoin
transactions are broadcast by a coordinator, not by the participants, so they
are left out of the attribution.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from backend.correlation.flows import FlowIndex, service_port

SINGLE_OBSERVATION_CONFIDENCE = 0.6
LEAD_TIME_SCALE_SECONDS = 2.0   # typical per-hop announcement delay
ATTRIBUTION_THRESHOLD = 0.5
RELAY_DISCOUNT = 0.9


@dataclass
class WalletNetwork:
    """Network footprint of one address across the transactions it spent in."""

    ips: Counter = field(default_factory=Counter)
    countries: Counter = field(default_factory=Counter)
    asns: Counter = field(default_factory=Counter)
    ports: Counter = field(default_factory=Counter)
    broadcasts: int = 0
    nonstandard: int = 0

    @property
    def nonstandard_port_ratio(self) -> float:
        return self.nonstandard / self.broadcasts if self.broadcasts else 0.0


@dataclass
class IpProfile:
    """What one IP address did in the capture."""

    ip: str
    first_seen: int = 0            # transactions for which it was the first-seen relay
    later_seen: int = 0            # observations in which it relayed a transaction someone else relayed first
    originated: list[int] = field(default_factory=list)   # transaction indices attributed to it as origin
    wallets: set[str] = field(default_factory=set)        # addresses that spent in those transactions
    value: float = 0.0
    nonstandard: int = 0
    ports: Counter = field(default_factory=Counter)
    first_ts: float = math.inf
    last_ts: float = -math.inf

    @property
    def relay_ratio(self) -> float:
        """Share of this IP's observations in which it merely passed on someone else's transaction."""
        total = self.first_seen + self.later_seen
        return self.later_seen / total if total else 0.0

    @property
    def nonstandard_port_ratio(self) -> float:
        return self.nonstandard / len(self.originated) if self.originated else 0.0


@dataclass
class Origin:
    """The inferred origin of one transaction."""

    ip: str
    confidence: float
    attributed: bool
    lead_seconds: float | None     # gap to the next distinct relay; None when observed once
    observations: int

    def to_dict(self) -> dict[str, Any]:
        return {"ip": self.ip, "confidence": round(self.confidence, 3), "attributed": self.attributed,
                "lead_seconds": None if self.lead_seconds is None else round(self.lead_seconds, 3), "observations": self.observations}


@dataclass
class NetworkCorrelation:
    """Address <-> IP associations in both directions, plus per-transaction origins and per-IP profiles."""

    wallets: dict[str, WalletNetwork]
    ip_wallets: dict[str, set[str]]
    ip_transactions: Counter
    ip_country: dict[str, str]
    ip_asn: dict[str, int]
    ips: dict[str, IpProfile] = field(default_factory=dict)
    origins: list[Origin] = field(default_factory=list)

    def shared_ip_wallets(self, address: str) -> int:
        """How many other addresses were broadcast from any IP this address used."""
        footprint = self.wallets.get(address)
        if footprint is None:
            return 0
        others: set[str] = set()
        for ip in footprint.ips:
            others |= self.ip_wallets.get(ip, set())
        others.discard(address)
        return len(others)

    def ip_associations(self, address: str, limit: int = 10) -> list[dict[str, Any]]:
        """Rank the IPs tied to an address.

        ``share`` is the fraction of the address's broadcasts seen from the IP;
        ``exclusivity`` is 1 / the number of addresses using that IP. Their product is
        a simple attribution strength: an IP that relays most of a wallet's spends and
        nobody else's is a strong lead to the operator's infrastructure.
        """
        footprint = self.wallets.get(address)
        if footprint is None or not footprint.broadcasts:
            return []
        rows = []
        for ip, count in footprint.ips.most_common():
            users = len(self.ip_wallets.get(ip, ())) or 1
            share = count / footprint.broadcasts
            rows.append({
                "ip": ip,
                "count": count,
                "country": self.ip_country.get(ip, ""),
                "asn": self.ip_asn.get(ip, 0),
                "share": round(share, 4),
                "exclusivity": round(1 / users, 4),
                "association": round(share / users, 4),
                "wallets_on_ip": users,
            })
        return rows[:limit]

    def ip_links(self, min_wallets: int = 2) -> list[tuple[str, set[str]]]:
        """IPs shared by several addresses (candidate co-location links)."""
        return [(ip, wallets) for ip, wallets in self.ip_wallets.items() if len(wallets) >= min_wallets]


def origin_confidence(observations: int, lead_seconds: float | None, relay_ratio: float) -> float:
    """Probability-like trust that the first-seen relay is where the transaction entered the network."""
    if observations <= 1 or lead_seconds is None:
        base = SINGLE_OBSERVATION_CONFIDENCE
    else:
        base = SINGLE_OBSERVATION_CONFIDENCE + 0.35 * (1 - math.exp(-max(lead_seconds, 0.0) / LEAD_TIME_SCALE_SECONDS))
    return base * (1 - RELAY_DISCOUNT * relay_ratio)


def build_network_correlation(flows: FlowIndex, exclude_txids: set[str] | frozenset[str] = frozenset()) -> NetworkCorrelation:
    """Infer each transaction's origin and build address/IP footprints from the confident ones.

    ``exclude_txids`` (CoinJoins) still get an origin but are not attributed to their input addresses.
    """
    profiles: dict[str, IpProfile] = {}

    def profile(ip: str) -> IpProfile:
        found = profiles.get(ip)
        if found is None:
            found = profiles[ip] = IpProfile(ip)
        return found

    # Pass 1: how each IP behaves across the whole capture (first-seen vs later relay).
    for tx in flows.txs:
        first_ip = tx.src_ip
        if first_ip:
            profile(first_ip).first_seen += 1
        for observation in tx.observations[1:]:
            if observation.src_ip and observation.src_ip != first_ip:
                profile(observation.src_ip).later_seen += 1

    wallets: dict[str, WalletNetwork] = defaultdict(WalletNetwork)
    ip_wallets: dict[str, set[str]] = defaultdict(set)
    ip_transactions: Counter = Counter()
    ip_country: dict[str, str] = {}
    ip_asn: dict[str, int] = {}
    origins: list[Origin] = []
    # Pass 2: per-transaction origin, then attribution of the confident ones to the spending addresses.
    for tx in flows.txs:
        first_ip = tx.src_ip
        count = len(tx.observations)
        if not first_ip:
            origins.append(Origin("", 0.0, False, None, count))
            continue
        lead = next((observation.ts - tx.observations[0].ts for observation in tx.observations[1:] if observation.src_ip != first_ip), None)
        entry = profiles[first_ip]
        confidence = origin_confidence(count, lead, entry.relay_ratio)
        attributed = confidence >= ATTRIBUTION_THRESHOLD
        origins.append(Origin(first_ip, confidence, attributed, lead, count))
        ip_transactions[first_ip] += 1
        if tx.country:
            ip_country.setdefault(first_ip, tx.country)
        if tx.asn:
            ip_asn.setdefault(first_ip, tx.asn)
        if not attributed:
            continue
        entry.originated.append(tx.index)
        entry.value += tx.total_out
        entry.first_ts = min(entry.first_ts, tx.ts)
        entry.last_ts = max(entry.last_ts, tx.ts)
        nonstandard = tx.nonstandard_port
        port = service_port(tx.src_port, tx.dst_port)
        entry.nonstandard += int(nonstandard)
        entry.ports[port] += 1
        if tx.txid in exclude_txids:
            continue
        for address in dict.fromkeys(tx.input_addresses):
            footprint = wallets[address]
            footprint.ips[first_ip] += 1
            if tx.country:
                footprint.countries[tx.country] += 1
            if tx.asn:
                footprint.asns[tx.asn] += 1
            footprint.ports[port] += 1
            footprint.broadcasts += 1
            footprint.nonstandard += int(nonstandard)
            ip_wallets[first_ip].add(address)
            entry.wallets.add(address)
    return NetworkCorrelation(dict(wallets), dict(ip_wallets), ip_transactions, ip_country, ip_asn, profiles, origins)


__all__ = ["WalletNetwork", "IpProfile", "Origin", "NetworkCorrelation", "origin_confidence", "build_network_correlation",
           "ATTRIBUTION_THRESHOLD", "SINGLE_OBSERVATION_CONFIDENCE"]
