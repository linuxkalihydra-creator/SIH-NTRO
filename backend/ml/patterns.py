"""Laundering-pattern detectors over the transaction flow graph.

Each detector returns ``Pattern`` objects that say which transactions and wallets
form the structure, what role each wallet plays, how strong the evidence is (0-1)
and why, in words an investigator can check against the raw transactions.

* CoinJoin: many inputs and outputs with several outputs of exactly the same value.
* Peeling chain: a large balance repeatedly split into a small "peel" and a large
  remainder that moves to a fresh address and is peeled again.
* Layering: funds fanned out to fresh intermediates that forward them within days
  and reconverge on one address.
* Rapid pass-through: a wallet that moves on nearly everything it receives within
  minutes, the signature of a money mule or hop wallet.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from backend.correlation.flows import FlowIndex, Tx

HOUR = 3600.0


@dataclass
class Pattern:
    pattern_id: str
    type: str
    strength: float
    txids: list[str]
    roles: dict[str, str]
    summary: str
    start_ts: float
    end_ts: float
    total_value: float
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def wallets(self) -> list[str]:
        return list(self.roles)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "type": self.type,
            "strength": round(self.strength, 4),
            "txids": self.txids,
            "roles": self.roles,
            "summary": self.summary,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "total_value": round(self.total_value, 8),
            "details": self.details,
        }


def _short(value: str) -> str:
    return value if len(value) <= 14 else f"{value[:8]}…{value[-4:]}"


# ── CoinJoin ────────────────────────────────────────────────────────────────
def is_coinjoin(tx: Tx, min_inputs: int = 5, min_equal: int = 3) -> tuple[bool, float, int]:
    """Return (is CoinJoin, equal output value, number of equal outputs)."""
    if len(tx.inputs) < min_inputs or len(tx.outputs) < min_inputs:
        return False, 0.0, 0
    counts = Counter(round(amount, 8) for _, amount in tx.outputs)
    value, equal = counts.most_common(1)[0]
    distinct_inputs = len(set(tx.input_addresses))
    ok = equal >= min_equal and equal >= 0.3 * len(tx.outputs) and distinct_inputs >= equal and value > 0
    return ok, value, equal


def detect_coinjoins(flows: FlowIndex) -> list[Pattern]:
    patterns = []
    for tx in flows.txs:
        ok, value, equal = is_coinjoin(tx)
        if not ok:
            continue
        equal_share = equal * value / tx.total_out if tx.total_out else 0.0
        strength = min(1.0, equal / 10) ** 0.5 * min(1.0, equal_share + 0.3)
        roles = {address: "coinjoin_input" for address in tx.input_addresses}
        for address, amount in tx.outputs:
            roles.setdefault(address, "coinjoin_output" if round(amount, 8) == value else "coinjoin_change")
        patterns.append(Pattern(
            pattern_id=f"coinjoin-{len(patterns) + 1}", type="coinjoin", strength=strength, txids=[tx.txid], roles=roles,
            summary=f"CoinJoin: {len(tx.inputs)} inputs, {equal} equal outputs of {value:g} BTC",
            start_ts=tx.ts, end_ts=tx.ts, total_value=tx.total_out,
            details={"inputs": len(tx.inputs), "outputs": len(tx.outputs), "equal_outputs": equal, "denomination": value, "equal_value_share": round(equal_share, 4)},
        ))
    return patterns


# ── Peeling chains ──────────────────────────────────────────────────────────
def _peel_split(flows: FlowIndex, tx: Tx, coinjoins: set[str]) -> tuple[str, float, str, float] | None:
    """(remainder address, remainder, peel address, peel) when ``tx`` looks like one peel step."""
    if tx.txid in coinjoins or not 1 <= len(tx.inputs) <= 2 or len(tx.outputs) != 2:
        return None
    (a_addr, a_amt), (b_addr, b_amt) = sorted(tx.outputs, key=lambda item: -item[1])
    total = a_amt + b_amt
    if total <= 0 or b_amt / total > 0.3 or not flows.is_fresh_output(a_addr, tx):
        return None
    return a_addr, a_amt, b_addr, b_amt


def detect_peeling_chains(flows: FlowIndex, coinjoins: set[str], min_hops: int = 5, max_gap_hours: float = 72.0) -> list[Pattern]:
    steps: dict[int, tuple[str, float, str, float]] = {}
    for tx in flows.txs:
        split = _peel_split(flows, tx, coinjoins)
        if split:
            steps[tx.index] = split
    successor: dict[int, int] = {}
    has_predecessor: set[int] = set()
    for index, (remainder, _, _, _) in steps.items():
        tx = flows.txs[index]
        nxt = flows.next_spend(remainder, tx.ts)
        if nxt is not None and nxt.index in steps and nxt.ts - tx.ts <= max_gap_hours * HOUR and remainder in nxt.input_addresses:
            successor[index] = nxt.index
            has_predecessor.add(nxt.index)
    patterns = []
    for start in sorted(steps):
        if start in has_predecessor:
            continue
        chain = [start]
        while chain[-1] in successor and len(chain) < 500:
            chain.append(successor[chain[-1]])
        if len(chain) < min_hops:
            continue
        txs = [flows.txs[index] for index in chain]
        roles: dict[str, str] = {}
        peeled = 0.0
        recipients = []
        for tx in txs:
            for address in tx.input_addresses:
                roles[address] = "chain_hop"
            remainder, _, peel_address, peel = steps[tx.index]
            roles.setdefault(remainder, "chain_hop")
            roles.setdefault(peel_address, "peel_recipient")
            recipients.append(peel_address)
            peeled += peel
        gaps = [(b.ts - a.ts) / 60 for a, b in zip(txs, txs[1:])]
        median_gap = median(gaps) if gaps else 0.0
        length_factor = 1 - math.exp(-(len(chain) - 2) / 4)
        speed_factor = 1.0 if median_gap <= 6 * 60 else 0.7
        start_value = txs[0].total_in
        patterns.append(Pattern(
            pattern_id=f"peeling-{len(patterns) + 1}", type="peeling_chain", strength=length_factor * speed_factor,
            txids=[tx.txid for tx in txs], roles=roles,
            summary=f"Peeling chain: {len(chain)} hops peel {peeled:.4f} of {start_value:.4f} BTC to {len(set(recipients))} recipients",
            start_ts=txs[0].ts, end_ts=txs[-1].ts, total_value=start_value,
            details={"hops": len(chain), "start_value": round(start_value, 8), "end_value": round(steps[chain[-1]][1], 8),
                     "peeled_value": round(peeled, 8), "recipients": len(set(recipients)), "median_gap_minutes": round(median_gap, 1)},
        ))
    return patterns


# ── Layering (fan-out / fan-in) ─────────────────────────────────────────────
def detect_layering(flows: FlowIndex, coinjoins: set[str], min_branches: int = 3, max_hops: int = 3, window_hours: float = 96.0) -> list[Pattern]:
    patterns = []
    used: set[str] = set()
    for tx in flows.txs:
        if tx.txid in coinjoins or tx.txid in used or len(tx.outputs) < min_branches:
            continue
        fresh = [(address, amount) for address, amount in tx.outputs if flows.is_fresh_output(address, tx)]
        if len(fresh) < min_branches:
            continue
        branches: list[tuple[list[Tx], list[str]]] = []
        for address, amount in fresh:
            current, value, path, reached = address, amount, [], []
            for _ in range(max_hops):
                nxt = flows.next_spend(current, tx.ts)
                if nxt is None or nxt.ts - tx.ts > window_hours * HOUR or nxt.txid in coinjoins or len(nxt.outputs) > 2:
                    break
                target, forwarded = max(nxt.outputs, key=lambda item: item[1])
                if forwarded < 0.8 * value:  # an intermediate passes the funds on, it does not spend them
                    break
                path.append(nxt)
                reached.append(target)
                current, value = target, forwarded
            if path:
                branches.append((path, reached))
        hits = Counter(address for _, reached in branches for address in set(reached))
        if not hits:
            continue
        sink, converged = hits.most_common(1)[0]
        if converged < min_branches:
            continue
        member_txs = [tx]
        roles = {address: "layering_source" for address in tx.input_addresses}
        for path, reached in branches:
            if sink not in reached:
                continue
            for hop, target in zip(path, reached):
                member_txs.append(hop)
                for address in hop.input_addresses:
                    roles.setdefault(address, "layering_intermediate")
                if target == sink:
                    break
        roles[sink] = "layering_sink"
        txids = list(dict.fromkeys(item.txid for item in sorted(member_txs, key=lambda item: item.ts)))
        used.update(txids)
        conservation = min(1.0, sum(amount for item in member_txs for address, amount in item.outputs if address == sink) / max(tx.total_out, 1e-12))
        strength = min(1.0, converged / 5) ** 0.5 * (0.5 + 0.5 * conservation)
        patterns.append(Pattern(
            pattern_id=f"layering-{len(patterns) + 1}", type="layering", strength=strength, txids=txids, roles=roles,
            summary=f"Layering: split across {len(fresh)} fresh addresses, {converged} branches reconverge on {_short(sink)}",
            start_ts=tx.ts, end_ts=max(item.ts for item in member_txs), total_value=tx.total_out,
            details={"branches": len(fresh), "converged_branches": converged, "sink": sink, "value_reaching_sink": round(conservation, 4),
                     "duration_hours": round((max(item.ts for item in member_txs) - tx.ts) / HOUR, 1)},
        ))
    return patterns


# ── Rapid pass-through ──────────────────────────────────────────────────────
def detect_rapid_passthrough(flows: FlowIndex, coinjoins: set[str], max_minutes: float = 30.0, min_forward: float = 0.9) -> list[Pattern]:
    events: dict[str, list[tuple[Tx, Tx, float, float]]] = defaultdict(list)
    for address, receipts in flows.received.items():
        for index in receipts:
            received_tx = flows.txs[index]
            if received_tx.txid in coinjoins:
                continue
            amount = sum(value for output, value in received_tx.outputs if output == address)
            spend = flows.next_spend(address, received_tx.ts)
            # A forward is a small spend (a sweep of many deposit addresses is a service consolidating).
            if spend is None or spend.txid in coinjoins or amount <= 0 or len(spend.inputs) > 2:
                continue
            minutes = (spend.ts - received_tx.ts) / 60
            if minutes > max_minutes:
                continue
            onward = [(output, value) for output, value in spend.outputs if output not in spend.input_addresses]
            if not onward:
                continue
            target, forwarded = max(onward, key=lambda item: item[1])
            # Nearly all of the value moves on in one output and anything else is dust-sized:
            # an ordinary payment leaves a sizeable second output (the payment or the change).
            if forwarded >= min_forward * amount and spend.total_out - forwarded < 0.03 * amount:
                events[address].append((received_tx, spend, minutes, forwarded / amount))
    patterns = []
    for address, items in sorted(events.items(), key=lambda item: item[1][0][0].ts):
        minutes = median(item[2] for item in items)
        ratio = median(item[3] for item in items)
        receipts = len(flows.received.get(address, []))
        strength = math.exp(-minutes / 45) * min(1.0, ratio) * (0.6 + 0.4 * min(1.0, len(items) / max(receipts, 1)))
        txids = list(dict.fromkeys(txid for received_tx, spend, _, _ in items for txid in (received_tx.txid, spend.txid)))
        roles = {address: "pass_through"}
        for _, spend, _, _ in items:
            target, _ = max(((output, value) for output, value in spend.outputs if output not in spend.input_addresses), key=lambda item: item[1])
            roles.setdefault(target, "pass_through_recipient")
        patterns.append(Pattern(
            pattern_id=f"passthrough-{len(patterns) + 1}", type="rapid_passthrough", strength=strength, txids=txids, roles=roles,
            summary=f"Rapid pass-through: forwards {ratio:.0%} of received value after a median {minutes:.0f} min ({len(items)} of {receipts} receipts)",
            start_ts=items[0][0].ts, end_ts=items[-1][1].ts, total_value=sum(item[1].total_out for item in items),
            details={"events": len(items), "receipts": receipts, "median_minutes": round(minutes, 1), "median_forward_ratio": round(ratio, 4),
                     "forward_txids": [spend.txid for _, spend, _, _ in items]},
        ))
    return patterns


# Weight of each role in the wallet's pattern signal: hop wallets move the money,
# peel recipients and CoinJoin participants may be unwitting or privacy-minded.
ROLE_WEIGHTS = {
    "chain_hop": 1.0,
    "layering_source": 0.9,
    "layering_intermediate": 1.0,
    "layering_sink": 1.0,
    "pass_through": 0.9,
    "peel_recipient": 0.35,
    "pass_through_recipient": 0.25,
    "coinjoin_input": 0.35,
    "coinjoin_output": 0.25,
    "coinjoin_change": 0.2,
}
# Weight of each pattern type in a transaction's pattern signal.
TYPE_WEIGHTS = {"peeling_chain": 1.0, "layering": 1.0, "rapid_passthrough": 0.9, "coinjoin": 0.45}


def detect_all(flows: FlowIndex) -> list[Pattern]:
    coinjoins = detect_coinjoins(flows)
    coinjoin_txids = {pattern.txids[0] for pattern in coinjoins}
    return (detect_peeling_chains(flows, coinjoin_txids) + detect_layering(flows, coinjoin_txids)
            + detect_rapid_passthrough(flows, coinjoin_txids) + coinjoins)


__all__ = ["Pattern", "is_coinjoin", "detect_coinjoins", "detect_peeling_chains", "detect_layering", "detect_rapid_passthrough", "detect_all", "ROLE_WEIGHTS", "TYPE_WEIGHTS"]
