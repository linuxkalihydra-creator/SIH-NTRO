"""High-level enrichment service for offline GeoIP/ASN resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.enrichment.asn import ASNAdapter, asn_db_path as configured_asn_db_path
from backend.enrichment.geoip import GeoIPAdapter, country_db_path as configured_country_db_path


def enrich_record(record: dict[str, Any], geoip_db_path: str | Path | None = None, asn_db_path: str | Path | None = None) -> dict[str, Any]:
    """Apply offline enrichment while preserving the synthetic dataset fallback behavior."""
    return enrich_records([record], geoip_db_path, asn_db_path)[0]


def enrich_records(records: list[dict[str, Any]], geoip_db_path: str | Path | None = None, asn_db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Enrich records with offline GeoIP/ASN metadata, opening each database once."""
    geoip = GeoIPAdapter(geoip_db_path)
    asn = ASNAdapter(asn_db_path)
    try:
        return [asn.enrich_record(geoip.enrich_record(record)) for record in records]
    finally:
        geoip.close()
        asn.close()


def get_offline_db_locations() -> dict[str, str]:
    """Return the configured local GeoIP/ASN database locations for this project."""
    return {
        "geoip": str(configured_country_db_path()),
        "asn": str(configured_asn_db_path()),
        "note": "Download GeoLite2-Country/ASN .mmdb files once before running offline; without them the dataset's own geo_country/asn fields are used.",
    }


__all__ = ["enrich_record", "enrich_records", "get_offline_db_locations"]
