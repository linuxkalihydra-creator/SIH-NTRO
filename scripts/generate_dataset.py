#!/usr/bin/env python3
"""Generate a synthetic Bitcoin transaction/network dataset for offline SIH prototyping.

This module creates a reproducible synthetic dataset with internally consistent wallet,
network, and transaction relationships. It is intentionally not connected to real
Bitcoin data or criminal activity records.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import uuid
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Any

SUPPORTED_BEHAVIORS = (
    "NORMAL",
    "EXCHANGE_LIKE",
    "RAPID_TRANSFER",
    "LAYERING_LIKE",
    "MIXING_LIKE",
    "HIGH_NETWORK_DIVERSITY",
)

PROFILE_WEIGHTS = {
    "NORMAL": 0.35,
    "EXCHANGE_LIKE": 0.20,
    "RAPID_TRANSFER": 0.15,
    "LAYERING_LIKE": 0.10,
    "MIXING_LIKE": 0.15,
    "HIGH_NETWORK_DIVERSITY": 0.05,
}

COUNTRIES = [
    "US",
    "CA",
    "GB",
    "DE",
    "NL",
    "FR",
    "JP",
    "AU",
    "BR",
    "SG",
    "IN",
    "ZA",
]

SCRIPT_TYPES = ["P2PKH", "P2SH_P2WPKH", "P2WPKH", "P2TR"]


def _is_public_network(network: IPv4Network) -> bool:
    """True for globally routable unicast space that a real GeoIP database can resolve."""
    return network.is_global and not (network.is_multicast or network.is_reserved or network.is_private or network.is_loopback or network.is_link_local)


def _public_networks(count: int = 12, seed: int = 8333) -> list[IPv4Network]:
    """Pick a fixed, deterministic set of public /24 networks.

    Documentation (RFC 5737), private, CGNAT, multicast and reserved blocks are
    excluded because they can never resolve in a real GeoIP database. A small
    fixed pool keeps realistic IP reuse between wallets.
    """
    picker = random.Random(seed)
    networks: list[IPv4Network] = []
    while len(networks) < count:
        network = IPv4Network((picker.getrandbits(24) << 8, 24))
        if _is_public_network(network) and network not in networks:
            networks.append(network)
    return networks


NETWORK_RANGES = _public_networks()

BASE58_CHARS = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def utc_now() -> datetime:
    """Return timezone-aware UTC timestamps for the synthetic records."""
    return datetime.now(timezone.utc)


def make_wallet_address(rng: random.Random) -> str:
    """Create a synthetic Bitcoin-like address string without implying a real wallet."""
    prefix = rng.choice(["1", "3", "bc1q"]) if rng.random() > 0.5 else "1"
    body = "".join(rng.choice(BASE58_CHARS) for _ in range(25))
    return prefix + body


def make_txid(rng: random.Random) -> str:
    """Create a deterministic synthetic transaction identifier."""
    return "tx_" + "".join(rng.choice("abcdef0123456789") for _ in range(16))


def make_ip(rng: random.Random) -> str:
    """Generate a public IPv4 address from the fixed synthetic network pool."""
    network = rng.choice(NETWORK_RANGES)
    host = rng.randint(1, network.num_addresses - 2)
    return str(IPv4Address(int(network.network_address) + host))


def make_port(rng: random.Random, standard: bool = False) -> int:
    """Create a realistic synthetic port, with 8333 used frequently to resemble Bitcoin P2P."""
    if standard and rng.random() < 0.7:
        return 8333
    return rng.randint(1024, 65535)


def make_timestamp(rng: random.Random, profile: str, index: int) -> datetime:
    """Return a timezone-aware UTC timestamp using profile-specific spacing."""
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    if profile == "NORMAL":
        seconds = rng.randint(900, 86400)
    elif profile == "EXCHANGE_LIKE":
        seconds = rng.randint(60, 3600)
    elif profile == "RAPID_TRANSFER":
        seconds = rng.randint(5, 600)
    elif profile == "LAYERING_LIKE":
        seconds = rng.randint(15, 1800)
    elif profile == "MIXING_LIKE":
        seconds = rng.randint(30, 2400)
    elif profile == "HIGH_NETWORK_DIVERSITY":
        seconds = rng.randint(60, 7200)
    else:
        seconds = rng.randint(60, 7200)
    return base + timedelta(seconds=(index * 11) + seconds)


def make_amount(rng: random.Random, low: float, high: float) -> float:
    """Generate a plausible BTC-like amount with a small float precision."""
    return round(rng.uniform(low, high), 8)


def split_output_amounts(total_output: float, count: int) -> list[float]:
    """Split a total into count positive transaction outputs while preserving the sum exactly."""
    if count <= 0:
        raise ValueError("Output count must be positive.")
    if count == 1:
        return [round(total_output, 8)]

    base = total_output / count
    amounts = [round(base, 8) for _ in range(count)]
    remainder = round(total_output - sum(amounts), 8)
    amounts[-1] = round(amounts[-1] + remainder, 8)
    if any(amount < 0 for amount in amounts):
        raise ValueError(f"Negative split output discovered: {amounts}")
    return amounts


def ensure_valid_amounts(input_amounts: list[float], output_amounts: list[float]) -> tuple[list[float], list[float], float]:
    """Return normalized amounts and a fee while maintaining input_total >= output_total."""
    input_total = sum(input_amounts)
    output_total = sum(output_amounts)
    if output_total > input_total:
        extra = output_total - input_total
        output_amounts[-1] = round(output_amounts[-1] - extra, 8)
        output_total = sum(output_amounts)
    fee = round(max(input_total - output_total, 0.0), 8)
    return input_amounts, output_amounts, fee


def make_wallet_pool(rng: random.Random, profile: str, size: int) -> list[dict[str, Any]]:
    """Build a pool of synthetic wallets tied to synthetic IP/ASN/country metadata."""
    wallet_pool: list[dict[str, Any]] = []
    country_pool = COUNTRIES[:]
    for idx in range(size):
        wallet = {
            "wallet": make_wallet_address(rng),
            "country": rng.choice(country_pool),
            "asn": rng.choice([64512, 64513, 64514, 64515, 64516, 64517, 64518, 64519, 64520]),
            "ips": [make_ip(rng) for _ in range(rng.randint(1, 3))],
            "behavior": profile,
        }
        wallet_pool.append(wallet)
    return wallet_pool


def assign_ip_metadata(rng: random.Random, country: str, asn: int) -> tuple[str, str, int]:
    """Return one IP + country + ASN pair for a wallet/network observation."""
    return make_ip(rng), country, asn


def pick_wallet_pair(rng: random.Random, wallet_pool: list[dict[str, Any]], fixed_source: dict[str, Any] | None = None):
    """Choose a wallet pair while favoring repeated consistent relationships."""
    if fixed_source is not None:
        candidate = rng.choice(wallet_pool)
        while candidate["wallet"] == fixed_source["wallet"]:
            candidate = rng.choice(wallet_pool)
        return fixed_source, candidate
    source = rng.choice(wallet_pool)
    target = rng.choice(wallet_pool)
    while target["wallet"] == source["wallet"]:
        target = rng.choice(wallet_pool)
    return source, target


def generate_normal_record(rng: random.Random, wallet_pool: list[dict[str, Any]], timestamp: datetime, index: int) -> dict[str, Any]:
    """Generate a lower-volume, low-diversity transaction with sparse network variation."""
    source, target = pick_wallet_pair(rng, wallet_pool)
    src_ip, src_country, src_asn = assign_ip_metadata(rng, source["country"], source["asn"])
    dst_ip, dst_country, dst_asn = assign_ip_metadata(rng, target["country"], target["asn"])
    input_amount = make_amount(rng, 0.05, 1.5)
    fee = round(rng.uniform(0.00005, 0.002), 8)
    output_amount = round(max(input_amount - fee, 0.0), 8)
    record = {
        "timestamp": timestamp.isoformat(),
        "src_ip": src_ip,
        "dst_ip": dst_ip,
        "src_port": make_port(rng, standard=True),
        "dst_port": make_port(rng, standard=True),
        "txid": make_txid(rng),
        "input_addresses": [source["wallet"]],
        "output_addresses": [target["wallet"]],
        "input_amounts": [round(input_amount, 8)],
        "output_amounts": [output_amount],
        "fee": fee,
        "script_type": rng.choice(SCRIPT_TYPES),
        "geo_country": src_country,
        "asn": src_asn,
        "behavior_type": "NORMAL",
        "block_height": 900000 + index,
        "transaction_size": rng.randint(200, 700),
    }
    return record


def generate_exchange_record(rng: random.Random, wallet_pool: list[dict[str, Any]], timestamp: datetime, index: int) -> dict[str, Any]:
    """Generate exchange-like behavior with many counterparties and higher turnover."""
    source = rng.choice(wallet_pool)
    outputs = rng.sample(wallet_pool, k=rng.randint(2, 5))
    input_amount = make_amount(rng, 2.0, 25.0)
    fee = round(rng.uniform(0.0005, 0.012), 8)
    output_total = round(max(input_amount - fee, 0.0), 8)
    output_amounts = split_output_amounts(output_total, len(outputs))
    src_ip, src_country, src_asn = assign_ip_metadata(rng, source["country"], source["asn"])
    record = {
        "timestamp": timestamp.isoformat(),
        "src_ip": src_ip,
        "dst_ip": rng.choice(source["ips"] + [make_ip(rng)]),
        "src_port": make_port(rng, standard=True),
        "dst_port": make_port(rng, standard=True),
        "txid": "",
        "input_addresses": [source["wallet"]],
        "output_addresses": [wallet["wallet"] for wallet in outputs],
        "input_amounts": [round(input_amount, 8)],
        "output_amounts": output_amounts,
        "fee": fee,
        "script_type": rng.choice(SCRIPT_TYPES),
        "geo_country": src_country,
        "asn": src_asn,
        "behavior_type": "EXCHANGE_LIKE",
        "block_height": 900000 + index,
        "transaction_size": rng.randint(400, 1200),
    }
    return record


def generate_rapid_record(rng: random.Random, wallet_pool: list[dict[str, Any]], timestamp: datetime, index: int, previous_wallet: dict[str, Any] | None = None) -> dict[str, Any]:
    """Generate rapid transfers: funds move quickly between related wallets."""
    source = previous_wallet or rng.choice(wallet_pool)
    target = rng.choice(wallet_pool)
    while target["wallet"] == source["wallet"]:
        target = rng.choice(wallet_pool)
    src_ip, src_country, src_asn = assign_ip_metadata(rng, source["country"], source["asn"])
    dst_ip, dst_country, dst_asn = assign_ip_metadata(rng, target["country"], target["asn"])
    input_amount = make_amount(rng, 0.4, 8.0)
    fee = round(rng.uniform(0.0001, 0.004), 8)
    output_amount = round(max(input_amount - fee, 0.0), 8)
    record = {
        "timestamp": timestamp.isoformat(),
        "src_ip": src_ip,
        "dst_ip": dst_ip,
        "src_port": make_port(rng, standard=True),
        "dst_port": make_port(rng, standard=True),
        "txid": make_txid(rng),
        "input_addresses": [source["wallet"]],
        "output_addresses": [target["wallet"]],
        "input_amounts": [round(input_amount, 8)],
        "output_amounts": [output_amount],
        "fee": fee,
        "script_type": rng.choice(SCRIPT_TYPES),
        "geo_country": src_country,
        "asn": src_asn,
        "behavior_type": "RAPID_TRANSFER",
        "block_height": 900000 + index,
        "transaction_size": rng.randint(220, 900),
    }
    if dst_country:
        record["geo_country"] = src_country
    return record


def generate_layering_record(rng: random.Random, wallet_pool: list[dict[str, Any]], timestamp: datetime, index: int, chain_wallets: list[dict[str, Any]]) -> dict[str, Any]:
    """Generate a wallet-to-wallet layering chain: A -> B -> C -> D -> E."""
    if len(chain_wallets) < 2:
        source = rng.choice(wallet_pool)
        target = rng.choice(wallet_pool)
    else:
        source = chain_wallets[-1]
        pool_without_source = [w for w in wallet_pool if w["wallet"] != source["wallet"]]
        target = rng.choice(pool_without_source)
    src_ip, src_country, src_asn = assign_ip_metadata(rng, source["country"], source["asn"])
    dst_ip, dst_country, dst_asn = assign_ip_metadata(rng, target["country"], target["asn"])
    input_amount = make_amount(rng, 0.8, 12.0)
    fee = round(rng.uniform(0.0002, 0.006), 8)
    output_amount = round(max(input_amount - fee, 0.0), 8)
    record = {
        "timestamp": timestamp.isoformat(),
        "src_ip": src_ip,
        "dst_ip": dst_ip,
        "src_port": make_port(rng, standard=True),
        "dst_port": make_port(rng, standard=True),
        "txid": make_txid(rng),
        "input_addresses": [source["wallet"]],
        "output_addresses": [target["wallet"]],
        "input_amounts": [round(input_amount, 8)],
        "output_amounts": [output_amount],
        "fee": fee,
        "script_type": rng.choice(SCRIPT_TYPES),
        "geo_country": src_country,
        "asn": src_asn,
        "behavior_type": "LAYERING_LIKE",
        "block_height": 900000 + index,
        "transaction_size": rng.randint(250, 950),
    }
    return record


def generate_mixing_record(rng: random.Random, wallet_pool: list[dict[str, Any]], timestamp: datetime, index: int) -> dict[str, Any]:
    """Generate mixing-like behavior with multiple inputs and multiple outputs."""
    input_wallets = rng.sample(wallet_pool, k=rng.randint(3, 6))
    output_wallets = rng.sample(wallet_pool, k=rng.randint(3, 6))
    input_amounts = [round(make_amount(rng, 1.5, 8.0), 8) for _ in input_wallets]
    input_total = sum(input_amounts)
    fee = round(rng.uniform(0.001, 0.01), 8)
    output_total = round(max(input_total - fee, 0.0), 8)
    output_amounts = split_output_amounts(output_total, len(output_wallets))
    src_ip = make_ip(rng)
    record = {
        "timestamp": timestamp.isoformat(),
        "src_ip": src_ip,
        "dst_ip": make_ip(rng),
        "src_port": make_port(rng, standard=True),
        "dst_port": make_port(rng, standard=True),
        "txid": "",
        "input_addresses": [wallet["wallet"] for wallet in input_wallets],
        "output_addresses": [wallet["wallet"] for wallet in output_wallets],
        "input_amounts": input_amounts,
        "output_amounts": output_amounts,
        "fee": fee,
        "script_type": rng.choice(SCRIPT_TYPES),
        "geo_country": rng.choice(COUNTRIES),
        "asn": rng.choice([64512, 64513, 64514, 64515, 64516, 64517, 64518, 64519, 64520]),
        "behavior_type": "MIXING_LIKE",
        "block_height": 900000 + index,
        "transaction_size": rng.randint(600, 1800),
    }
    return record


def generate_high_diversity_record(rng: random.Random, wallet_pool: list[dict[str, Any]], timestamp: datetime, index: int) -> dict[str, Any]:
    """Generate a profile with several IPs, ASNs, and countries tied to one wallet."""
    source = rng.choice(wallet_pool)
    target = rng.choice(wallet_pool)
    src_ip, src_country, src_asn = assign_ip_metadata(rng, source["country"], source["asn"])
    dst_ip, dst_country, dst_asn = assign_ip_metadata(rng, target["country"], target["asn"])
    input_amount = make_amount(rng, 5.0, 40.0)
    fee = round(rng.uniform(0.001, 0.02), 8)
    output_amount = round(max(input_amount - fee, 0.0), 8)
    record = {
        "timestamp": timestamp.isoformat(),
        "src_ip": src_ip,
        "dst_ip": dst_ip,
        "src_port": make_port(rng, standard=False),
        "dst_port": make_port(rng, standard=False),
        "txid": make_txid(rng),
        "input_addresses": [source["wallet"]],
        "output_addresses": [target["wallet"]],
        "input_amounts": [round(input_amount, 8)],
        "output_amounts": [output_amount],
        "fee": fee,
        "script_type": rng.choice(SCRIPT_TYPES),
        "geo_country": src_country,
        "asn": src_asn,
        "behavior_type": "HIGH_NETWORK_DIVERSITY",
        "block_height": 900000 + index,
        "transaction_size": rng.randint(300, 1100),
    }
    return record


def allocate_counts(total_records: int) -> dict[str, int]:
    """Allocate a deterministic distribution across the synthetic behavioral profiles."""
    counts = {name: int(total_records * weight) for name, weight in PROFILE_WEIGHTS.items()}
    remainder = total_records - sum(counts.values())
    ordered = list(PROFILE_WEIGHTS.keys())
    for idx in range(remainder):
        counts[ordered[idx % len(ordered)]] += 1
    return counts


def generate_dataset(records: int = 10000, seed: int = 42) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Generate the synthetic dataset and return records plus a behavioral summary."""
    rng = random.Random(seed)
    counts = allocate_counts(records)
    all_wallets: dict[str, list[dict[str, Any]]] = {}
    for profile in SUPPORTED_BEHAVIORS:
        all_wallets[profile] = make_wallet_pool(rng, profile, max(12, counts[profile] // 4 + 8))

    generated: list[dict[str, Any]] = []
    behavioral_counter: Counter[str] = Counter()
    time_offsets = defaultdict(int)
    used_txids: set[str] = set()

    for profile in SUPPORTED_BEHAVIORS:
        for idx in range(counts[profile]):
            timestamp = make_timestamp(rng, profile, time_offsets[profile] + idx)
            if profile == "NORMAL":
                record = generate_normal_record(rng, all_wallets[profile], timestamp, idx)
            elif profile == "EXCHANGE_LIKE":
                record = generate_exchange_record(rng, all_wallets[profile], timestamp, idx)
            elif profile == "RAPID_TRANSFER":
                record = generate_rapid_record(rng, all_wallets[profile], timestamp, idx, None)
            elif profile == "LAYERING_LIKE":
                chain = all_wallets[profile][:3]
                record = generate_layering_record(rng, all_wallets[profile], timestamp, idx, chain)
            elif profile == "MIXING_LIKE":
                record = generate_mixing_record(rng, all_wallets[profile], timestamp, idx)
            elif profile == "HIGH_NETWORK_DIVERSITY":
                record = generate_high_diversity_record(rng, all_wallets[profile], timestamp, idx)
            else:
                raise ValueError(f"Unsupported profile: {profile}")

            txid = make_txid(rng)
            while txid in used_txids:
                txid = make_txid(rng)
            record["txid"] = txid
            used_txids.add(txid)

            errors = validate_record(record)
            if errors:
                raise ValueError(f"Validation failed for {record.get('txid')}: {errors}")
            generated.append(record)
            behavioral_counter[profile] += 1

    return generated, behavioral_counter


def validate_record(record: dict[str, Any]) -> list[str]:
    """Validate a single synthetic record before writing to disk."""
    errors: list[str] = []

    required_fields = [
        "timestamp",
        "src_ip",
        "dst_ip",
        "src_port",
        "dst_port",
        "txid",
        "input_addresses",
        "output_addresses",
        "input_amounts",
        "output_amounts",
        "fee",
        "script_type",
        "geo_country",
        "asn",
        "behavior_type",
    ]
    for field in required_fields:
        if field not in record:
            errors.append(f"Missing field: {field}")

    if record.get("behavior_type") not in SUPPORTED_BEHAVIORS:
        errors.append("Unsupported behavior_type")

    try:
        datetime.fromisoformat(record["timestamp"])  # type: ignore[index]
    except (TypeError, ValueError):
        errors.append("Invalid timestamp")

    if not isinstance(record.get("txid"), str) or not record["txid"]:
        errors.append("Invalid txid")

    if not isinstance(record.get("input_addresses"), list) or not record["input_addresses"]:
        errors.append("Invalid input_addresses")
    if not isinstance(record.get("output_addresses"), list) or not record["output_addresses"]:
        errors.append("Invalid output_addresses")
    if not isinstance(record.get("input_amounts"), list) or not record["input_amounts"]:
        errors.append("Invalid input_amounts")
    if not isinstance(record.get("output_amounts"), list) or not record["output_amounts"]:
        errors.append("Invalid output_amounts")

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
    if input_total < 0 or output_total < 0:
        errors.append("Amounts cannot be negative")
    if fee < 0:
        errors.append("Fee cannot be negative")
    if input_total < output_total:
        errors.append("Input total is less than output total")

    return errors


def write_csv(records: list[dict[str, Any]], output_path: Path) -> None:
    """Write CSV with array fields serialized as JSON strings."""
    fieldnames = [
        "timestamp",
        "src_ip",
        "dst_ip",
        "src_port",
        "dst_port",
        "txid",
        "input_addresses",
        "output_addresses",
        "input_amounts",
        "output_amounts",
        "fee",
        "script_type",
        "geo_country",
        "asn",
        "behavior_type",
        "block_height",
        "transaction_size",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            row = {
                **record,
                "input_addresses": json.dumps(record["input_addresses"]),
                "output_addresses": json.dumps(record["output_addresses"]),
                "input_amounts": json.dumps(record["input_amounts"]),
                "output_amounts": json.dumps(record["output_amounts"]),
            }
            writer.writerow(row)


def write_json(records: list[dict[str, Any]], output_path: Path) -> None:
    """Write the same records as JSON, keeping arrays as native lists."""
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2)


def write_xml(records: list[dict[str, Any]], output_path: Path) -> None:
    """Write the same records as XML with nested array elements."""
    root = ET.Element("transactions")
    for record in records:
        item = ET.SubElement(root, "transaction")
        for field in [
            "timestamp",
            "src_ip",
            "dst_ip",
            "src_port",
            "dst_port",
            "txid",
            "fee",
            "script_type",
            "geo_country",
            "asn",
            "behavior_type",
            "block_height",
            "transaction_size",
        ]:
            child = ET.SubElement(item, field)
            child.text = str(record[field])

        input_addresses = ET.SubElement(item, "input_addresses")
        for value in record["input_addresses"]:
            element = ET.SubElement(input_addresses, "address")
            element.text = value

        output_addresses = ET.SubElement(item, "output_addresses")
        for value in record["output_addresses"]:
            element = ET.SubElement(output_addresses, "address")
            element.text = value

        input_amounts = ET.SubElement(item, "input_amounts")
        for value in record["input_amounts"]:
            element = ET.SubElement(input_amounts, "amount")
            element.text = str(value)

        output_amounts = ET.SubElement(item, "output_amounts")
        for value in record["output_amounts"]:
            element = ET.SubElement(output_amounts, "amount")
            element.text = str(value)

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


def print_summary(records: list[dict[str, Any]], outputs: tuple[Path, Path, Path], behavior_counts: Counter[str]) -> None:
    """Print a user-friendly summary of the generated synthetic dataset."""
    unique_wallets = {addr for record in records for addr in record["input_addresses"] + record["output_addresses"]}
    unique_ips = {record["src_ip"] for record in records} | {record["dst_ip"] for record in records}
    unique_asns = {record["asn"] for record in records}
    countries = {record["geo_country"] for record in records}

    print("Synthetic dataset generated successfully.")
    print()
    print("Records:")
    print(len(records))
    print()
    print("Behavior distribution:")
    for profile in SUPPORTED_BEHAVIORS:
        print(f"{profile}: {behavior_counts.get(profile, 0)}")
    print()
    print("Unique:")
    print(f"Wallets: {len(unique_wallets)}")
    print(f"Transactions: {len({record['txid'] for record in records})}")
    print(f"IPs: {len(unique_ips)}")
    print(f"ASNs: {len(unique_asns)}")
    print(f"Countries: {len(countries)}")
    print()
    print("Output:")
    for path in outputs:
        print(path)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for record count and seed."""
    parser = argparse.ArgumentParser(description="Generate a synthetic Bitcoin transaction/network dataset.")
    parser.add_argument("--records", type=int, default=10000, help="Number of synthetic records to generate.")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic random seed.")
    return parser.parse_args()


def main() -> None:
    """Command-line entrypoint for dataset generation."""
    args = parse_args()
    if args.records <= 0:
        raise ValueError("Record count must be positive.")

    records, behavior_counts = generate_dataset(records=args.records, seed=args.seed)
    output_dir = Path("data/synthetic")
    outputs = write_outputs(records, output_dir)
    print_summary(records, outputs, behavior_counts)


if __name__ == "__main__":
    main()
