#!/usr/bin/env python3
"""Generate a synthetic Bitcoin transaction/network dataset for offline prototyping.

The dataset is produced by a small chronological simulation of a Bitcoin economy
rather than by drawing independent records, so the transaction flows have the
structure the detectors look for:

* entities own several addresses, spend them together (common-input ownership)
  and send change to fresh addresses;
* exchanges batch withdrawals and sweep customer deposit addresses;
* CoinJoin rounds mix equal-value outputs from many participants;
* two ransomware groups collect victim payments, consolidate them and launder the
  proceeds through peeling chains, fan-out/fan-in layering, CoinJoins and fast
  "mule" pass-through wallets before cashing out at exchanges;
* a darknet vendor broadcasts from many networks.

Every address keeps a balance, so each transaction spends funds its inputs hold
and ``sum(inputs) = sum(outputs) + fee`` exactly. The network layer is simulated
per entity: home networks, datacenter networks for services, and an anonymising
pool (VPN/Tor-exit-like) with non-standard ports for illicit actors.

Nothing here corresponds to real wallets, people or crimes. Besides the CSV, JSON
and XML datasets, the generator writes ``ground_truth.json`` (per-address roles,
illicit flags and pattern transaction ids, used for evaluation only) and
``seed_wallets.txt`` (the ransomware addresses victims "reported", used as known
illicit seeds for risk propagation).
"""

from __future__ import annotations

import argparse
import csv
import heapq
import json
import random
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Any, Callable

SUPPORTED_BEHAVIORS = (
    "NORMAL",
    "EXCHANGE_LIKE",
    "RAPID_TRANSFER",
    "LAYERING_LIKE",
    "MIXING_LIKE",
    "HIGH_NETWORK_DIVERSITY",
    "PEELING_CHAIN",
    "RANSOMWARE",
)

# Transaction labels that belong to an illicit money flow (evaluation only).
ILLICIT_BEHAVIORS = ("RANSOMWARE", "PEELING_CHAIN", "LAYERING_LIKE", "RAPID_TRANSFER", "HIGH_NETWORK_DIVERSITY")

COUNTRIES = ["US", "CA", "GB", "DE", "NL", "FR", "JP", "AU", "BR", "SG", "IN", "ZA", "SE", "CH", "RU", "UA", "RO", "HK", "KR", "PA"]

START_TIME = datetime(2024, 1, 1, tzinfo=timezone.utc)
WINDOW_DAYS = 60
START_BLOCK = 823_000
STANDARD_PORT = 8333
NONSTANDARD_PORTS = (8334, 18333, 9050, 9150, 28333, 38333, 50001)
BECH32_CHARS = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
BASE58_CHARS = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
SATOSHI = 1e-8


def _is_public_network(network: IPv4Network) -> bool:
    """True for globally routable unicast space that a real GeoIP database can resolve."""
    return network.is_global and not (network.is_multicast or network.is_reserved or network.is_private or network.is_loopback or network.is_link_local)


def _public_networks(count: int = 64, seed: int = 8333) -> list[IPv4Network]:
    """Pick a fixed, deterministic set of public /24 networks.

    Documentation (RFC 5737), private, CGNAT, multicast and reserved blocks are
    excluded because they can never resolve in a real GeoIP database.
    """
    picker = random.Random(seed)
    networks: list[IPv4Network] = []
    while len(networks) < count:
        network = IPv4Network((picker.getrandbits(24) << 8, 24))
        if _is_public_network(network) and network not in networks:
            networks.append(network)
    return networks


NETWORK_RANGES = _public_networks()
# Network roles: home broadband, service datacenters, and an anonymising pool.
RESIDENTIAL_NETWORKS = NETWORK_RANGES[:40]
DATACENTER_NETWORKS = NETWORK_RANGES[40:52]
ANONYMIZING_NETWORKS = NETWORK_RANGES[52:]
# Synthetic country/ASN per network, used only when no GeoIP database is present.
NETWORK_COUNTRY = {network: COUNTRIES[index % len(COUNTRIES)] for index, network in enumerate(NETWORK_RANGES)}
NETWORK_ASN = {network: 64512 + index // 2 for index, network in enumerate(NETWORK_RANGES)}
# Reachable (listening) full nodes that relay transactions: dst_ip is drawn from these.
_peer_picker = random.Random(18333)
LISTENING_PEERS = [str(IPv4Address(int(network.network_address) + _peer_picker.randint(1, 254))) for network in NETWORK_RANGES for _ in range(4)]


def network_of(ip: str) -> IPv4Network:
    return IPv4Network(f"{ip}/24", strict=False)


def round_btc(value: float) -> float:
    return round(value + 0.0, 8)


@dataclass
class Entity:
    """One real-world owner (a person, a service or a criminal group) of several addresses."""

    entity_id: str
    kind: str
    ips: list[str]
    address_style: str
    illicit: bool = False
    nonstandard_port_rate: float = 0.02
    fee_rate: tuple[float, float] = (4.0, 30.0)
    addresses: list[str] = field(default_factory=list)


class Economy:
    """Chronological simulation state: addresses, balances, entities and emitted transactions."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.balance: dict[str, float] = {}
        self.owner: dict[str, str] = {}
        self.role: dict[str, str] = {}
        self.entities: dict[str, Entity] = {}
        self.records: list[dict[str, Any]] = []
        self.txids: set[str] = set()
        self.patterns: list[dict[str, Any]] = []
        self.seeds: list[str] = []

    # ── identities ──────────────────────────────────────────────────────────
    def new_entity(self, kind: str, ips: list[str], illicit: bool = False, **kwargs: Any) -> Entity:
        entity_id = f"{kind}_{len(self.entities):04d}"
        style = self.rng.choices(["p2pkh", "p2sh", "p2wpkh", "p2tr"], weights=[2, 2, 5, 1])[0]
        entity = Entity(entity_id, kind, ips, style, illicit, **kwargs)
        self.entities[entity_id] = entity
        return entity

    def new_address(self, entity: Entity, role: str | None = None) -> str:
        while True:
            if entity.address_style == "p2pkh":
                address = "1" + "".join(self.rng.choice(BASE58_CHARS) for _ in range(33))
            elif entity.address_style == "p2sh":
                address = "3" + "".join(self.rng.choice(BASE58_CHARS) for _ in range(33))
            elif entity.address_style == "p2tr":
                address = "bc1p" + "".join(self.rng.choice(BECH32_CHARS) for _ in range(58))
            else:
                address = "bc1q" + "".join(self.rng.choice(BECH32_CHARS) for _ in range(38))
            if address not in self.owner:
                break
        self.owner[address] = entity.entity_id
        self.role[address] = role or entity.kind
        self.balance.setdefault(address, 0.0)
        entity.addresses.append(address)
        return address

    def fund(self, address: str, amount: float) -> None:
        """Credit funds that predate the observation window (no transaction is emitted)."""
        self.balance[address] = round_btc(self.balance.get(address, 0.0) + amount)

    def funded_addresses(self, entity: Entity) -> list[str]:
        return [address for address in entity.addresses if self.balance.get(address, 0.0) > 0.0001]

    # ── transactions ────────────────────────────────────────────────────────
    def _txid(self) -> str:
        while True:
            txid = f"{self.rng.getrandbits(256):064x}"
            if txid not in self.txids:
                self.txids.add(txid)
                return txid

    def _script_type(self, address: str) -> str:
        if address.startswith("bc1p"):
            return "P2TR"
        if address.startswith("bc1q"):
            return "P2WPKH"
        if address.startswith("3"):
            return "P2SH_P2WPKH"
        return "P2PKH"

    def emit(self, when: datetime, sender: Entity, inputs: list[str], payments: list[tuple[str, float]], behavior: str,
             change_to: str | None = None, spend_all: bool = True, fee_rate: float | None = None) -> dict[str, Any] | None:
        """Spend ``inputs`` (whole balances, like UTXOs) to ``payments`` plus change; return the record or None if unaffordable."""
        inputs = list(dict.fromkeys(address for address in inputs if self.balance.get(address, 0.0) > 0))
        if not inputs or not payments:
            return None
        input_amounts = [round_btc(self.balance[address]) for address in inputs] if spend_all else []
        total_in = sum(input_amounts)
        size = int(10.5 + 68 * len(inputs) + 31 * (len(payments) + 1))
        rate = fee_rate if fee_rate is not None else self.rng.uniform(*sender.fee_rate)
        fee = round_btc(max(size * rate * SATOSHI, 0.00000141))
        paid = sum(amount for _, amount in payments)
        change = round_btc(total_in - paid - fee)
        if change < -1e-9 or any(amount <= 0 for _, amount in payments):
            return None
        outputs = [(address, round_btc(amount)) for address, amount in payments]
        if change > 0.00000546:  # dust threshold: smaller change is left to the fee
            outputs.append((change_to or self.new_address(sender, "change"), change))
        else:
            fee = round_btc(total_in - sum(amount for _, amount in outputs))
        for address in inputs:
            self.balance[address] = 0.0
        for address, amount in outputs:
            self.balance[address] = round_btc(self.balance.get(address, 0.0) + amount)
        src_ip = self.rng.choice(sender.ips)
        dst_ip = self.rng.choice(LISTENING_PEERS)
        nonstandard = self.rng.random() < sender.nonstandard_port_rate
        record = {
            "timestamp": when.replace(microsecond=0).isoformat(),
            "src_ip": src_ip,
            "dst_ip": dst_ip,
            "src_port": self.rng.randint(32768, 60999),
            "dst_port": self.rng.choice(NONSTANDARD_PORTS) if nonstandard else STANDARD_PORT,
            "txid": self._txid(),
            "input_addresses": inputs,
            "output_addresses": [address for address, _ in outputs],
            "input_amounts": input_amounts,
            "output_amounts": [amount for _, amount in outputs],
            "fee": round_btc(fee),
            "script_type": self._script_type(inputs[0]),
            "geo_country": NETWORK_COUNTRY[network_of(src_ip)],
            "asn": NETWORK_ASN[network_of(src_ip)],
            "behavior_type": behavior,
            "block_height": START_BLOCK + int((when - START_TIME).total_seconds() // 600),
            "transaction_size": size,
        }
        self.records.append(record)
        return record

    def pay(self, when: datetime, sender: Entity, payments: list[tuple[str, float]], behavior: str,
            fresh_change: bool = True, fee_rate: float | None = None) -> dict[str, Any] | None:
        """Coin-select from the sender's funded addresses (largest first) and pay."""
        needed = sum(amount for _, amount in payments) + 0.0003
        funded = sorted(self.funded_addresses(sender), key=lambda address: -self.balance[address])
        chosen: list[str] = []
        for address in funded:
            chosen.append(address)
            if sum(self.balance[item] for item in chosen) >= needed:
                break
        if not chosen or sum(self.balance[item] for item in chosen) < needed:
            return None
        change_to = None if fresh_change or self.rng.random() < 0.5 else chosen[0]
        return self.emit(when, sender, chosen, payments, behavior, change_to=change_to, fee_rate=fee_rate)


def _ips(rng: random.Random, networks: list[IPv4Network], count: int) -> list[str]:
    return [str(IPv4Address(int(network.network_address) + rng.randint(1, 254))) for network in rng.sample(networks, min(count, len(networks)))]


def _minutes(value: float) -> timedelta:
    return timedelta(minutes=value)


class Scenario:
    """Builds the world and schedules every action on one timeline."""

    def __init__(self, records: int, seed: int) -> None:
        self.target = records
        self.rng = random.Random(seed)
        self.economy = Economy(self.rng)
        self.queue: list[tuple[datetime, int, Callable[[datetime], None]]] = []
        self.counter = 0
        scale = max(records / 10000, 0.05)
        self.scale = scale
        self._build_world()

    def schedule(self, when: datetime, action: Callable[[datetime], None]) -> None:
        self.counter += 1
        heapq.heappush(self.queue, (when, self.counter, action))

    def at_day(self, low: float, high: float) -> datetime:
        return START_TIME + timedelta(days=self.rng.uniform(low, high))

    # ── world ───────────────────────────────────────────────────────────────
    def _build_world(self) -> None:
        rng, eco = self.rng, self.economy
        self.exchanges = []
        for _ in range(4):
            exchange = eco.new_entity("exchange", _ips(rng, DATACENTER_NETWORKS, 3), fee_rate=(8, 25), nonstandard_port_rate=0.0)
            hot = eco.new_address(exchange, "exchange_hot_wallet")
            eco.fund(hot, rng.uniform(900, 2500))
            exchange.hot = hot  # type: ignore[attr-defined]
            exchange.deposits = {}  # type: ignore[attr-defined]
            self.exchanges.append(exchange)

        self.merchants = []
        for _ in range(max(3, int(14 * self.scale))):
            merchant = eco.new_entity("merchant", _ips(rng, DATACENTER_NETWORKS + RESIDENTIAL_NETWORKS, 2), fee_rate=(3, 15), nonstandard_port_rate=0.0)
            for _ in range(3):
                eco.new_address(merchant, "merchant_receive")
            self.merchants.append(merchant)

        self.users = []
        for index in range(max(12, int(1500 * self.scale))):
            vpn_user = rng.random() < 0.06  # benign privacy users: several IPs, some odd ports
            ips = _ips(rng, ANONYMIZING_NETWORKS + RESIDENTIAL_NETWORKS, rng.randint(3, 6)) if vpn_user else _ips(rng, RESIDENTIAL_NETWORKS, rng.choice([1, 1, 2]))
            user = eco.new_entity("user", ips, nonstandard_port_rate=0.25 if vpn_user else 0.01)
            for _ in range(rng.choice([1, 1, 2, 2, 3, 4])):
                address = eco.new_address(user, "user_wallet")
                eco.fund(address, round_btc(rng.lognormvariate(-2.2, 1.1)))
            self.users.append(user)

        self.coordinator = eco.new_entity("coinjoin_coordinator", _ips(rng, DATACENTER_NETWORKS, 2), nonstandard_port_rate=0.0)
        self.vendor = eco.new_entity("darknet_vendor", _ips(rng, NETWORK_RANGES, 26), illicit=True, nonstandard_port_rate=0.35, fee_rate=(10, 40))
        for _ in range(4):
            eco.fund(eco.new_address(self.vendor, "darknet_vendor"), rng.uniform(3, 8))
        self.vendor_mules = [eco.new_entity("money_mule", rng.sample(self.vendor.ips, 3) + _ips(rng, RESIDENTIAL_NETWORKS, 1), illicit=True, nonstandard_port_rate=0.3, fee_rate=(60, 150)) for _ in range(3)]

    def deposit_address(self, exchange: Entity, customer: Entity) -> str:
        """One reusable deposit address per (exchange, customer), owned by the exchange."""
        deposits = exchange.deposits  # type: ignore[attr-defined]
        if customer.entity_id not in deposits:
            deposits[customer.entity_id] = self.economy.new_address(exchange, "exchange_deposit")
        return deposits[customer.entity_id]

    # ── licit activity ──────────────────────────────────────────────────────
    def user_payment(self, when: datetime) -> None:
        rng, eco = self.rng, self.economy
        user = rng.choice(self.users)
        roll = rng.random()
        if roll < 0.5:
            merchant = rng.choice(self.merchants)
            target = eco.new_address(merchant, "merchant_receive") if rng.random() < 0.6 else rng.choice(merchant.addresses)
            amount = rng.uniform(0.0005, 0.08)
        elif roll < 0.8:
            peer = rng.choice(self.users)
            target = rng.choice(peer.addresses) if rng.random() < 0.7 else eco.new_address(peer, "user_wallet")
            amount = rng.uniform(0.001, 0.2)
        else:
            target = self.deposit_address(rng.choice(self.exchanges), user)
            amount = rng.uniform(0.01, 0.5)
        funded_total = sum(eco.balance[address] for address in eco.funded_addresses(user))
        amount = min(amount, funded_total * rng.uniform(0.2, 0.8))
        if amount > 0.0002:
            eco.pay(when, user, [(target, round_btc(amount))], "NORMAL", fresh_change=rng.random() < 0.7)

    def exchange_withdrawal(self, when: datetime) -> None:
        rng, eco = self.rng, self.economy
        exchange = rng.choice(self.exchanges)
        payments = []
        for user in rng.sample(self.users, min(len(self.users), rng.randint(4, 14))):
            target = rng.choice(user.addresses) if rng.random() < 0.6 else eco.new_address(user, "user_wallet")
            payments.append((target, round_btc(rng.lognormvariate(-2.0, 0.9))))
        if eco.balance[exchange.hot] > sum(amount for _, amount in payments) + 1:  # type: ignore[attr-defined]
            eco.emit(when, exchange, [exchange.hot], payments, "EXCHANGE_LIKE", change_to=exchange.hot)  # type: ignore[attr-defined]

    def exchange_sweep(self, when: datetime) -> None:
        eco = self.economy
        for exchange in self.exchanges:
            deposits = [address for address in exchange.deposits.values() if eco.balance.get(address, 0) > 0.001]  # type: ignore[attr-defined]
            for start in range(0, len(deposits), 40):
                chunk = deposits[start:start + 40]
                if len(chunk) >= 2:
                    total = sum(eco.balance[address] for address in chunk)
                    eco.emit(when, exchange, chunk, [(exchange.hot, round_btc(total - 0.0004))], "EXCHANGE_LIKE", change_to=exchange.hot, fee_rate=2.0)  # type: ignore[attr-defined]
                    when += _minutes(self.rng.uniform(1, 5))

    def merchant_sweep(self, when: datetime) -> None:
        rng, eco = self.rng, self.economy
        merchant = rng.choice(self.merchants)
        funded = eco.funded_addresses(merchant)
        if len(funded) >= 2:
            total = sum(eco.balance[address] for address in funded)
            target = self.deposit_address(rng.choice(self.exchanges), merchant)
            eco.emit(when, merchant, funded, [(target, round_btc(total * 0.9))], "NORMAL", change_to=merchant.addresses[0])

    def coinjoin_round(self, when: datetime, forced: list[tuple[Entity, str]] | None = None) -> dict[str, Any] | None:
        """Equal-denomination CoinJoin: each participant gets one denomination output and fresh change."""
        rng, eco = self.rng, self.economy
        denomination = rng.choice([0.01, 0.05, 0.1])
        participants: list[tuple[Entity, str]] = list(forced or [])
        candidates = [user for user in self.users if any(eco.balance[address] > denomination * 1.05 for address in user.addresses)]
        rng.shuffle(candidates)
        for user in candidates[: rng.randint(5, 11)]:
            source = max(user.addresses, key=lambda address: eco.balance[address])
            participants.append((user, source))
        if len(participants) < 5:
            return None
        inputs, input_amounts, outputs = [], [], []
        for participant, source in participants:
            amount = eco.balance[source]
            if amount < denomination * 1.02:
                continue
            inputs.append(source)
            input_amounts.append(round_btc(amount))
            outputs.append((eco.new_address(participant, "coinjoin_output"), denomination))
            change = round_btc(amount - denomination - 0.00003)
            if change > 0.00001:
                outputs.append((eco.new_address(participant, "change"), change))
        if len(inputs) < 5:
            return None
        rng.shuffle(outputs)
        fee = round_btc(sum(input_amounts) - sum(amount for _, amount in outputs))
        for address in inputs:
            eco.balance[address] = 0.0
        for address, amount in outputs:
            eco.balance[address] = round_btc(eco.balance.get(address, 0.0) + amount)
        src_ip = rng.choice(self.coordinator.ips)
        record = {
            "timestamp": when.replace(microsecond=0).isoformat(),
            "src_ip": src_ip,
            "dst_ip": rng.choice(LISTENING_PEERS),
            "src_port": rng.randint(32768, 60999),
            "dst_port": STANDARD_PORT,
            "txid": eco._txid(),
            "input_addresses": inputs,
            "output_addresses": [address for address, _ in outputs],
            "input_amounts": input_amounts,
            "output_amounts": [amount for _, amount in outputs],
            "fee": fee,
            "script_type": "P2WPKH",
            "geo_country": NETWORK_COUNTRY[network_of(src_ip)],
            "asn": NETWORK_ASN[network_of(src_ip)],
            "behavior_type": "MIXING_LIKE",
            "block_height": START_BLOCK + int((when - START_TIME).total_seconds() // 600),
            "transaction_size": int(10.5 + 68 * len(inputs) + 31 * len(outputs)),
        }
        eco.records.append(record)
        eco.patterns.append({"type": "coinjoin", "txids": [record["txid"]]})
        return record

    def vendor_payout(self, when: datetime) -> None:
        rng, eco = self.rng, self.economy
        if not eco.funded_addresses(self.vendor):
            eco.fund(eco.new_address(self.vendor, "darknet_vendor"), rng.uniform(2, 6))  # market escrow release
        if rng.random() < 0.35:  # cash out through a pass-through mule instead of directly
            mule = rng.choice(self.vendor_mules)
            target = eco.new_address(mule, "mule_wallet")
            self.schedule(when + _minutes(rng.uniform(2, 20)), lambda at: self.mule_forward(at, mule, target))
        else:
            target = self.deposit_address(rng.choice(self.exchanges), self.vendor)
        payments = [(target, round_btc(rng.uniform(0.05, 0.6)))]
        if rng.random() < 0.4:
            payments.append((eco.new_address(self.vendor, "darknet_vendor"), round_btc(rng.uniform(0.01, 0.2))))
        eco.pay(when, self.vendor, payments, "HIGH_NETWORK_DIVERSITY")

    # ── ransomware campaign ─────────────────────────────────────────────────
    def ransomware_campaign(self, group_index: int, report_rate: float) -> None:
        rng, eco = self.rng, self.economy
        operator = eco.new_entity("ransomware_operator", _ips(rng, ANONYMIZING_NETWORKS, 8), illicit=True, nonstandard_port_rate=0.45, fee_rate=(15, 60))
        mule_ips = _ips(rng, RESIDENTIAL_NETWORKS, 4) + operator.ips[:2]  # compromised hosts plus shared operator infrastructure
        mules = [eco.new_entity("money_mule", rng.sample(mule_ips, 3), illicit=True, nonstandard_port_rate=0.3, fee_rate=(60, 150)) for _ in range(max(2, int(6 * self.scale)))]
        victims = rng.sample(self.users, min(len(self.users), max(4, int(40 * self.scale))))
        start = 4 + group_index * 12
        collection: list[str] = []
        for victim in victims:
            address = eco.new_address(operator, "ransom_collection")
            collection.append(address)
            when = self.at_day(start, start + 10)

            def pay_ransom(when: datetime, victim: Entity = victim, address: str = address) -> None:
                if not eco.funded_addresses(victim):
                    eco.fund(eco.new_address(victim, "user_wallet"), rng.uniform(1.5, 4.0))  # bought on an exchange
                funded_total = sum(eco.balance[item] for item in eco.funded_addresses(victim))
                ransom = round_btc(min(rng.uniform(0.4, 2.5), funded_total * 0.9))
                if ransom < 0.3:
                    eco.fund(eco.new_address(victim, "user_wallet"), 2.0)
                    ransom = round_btc(rng.uniform(0.4, 1.5))
                eco.pay(when, victim, [(address, ransom)], "RANSOMWARE")

            self.schedule(when, pay_ransom)
        reported = rng.sample(collection, max(1, int(len(collection) * report_rate))) if report_rate > 0 else []
        eco.seeds.extend(reported)

        def launder(when: datetime) -> None:
            funded = [address for address in collection if eco.balance[address] > 0]
            if not funded:
                return
            # 1. consolidate the victim payments (links every collection address by common-input ownership)
            vault = eco.new_address(operator, "ransom_consolidation")
            for start_index in range(0, len(funded), 8):
                chunk = funded[start_index:start_index + 8]
                total = sum(eco.balance[address] for address in chunk)
                eco.emit(when, operator, chunk, [(vault, round_btc(total - 0.0006))], "RANSOMWARE")
                when += _minutes(rng.uniform(20, 90))
            balance = eco.balance[vault]
            # 2. split the proceeds across three laundering routes
            peel_seeds = [eco.new_address(operator, "peel_chain") for _ in range(2)]
            layer_seed = eco.new_address(operator, "layering_source")
            mix_seed = eco.new_address(operator, "mixing_input")
            routes = [(peel_seeds[0], round_btc(balance * 0.3)), (peel_seeds[1], round_btc(balance * 0.25)), (layer_seed, round_btc(balance * 0.25)), (mix_seed, round_btc(balance * 0.2 - 0.001))]
            eco.emit(when, operator, [vault], routes, "RANSOMWARE", change_to=vault)
            for delay, peel_seed in zip((rng.uniform(30, 120), rng.uniform(600, 1400)), peel_seeds):
                self.schedule(when + _minutes(delay), lambda at, peel_seed=peel_seed: self.peeling_chain(at, operator, mules, peel_seed))
            self.schedule(when + _minutes(rng.uniform(60, 240)), lambda at: self.layering(at, operator, layer_seed))
            self.schedule(when + timedelta(hours=rng.uniform(4, 30)), lambda at: self.mix_proceeds(at, operator, mix_seed))

        self.schedule(self.at_day(start + 10.5, start + 12), launder)

    def peeling_chain(self, when: datetime, operator: Entity, mules: list[Entity], current: str) -> None:
        rng, eco = self.rng, self.economy
        txids = []
        for _ in range(rng.randint(12, 24)):
            balance = eco.balance[current]
            if balance < 0.05:
                break
            roll = rng.random()
            if roll < 0.35:
                recipient = self.deposit_address(rng.choice(self.exchanges), operator)  # cash-out
            elif roll < 0.8:
                mule = rng.choice(mules)
                recipient = eco.new_address(mule, "mule_wallet")
                self.schedule(when + _minutes(rng.uniform(3, 25)), lambda at, mule=mule, address=recipient: self.mule_forward(at, mule, address))
            else:
                recipient = eco.new_address(rng.choice(self.users), "user_wallet")  # payment to an unwitting service
            peel = round_btc(balance * rng.uniform(0.04, 0.12))
            change = eco.new_address(operator, "peel_chain")
            record = eco.emit(when, operator, [current], [(recipient, peel)], "PEELING_CHAIN", change_to=change)
            if record is None:
                break
            txids.append(record["txid"])
            current = change
            when += _minutes(rng.uniform(8, 150))
        if txids:
            eco.patterns.append({"type": "peeling_chain", "txids": txids})

    def mule_forward(self, when: datetime, mule: Entity, address: str) -> None:
        eco = self.economy
        amount = eco.balance.get(address, 0.0)
        if amount > 0.001:
            target = self.deposit_address(self.rng.choice(self.exchanges), mule)
            record = eco.emit(when, mule, [address], [(target, round_btc(amount * 0.985))], "RAPID_TRANSFER")
            if record:
                eco.patterns.append({"type": "rapid_passthrough", "txids": [record["txid"]]})

    def layering(self, when: datetime, operator: Entity, source: str) -> None:
        rng, eco = self.rng, self.economy
        balance = eco.balance[source]
        width = rng.randint(4, 7)
        intermediates = [eco.new_address(operator, "layering_intermediate") for _ in range(width)]
        shares = [rng.uniform(0.7, 1.3) for _ in range(width)]
        split = [round_btc((balance - 0.002) * share / sum(shares)) for share in shares]
        first = eco.emit(when, operator, [source], list(zip(intermediates, split)), "LAYERING_LIKE")
        if first is None:
            return
        txids = [first["txid"]]
        sink = eco.new_address(operator, "layering_sink")
        for intermediate in intermediates:
            at = when + timedelta(hours=rng.uniform(1, 18))
            hop = eco.new_address(operator, "layering_intermediate")
            record = eco.emit(at, operator, [intermediate], [(hop, round_btc(eco.balance[intermediate] * 0.97))], "LAYERING_LIKE")
            if record:
                txids.append(record["txid"])
            at += timedelta(hours=rng.uniform(1, 12))
            record = eco.emit(at, operator, [hop], [(sink, round_btc(eco.balance[hop] - 0.0004))], "LAYERING_LIKE")
            if record:
                txids.append(record["txid"])
        cash_out = when + timedelta(hours=rng.uniform(32, 48))
        record = eco.emit(cash_out, operator, [sink], [(self.deposit_address(rng.choice(self.exchanges), operator), round_btc(eco.balance[sink] - 0.0005))], "LAYERING_LIKE")
        if record:
            txids.append(record["txid"])
        eco.patterns.append({"type": "layering", "txids": txids})

    def mix_proceeds(self, when: datetime, operator: Entity, source: str) -> None:
        """Split into denomination-sized pieces and join CoinJoin rounds with them."""
        eco = self.economy
        pieces = [eco.new_address(operator, "mixing_input") for _ in range(3)]
        balance = eco.balance[source]
        eco.emit(when, operator, [source], [(piece, round_btc((balance - 0.001) / 3)) for piece in pieces], "MIXING_LIKE")
        for index, piece in enumerate(pieces):
            self.schedule(when + timedelta(hours=2 + index * 5), lambda at, piece=piece: self.coinjoin_round(at, forced=[(operator, piece)]))

    # ── run ─────────────────────────────────────────────────────────────────
    def run(self) -> None:
        rng = self.rng
        self.ransomware_campaign(0, report_rate=0.35)  # group 1: some victims reported addresses (seeds)
        self.ransomware_campaign(1, report_rate=0.0)   # group 2: nobody reported anything
        for _ in range(max(1, int(28 * self.scale))):
            self.schedule(self.at_day(0, WINDOW_DAYS), self.coinjoin_round)
        for _ in range(max(2, int(130 * self.scale))):
            self.schedule(self.at_day(0, WINDOW_DAYS), self.vendor_payout)
        for _ in range(max(2, int(700 * self.scale))):
            self.schedule(self.at_day(0, WINDOW_DAYS), self.exchange_withdrawal)
        for day in range(0, WINDOW_DAYS, 2):
            self.schedule(START_TIME + timedelta(days=day, hours=rng.uniform(1, 4)), self.exchange_sweep)
        for _ in range(max(2, int(260 * self.scale))):
            self.schedule(self.at_day(0, WINDOW_DAYS), self.merchant_sweep)
        # Licit payments fill the timeline; the dataset is later cut to exactly ``target`` records.
        for _ in range(int(self.target * 1.1) + 20):
            self.schedule(self.at_day(0, WINDOW_DAYS), self.user_payment)
        while self.queue:
            when, _, action = heapq.heappop(self.queue)
            action(when)


def generate_world(records: int = 10000, seed: int = 42) -> tuple[list[dict[str, Any]], Counter[str], dict[str, Any]]:
    """Simulate the economy and return (records, behaviour counts, ground truth)."""
    if records <= 0:
        raise ValueError("Record count must be positive.")
    scenario = Scenario(records, seed)
    scenario.run()
    eco = scenario.economy
    ordered = sorted(eco.records, key=lambda record: (record["timestamp"], record["txid"]))
    # Keep every illicit-flow and CoinJoin transaction, then fill with licit traffic up to the target.
    keep_labels = set(ILLICIT_BEHAVIORS) | {"MIXING_LIKE"}
    priority = [record for record in ordered if record["behavior_type"] in keep_labels]
    rest = [record for record in ordered if record["behavior_type"] not in keep_labels]
    if len(priority) >= records:
        selected = priority[:records]
    else:
        stride = len(rest) / (records - len(priority))
        selected = priority + [rest[int(index * stride)] for index in range(records - len(priority))]
    selected.sort(key=lambda record: (record["timestamp"], record["txid"]))
    kept = {record["txid"] for record in selected}

    counts = Counter(record["behavior_type"] for record in selected)
    seen = {address for record in selected for address in record["input_addresses"] + record["output_addresses"]}
    wallets = {}
    for address in sorted(seen):
        entity = eco.entities[eco.owner[address]]
        wallets[address] = {"entity_id": entity.entity_id, "entity_kind": entity.kind, "role": eco.role[address], "illicit": entity.illicit}
    patterns = []
    for pattern in eco.patterns:
        txids = [txid for txid in pattern["txids"] if txid in kept]
        if txids:
            patterns.append({"type": pattern["type"], "txids": txids})
    ground_truth = {
        "note": "Synthetic evaluation labels. Not available to the models; used only to measure them.",
        "seed_wallets": [address for address in eco.seeds if address in seen],
        "wallets": wallets,
        "patterns": patterns,
    }
    return selected, counts, ground_truth


def generate_dataset(records: int = 10000, seed: int = 42) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Return ``records`` synthetic transactions and their behaviour-label counts."""
    selected, counts, _ = generate_world(records, seed)
    return selected, counts


def validate_record(record: dict[str, Any]) -> list[str]:
    """Validate a single synthetic record before writing to disk."""
    errors: list[str] = []
    required_fields = [
        "timestamp", "src_ip", "dst_ip", "src_port", "dst_port", "txid", "input_addresses", "output_addresses",
        "input_amounts", "output_amounts", "fee", "script_type", "geo_country", "asn", "behavior_type",
    ]
    for field_name in required_fields:
        if field_name not in record:
            errors.append(f"Missing field: {field_name}")
    if record.get("behavior_type") not in SUPPORTED_BEHAVIORS:
        errors.append("Unsupported behavior_type")
    try:
        datetime.fromisoformat(record["timestamp"])  # type: ignore[index]
    except (TypeError, ValueError, KeyError):
        errors.append("Invalid timestamp")
    if not isinstance(record.get("txid"), str) or not record["txid"]:
        errors.append("Invalid txid")
    for list_field in ("input_addresses", "output_addresses", "input_amounts", "output_amounts"):
        if not isinstance(record.get(list_field), list) or not record[list_field]:
            errors.append(f"Invalid {list_field}")
    if len(record.get("input_addresses", [])) != len(record.get("input_amounts", [])) or len(record.get("output_addresses", [])) != len(record.get("output_amounts", [])):
        errors.append("Address and amount lists differ in length")
    for ip_field in ["src_ip", "dst_ip"]:
        try:
            address = IPv4Address(record.get(ip_field))
        except (ValueError, TypeError):
            errors.append(f"Invalid IPv4 in {ip_field}")
            continue
        if not _is_public_network(IPv4Network(f"{address}/32")):
            errors.append(f"Non-public IPv4 in {ip_field}")
    for port_field in ["src_port", "dst_port"]:
        port = record.get(port_field)
        if not isinstance(port, int) or not (0 <= port <= 65535):
            errors.append(f"Invalid port in {port_field}")
    input_total = sum(float(v) for v in record.get("input_amounts", []))
    output_total = sum(float(v) for v in record.get("output_amounts", []))
    fee = float(record.get("fee", -1))
    if input_total < 0 or output_total < 0 or any(float(v) <= 0 for v in record.get("output_amounts", [])):
        errors.append("Amounts must be positive")
    if fee < 0:
        errors.append("Fee cannot be negative")
    if input_total + 1e-8 < output_total:
        errors.append("Input total is less than output total")
    return errors


CSV_FIELDS = [
    "timestamp", "src_ip", "dst_ip", "src_port", "dst_port", "txid", "input_addresses", "output_addresses",
    "input_amounts", "output_amounts", "fee", "script_type", "geo_country", "asn", "behavior_type", "block_height", "transaction_size",
]


def write_csv(records: list[dict[str, Any]], output_path: Path) -> None:
    """Write CSV with array fields serialized as JSON strings."""
    with output_path.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow({
                **record,
                "input_addresses": json.dumps(record["input_addresses"]),
                "output_addresses": json.dumps(record["output_addresses"]),
                "input_amounts": json.dumps(record["input_amounts"]),
                "output_amounts": json.dumps(record["output_amounts"]),
            })


def write_json(records: list[dict[str, Any]], output_path: Path) -> None:
    """Write the same records as JSON, keeping arrays as native lists."""
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2)


def write_xml(records: list[dict[str, Any]], output_path: Path) -> None:
    """Write the same records as XML with nested array elements."""
    root = ET.Element("transactions")
    scalar_fields = ["timestamp", "src_ip", "dst_ip", "src_port", "dst_port", "txid", "fee", "script_type", "geo_country", "asn", "behavior_type", "block_height", "transaction_size"]
    for record in records:
        item = ET.SubElement(root, "transaction")
        for field_name in scalar_fields:
            ET.SubElement(item, field_name).text = str(record[field_name])
        for list_name, child_name in (("input_addresses", "address"), ("output_addresses", "address"), ("input_amounts", "amount"), ("output_amounts", "amount")):
            container = ET.SubElement(item, list_name)
            for value in record[list_name]:
                ET.SubElement(container, child_name).text = str(value)
    ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)


def write_outputs(records: list[dict[str, Any]], output_dir: Path) -> tuple[Path, Path, Path]:
    """Persist the same synthetic dataset to CSV, JSON, and XML."""
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "transactions.csv"
    json_path = output_dir / "transactions.json"
    xml_path = output_dir / "transactions.xml"
    write_csv(records, csv_path)
    write_json(records, json_path)
    write_xml(records, xml_path)
    return csv_path, json_path, xml_path


def write_ground_truth(ground_truth: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    """Write evaluation labels and the known-illicit seed list next to the dataset."""
    truth_path = output_dir / "ground_truth.json"
    seeds_path = output_dir / "seed_wallets.txt"
    truth_path.write_text(json.dumps(ground_truth, indent=1), encoding="utf-8")
    seeds_path.write_text("".join(f"{address}\n" for address in ground_truth["seed_wallets"]), encoding="utf-8")
    return truth_path, seeds_path


def print_summary(records: list[dict[str, Any]], outputs: tuple[Path, ...], behavior_counts: Counter[str], ground_truth: dict[str, Any]) -> None:
    """Print a user-friendly summary of the generated synthetic dataset."""
    wallets = ground_truth["wallets"]
    print("Synthetic dataset generated successfully.\n")
    print(f"Records: {len(records)}")
    print("Behavior distribution:")
    for profile in SUPPORTED_BEHAVIORS:
        print(f"  {profile}: {behavior_counts.get(profile, 0)}")
    print(f"Addresses: {len(wallets)} ({sum(1 for item in wallets.values() if item['illicit'])} illicit)")
    print(f"Entities: {len({item['entity_id'] for item in wallets.values()})}")
    print(f"IPs: {len({record['src_ip'] for record in records} | {record['dst_ip'] for record in records})}")
    print(f"Patterns: {dict(Counter(pattern['type'] for pattern in ground_truth['patterns']))}")
    print(f"Seed wallets: {len(ground_truth['seed_wallets'])}")
    print("Output:")
    for path in outputs:
        print(f"  {path}")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for record count and seed."""
    parser = argparse.ArgumentParser(description="Generate a synthetic Bitcoin transaction/network dataset.")
    parser.add_argument("--records", type=int, default=10000, help="Number of synthetic records to generate.")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic random seed.")
    parser.add_argument("--output-dir", default="data/synthetic", help="Directory for the dataset, ground truth and seed list.")
    return parser.parse_args()


def main() -> None:
    """Command-line entrypoint for dataset generation."""
    args = parse_args()
    records, behavior_counts, ground_truth = generate_world(records=args.records, seed=args.seed)
    output_dir = Path(args.output_dir)
    outputs = write_outputs(records, output_dir) + write_ground_truth(ground_truth, output_dir)
    print_summary(records, outputs, behavior_counts, ground_truth)


if __name__ == "__main__":
    main()
