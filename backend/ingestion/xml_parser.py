"""XML ingestion utilities (thin wrapper over the streaming reader in ``readers.py``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.ingestion.readers import read_xml


def parse_xml(path: str | Path) -> list[dict[str, Any]]:
    """Parse an XML document into transaction dictionaries.

    Any element carrying a transaction id is a record; list fields may be nested
    elements, repeated tags or attribute-only elements. Use ``readers.read_xml``
    to stream large files.
    """
    return list(read_xml(path))
