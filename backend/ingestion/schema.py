"""Canonical dataset schema, column aliases and tolerant value coercion.

Every input row is one *network observation* of a transaction: a peer (``src_ip``)
relayed a transaction (``txid`` with its input/output addresses and amounts) to
another peer (``dst_ip``) at ``timestamp``. Datasets from different capture tools
name these fields differently and encode lists, timestamps and amounts in
different ways, so ingestion maps whatever it finds onto one canonical schema:

* column names are matched case-insensitively against an alias table
  (``tx_hash`` -> ``txid``, ``source_ip`` -> ``src_ip``, ``inputs`` -> ``input_addresses`` ...);
* a ``_sat`` / ``_satoshi`` suffix marks an amount column as satoshis;
* lists may be JSON arrays, Python-style lists, or ``;`` ``|`` ``,`` separated text;
* inputs/outputs may also be lists of ``{address, amount}`` objects;
* timestamps may be ISO-8601 (with ``Z`` or an offset), ``YYYY-MM-DD HH:MM:SS``
  style text, or Unix epochs in seconds, milliseconds, microseconds or nanoseconds.
"""

from __future__ import annotations

import ast
import json
import re
from functools import lru_cache
from dataclasses import dataclass, field
from datetime import datetime, timezone
from ipaddress import ip_address
from typing import Any

CANONICAL_FIELDS = (
    "timestamp", "src_ip", "dst_ip", "src_port", "dst_port", "txid",
    "input_addresses", "output_addresses", "input_amounts", "output_amounts",
    "fee", "script_type", "geo_country", "asn", "behavior_type",
)
OPTIONAL_EXTRA_FIELDS = ("block_height", "transaction_size")

# The minimum fields named in the problem statement. ``fee`` is derived when absent.
REQUIRED_FIELDS = (
    "timestamp", "src_ip", "dst_ip", "src_port", "dst_port", "txid",
    "input_addresses", "output_addresses", "input_amounts", "output_amounts", "fee",
)

ALIASES: dict[str, tuple[str, ...]] = {
    "timestamp": ("timestamp", "time", "ts", "datetime", "date_time", "date", "seen", "first_seen", "seen_time", "seen_at",
                  "observed_at", "observed_time", "capture_time", "captured_at", "event_time", "time_stamp", "received_time",
                  "received_at", "rx_time", "epoch", "unix_time", "unixtime", "time_utc", "timestamp_utc"),
    "src_ip": ("src_ip", "source_ip", "srcip", "sourceip", "ip_src", "src", "source", "src_addr", "source_address", "src_address",
               "sender_ip", "relay_ip", "peer_ip", "origin_ip", "from_ip", "saddr", "src_host", "source_host", "ip_source"),
    "dst_ip": ("dst_ip", "destination_ip", "dstip", "destip", "dest_ip", "ip_dst", "dst", "dest", "destination", "dst_addr",
               "destination_address", "dst_address", "receiver_ip", "to_ip", "daddr", "dst_host", "destination_host", "ip_destination"),
    "src_port": ("src_port", "source_port", "srcport", "sourceport", "sport", "port_src", "src_prt"),
    "dst_port": ("dst_port", "destination_port", "dstport", "destport", "dest_port", "destinationport", "dport", "port_dst", "dst_prt"),
    "txid": ("txid", "tx_id", "tx_hash", "txhash", "hash", "transaction_id", "transaction_hash", "transactionid",
             "transactionhash", "tx", "txn_id", "txn_hash"),
    "input_addresses": ("input_addresses", "input_address", "inputs", "input_addrs", "in_addresses", "in_addrs", "from_addresses",
                        "from_address", "sender_addresses", "sender_address", "input_wallets", "input_wallet", "source_wallets",
                        "sender_wallets", "from_wallets", "input_wallet_addresses", "vin", "senders", "inputs_addresses", "input"),
    "output_addresses": ("output_addresses", "output_address", "outputs", "output_addrs", "out_addresses", "out_addrs", "to_addresses",
                         "to_address", "receiver_addresses", "receiver_address", "recipient_addresses", "output_wallets",
                         "output_wallet", "dest_wallets", "destination_wallets", "to_wallets", "output_wallet_addresses", "vout",
                         "receivers", "recipients", "outputs_addresses", "output"),
    "input_amounts": ("input_amounts", "input_amount", "input_values", "input_value", "in_amounts", "in_values", "amounts_in",
                      "values_in", "input_btc", "inputs_value", "inputs_values", "inputs_amounts", "vin_values", "vin_value",
                      "input_amounts_btc", "input_values_btc"),
    "output_amounts": ("output_amounts", "output_amount", "output_values", "output_value", "out_amounts", "out_values", "amounts_out",
                       "values_out", "output_btc", "outputs_value", "outputs_values", "outputs_amounts", "vout_values", "vout_value",
                       "output_amounts_btc", "output_values_btc"),
    "fee": ("fee", "fees", "tx_fee", "txfee", "transaction_fee", "fee_btc", "miner_fee", "mining_fee"),
    "script_type": ("script_type", "scripttype", "script", "address_type", "addr_type", "output_script_type", "input_script_type",
                    "script_pubkey_type", "spk_type", "scriptpubkey_type"),
    "geo_country": ("geo_country", "country", "country_code", "geo", "src_country", "source_country", "geoip_country", "cc",
                    "geo_country_code", "country_iso", "iso_country", "geo_cc"),
    "asn": ("asn", "as_number", "asnum", "src_asn", "source_asn", "autonomous_system", "autonomous_system_number", "as_num", "geo_asn"),
    "behavior_type": ("behavior_type", "behaviour_type", "behavior", "behaviour", "label", "class", "category", "ground_truth",
                      "tx_label", "activity_type", "behavior_label", "behaviour_label"),
    "block_height": ("block_height", "height", "block", "blockheight", "block_number", "block_no"),
    "transaction_size": ("transaction_size", "tx_size", "size", "vsize", "size_bytes", "vbytes", "virtual_size", "tx_vsize"),
}
AMOUNT_FIELDS = ("input_amounts", "output_amounts", "fee")
SATOSHI_SUFFIXES = ("_satoshis", "_satoshi", "_sats", "_sat")
SATOSHI = 1e-8
MAX_BTC = 21_000_000.0

_ALIAS_INDEX: dict[str, str] = {alias: canonical for canonical, aliases in ALIASES.items() for alias in aliases}
_KEY_CLEAN = re.compile(r"[^a-z0-9]+")
_ADDRESS_KEYS = ("address", "addr", "wallet", "wallet_address", "scriptpubkey_address", "script_pub_key_address", "prev_address", "id")
_VALUE_KEYS = ("amount", "value", "btc", "amount_btc", "value_btc", "prev_value")
_VALUE_KEYS_SAT = ("value_sat", "amount_sat", "satoshis", "satoshi", "sats", "value_satoshi", "amount_satoshi")
_TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M",
    "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y%m%d%H%M%S", "%Y%m%dT%H%M%S", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y",
)
_EPOCH_MIN, _EPOCH_MAX = 1_230_000_000.0, 4_102_444_800.0  # 2008-12 (just before the genesis block) .. 2100-01


@lru_cache(maxsize=4096)
def _clean_text(name: str) -> str:
    return _KEY_CLEAN.sub("_", name.strip().lower()).strip("_")


def clean_key(name: Any) -> str:
    """``'Source IP'`` / ``'sourceIp'`` -> ``'source_ip'`` / ``'sourceip'`` (both are in the alias table)."""
    return _clean_text(name if isinstance(name, str) else str(name))


@dataclass
class ColumnMapping:
    """Which source column feeds each canonical field, and which amount columns are in satoshis."""

    source: dict[str, str] = field(default_factory=dict)          # canonical -> source column
    satoshi: set[str] = field(default_factory=set)                # canonical amount fields given in satoshis
    unmapped: list[str] = field(default_factory=list)             # source columns nothing was mapped to
    time_part: str | None = None                                  # a separate time-of-day column next to a date column

    def describe(self) -> dict[str, str]:
        described = {source: canonical for canonical, source in self.source.items()}
        if self.time_part:
            described[self.time_part] = "timestamp"
        return described


def map_columns(columns: Any) -> ColumnMapping:
    """Resolve a row's keys to canonical fields. Exact canonical names win over aliases."""
    mapping = ColumnMapping()
    alias_hits: dict[str, tuple[str, bool]] = {}
    for column in columns:
        if column is None:
            continue
        key = clean_key(column)
        satoshi = False
        canonical = _ALIAS_INDEX.get(key)
        if canonical is None:
            for suffix in SATOSHI_SUFFIXES:
                if key.endswith(suffix) and _ALIAS_INDEX.get(key[: -len(suffix)]) in AMOUNT_FIELDS:
                    canonical, satoshi = _ALIAS_INDEX[key[: -len(suffix)]], True
                    break
        if canonical is None:
            mapping.unmapped.append(str(column))
            continue
        exact = key == canonical
        if canonical in mapping.source and not (exact and not alias_hits[canonical][1]):
            mapping.unmapped.append(str(column))  # a second column for the same field: keep the first / the exact one
            continue
        if canonical in mapping.source:
            mapping.unmapped.append(mapping.source[canonical])
            mapping.satoshi.discard(canonical)
        mapping.source[canonical] = str(column)
        alias_hits[canonical] = (str(column), exact)
        if satoshi:
            mapping.satoshi.add(canonical)
    # "date" + "time" given as two columns: join them instead of dropping the time of day.
    keys = {clean_key(column): column for column in mapping.unmapped}
    stamp = clean_key(mapping.source.get("timestamp", ""))
    if stamp == "date" and "time" in keys:
        mapping.time_part = keys["time"]
    elif stamp == "time" and "date" in keys:
        mapping.source["timestamp"], mapping.time_part = keys["date"], mapping.source["timestamp"]
    if mapping.time_part:
        mapping.unmapped = [column for column in mapping.unmapped if column not in (mapping.time_part, mapping.source["timestamp"])]
    return mapping


# ── value coercion ──────────────────────────────────────────────────────────
def is_blank(value: Any) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, str):
        return not value.strip()
    return isinstance(value, (list, tuple, dict)) and not value


def parse_timestamp(value: Any) -> datetime | None:
    """Parse ISO-8601 text, common date formats or a Unix epoch into an aware UTC datetime."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return _from_epoch(float(value))
    text = str(value).strip()
    if not text:
        return None
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        when = None
    if when is None:
        try:
            return _from_epoch(float(text))
        except ValueError:
            pass
        candidate = text[:-1].strip() if text[-1] in "zZ" else text.removesuffix(" UTC").removesuffix(" GMT").strip()
        for pattern in _TIME_FORMATS:
            try:
                when = datetime.strptime(candidate, pattern)
                break
            except ValueError:
                continue
        if when is None:
            return None
    return when.astimezone(timezone.utc) if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _from_epoch(number: float) -> datetime | None:
    if number != number or number <= 0:  # NaN or non-positive
        return None
    for scale in (1.0, 1e3, 1e6, 1e9):  # seconds, milliseconds, microseconds, nanoseconds
        seconds = number / scale
        if _EPOCH_MIN <= seconds <= _EPOCH_MAX:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
    return None


def format_timestamp(when: datetime) -> str:
    """Canonical ISO-8601 UTC text; sub-second precision is kept only when present."""
    return when.isoformat(timespec="milliseconds" if when.microsecond else "seconds")


def parse_list(value: Any) -> list[Any]:
    """Decode a list field that may be a list, a JSON/Python array string or delimiter-separated text."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, dict):
        return [value]
    if not isinstance(value, str):
        return [value]
    text = value.strip()
    if not text:
        return []
    if text[0] in "[(" and text[-1] in "])":
        try:
            decoded = json.loads(text)
            return decoded if isinstance(decoded, list) else [decoded]
        except ValueError:
            pass
        try:
            decoded = ast.literal_eval(text)
            return list(decoded) if isinstance(decoded, (list, tuple)) else [decoded]
        except (ValueError, SyntaxError):
            text = text[1:-1].strip()
            if not text:
                return []
    for delimiter in (";", "|", ","):
        if delimiter in text:
            return [part for part in (piece.strip().strip("'\"") for piece in text.split(delimiter)) if part]
    parts = text.split()
    return [part.strip("'\"") for part in parts] if len(parts) > 1 else [text.strip("'\"")]


def parse_amount(value: Any) -> float:
    """A non-negative float; raises ValueError for text that is not a number."""
    if isinstance(value, bool):
        raise ValueError("boolean is not an amount")
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if text.lower().endswith("btc"):
            text = text[:-3].strip()
        # "0,0005" is a decimal comma; "1,234.5" uses the comma as a thousands separator.
        text = text.replace(",", "") if "." in text else text.replace(",", ".")
        number = float(text)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError("amount is not finite")
    return number


def parse_int(value: Any, default: int = 0) -> int:
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value == value else default
    text = str(value).strip()
    if not text:
        return default
    if text[:2].upper() == "AS" and text[2:].isdigit():  # "AS15169"
        return int(text[2:])
    return int(float(text))


def split_endpoint(value: Any) -> tuple[str, int | None]:
    """``'1.2.3.4:8333'`` / ``'[2001:db8::1]:8333'`` -> (ip, port); a bare IP -> (ip, None); invalid -> ('', None)."""
    return _split_endpoint("" if value is None else str(value))


@lru_cache(maxsize=262144)  # a capture mentions the same few thousand IPs over and over
def _split_endpoint(value: str) -> tuple[str, int | None]:
    text = value.strip()
    if not text:
        return "", None
    port: int | None = None
    if text.startswith("[") and "]" in text:
        host, _, rest = text[1:].partition("]")
        if rest.startswith(":") and rest[1:].isdigit():
            port = int(rest[1:])
        text = host
    elif text.count(":") == 1 and text.rsplit(":", 1)[1].isdigit():
        text, raw_port = text.rsplit(":", 1)
        port = int(raw_port)
    try:
        return str(ip_address(text)), port
    except ValueError:
        return "", None


def split_parties(value: Any) -> tuple[list[str], list[Any] | None]:
    """Addresses from a list field, plus embedded amounts when items are ``{address, amount}`` objects.

    Returns (addresses, amounts or None). Embedded amounts under a ``*_sat`` key are converted to BTC here,
    item by item; amounts under a plain ``value`` / ``amount`` key are returned as given.
    """
    items = parse_list(value)
    if not items or not isinstance(items[0], dict):
        return [str(item).strip() for item in items if not is_blank(item)], None
    addresses: list[str] = []
    amounts: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        keys = {clean_key(key): key for key in item}
        address = next((item[keys[name]] for name in _ADDRESS_KEYS if name in keys and not is_blank(item[keys[name]])), None)
        if address is None:
            nested = next((item[keys[name]] for name in ("prevout", "prev_out", "scriptpubkey", "script_pub_key") if name in keys and isinstance(item[keys[name]], dict)), None)
            if nested:
                nested_addresses, nested_amounts = split_parties([nested])
                if nested_addresses:
                    addresses.append(nested_addresses[0])
                    amounts.append(nested_amounts[0] if nested_amounts else None)
            continue
        addresses.append(str(address).strip())
        amount = next((item[keys[name]] for name in _VALUE_KEYS if name in keys and not is_blank(item[keys[name]])), None)
        if amount is None:
            satoshis = next((item[keys[name]] for name in _VALUE_KEYS_SAT if name in keys and not is_blank(item[keys[name]])), None)
            amount = None if satoshis is None else parse_amount(satoshis) * SATOSHI
        amounts.append(amount)
    if all(amount is None for amount in amounts):
        return addresses, None
    return addresses, [0 if amount is None else amount for amount in amounts]


def address_type(address: str) -> str:
    """Script type implied by a Bitcoin address' prefix ('' when it is not a recognisable address)."""
    if not address:
        return ""
    lowered = address.lower()
    if lowered.startswith(("bc1p", "tb1p", "bcrt1p")):
        return "P2TR"
    if lowered.startswith(("bc1q", "tb1q", "bcrt1q")):
        return "P2WSH" if len(address) > 50 else "P2WPKH"
    if not 26 <= len(address) <= 35:
        return ""
    if address[0] == "1" or address[0] in "mn":
        return "P2PKH"
    if address[0] in "32":
        return "P2SH"
    return ""


__all__ = [
    "CANONICAL_FIELDS", "OPTIONAL_EXTRA_FIELDS", "REQUIRED_FIELDS", "ALIASES", "AMOUNT_FIELDS", "SATOSHI", "MAX_BTC", "ColumnMapping",
    "map_columns", "clean_key", "is_blank", "parse_timestamp", "format_timestamp", "parse_list", "parse_amount", "parse_int",
    "split_endpoint", "split_parties", "address_type",
]
