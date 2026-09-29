"""Correlate network-layer observations (IP, port, ASN, country, timing) with addresses.

A transaction's ``src_ip`` is the node that relayed it first, which is usually the
spender's own node or proxy, so network evidence is attributed to the addresses
that *spend* in the transaction (its inputs). Receiving an output reveals nothing
about the receiver's network position.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from backend.correlation.flows import FlowIndex


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
class NetworkCorrelation:
    """Address ↔ IP associations in both directions."""

    wallets: dict[str, WalletNetwork]
    ip_wallets: dict[str, set[str]]
    ip_transactions: Counter
    ip_country: dict[str, str]
    ip_asn: dict[str, int]

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


def build_network_correlation(flows: FlowIndex) -> NetworkCorrelation:
    wallets: dict[str, WalletNetwork] = defaultdict(WalletNetwork)
    ip_wallets: dict[str, set[str]] = defaultdict(set)
    ip_transactions: Counter = Counter()
    ip_country: dict[str, str] = {}
    ip_asn: dict[str, int] = {}
    for tx in flows.txs:
        if not tx.src_ip:
            continue
        ip_transactions[tx.src_ip] += 1
        if tx.country:
            ip_country.setdefault(tx.src_ip, tx.country)
        if tx.asn:
            ip_asn.setdefault(tx.src_ip, tx.asn)
        for address in dict.fromkeys(tx.input_addresses):
            footprint = wallets[address]
            footprint.ips[tx.src_ip] += 1
            if tx.country:
                footprint.countries[tx.country] += 1
            if tx.asn:
                footprint.asns[tx.asn] += 1
            footprint.ports[tx.dst_port] += 1
            footprint.broadcasts += 1
            footprint.nonstandard += int(tx.nonstandard_port)
            ip_wallets[tx.src_ip].add(address)
    return NetworkCorrelation(dict(wallets), dict(ip_wallets), ip_transactions, ip_country, ip_asn)


__all__ = ["WalletNetwork", "NetworkCorrelation", "build_network_correlation"]
