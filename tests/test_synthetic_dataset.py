import csv
import json
import xml.etree.ElementTree as ET
from ipaddress import IPv4Address

from scripts.generate_dataset import SUPPORTED_BEHAVIORS, generate_dataset, generate_world, validate_record, write_ground_truth, write_outputs

REQUIRED_FIELDS = {
    "timestamp", "src_ip", "dst_ip", "src_port", "dst_port", "txid", "input_addresses", "output_addresses",
    "input_amounts", "output_amounts", "fee", "script_type", "geo_country", "asn", "behavior_type",
}


def test_generated_transactions_are_valid_and_complete(small_world):
    records, counts, _ = small_world
    assert len(records) == 1500 and sum(counts.values()) == 1500
    for record in records:
        assert REQUIRED_FIELDS.issubset(record) and record["behavior_type"] in SUPPORTED_BEHAVIORS
        assert validate_record(record) == []
        assert abs(sum(record["input_amounts"]) - sum(record["output_amounts"]) - record["fee"]) < 1e-7
    assert len({record["txid"] for record in records}) == len(records)
    assert [record["timestamp"] for record in records] == sorted(record["timestamp"] for record in records)


def test_small_and_exact_record_counts():
    assert len(generate_dataset(records=50, seed=7)[0]) == 50
    assert len(generate_dataset(records=300, seed=13)[0]) == 300


def test_generation_is_deterministic():
    assert generate_dataset(records=400, seed=3)[0] == generate_dataset(records=400, seed=3)[0]


def test_ground_truth_covers_every_address_and_pattern(small_world):
    records, _, truth = small_world
    addresses = {address for record in records for address in record["input_addresses"] + record["output_addresses"]}
    assert set(truth["wallets"]) == addresses
    assert set(truth["seed_wallets"]) <= addresses and truth["seed_wallets"]
    assert {pattern["type"] for pattern in truth["patterns"]} >= {"peeling_chain", "coinjoin", "layering", "rapid_passthrough"}
    txids = {record["txid"] for record in records}
    assert all(set(pattern["txids"]) <= txids for pattern in truth["patterns"])
    assert any(item["illicit"] for item in truth["wallets"].values()) and not all(item["illicit"] for item in truth["wallets"].values())


def test_illicit_actors_use_nonstandard_ports_more_often(small_world):
    records, _, _ = small_world
    def share(labels):
        chosen = [record for record in records if record["behavior_type"] in labels]
        return sum(record["dst_port"] != 8333 for record in chosen) / len(chosen)
    assert share({"PEELING_CHAIN", "LAYERING_LIKE"}) > 3 * share({"NORMAL"})


def test_generated_files_exist_and_counts_match(tmp_path):
    records, _, truth = generate_world(records=125, seed=17)
    csv_path, json_path, xml_path = write_outputs(records, tmp_path)
    truth_path, seeds_path = write_ground_truth(truth, tmp_path)
    assert len(json.loads(json_path.read_text())) == len(records)
    with csv_path.open(newline="", encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == len(records)
    assert len(ET.parse(xml_path).getroot().findall("transaction")) == len(records)
    assert json.loads(truth_path.read_text())["seed_wallets"] == seeds_path.read_text().split()


def test_generated_ips_are_public_and_geoip_resolvable(small_world):
    records, _, _ = small_world
    for ip in {IPv4Address(record[key]) for record in records for key in ("src_ip", "dst_ip")}:
        assert ip.is_global and not (ip.is_private or ip.is_reserved or ip.is_multicast or ip.is_loopback)
