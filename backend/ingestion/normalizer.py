"""Convert raw ingested rows into the canonical record schema.

``normalize_row`` is tolerant: it never raises. A row that cannot be used at all
(no transaction id, no usable timestamp, no addresses, non-numeric or negative
amounts) is returned with ``errors``; anything that can be repaired is repaired
and reported as a ``warning`` so the transaction still takes part in flow
analysis. Losing a transaction breaks every chain that runs through it, which
costs far more than a missing port or an unresolvable IP.

``normalize_record`` keeps the strict behaviour older callers expect: it returns
the canonical record or raises ``ValueError``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.ingestion.schema import (CANONICAL_FIELDS, REQUIRED_FIELDS, SATOSHI, ColumnMapping, format_timestamp, is_blank, map_columns,
                                      parse_amount, parse_int, parse_list, parse_timestamp, split_endpoint, split_parties)

# Outputs may exceed inputs by floating-point noise only.
VALUE_TOLERANCE = 1e-8


@dataclass
class RowResult:
    record: dict[str, Any] | None
    when: datetime | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    integral_amounts: bool = True
    max_amount: float = 0.0


def _amounts(values: list[Any], satoshi: bool) -> list[float]:
    numbers = [parse_amount(value) for value in values]
    return [number * SATOSHI for number in numbers] if satoshi else numbers


def _align(addresses: list[str], amounts: list[float], side: str, warnings: list[str]) -> list[float]:
    """Give every address an amount; a mismatched list is treated as a total and split evenly."""
    if len(amounts) == len(addresses):
        return amounts
    if not addresses:
        return amounts
    if amounts:
        warnings.append(f"{side}_amounts_not_aligned")
        share = sum(amounts) / len(addresses)
        return [share] * len(addresses)
    warnings.append(f"missing_{side}_amounts")
    return [0.0] * len(addresses)


def normalize_row(raw: dict[str, Any], mapping: ColumnMapping | None = None) -> RowResult:
    """Map one raw row onto the canonical schema, repairing what can be repaired."""
    if not isinstance(raw, dict):
        return RowResult(None, errors=["not_a_record"])
    mapping = mapping or map_columns(raw.keys())
    source = mapping.source
    result = RowResult(None)
    errors, warnings = result.errors, result.warnings

    values = {name: raw.get(column) for name, column in source.items()}
    value = values.get
    result.missing = [name for name in REQUIRED_FIELDS if is_blank(value(name))]

    txid = "" if value("txid") is None else str(value("txid")).strip()
    if not txid:
        errors.append("missing_txid")

    stamp = value("timestamp")
    if mapping.time_part and not is_blank(stamp) and not is_blank(raw.get(mapping.time_part)):
        stamp = f"{str(stamp).strip()} {str(raw[mapping.time_part]).strip()}"
    when = parse_timestamp(stamp)
    if when is None:
        errors.append("missing_timestamp" if is_blank(stamp) else "invalid_timestamp")
    result.when = when

    try:
        input_addresses, input_embedded = split_parties(value("input_addresses"))
        output_addresses, output_embedded = split_parties(value("output_addresses"))
    except (TypeError, ValueError):
        input_addresses, input_embedded, output_addresses, output_embedded = [], None, [], None
        errors.append("invalid_amounts")
    if not input_addresses and not output_addresses:
        if "invalid_amounts" not in errors:
            errors.append("no_addresses")
    elif not output_addresses:
        warnings.append("no_output_addresses")
    elif not input_addresses:
        warnings.append("no_input_addresses")  # coinbase transactions have no input address

    input_amounts: list[float] = []
    output_amounts: list[float] = []
    fee: float | None = None
    try:
        raw_inputs, raw_outputs, raw_fee = value("input_amounts"), value("output_amounts"), value("fee")
        if is_blank(raw_inputs) and input_embedded is not None:
            input_amounts = _amounts(input_embedded, False)
            result.missing = [name for name in result.missing if name != "input_amounts"]
        else:
            input_amounts = _amounts(parse_list(raw_inputs), "input_amounts" in mapping.satoshi)
        if is_blank(raw_outputs) and output_embedded is not None:
            output_amounts = _amounts(output_embedded, False)
            result.missing = [name for name in result.missing if name != "output_amounts"]
        else:
            output_amounts = _amounts(parse_list(raw_outputs), "output_amounts" in mapping.satoshi)
        if not is_blank(raw_fee):
            fee = parse_amount(raw_fee) * (SATOSHI if "fee" in mapping.satoshi else 1.0)
    except (TypeError, ValueError):
        errors.append("invalid_amounts")
    if any(amount < 0 for amount in (*input_amounts, *output_amounts)) or (fee is not None and fee < 0):
        errors.append("negative_amount")

    if errors:
        return result

    declared = [*input_amounts, *output_amounts]
    result.max_amount = max(declared, default=0.0)
    result.integral_amounts = bool(declared) and not mapping.satoshi and all(float(amount).is_integer() for amount in declared)

    input_amounts = _align(input_addresses, input_amounts, "input", warnings)
    output_amounts = _align(output_addresses, output_amounts, "output", warnings)
    input_total, output_total = sum(input_amounts), sum(output_amounts)
    # Reconstruct a missing side from the other side and the fee, so value still balances.
    if input_addresses and input_total == 0 and output_total > 0:
        input_total = output_total + (fee or 0.0)
        input_amounts = [input_total / len(input_addresses)] * len(input_addresses)
    elif output_addresses and output_total == 0 and input_total > 0:
        output_total = max(input_total - (fee or 0.0), 0.0)
        output_amounts = [output_total / len(output_addresses)] * len(output_addresses)
    if input_addresses and output_total > input_total * (1 + 1e-9) + VALUE_TOLERANCE:
        warnings.append("outputs_exceed_inputs")
    if not fee:  # absent or zero: the fee is whatever the inputs do not pass on
        fee = max(input_total - output_total, 0.0) if input_addresses else 0.0

    record: dict[str, Any] = {"timestamp": format_timestamp(when)}
    for side in ("src", "dst"):
        raw_ip, raw_port = value(f"{side}_ip"), value(f"{side}_port")
        ip, endpoint_port = split_endpoint(raw_ip)
        if not ip and not is_blank(raw_ip):
            warnings.append(f"invalid_{side}_ip")
        try:
            port = endpoint_port if is_blank(raw_port) and endpoint_port is not None else parse_int(raw_port)
        except (TypeError, ValueError):
            port = -1
        if not 0 <= port <= 65535:
            warnings.append(f"invalid_{side}_port")
            port = 0
        record[f"{side}_ip"], record[f"{side}_port"] = ip, port
    record = {
        "timestamp": record["timestamp"], "src_ip": record["src_ip"], "dst_ip": record["dst_ip"],
        "src_port": record["src_port"], "dst_port": record["dst_port"], "txid": txid,
        "input_addresses": input_addresses, "output_addresses": output_addresses,
        "input_amounts": [round(amount, 8) for amount in input_amounts], "output_amounts": [round(amount, 8) for amount in output_amounts],
        "fee": round(fee, 8),
        "script_type": "" if value("script_type") is None else str(value("script_type")).strip(),
        "geo_country": "" if value("geo_country") is None else str(value("geo_country")).strip().upper(),
        "asn": 0,
        "behavior_type": "" if value("behavior_type") is None else str(value("behavior_type")).strip(),
    }
    try:
        record["asn"] = max(parse_int(value("asn")), 0)
    except (TypeError, ValueError):
        warnings.append("invalid_asn")
    for name in ("block_height", "transaction_size"):
        if not is_blank(value(name)):
            try:
                record[name] = parse_int(value(name))
            except (TypeError, ValueError):
                warnings.append(f"invalid_{name}")
    result.record = record
    return result


def normalize_record(raw_record: dict[str, Any]) -> dict[str, Any]:
    """Strict variant of :func:`normalize_row`: the canonical record, or ``ValueError``."""
    if not isinstance(raw_record, dict):
        raise TypeError("Normalized records must be dictionaries.")
    result = normalize_row(raw_record)
    if result.record is None:
        if "invalid_timestamp" in result.errors:
            raise ValueError(f"Invalid ISO timestamp: {raw_record.get('timestamp')}")
        raise ValueError(f"Record cannot be normalized: {', '.join(result.errors)}")
    return result.record


def normalize_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply canonical normalization to each record in a list."""
    return [normalize_record(record) for record in records]


__all__ = ["CANONICAL_FIELDS", "RowResult", "normalize_row", "normalize_record", "normalize_records", "VALUE_TOLERANCE"]
