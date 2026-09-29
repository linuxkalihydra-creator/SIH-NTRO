"""Offline GeoIP and country enrichment helpers.

Countries are resolved from a local MaxMind GeoLite2-Country ``.mmdb`` file read
with the ``geoip2`` package; no network access happens at lookup time. The file
must be downloaded once in advance (see README). When it is absent, or an IP is
not in the database, the record keeps the ``geo_country`` it arrived with and is
marked ``synthetic_fallback``. A legacy ``[{"ip": ..., "country": ...}]`` JSON
file is still accepted as a database path.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

COUNTRY_DB_ENV = "GEOIP_COUNTRY_DB"
DEFAULT_COUNTRY_DB = "data/geoip/GeoLite2-Country.mmdb"


def country_db_path(db_path: str | Path | None = None) -> Path:
    """Return the configured GeoLite2-Country path: argument, then $GEOIP_COUNTRY_DB, then the default."""
    if db_path or os.getenv(COUNTRY_DB_ENV):
        return Path(db_path or os.getenv(COUNTRY_DB_ENV))
    # GeoLite2 (MaxMind account needed) or the account-free DB-IP Lite download (scripts/download_geoip.py).
    for candidate in (DEFAULT_COUNTRY_DB, "data/geoip/dbip-country-lite.mmdb"):
        if Path(candidate).exists():
            return Path(candidate)
    return Path(DEFAULT_COUNTRY_DB)


class GeoIPAdapter:
    """Resolve IPs to ISO country codes from a local GeoLite2-Country database."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = country_db_path(db_path)
        self._reader = None
        self._cache: dict[str, str] = {}
        self._lookups: dict[str, str | None] = {}
        self._load_local_database()

    @property
    def available(self) -> bool:
        return self._reader is not None or bool(self._cache)

    @property
    def source_name(self) -> str:
        if self._reader is None:
            return "local_geoip"
        database_type = str(self._reader.metadata().database_type).lower()
        return "dbip" if database_type.startswith("dbip") else "geolite2"

    def _load_local_database(self) -> None:
        """Open the local database if it exists; otherwise stay in fallback mode."""
        if not self.db_path.exists():
            return

        if self.db_path.suffix.lower() == ".mmdb":
            try:
                import geoip2.database

                self._reader = geoip2.database.Reader(str(self.db_path))
            except Exception as exc:  # unreadable/corrupt file or missing package: fall back
                logger.warning("GeoIP database %s could not be opened: %s", self.db_path, exc)
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
            country = record.get("country")
            if ip_value and country:
                self._cache[str(ip_value)] = str(country)

    def resolve_country(self, ip_address: str) -> str | None:
        """Resolve an IP to a country code if the local database knows it."""
        if not ip_address:
            return None
        ip_address = str(ip_address)
        if self._reader is None:
            return self._cache.get(ip_address)
        if ip_address not in self._lookups:
            import geoip2.errors

            try:
                self._lookups[ip_address] = self._reader.country(ip_address).country.iso_code
            except (geoip2.errors.AddressNotFoundError, ValueError):
                self._lookups[ip_address] = None
        return self._lookups[ip_address]

    def enrich_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Return a record with a resolved country (src_ip first, then dst_ip) when available."""
        enriched = dict(record)
        for ip_key in ("src_ip", "dst_ip"):
            resolved = self.resolve_country(str(record.get(ip_key, "")).strip())
            if resolved:
                enriched["geo_country"] = resolved
                enriched["geo_country_source"] = self.source_name
                return enriched
        enriched["geo_country"] = record.get("geo_country") or ""
        enriched["geo_country_source"] = "synthetic_fallback" if enriched["geo_country"] else "unresolved"
        return enriched

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None


def resolve_country_fallback(record: dict[str, Any], geoip_db_path: str | Path | None = None) -> dict[str, Any]:
    """Resolve the IP to a country if possible; otherwise fall back to the synthetic field."""
    adapter = GeoIPAdapter(geoip_db_path)
    try:
        return adapter.enrich_record(record)
    finally:
        adapter.close()
