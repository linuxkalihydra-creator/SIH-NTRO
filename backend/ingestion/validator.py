"""Validation helpers for transaction records."""

from __future__ import annotations

from collections import Counter
from ipaddress import ip_address
from typing import Any

REQUIRED_FIELDS = (
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
)

# Optional fields: geo_country/asn come from offline enrichment, script_type is
# informational, and behavior_type is a synthetic evaluation label only.
OPTIONAL_FIELDS = ("script_type", "geo_country", "asn", "behavior_type")

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


def validate_record(record: dict[str, Any]) -> list[str]:
    """Return a list of validation problems for one transaction record."""
    errors: list[str] = []

    for field in REQUIRED_FIELDS:
        if field not in record or record[field] in (None, ""):
            errors.append(f"Missing required field: {field}")

    behavior = record.get("behavior_type")
    if behavior not in (None, "") and behavior not in SUPPORTED_BEHAVIORS:
        errors.append("Unsupported behavior_type")

    for ip_key in ("src_ip", "dst_ip"):
        try:
            ip_address(str(record.get(ip_key, "")))
        except ValueError:
            errors.append(f"Invalid IP address in {ip_key}")

    for port_key in ("src_port", "dst_port"):
        port_value = record.get(port_key)
        try:
            port = int(port_value)
            if not 0 <= port <= 65535:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"Invalid port in {port_key}")

    try:
        if record.get("txid") is not None and not str(record["txid"]).strip():
            errors.append("Empty txid")
    except Exception:
        errors.append("Invalid txid")

    input_amounts = record.get("input_amounts", [])
    output_amounts = record.get("output_amounts", [])
    try:
        input_total = sum(float(value) for value in input_amounts)
        output_total = sum(float(value) for value in output_amounts)
    except (TypeError, ValueError):
        errors.append("Input/output amounts must be numeric lists")
        input_total = output_total = 0.0

    if input_total < 0 or output_total < 0:
        errors.append("Amount values cannot be negative")
    if input_total < output_total:
        errors.append("Input total is less than output total")

    try:
        fee_value = float(record.get("fee", -1))
        if fee_value < 0:
            errors.append("Fee cannot be negative")
    except (TypeError, ValueError):
        errors.append("Fee must be numeric")

    if not isinstance(record.get("input_addresses", []), list) or not record["input_addresses"]:
        errors.append("input_addresses must be a non-empty list")
    if not isinstance(record.get("output_addresses", []), list) or not record["output_addresses"]:
        errors.append("output_addresses must be a non-empty list")

    return errors


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize validity, duplicates, and missing fields for a list of records."""
    total_records = len(records)
    valid_records = 0
    invalid_records = 0
    duplicates = 0
    seen_txids: Counter[str] = Counter()
    missing_fields: Counter[str] = Counter()

    for record in records:
        txid = str(record.get("txid", "")).strip()
        if txid:
            seen_txids[txid] += 1

        errors = validate_record(record)
        if errors:
            invalid_records += 1
            for error in errors:
                if error.startswith("Missing required field:"):
                    field = error.split(": ", 1)[1]
                    missing_fields[field] += 1
        else:
            valid_records += 1

    duplicates = sum(count - 1 for count in seen_txids.values() if count > 1)

    return {
        "total_records": total_records,
        "valid_records": valid_records,
        "invalid_records": invalid_records,
        "duplicates": duplicates,
        "missing_fields": dict(sorted(missing_fields.items())),
    }
