"""Validation helpers for canonical transaction records.

``validate_record`` lists the problems that make a record unusable for analysis.
Problems that ingestion repairs (an unresolvable IP, an out-of-range port, a
label outside the known vocabulary) are reported by ``record_warnings`` instead:
the transaction is kept because dropping it would break the money flow it is
part of.
"""

from __future__ import annotations

from collections import Counter
from ipaddress import ip_address
from typing import Any

from backend.ingestion.schema import REQUIRED_FIELDS, parse_timestamp

# Optional fields: geo_country/asn come from offline enrichment, script_type is
# informational, and behavior_type is a synthetic evaluation label only.
OPTIONAL_FIELDS = ("script_type", "geo_country", "asn", "behavior_type")

# Evaluation labels written by the bundled synthetic generators.
SUPPORTED_BEHAVIORS = {
    "NORMAL",
    "EXCHANGE_LIKE",
    "RAPID_TRANSFER",
    "LAYERING_LIKE",
    "MIXING_LIKE",
    "HIGH_NETWORK_DIVERSITY",
    "PEELING_CHAIN",
    "RANSOMWARE",
}
VALUE_TOLERANCE = 1e-8


def _blank(value: Any) -> bool:
    return value is None or value == "" or value == []


def validate_record(record: dict[str, Any]) -> list[str]:
    """Return the problems that make one canonical record unusable (empty list = usable)."""
    errors: list[str] = []
    if _blank(record.get("txid")) or not str(record.get("txid")).strip():
        errors.append("Missing required field: txid")
    if _blank(record.get("timestamp")):
        errors.append("Missing required field: timestamp")
    elif parse_timestamp(record.get("timestamp")) is None:
        errors.append("Invalid timestamp")

    inputs, outputs = record.get("input_addresses"), record.get("output_addresses")
    if not isinstance(inputs, list) or not isinstance(outputs, list):
        errors.append("input_addresses and output_addresses must be lists")
    elif not inputs and not outputs:
        errors.append("Transaction has no input or output addresses")

    try:
        input_amounts = [float(value) for value in record.get("input_amounts") or []]
        output_amounts = [float(value) for value in record.get("output_amounts") or []]
    except (TypeError, ValueError):
        errors.append("Input/output amounts must be numeric lists")
        input_amounts = output_amounts = []
    if any(value < 0 for value in (*input_amounts, *output_amounts)):
        errors.append("Amount values cannot be negative")
    try:
        if float(record.get("fee", 0) or 0) < 0:
            errors.append("Fee cannot be negative")
    except (TypeError, ValueError):
        errors.append("Fee must be numeric")
    return errors


def record_warnings(record: dict[str, Any]) -> list[str]:
    """Problems that do not stop a record from being analysed but reduce what can be said about it."""
    warnings: list[str] = []
    for key in ("src_ip", "dst_ip"):
        value = record.get(key)
        if _blank(value):
            warnings.append(f"Missing {key}")
            continue
        try:
            ip_address(str(value))
        except ValueError:
            warnings.append(f"Invalid IP address in {key}")
    for key in ("src_port", "dst_port"):
        try:
            if not 0 <= int(record.get(key, 0) or 0) <= 65535:
                raise ValueError
        except (TypeError, ValueError):
            warnings.append(f"Invalid port in {key}")
    try:
        input_total = sum(float(value) for value in record.get("input_amounts") or [])
        output_total = sum(float(value) for value in record.get("output_amounts") or [])
        if record.get("input_addresses") and output_total > input_total * (1 + 1e-9) + VALUE_TOLERANCE:
            warnings.append("Input total is less than output total")
    except (TypeError, ValueError):
        pass
    for side in ("input", "output"):
        if len(record.get(f"{side}_addresses") or []) != len(record.get(f"{side}_amounts") or []):
            warnings.append(f"{side}_amounts are not aligned with {side}_addresses")
    behavior = record.get("behavior_type")
    if not _blank(behavior) and behavior not in SUPPORTED_BEHAVIORS:
        warnings.append("Unrecognised behavior_type (kept as a free-text label, not used for evaluation)")
    return warnings


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize validity, duplicates, and missing fields for a list of records."""
    valid_records = invalid_records = 0
    seen_txids: Counter[str] = Counter()
    missing_fields: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    for record in records:
        txid = str(record.get("txid", "") or "").strip()
        if txid:
            seen_txids[txid] += 1
        for name in REQUIRED_FIELDS:
            if _blank(record.get(name)) and name != "fee":
                missing_fields[name] += 1
        if validate_record(record):
            invalid_records += 1
        else:
            valid_records += 1
            warning_counts.update(record_warnings(record))
    return {
        "total_records": len(records),
        "valid_records": valid_records,
        "invalid_records": invalid_records,
        "duplicates": sum(count - 1 for count in seen_txids.values() if count > 1),
        "missing_fields": dict(sorted(missing_fields.items())),
        "warnings": dict(sorted(warning_counts.items())),
    }


__all__ = ["REQUIRED_FIELDS", "OPTIONAL_FIELDS", "SUPPORTED_BEHAVIORS", "validate_record", "record_warnings", "summarize_records"]
