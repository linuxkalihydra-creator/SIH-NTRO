from backend.enrichment.service import enrich_record, enrich_records, get_offline_db_locations


def test_enrich_record_keeps_synthetic_fallback():
    record = {
        "src_ip": "203.0.113.10",
        "dst_ip": "198.51.100.15",
        "geo_country": "US",
        "asn": 64512,
    }
    enriched = enrich_record(record)
    assert enriched["geo_country"] == "US"
    assert enriched["asn"] == 64512


def test_offline_db_locations_are_documented():
    locations = get_offline_db_locations()
    assert "geoip" in locations
    assert "asn" in locations


def _write_country_mmdb(path, networks):
    from mmdb_writer import MMDBWriter
    from netaddr import IPSet

    writer = MMDBWriter(ip_version=4, database_type="GeoLite2-Country", languages=["en"], description={"en": "test GeoLite2-Country"})
    for cidr, iso_code in networks.items():
        writer.insert_network(IPSet([cidr]), {"country": {"iso_code": iso_code, "names": {"en": iso_code}}})
    writer.to_db_file(str(path))
    return path


def test_geolite2_mmdb_resolves_country_and_falls_back_for_unknown_ips(tmp_path):
    db = _write_country_mmdb(tmp_path / "GeoLite2-Country.mmdb", {"8.8.8.0/24": "US", "81.2.69.0/24": "GB"})
    records = [
        {"src_ip": "8.8.8.8", "dst_ip": "81.2.69.160", "geo_country": "ZZ", "asn": 64512},
        {"src_ip": "1.1.1.1", "dst_ip": "81.2.69.160", "geo_country": "ZZ", "asn": 64512},
        {"src_ip": "1.1.1.1", "dst_ip": "9.9.9.9", "geo_country": "ZZ", "asn": 64512},
        {"src_ip": "1.1.1.1", "dst_ip": "9.9.9.9", "asn": 64512},
    ]
    enriched = enrich_records(records, geoip_db_path=db)
    assert [(r["geo_country"], r["geo_country_source"]) for r in enriched] == [
        ("US", "geolite2"), ("GB", "geolite2"), ("ZZ", "synthetic_fallback"), ("", "unresolved"),
    ]


def test_missing_mmdb_keeps_dataset_country(tmp_path):
    enriched = enrich_record({"src_ip": "8.8.8.8", "dst_ip": "1.1.1.1", "geo_country": "US", "asn": 64512}, geoip_db_path=tmp_path / "absent.mmdb")
    assert enriched["geo_country"] == "US"
    assert enriched["geo_country_source"] == "synthetic_fallback"


def test_country_db_path_is_configurable_by_env(tmp_path, monkeypatch):
    db = _write_country_mmdb(tmp_path / "custom.mmdb", {"8.8.8.0/24": "US"})
    monkeypatch.setenv("GEOIP_COUNTRY_DB", str(db))
    assert get_offline_db_locations()["geoip"] == str(db)
    assert enrich_record({"src_ip": "8.8.8.8", "dst_ip": "1.1.1.1", "geo_country": "ZZ"})["geo_country_source"] == "geolite2"


def test_geolite2_asn_mmdb_resolves_asn(tmp_path):
    from mmdb_writer import MMDBWriter
    from netaddr import IPSet

    writer = MMDBWriter(ip_version=4, database_type="GeoLite2-ASN", languages=["en"], description={"en": "test GeoLite2-ASN"})
    writer.insert_network(IPSet(["8.8.8.0/24"]), {"autonomous_system_number": 15169, "autonomous_system_organization": "TEST-AS"})
    writer.to_db_file(str(tmp_path / "GeoLite2-ASN.mmdb"))
    enriched = enrich_record({"src_ip": "8.8.8.8", "dst_ip": "1.1.1.1", "asn": 64512}, geoip_db_path=tmp_path / "absent.mmdb", asn_db_path=tmp_path / "GeoLite2-ASN.mmdb")
    assert (enriched["asn"], enriched["asn_source"]) == (15169, "geolite2")


def test_orchestrator_uses_configured_geoip_database(tmp_path):
    import json

    from backend.pipeline.orchestrator import AnalysisOrchestrator
    from scripts.generate_dataset import NETWORK_RANGES, generate_dataset

    records, _ = generate_dataset(records=300, seed=5)
    dataset = tmp_path / "transactions.json"
    dataset.write_text(json.dumps(records), encoding="utf-8")
    db = _write_country_mmdb(tmp_path / "GeoLite2-Country.mmdb", {str(network): "NL" for network in NETWORK_RANGES})

    result = AnalysisOrchestrator(geoip_db_path=db).run(str(dataset), output_dir=str(tmp_path / "out"))
    assert result.dataset_statistics["geo_country_sources"] == {"geolite2": 300}
    assert {record["geo_country"] for record in result.records} == {"NL"}
