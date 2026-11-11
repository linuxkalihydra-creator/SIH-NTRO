"""JSON ingestion utilities (thin wrapper over the streaming reader in ``readers.py``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.ingestion.readers import read_json


def parse_json(path: str | Path) -> list[dict[str, Any]]:
    """Load a JSON file and return its transaction dictionaries.

    Accepts a top-level array, an object wrapping a ``transactions`` list, or JSON
    Lines. Use ``readers.read_json`` to stream large files.
    """
    return [dict(record) for record in read_json(path)]
