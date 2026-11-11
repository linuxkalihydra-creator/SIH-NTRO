from backend.enrichment.geoip import IpResolver, ip_scope
from backend.enrichment.service import enrich_dataset, enrich_record, get_offline_db_locations


def test_enrich_record_keeps_the_datasets_own_values_without_a_database():
    record = {"src_ip": "203.0.113.10", "dst_ip": "198.51.100.15", "geo_country": "US", "asn": 64512}
    enriched = enrich_record(record)
    assert (enriched["geo_country"], enriched["geo_country_source"]) == ("US", "dataset")
    assert (enriched["asn"], enriched["asn_source"]) == (64512, "dataset")


def test_offline_db_locations_are_documented():
    locations = get_offline_db_locations()
    assert "geoip" in locations and "asn" in locations


def _write_country_mmdb(path, networks):
    from mmdb_writer import MMDBWriter
    from netaddr import IPSet

    writer = MMDBWriter(ip_version=4, database_type="GeoLite2-Country", languages=["en"], description={"en": "test GeoLite2-Country"})
    for cidr, iso_code in networks.items():
        writer.insert_network(IPSet([cidr]), {"country": {"iso_code": iso_code, "names": {"en": iso_code}}})
    writer.to_db_file(str(path))
    return path


def _write_asn_mmdb(path, networks):
    from mmdb_writer import MMDBWriter
    from netaddr import IPSet

    writer = MMDBWriter(ip_version=4, database_type="GeoLite2-ASN", languages=["en"], description={"en": "test GeoLite2-ASN"})
    for cidr, (number, organisation) in networks.items():
        writer.insert_network(IPSet([cidr]), {"autonomous_system_number": number, "autonomous_system_organization": organisation})
    writer.to_db_file(str(path))
    return path


def test_the_source_ip_decides_the_country_and_the_peer_gets_its_own(tmp_path):
    db = _write_country_mmdb(tmp_path / "GeoLite2-Country.mmdb", {"8.8.8.0/24": "US", "81.2.69.0/24": "GB"})
    records = [
        {"src_ip": "8.8.8.8", "dst_ip": "81.2.69.160", "geo_country": "ZZ", "asn": 64512},
        {"src_ip": "1.1.1.1", "dst_ip": "81.2.69.160", "geo_country": "ZZ", "asn": 64512},   # the peer's country is not the sender's
        {"src_ip": "1.1.1.1", "dst_ip": "9.9.9.9", "asn": 64512},
        {"src_ip": "2.2.2.2", "dst_ip": "9.9.9.9"},
    ]
    enriched, table = enrich_dataset(records, geoip_db_path=db)
    assert [(r["geo_country"], r["geo_country_source"]) for r in enriched] == [
        ("US", "geolite2"), ("ZZ", "dataset"), ("ZZ", "dataset"), ("", "unresolved"),
    ]
    assert enriched[0]["dst_country"] == "GB" and enriched[1]["dst_country"] == "GB" and enriched[2]["dst_country"] == ""
    assert table["8.8.8.8"]["country"] == "US" and table["81.2.69.160"]["country"] == "GB"
    assert table["1.1.1.1"] == {"country": "ZZ", "asn": 64512, "as_org": "", "country_source": "dataset", "asn_source": "dataset", "scope": "global"}


def test_missing_mmdb_keeps_dataset_country(tmp_path):
    enriched = enrich_record({"src_ip": "8.8.8.8", "dst_ip": "1.1.1.1", "geo_country": "US", "asn": 64512}, geoip_db_path=tmp_path / "absent.mmdb")
    assert enriched["geo_country"] == "US"
    assert enriched["geo_country_source"] == "dataset"


def test_country_db_path_is_configurable_by_env(tmp_path, monkeypatch):
    db = _write_country_mmdb(tmp_path / "custom.mmdb", {"8.8.8.0/24": "US"})
    monkeypatch.setenv("GEOIP_COUNTRY_DB", str(db))
    assert get_offline_db_locations()["geoip"] == str(db)
    assert enrich_record({"src_ip": "8.8.8.8", "dst_ip": "1.1.1.1", "geo_country": "ZZ"})["geo_country_source"] == "geolite2"


def test_asn_mmdb_resolves_number_and_organisation(tmp_path):
    asn_db = _write_asn_mmdb(tmp_path / "GeoLite2-ASN.mmdb", {"8.8.8.0/24": (15169, "TEST-AS")})
    enriched = enrich_record({"src_ip": "8.8.8.8", "dst_ip": "1.1.1.1", "asn": 64512}, geoip_db_path=tmp_path / "absent.mmdb", asn_db_path=asn_db)
    assert (enriched["asn"], enriched["asn_source"], enriched["as_org"]) == (15169, "geolite2", "TEST-AS")


def test_every_ip_in_the_capture_is_resolved_once_including_relays(tmp_path):
    db = _write_country_mmdb(tmp_path / "GeoLite2-Country.mmdb", {"8.8.8.0/24": "US", "9.9.9.0/24": "CH"})
    record = {"src_ip": "8.8.8.8", "dst_ip": "1.1.1.1", "observations": [
        {"timestamp": "2024-01-01T00:00:00Z", "src_ip": "8.8.8.8", "dst_ip": "1.1.1.1"},
        {"timestamp": "2024-01-01T00:00:02Z", "src_ip": "9.9.9.9", "dst_ip": "7.7.7.7", "geo_country": "XX", "asn": 64999},
    ]}
    enriched, table = enrich_dataset([record, dict(record)], geoip_db_path=db)
    assert set(table) == {"8.8.8.8", "1.1.1.1", "9.9.9.9", "7.7.7.7"}
    assert table["9.9.9.9"]["country"] == "CH" and table["9.9.9.9"]["asn"] == 64999   # the database wins; the row fills what it lacks
    assert enriched[0] is not record and "geo_country_source" not in record          # inputs are not modified


def test_resolver_handles_private_invalid_and_empty_addresses(tmp_path):
    resolver = IpResolver(tmp_path / "none.mmdb", tmp_path / "none-asn.mmdb")
    try:
        assert not resolver.available
        assert resolver.lookup("10.0.0.1").scope == "private" and resolver.lookup("not-an-ip").scope == "invalid" and resolver.lookup("").scope == "invalid"
        assert resolver.lookup("8.8.8.8").country == "" and resolver.lookup("8.8.8.8").country_source == "unresolved"
    finally:
        resolver.close()
    assert ip_scope("127.0.0.1") == "loopback" and ip_scope("8.8.8.8") == "global" and ip_scope("2001:4860:4860::8888") == "global"


def test_orchestrator_uses_configured_geoip_database(tmp_path):
    import json

    from backend.pipeline.orchestrator import AnalysisOrchestrator
    from scripts.generate_dataset import NETWORK_RANGES, generate_dataset, observation_rows

    records, _ = generate_dataset(records=300, seed=5)
    dataset = tmp_path / "transactions.json"
    dataset.write_text(json.dumps(observation_rows(records)), encoding="utf-8")
    db = _write_country_mmdb(tmp_path / "GeoLite2-Country.mmdb", {str(network): "NL" for network in NETWORK_RANGES})
    asn_db = _write_asn_mmdb(tmp_path / "GeoLite2-ASN.mmdb", {str(network): (64496 + index, f"TEST-NET-{index}") for index, network in enumerate(NETWORK_RANGES)})

    result = AnalysisOrchestrator(geoip_db_path=db, asn_db_path=asn_db).run(str(dataset), output_dir=str(tmp_path / "out"), persist_graph=False)
    assert result.overview["geo_sources"] == {"geolite2": 300}
    assert {record["geo_country"] for record in result.records} == {"NL"}
    lead = result.investigation.ip_alerts(1)[0]
    assert lead["country"] == "NL" and lead["as_org"].startswith("TEST-NET-")
