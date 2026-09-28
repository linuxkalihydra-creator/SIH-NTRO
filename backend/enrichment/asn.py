"""Offline ASN enrichment helpers.

ASNs are resolved from an optional local MaxMind GeoLite2-ASN ``.mmdb`` file
(``$GEOIP_ASN_DB``), read offline with ``geoip2``. Without it, the record keeps
the ``asn`` it arrived with. A legacy ``[{"ip": ..., "asn": ...}]`` JSON file is
still accepted as a database path.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ASN_DB_ENV = "GEOIP_ASN_DB"
DEFAULT_ASN_DB = "data/geoip/GeoLite2-ASN.mmdb"


def asn_db_path(db_path: str | Path | None = None) -> Path:
    """Return the configured GeoLite2-ASN path: argument, then $GEOIP_ASN_DB, then the default."""
    return Path(db_path or os.getenv(ASN_DB_ENV) or DEFAULT_ASN_DB)


class ASNAdapter:
    """Resolve IPs to autonomous system numbers from a local database."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = asn_db_path(db_path)
        self._reader = None
        self._cache: dict[str, int] = {}
        self._lookups: dict[str, int | None] = {}
        self._load_local_database()

    @property
    def available(self) -> bool:
        return self._reader is not None or bool(self._cache)

    @property
    def source_name(self) -> str:
        return "geolite2" if self._reader is not None else "local_asn"

    def _load_local_database(self) -> None:
        """Open the local database if it exists; otherwise stay in fallback mode."""
        if not self.db_path.exists():
            return

        if self.db_path.suffix.lower() == ".mmdb":
            try:
                import geoip2.database

                self._reader = geoip2.database.Reader(str(self.db_path))
            except Exception as exc:  # unreadable/corrupt file or missing package: fall back
                logger.warning("ASN database %s could not be opened: %s", self.db_path, exc)
            return

        try:
            with self.db_path.open("r", encoding="utf-8") as handle:
                records = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return

        if isinstance(records, dict):
            records = records.get("data", [])

        for record in records:
            if not isinstance(record, dict):
                continue
            ip_value = record.get("ip")
            asn_value = record.get("asn")
            if ip_value and asn_value is not None:
                self._cache[str(ip_value)] = int(asn_value)

    def resolve_asn(self, ip_address: str) -> int | None:
        """Return an ASN for an IP when the local database knows it."""
        if not ip_address:
            return None
        ip_address = str(ip_address)
        if self._reader is None:
            return self._cache.get(ip_address)
        if ip_address not in self._lookups:
            import geoip2.errors

            try:
                self._lookups[ip_address] = self._reader.asn(ip_address).autonomous_system_number
            except (geoip2.errors.AddressNotFoundError, ValueError):
                self._lookups[ip_address] = None
        return self._lookups[ip_address]

    def enrich_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Return a record with an ASN resolved (src_ip first, then dst_ip) when possible."""
        enriched = dict(record)
        for ip_key in ("src_ip", "dst_ip"):
            resolved = self.resolve_asn(str(record.get(ip_key, "")).strip())
            if resolved is not None:
                enriched["asn"] = resolved
                enriched["asn_source"] = self.source_name
                return enriched
        enriched["asn"] = record.get("asn") or 0
        enriched["asn_source"] = "synthetic_fallback" if enriched["asn"] else "unresolved"
        return enriched

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None


def resolve_asn_fallback(record: dict[str, Any], asn_db_path: str | Path | None = None) -> dict[str, Any]:
    """Resolve ASN for an IP if local data exists; otherwise keep the synthetic ASN value."""
    adapter = ASNAdapter(asn_db_path)
    try:
        return adapter.enrich_record(record)
    finally:
        adapter.close()
