"""Time-ordered transaction flow index shared by the detectors.

Records carry address lists, not UTXO outpoints, so funds are followed by address:
an address that receives in transaction A and later appears as an input of B is
treated as B spending what A paid it. Amount lists are aligned with address lists
(``input_amounts[i]`` belongs to ``input_addresses[i]``); when a dataset does not
align them, the total is split evenly so every address still has an amount.

A transaction may have been observed several times while it propagated through
the peer-to-peer network. All observations are kept in time order; the earliest
one (the *first-seen relay*) supplies the transaction's own timestamp and
endpoints.
"""

from __future__ import annotations

import os
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.ingestion.schema import address_type, parse_timestamp


def _standard_ports() -> frozenset[int]:
    configured = os.getenv("BITCOIN_STANDARD_PORTS", "8333")
    ports = {int(part) for part in configured.replace(";", ",").split(",") if part.strip().isdigit()}
    return frozenset(ports or {8333})


# Ports a Bitcoin node normally listens on (mainnet 8333). Override with BITCOIN_STANDARD_PORTS="8333,18333".
STANDARD_PORTS = _standard_ports()


def service_port(src_port: int, dst_port: int) -> int:
    """The listening port of a connection: whichever side uses a standard port, else the destination port."""
    if dst_port in STANDARD_PORTS or src_port not in STANDARD_PORTS:
        return dst_port
    return src_port


def is_nonstandard_port(src_port: int, dst_port: int) -> bool:
    """True when the connection uses a known port and neither side is a standard Bitcoin port."""
    port = service_port(src_port, dst_port)
    return port > 0 and port not in STANDARD_PORTS


def _aligned(addresses: list[Any], amounts: list[Any]) -> list[tuple[str, float]]:
    addresses = [str(address) for address in addresses]
    values = [float(amount) for amount in amounts]
    if len(values) == len(addresses):
        return list(zip(addresses, values))
    total = sum(values)
    share = total / len(addresses) if addresses else 0.0
    return [(address, share) for address in addresses]


@dataclass(slots=True)
class Observation:
    """One sighting of a transaction on the network: ``src`` relayed it to ``dst`` at ``ts``."""

    ts: float
    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int

    @property
    def nonstandard_port(self) -> bool:
        return is_nonstandard_port(self.src_port, self.dst_port)


@dataclass(slots=True)
class Tx:
    """One transaction with aligned per-address amounts and its network observations."""

    index: int
    txid: str
    time: datetime
    ts: float
    inputs: list[tuple[str, float]]
    outputs: list[tuple[str, float]]
    fee: float
    size: int
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    script_type: str
    country: str
    asn: int
    label: str
    input_addresses: list[str] = field(default_factory=list)
    output_addresses: list[str] = field(default_factory=list)
    total_in: float = 0.0
    total_out: float = 0.0
    observations: list[Observation] = field(default_factory=list)

    @property
    def nonstandard_port(self) -> bool:
        return is_nonstandard_port(self.src_port, self.dst_port)

    @property
    def relay_count(self) -> int:
        """How many times the transaction was observed on the network."""
        return len(self.observations)

    @property
    def propagation_seconds(self) -> float:
        return self.observations[-1].ts - self.observations[0].ts if len(self.observations) > 1 else 0.0


@dataclass
class FlowIndex:
    """Transactions in time order plus per-address receive/spend lookups."""

    txs: list[Tx]
    by_txid: dict[str, int] = field(default_factory=dict)
    received: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    spent: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    first_seen: dict[str, int] = field(default_factory=dict)

    _spent_times: dict[str, list[float]] = field(default_factory=dict)
    _types: dict[str, str] = field(default_factory=dict)

    def next_spend(self, address: str, after_ts: float) -> Tx | None:
        """The first transaction at or after ``after_ts`` that spends from ``address``."""
        indices = self.spent.get(address)
        if not indices:
            return None
        times = self._spent_times.get(address)
        if times is None:
            times = self._spent_times[address] = [self.txs[index].ts for index in indices]
        position = bisect_right(times, after_ts - 1e-9)
        return self.txs[indices[position]] if position < len(indices) else None

    def is_fresh_output(self, address: str, tx: Tx) -> bool:
        """True when ``tx`` is the first time ``address`` appears at all."""
        return self.first_seen.get(address) == tx.index

    def address_type(self, address: str) -> str:
        """Script type implied by the address prefix ('' for identifiers that are not Bitcoin addresses)."""
        kind = self._types.get(address)
        if kind is None:
            kind = self._types[address] = address_type(address)
        return kind

    @property
    def addresses(self) -> list[str]:
        return sorted(self.first_seen)

    @property
    def start_ts(self) -> float:
        return self.txs[0].ts if self.txs else 0.0

    @property
    def end_ts(self) -> float:
        return self.txs[-1].ts if self.txs else 0.0


def _observations(record: dict[str, Any], first_ts: float) -> list[Observation]:
    listed = record.get("observations")
    if not listed:
        return [Observation(first_ts, str(record.get("src_ip", "") or ""), int(record.get("src_port", 0) or 0),
                            str(record.get("dst_ip", "") or ""), int(record.get("dst_port", 0) or 0))]
    observations = []
    for item in listed:
        when = parse_timestamp(item.get("timestamp"))
        observations.append(Observation(when.timestamp() if when else first_ts, str(item.get("src_ip", "") or ""), int(item.get("src_port", 0) or 0),
                                        str(item.get("dst_ip", "") or ""), int(item.get("dst_port", 0) or 0)))
    observations.sort(key=lambda observation: observation.ts)
    return observations


def build_flow_index(records: list[dict[str, Any]]) -> FlowIndex:
    """Sort records chronologically and index every address's receives and spends."""
    parsed = []
    for record in records:
        when = parse_timestamp(record.get("timestamp"))
        if when is None:
            raise ValueError(f"Record {record.get('txid')!r} has no usable timestamp: {record.get('timestamp')!r}")
        parsed.append((when, str(record.get("txid", "")), record))
    parsed.sort(key=lambda item: (item[0], item[1]))
    txs: list[Tx] = []
    index = FlowIndex(txs=txs)
    for position, (when, txid, record) in enumerate(parsed):
        inputs = _aligned(record.get("input_addresses", []), record.get("input_amounts", []))
        outputs = _aligned(record.get("output_addresses", []), record.get("output_amounts", []))
        observations = _observations(record, when.timestamp())
        first = observations[0]
        tx = Tx(
            index=position,
            txid=txid,
            time=when,
            ts=when.timestamp(),
            inputs=inputs,
            outputs=outputs,
            fee=float(record.get("fee", 0.0) or 0.0),
            size=int(record.get("transaction_size") or (10 + 68 * len(inputs) + 31 * len(outputs))),
            src_ip=first.src_ip,
            dst_ip=first.dst_ip,
            src_port=first.src_port,
            dst_port=first.dst_port,
            script_type=str(record.get("script_type", "") or ""),
            country=str(record.get("geo_country", "") or ""),
            asn=int(record.get("asn", 0) or 0),
            label=str(record.get("behavior_type", "") or ""),
            input_addresses=[address for address, _ in inputs],
            output_addresses=[address for address, _ in outputs],
            total_in=sum(amount for _, amount in inputs),
            total_out=sum(amount for _, amount in outputs),
            observations=observations,
        )
        txs.append(tx)
        index.by_txid[txid] = position
        for address in dict.fromkeys(tx.input_addresses):
            index.spent[address].append(position)
            index.first_seen.setdefault(address, position)
        for address in dict.fromkeys(tx.output_addresses):
            index.received[address].append(position)
            index.first_seen.setdefault(address, position)
    return index


__all__ = ["Observation", "Tx", "FlowIndex", "build_flow_index", "STANDARD_PORTS", "service_port", "is_nonstandard_port"]
