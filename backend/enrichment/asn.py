"""Offline ASN enrichment (kept for callers that used the previous per-record adapter).

ASN and country lookups now share ``backend.enrichment.geoip.IpResolver``, which
resolves every distinct IP once. This module keeps the ASN-only adapter API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.enrichment.geoip import ASN_DB_ENV, SOURCE_DATASET, SOURCE_UNRESOLVED, IpResolver, _Database, asn_db_path

DEFAULT_ASN_DB = "data/geoip/GeoLite2-ASN.mmdb"


class ASNAdapter:
    """Resolve IPs to autonomous system numbers from a local database."""

    def __init__(self, db_path: str | Path | None = None):
        self._database = _Database(asn_db_path(db_path), "asn")
        self._resolver = IpResolver.__new__(IpResolver)
        self._resolver.country, self._resolver.asn, self._resolver._cache = _Database(Path("__no_country_database__"), "country"), self._database, {}
        self.db_path = self._database.path

    @property
    def available(self) -> bool:
        return self._database.available

    @property
    def source_name(self) -> str:
        return self._database.source

    def resolve_asn(self, ip: str) -> int | None:
        return self._resolver.lookup(ip).asn or None

    def enrich_record(self, record: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(record)
        resolved = self.resolve_asn(str(record.get("src_ip", "")))
        if resolved is not None:
            enriched["asn"], enriched["asn_source"] = resolved, self.source_name
        else:
            enriched["asn"] = record.get("asn") or 0
            enriched["asn_source"] = SOURCE_DATASET if enriched["asn"] else SOURCE_UNRESOLVED
        return enriched

    def close(self) -> None:
        self._database.close()


def resolve_asn_fallback(record: dict[str, Any], asn_db_path: str | Path | None = None) -> dict[str, Any]:
    """Resolve the source IP's ASN if local data exists; otherwise keep the dataset's own value."""
    adapter = ASNAdapter(asn_db_path)
    try:
        return adapter.enrich_record(record)
    finally:
        adapter.close()


__all__ = ["ASNAdapter", "resolve_asn_fallback", "asn_db_path", "ASN_DB_ENV", "DEFAULT_ASN_DB"]
