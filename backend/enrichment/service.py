"""Enrich transaction records with offline GeoIP country and ASN data.

``enrich_dataset`` resolves every distinct IP in the capture once - the source of
every observation and every destination peer - and returns the records plus an
IP table (``ip -> country, ASN, AS organisation, scope``) that the correlation
and link-analysis layers use.

On each record, ``geo_country`` / ``asn`` describe the **first-seen source IP**
(the node the transaction is attributed to); the destination peer gets its own
``dst_country`` / ``dst_asn``. Values the database cannot supply fall back to
what the dataset itself carried, and ``geo_country_source`` / ``asn_source`` say
which of the two was used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.enrichment.geoip import SOURCE_DATASET, SOURCE_UNRESOLVED, IpInfo, IpResolver
from backend.enrichment.geoip import asn_db_path as configured_asn_db_path
from backend.enrichment.geoip import country_db_path as configured_country_db_path


def _merge(info: IpInfo, country: Any, asn: Any) -> IpInfo:
    """Fill what the database did not know from the dataset's own columns."""
    if info.country and info.asn:
        return info
    merged = IpInfo(info.country, info.asn, info.as_org, info.country_source, info.asn_source, info.scope)
    if not merged.country and country:
        merged.country, merged.country_source = str(country), SOURCE_DATASET
    if not merged.asn and asn:
        merged.asn, merged.asn_source = int(asn), SOURCE_DATASET
    return merged


def enrich_dataset(records: list[dict[str, Any]], geoip_db_path: str | Path | None = None,
                   asn_db_path: str | Path | None = None) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Return (enriched copies of the records, IP table for every IP in the capture)."""
    resolver = IpResolver(geoip_db_path, asn_db_path)
    table: dict[str, IpInfo] = {}

    def resolve(ip: str, country: Any = None, asn: Any = None) -> IpInfo:
        known = table.get(ip)
        if known is None or ((country or asn) and not (known.country and known.asn)):
            known = table[ip] = _merge(known or resolver.lookup(ip), country, asn)
        return known

    try:
        enriched_records = []
        for record in records:
            enriched = dict(record)
            source = resolve(str(record.get("src_ip", "") or ""), record.get("geo_country"), record.get("asn"))
            enriched["geo_country"], enriched["geo_country_source"] = source.country, source.country_source
            enriched["asn"], enriched["asn_source"] = source.asn, source.asn_source
            if source.as_org:
                enriched["as_org"] = source.as_org
            destination_ip = str(record.get("dst_ip", "") or "")
            if destination_ip:
                destination = resolve(destination_ip)
                enriched["dst_country"], enriched["dst_asn"] = destination.country, destination.asn
            for observation in record.get("observations") or ():
                resolve(str(observation.get("src_ip", "") or ""), observation.get("geo_country"), observation.get("asn"))
                if observation.get("dst_ip"):
                    resolve(str(observation["dst_ip"]))
            enriched_records.append(enriched)
    finally:
        resolver.close()
    table.pop("", None)
    return enriched_records, {ip: info.to_dict() for ip, info in table.items()}


def enrich_records(records: list[dict[str, Any]], geoip_db_path: str | Path | None = None, asn_db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Enrich records with offline GeoIP/ASN metadata, opening each database once."""
    return enrich_dataset(records, geoip_db_path, asn_db_path)[0]


def enrich_record(record: dict[str, Any], geoip_db_path: str | Path | None = None, asn_db_path: str | Path | None = None) -> dict[str, Any]:
    """Enrich one record (see :func:`enrich_dataset`)."""
    return enrich_records([record], geoip_db_path, asn_db_path)[0]


def get_offline_db_locations() -> dict[str, str]:
    """Return the configured local GeoIP/ASN database locations for this project."""
    return {
        "geoip": str(configured_country_db_path()),
        "asn": str(configured_asn_db_path()),
        "note": "Download DB-IP Lite or GeoLite2 .mmdb files once before going offline; without them the dataset's own geo_country/asn fields are used.",
    }


__all__ = ["enrich_dataset", "enrich_record", "enrich_records", "get_offline_db_locations", "SOURCE_DATASET", "SOURCE_UNRESOLVED"]
