"""Time-ordered transaction flow index shared by the detectors.

Records carry address lists, not UTXO outpoints, so funds are followed by address:
an address that receives in transaction A and later appears as an input of B is
treated as B spending what A paid it. Amount lists are aligned with address lists
(``input_amounts[i]`` belongs to ``input_addresses[i]``); when a dataset does not
align them, the total is split evenly so every address still has an amount.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

STANDARD_PORTS = frozenset({8333})


def _aligned(addresses: list[Any], amounts: list[Any]) -> list[tuple[str, float]]:
    addresses = [str(address) for address in addresses]
    values = [float(amount) for amount in amounts]
    if len(values) == len(addresses):
        return list(zip(addresses, values))
    total = sum(values)
    share = total / len(addresses) if addresses else 0.0
    return [(address, share) for address in addresses]


@dataclass
class Tx:
    """One transaction with aligned per-address amounts and its network observation."""

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

    @property
    def total_in(self) -> float:
        return sum(amount for _, amount in self.inputs)

    @property
    def total_out(self) -> float:
        return sum(amount for _, amount in self.outputs)

    @property
    def input_addresses(self) -> list[str]:
        return [address for address, _ in self.inputs]

    @property
    def output_addresses(self) -> list[str]:
        return [address for address, _ in self.outputs]

    @property
    def nonstandard_port(self) -> bool:
        return self.dst_port not in STANDARD_PORTS


@dataclass
class FlowIndex:
    """Transactions in time order plus per-address receive/spend lookups."""

    txs: list[Tx]
    by_txid: dict[str, int] = field(default_factory=dict)
    received: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    spent: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    first_seen: dict[str, int] = field(default_factory=dict)

    _spent_times: dict[str, list[float]] = field(default_factory=dict)

    def next_spend(self, address: str, after_ts: float) -> Tx | None:
        """The first transaction at or after ``after_ts`` that spends from ``address``."""
        indices = self.spent.get(address, [])
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

    @property
    def addresses(self) -> list[str]:
        return sorted(self.first_seen)


def build_flow_index(records: list[dict[str, Any]]) -> FlowIndex:
    """Sort records chronologically and index every address's receives and spends."""
    parsed = []
    for record in records:
        when = datetime.fromisoformat(str(record.get("timestamp")))
        parsed.append((when, str(record.get("txid", "")), record))
    parsed.sort(key=lambda item: (item[0], item[1]))
    txs: list[Tx] = []
    index = FlowIndex(txs=txs)
    for position, (when, txid, record) in enumerate(parsed):
        tx = Tx(
            index=position,
            txid=txid,
            time=when,
            ts=when.timestamp(),
            inputs=_aligned(record.get("input_addresses", []), record.get("input_amounts", [])),
            outputs=_aligned(record.get("output_addresses", []), record.get("output_amounts", [])),
            fee=float(record.get("fee", 0.0) or 0.0),
            size=int(record.get("transaction_size") or (10 + 68 * len(record.get("input_addresses", [])) + 31 * len(record.get("output_addresses", [])))),
            src_ip=str(record.get("src_ip", "")),
            dst_ip=str(record.get("dst_ip", "")),
            src_port=int(record.get("src_port", 0) or 0),
            dst_port=int(record.get("dst_port", 0) or 0),
            script_type=str(record.get("script_type", "") or ""),
            country=str(record.get("geo_country", "") or ""),
            asn=int(record.get("asn", 0) or 0),
            label=str(record.get("behavior_type", "") or ""),
        )
        txs.append(tx)
        index.by_txid[txid] = position
        for address, _ in tx.inputs:
            index.spent[address].append(position)
            index.first_seen.setdefault(address, position)
        for address, _ in tx.outputs:
            index.received[address].append(position)
            index.first_seen.setdefault(address, position)
    return index


__all__ = ["Tx", "FlowIndex", "build_flow_index", "STANDARD_PORTS"]
