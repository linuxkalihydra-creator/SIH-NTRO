"""Load a bulk transaction/network metadata file into canonical transaction records.

One input row is one network observation of a transaction. Rows are streamed,
mapped onto the canonical schema and grouped by transaction id:

* the first usable row of a ``txid`` defines the transaction (addresses, amounts, fee);
* further rows with the same ``txid`` but another timestamp or endpoint are extra
  *observations* of the same transaction propagating through the network. They are
  kept, ordered by time, under ``record["observations"]``, and the earliest one
  becomes the record's own ``timestamp`` / ``src_ip`` / ``dst_ip`` / ports (the
  first-seen relay, used later to infer where the transaction entered the network);
* rows that repeat an observation exactly are duplicates and are dropped;
* unusable rows are rejected and counted by reason, with example row numbers.

The returned summary is the data-quality report shown in the dashboard.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from backend.ingestion.normalizer import normalize_row
from backend.ingestion.readers import dataset_suffix, detect_format, read_rows
from backend.ingestion.schema import SATOSHI, ColumnMapping, map_columns

OBSERVATION_KEYS = ("timestamp", "src_ip", "src_port", "dst_ip", "dst_port")
# Amounts that are all whole numbers and reach this size cannot be BTC: they are satoshis.
SATOSHI_DETECTION_MINIMUM = 10_000.0
MAX_EXAMPLES = 25

REASON_TEXT = {
    "missing_txid": "No transaction id",
    "missing_timestamp": "No timestamp",
    "invalid_timestamp": "Timestamp could not be parsed",
    "no_addresses": "No input or output addresses",
    "invalid_amounts": "Amounts are not numeric",
    "negative_amount": "Negative amount or fee",
    "not_a_record": "Row is not a record",
    "invalid_src_ip": "Source IP is not a valid address (kept without it)",
    "invalid_dst_ip": "Destination IP is not a valid address (kept without it)",
    "invalid_src_port": "Source port out of range (set to 0)",
    "invalid_dst_port": "Destination port out of range (set to 0)",
    "outputs_exceed_inputs": "Outputs exceed inputs (kept; fee set to 0)",
    "input_amounts_not_aligned": "Input amounts not aligned with input addresses (total split evenly)",
    "output_amounts_not_aligned": "Output amounts not aligned with output addresses (total split evenly)",
    "missing_input_amounts": "Input amounts missing (reconstructed from outputs + fee)",
    "missing_output_amounts": "Output amounts missing (reconstructed from inputs - fee)",
    "no_input_addresses": "No input addresses (coinbase or unknown source)",
    "no_output_addresses": "No output addresses",
    "invalid_asn": "ASN is not a number (ignored)",
    "invalid_block_height": "Block height is not a number (ignored)",
    "invalid_transaction_size": "Transaction size is not a number (ignored)",
    "conflicting_transaction": "Same txid with different addresses or amounts (first version kept)",
}


class DatasetBuilder:
    """Accumulates normalised rows into transactions with their network observations."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.first_seen: dict[str, float] = {}
        # txid -> [(epoch seconds, observation, geo_country, asn)], only for transactions seen more than once
        self.extra: dict[str, list[tuple[float, tuple[Any, ...], str, int]]] = {}
        self.mappings: dict[tuple[str, ...], ColumnMapping] = {}
        self.columns: dict[str, str] = {}
        self.unmapped: dict[str, None] = {}
        self.satoshi_declared = False
        self.rows = self.invalid = self.duplicates = self.conflicts = 0
        self.rejected: Counter[str] = Counter()
        self.warnings: Counter[str] = Counter()
        self.missing: Counter[str] = Counter()
        self.examples: list[dict[str, Any]] = []
        self.integral = True
        self.max_amount = 0.0

    def _mapping(self, raw: dict[str, Any]) -> ColumnMapping:
        key = tuple(raw)
        mapping = self.mappings.get(key)
        if mapping is None:
            mapping = self.mappings[key] = map_columns(raw.keys())
            self.columns.update(mapping.describe())
            self.unmapped.update(dict.fromkeys(mapping.unmapped))
            self.satoshi_declared = self.satoshi_declared or bool(mapping.satoshi)
        return mapping

    def _example(self, row: int, reasons: list[str], txid: str = "") -> None:
        if len(self.examples) < MAX_EXAMPLES:
            self.examples.append({"row": row, "txid": txid, "problems": [REASON_TEXT.get(reason, reason) for reason in reasons]})

    def add(self, raw: Any, row: int) -> None:
        self.rows += 1
        mapping = self._mapping(raw) if isinstance(raw, dict) else None
        result = normalize_row(raw, mapping)
        self.missing.update(result.missing)
        if result.record is None:
            self.invalid += 1
            self.rejected.update(result.errors)
            txid_column = mapping.source.get("txid") if mapping else None
            self._example(row, result.errors, str(raw.get(txid_column) or "") if txid_column else "")
            return
        record = result.record
        txid = record["txid"]
        stamp = result.when.timestamp()
        observation = tuple(record[key] for key in OBSERVATION_KEYS)
        base = self.records.get(txid)
        if base is None:
            self.warnings.update(result.warnings)
            self.integral = self.integral and result.integral_amounts
            self.max_amount = max(self.max_amount, result.max_amount)
            self.records[txid] = record
            self.first_seen[txid] = stamp
            return
        seen = self.extra.get(txid)
        if seen is None:
            seen = self.extra[txid] = [(self.first_seen[txid], tuple(base[key] for key in OBSERVATION_KEYS), base["geo_country"], base["asn"])]
        if any(observation == known[1] for known in seen):
            self.duplicates += 1
            return
        if (record["input_addresses"], record["output_addresses"]) != (base["input_addresses"], base["output_addresses"]) or \
                record["output_amounts"] != base["output_amounts"]:
            self.conflicts += 1
            self.warnings["conflicting_transaction"] += 1
            self._example(row, ["conflicting_transaction"], txid)
        seen.append((stamp, observation, record["geo_country"], record["asn"]))

    def finish(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        observation_count = len(self.records)
        multi_observed = 0
        for txid, seen in self.extra.items():
            if len(seen) < 2:  # only exact duplicates followed the first row
                continue
            seen.sort(key=lambda item: item[0])
            record = self.records[txid]
            _, first, country, asn = seen[0]
            record.update(zip(OBSERVATION_KEYS, first))  # the earliest observation is the first-seen relay
            record["geo_country"], record["asn"] = country, asn
            record["observations"] = [
                {**dict(zip(OBSERVATION_KEYS, observation)), **({"geo_country": geo} if geo else {}), **({"asn": number} if number else {})}
                for _, observation, geo, number in seen
            ]
            observation_count += len(seen) - 1
            multi_observed += 1
        records = list(self.records.values())

        unit = "satoshi (declared by column name)" if self.satoshi_declared else "BTC"
        if not self.satoshi_declared and records and self.integral and self.max_amount >= SATOSHI_DETECTION_MINIMUM:
            unit = "satoshi (detected: whole-number amounts too large to be BTC)"
            for record in records:
                record["input_amounts"] = [round(amount * SATOSHI, 8) for amount in record["input_amounts"]]
                record["output_amounts"] = [round(amount * SATOSHI, 8) for amount in record["output_amounts"]]
                record["fee"] = round(record["fee"] * SATOSHI, 8)

        stamps = [record["timestamp"] for record in records]
        summary = {
            "total_records": self.rows,
            "valid_records": len(records),
            "invalid_records": self.invalid,
            "duplicates": self.duplicates,
            "missing_fields": dict(sorted(self.missing.items())),
            "rows": self.rows,
            "transactions": len(records),
            "observations": observation_count,
            "multi_observation_transactions": multi_observed,
            "conflicting_rows": self.conflicts,
            "rejected": {REASON_TEXT.get(reason, reason): count for reason, count in self.rejected.most_common()},
            "warnings": {REASON_TEXT.get(reason, reason): count for reason, count in self.warnings.most_common()},
            "examples": self.examples,
            "column_mapping": self.columns,
            "unmapped_columns": list(self.unmapped),
            "amount_unit": unit,
            "time_range": [min(stamps), max(stamps)] if stamps else None,
        }
        return records, summary


def build_dataset(rows: Iterable[Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Normalise raw rows (dicts in any supported naming) into transaction records plus a quality summary."""
    builder = DatasetBuilder()
    for number, raw in enumerate(rows, start=1):
        builder.add(raw, number)
    return builder.finish()


def load_dataset(path: str | Path, include_summary: bool = False) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load, normalise, validate and summarise a dataset file.

    Returns one canonical record per transaction, in order of first appearance.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Dataset not found: {file_path}")
    format_name = detect_format(file_path)
    records, summary = build_dataset(read_rows(file_path))
    summary["format"] = format_name + (" (gzip)" if dataset_suffix(file_path)[1] else "")
    if include_summary:
        return records, summary
    return records


__all__ = ["load_dataset", "build_dataset", "detect_format", "DatasetBuilder", "OBSERVATION_KEYS"]
