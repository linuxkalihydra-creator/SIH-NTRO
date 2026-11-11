"""Offline GeoIP country and ASN resolution.

Every lookup reads a local MaxMind-format ``.mmdb`` file with the ``geoip2``
package; nothing touches the network. Two open databases work out of the box:

* **DB-IP Lite** (CC BY 4.0, no account) - fetched once by ``scripts/download_geoip.py``;
* **MaxMind GeoLite2** - ``GeoLite2-Country.mmdb`` / ``GeoLite2-ASN.mmdb`` placed in ``data/geoip/``.

Paths come from the constructor argument, then ``$GEOIP_COUNTRY_DB`` /
``$GEOIP_ASN_DB``, then the first database found in ``data/geoip/``. When no
database is present, or an IP is not in it, the dataset's own ``geo_country`` /
``asn`` values are kept and marked as coming from the dataset. A legacy
``[{"ip": ..., "country": ..., "asn": ...}]`` JSON file is still accepted.

Each distinct IP is resolved once, however many rows mention it.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

COUNTRY_DB_ENV = "GEOIP_COUNTRY_DB"
ASN_DB_ENV = "GEOIP_ASN_DB"
DEFAULT_COUNTRY_DB = "data/geoip/GeoLite2-Country.mmdb"
DEFAULT_ASN_DB = "data/geoip/GeoLite2-ASN.mmdb"
COUNTRY_CANDIDATES = (DEFAULT_COUNTRY_DB, "data/geoip/dbip-country-lite.mmdb")
ASN_CANDIDATES = (DEFAULT_ASN_DB, "data/geoip/dbip-asn-lite.mmdb")
SOURCE_DATASET = "dataset"
SOURCE_UNRESOLVED = "unresolved"


def _configured(db_path: str | Path | None, env: str, candidates: tuple[str, ...]) -> Path:
    if db_path or os.getenv(env):
        return Path(db_path or os.getenv(env))
    for candidate in candidates:
        if Path(candidate).exists():
            return Path(candidate)
    return Path(candidates[0])


def country_db_path(db_path: str | Path | None = None) -> Path:
    """The country database: argument, then $GEOIP_COUNTRY_DB, then GeoLite2 or DB-IP Lite in data/geoip/."""
    return _configured(db_path, COUNTRY_DB_ENV, COUNTRY_CANDIDATES)


def asn_db_path(db_path: str | Path | None = None) -> Path:
    """The ASN database: argument, then $GEOIP_ASN_DB, then GeoLite2 or DB-IP Lite in data/geoip/."""
    return _configured(db_path, ASN_DB_ENV, ASN_CANDIDATES)


def ip_scope(ip: str) -> str:
    """``global`` for routable addresses; otherwise why a GeoIP database cannot place the address."""
    try:
        parsed = ip_address(ip)
    except ValueError:
        return "invalid"
    for name in ("is_loopback", "is_link_local", "is_multicast", "is_unspecified", "is_private", "is_reserved"):
        if getattr(parsed, name):
            return name[3:]
    return "global"


@dataclass(slots=True)
class IpInfo:
    """Where an IP is and who announces it."""

    country: str = ""
    asn: int = 0
    as_org: str = ""
    country_source: str = SOURCE_UNRESOLVED
    asn_source: str = SOURCE_UNRESOLVED
    scope: str = "global"

    def to_dict(self) -> dict[str, Any]:
        return {"country": self.country, "asn": self.asn, "as_org": self.as_org, "country_source": self.country_source,
                "asn_source": self.asn_source, "scope": self.scope}


class _Database:
    """One local database: an ``.mmdb`` reader or a legacy JSON lookup table."""

    def __init__(self, path: Path, kind: str) -> None:
        self.path = path
        self.kind = kind
        self.reader = None
        self.table: dict[str, dict[str, Any]] = {}
        if not path.exists():
            return
        if path.suffix.lower() == ".mmdb":
            try:
                import geoip2.database

                self.reader = geoip2.database.Reader(str(path))
            except Exception as exc:  # unreadable or corrupt file, or geoip2 missing: stay in fallback mode
                logger.warning("%s database %s could not be opened: %s", kind, path, exc)
            return
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if isinstance(rows, dict):
            rows = rows.get("data", [])
        for row in rows:
            if isinstance(row, dict) and row.get("ip"):
                self.table[str(row["ip"])] = row

    @property
    def available(self) -> bool:
        return self.reader is not None or bool(self.table)

    @property
    def source(self) -> str:
        if self.reader is None:
            return "local_geoip" if self.kind == "country" else "local_asn"
        return "dbip" if str(self.reader.metadata().database_type).lower().startswith("dbip") else "geolite2"

    def close(self) -> None:
        if self.reader is not None:
            self.reader.close()
            self.reader = None


class IpResolver:
    """Resolve IPs to country and ASN from local databases, caching every answer."""

    def __init__(self, country_db: str | Path | None = None, asn_db: str | Path | None = None) -> None:
        self.country = _Database(country_db_path(country_db), "country")
        self.asn = _Database(asn_db_path(asn_db), "asn")
        self._cache: dict[str, IpInfo] = {}

    @property
    def available(self) -> bool:
        return self.country.available or self.asn.available

    def lookup(self, ip: str) -> IpInfo:
        ip = str(ip or "").strip()
        cached = self._cache.get(ip)
        if cached is not None:
            return cached
        info = IpInfo(scope=ip_scope(ip) if ip else "invalid")
        if ip and info.scope != "invalid":
            self._country(ip, info)
            self._asn(ip, info)
        self._cache[ip] = info
        return info

    def _country(self, ip: str, info: IpInfo) -> None:
        database = self.country
        if database.reader is not None:
            import geoip2.errors

            try:
                code = database.reader.country(ip).country.iso_code
            except (geoip2.errors.AddressNotFoundError, ValueError):
                code = None
            if code:
                info.country, info.country_source = code, database.source
        elif ip in database.table and database.table[ip].get("country"):
            info.country, info.country_source = str(database.table[ip]["country"]), database.source

    def _asn(self, ip: str, info: IpInfo) -> None:
        database = self.asn
        if database.reader is not None:
            import geoip2.errors

            try:
                answer = database.reader.asn(ip)
            except (geoip2.errors.AddressNotFoundError, ValueError):
                return
            if answer.autonomous_system_number:
                info.asn, info.asn_source = int(answer.autonomous_system_number), database.source
                info.as_org = str(answer.autonomous_system_organization or "")
        elif ip in database.table and database.table[ip].get("asn") is not None:
            info.asn, info.asn_source = int(database.table[ip]["asn"]), database.source
            info.as_org = str(database.table[ip].get("as_org", "") or "")

    def close(self) -> None:
        self.country.close()
        self.asn.close()


class GeoIPAdapter:
    """Country lookups only (kept for callers that used the previous per-record adapter)."""

    def __init__(self, db_path: str | Path | None = None):
        self._database = _Database(country_db_path(db_path), "country")
        self._resolver = IpResolver.__new__(IpResolver)
        self._resolver.country, self._resolver.asn, self._resolver._cache = self._database, _Database(Path("__no_asn_database__"), "asn"), {}
        self.db_path = self._database.path

    @property
    def available(self) -> bool:
        return self._database.available

    @property
    def source_name(self) -> str:
        return self._database.source

    def resolve_country(self, ip: str) -> str | None:
        return self._resolver.lookup(ip).country or None

    def enrich_record(self, record: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(record)
        resolved = self.resolve_country(str(record.get("src_ip", "")))
        if resolved:
            enriched["geo_country"], enriched["geo_country_source"] = resolved, self.source_name
        else:
            enriched["geo_country"] = record.get("geo_country") or ""
            enriched["geo_country_source"] = SOURCE_DATASET if enriched["geo_country"] else SOURCE_UNRESOLVED
        return enriched

    def close(self) -> None:
        self._database.close()


def resolve_country_fallback(record: dict[str, Any], geoip_db_path: str | Path | None = None) -> dict[str, Any]:
    """Resolve the source IP to a country if possible; otherwise keep the dataset's own value."""
    adapter = GeoIPAdapter(geoip_db_path)
    try:
        return adapter.enrich_record(record)
    finally:
        adapter.close()


__all__ = ["IpInfo", "IpResolver", "GeoIPAdapter", "country_db_path", "asn_db_path", "ip_scope", "resolve_country_fallback",
           "SOURCE_DATASET", "SOURCE_UNRESOLVED", "COUNTRY_DB_ENV", "ASN_DB_ENV"]
