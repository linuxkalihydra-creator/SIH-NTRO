"""CSV ingestion utilities (thin wrapper over the streaming reader in ``readers.py``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.ingestion.readers import read_csv


def _coerce_field(value: Any) -> Any:
    """Convert CSV text to Python values: JSON arrays, booleans, integers and floats."""
    if value is None:
        return value
    stripped = value.strip()
    if not stripped:
        return value
    if stripped.startswith("[") and stripped.endswith("]"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
    if stripped.lower() in {"true", "false"}:
        return stripped.lower() == "true"
    try:
        return int(stripped)
    except ValueError:
        pass
    try:
        return float(stripped)
    except ValueError:
        return stripped


def parse_csv(path: str | Path) -> list[dict[str, Any]]:
    """Parse a CSV dataset into a list of dictionaries with typed values.

    Array-like fields such as input_addresses/output_addresses are decoded from
    JSON strings when present. Use ``readers.read_csv`` to stream large files.
    """
    return [{key: _coerce_field(value) for key, value in row.items()} for row in read_csv(path)]
