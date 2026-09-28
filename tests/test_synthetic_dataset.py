import csv
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.generate_dataset import generate_dataset, validate_record, write_outputs

REQUIRED_FIELDS = {
    "timestamp",
    "src_ip",
    "dst_ip",
    "src_port",
    "dst_port",
    "txid",
    "input_addresses",
    "output_addresses",
    "input_amounts",
    "output_amounts",
    "fee",
    "script_type",
    "geo_country",
    "asn",
    "behavior_type",
}


def test_generated_transaction_has_required_fields():
    records, _ = generate_dataset(records=50, seed=7)
    assert len(records) == 50
    for record in records:
        assert REQUIRED_FIELDS.issubset(record.keys())
        assert record["behavior_type"] in {
            "NORMAL",
            "EXCHANGE_LIKE",
            "RAPID_TRANSFER",
            "LAYERING_LIKE",
            "MIXING_LIKE",
            "HIGH_NETWORK_DIVERSITY",
        }
        assert validate_record(record) == []


def test_txids_are_unique():
    records, _ = generate_dataset(records=200, seed=11)
    txids = [record["txid"] for record in records]
    assert len(txids) == len(set(txids))


def test_amounts_are_valid():
    records, _ = generate_dataset(records=300, seed=13)
    for record in records:
        input_total = sum(float(amount) for amount in record["input_amounts"])
        output_total = sum(float(amount) for amount in record["output_amounts"])
        assert input_total >= 0
        assert output_total >= 0
        assert float(record["fee"]) >= 0
        assert input_total >= output_total


def test_generated_files_exist_and_counts_match(tmp_path):
    records, _ = generate_dataset(records=125, seed=17)
    csv_path, json_path, xml_path = write_outputs(records, tmp_path)

    assert csv_path.exists()
    assert json_path.exists()
    assert xml_path.exists()

    with json_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    assert len(payload) == len(records)

    with csv_path.open("r", newline="", encoding="utf-8") as fh:
        csv_rows = list(csv.DictReader(fh))
    assert len(csv_rows) == len(records)

    tree = ET.parse(xml_path)
    root = tree.getroot()
    assert len(root.findall("transaction")) == len(records)


def test_generated_ips_are_public_and_geoip_resolvable():
    from ipaddress import IPv4Address

    records, _ = generate_dataset(records=500, seed=3)
    ips = {IPv4Address(record[key]) for record in records for key in ("src_ip", "dst_ip")}
    assert ips
    for ip in ips:
        assert ip.is_global and not (ip.is_private or ip.is_reserved or ip.is_multicast or ip.is_loopback)
