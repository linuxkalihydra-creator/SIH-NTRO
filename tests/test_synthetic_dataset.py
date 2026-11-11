import csv
import json
import xml.etree.ElementTree as ET
from ipaddress import IPv4Address

from backend.ingestion.service import load_dataset
from scripts.generate_dataset import (SUPPORTED_BEHAVIORS, generate_dataset, generate_world, observation_rows, validate_record, write_ground_truth,
                                      write_outputs)

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
    assert generate_dataset(records=400, seed=3)[0] != generate_dataset(records=400, seed=4)[0]


def test_ground_truth_covers_every_address_and_pattern(small_world):
    records, _, truth = small_world
    addresses = {address for record in records for address in record["input_addresses"] + record["output_addresses"]}
    assert set(truth["wallets"]) == addresses
    assert set(truth["seed_wallets"]) <= addresses and truth["seed_wallets"]
    assert {pattern["type"] for pattern in truth["patterns"]} >= {"peeling_chain", "coinjoin", "layering", "rapid_passthrough"}
    txids = {record["txid"] for record in records}
    assert all(set(pattern["txids"]) <= txids for pattern in truth["patterns"])
    assert any(item["illicit"] for item in truth["wallets"].values()) and not all(item["illicit"] for item in truth["wallets"].values())


def test_ground_truth_names_operations_and_cash_out_points(small_world):
    _, _, truth = small_world
    wallets = truth["wallets"]
    groups = {item["group"] for item in wallets.values() if item.get("group")}
    assert {"ransomware_group_1", "ransomware_group_2", "darknet_vendor"} <= groups
    assert all(item["illicit"] for item in wallets.values() if item.get("group"))
    # A mule is its own entity but belongs to its operator's operation.
    assert {item["entity_kind"] for item in wallets.values() if item.get("group") == "ransomware_group_1"} >= {"ransomware_operator", "money_mule"}
    cashouts = [item for item in wallets.values() if item.get("cashout_for")]
    assert cashouts and all(item["role"] == "exchange_deposit" and not item["illicit"] for item in cashouts)
    seeds = {wallets[address]["group"] for address in truth["seed_wallets"]}
    assert seeds == {"ransomware_group_1"}  # the second group and the vendor have to be found without seeds


def test_capture_rows_show_transactions_propagating(small_world):
    records, _, truth = small_world
    rows = observation_rows(records)
    assert len(rows) > 1.5 * len(records)
    assert [row["timestamp"] for row in rows] == sorted(row["timestamp"] for row in rows)
    multi = [record for record in records if record.get("observations")]
    assert 0.3 < len(multi) / len(records) < 0.9
    for record in multi[:200]:
        observations = record["observations"]
        assert [item["timestamp"] for item in observations] == sorted(item["timestamp"] for item in observations)
        assert (record["timestamp"], record["src_ip"], record["dst_ip"]) == (observations[0]["timestamp"], observations[0]["src_ip"], observations[0]["dst_ip"])
    # The creator of most transactions is the first sighting; for the rest only relays were captured.
    origins = truth["origin_ips"]
    assert set(origins) == {record["txid"] for record in records}
    first_is_creator = sum(record["src_ip"] == origins[record["txid"]] for record in records) / len(records)
    assert 0.85 < first_is_creator < 0.99


def test_illicit_actors_use_nonstandard_ports_more_often(small_world):
    records, _, truth = small_world
    origins = truth["origin_ips"]

    def share(labels):
        chosen = [record for record in records if record["behavior_type"] in labels and record["src_ip"] == origins[record["txid"]]]
        return sum(record["dst_port"] != 8333 for record in chosen) / len(chosen)
    assert share({"PEELING_CHAIN", "LAYERING_LIKE"}) > 3 * share({"NORMAL"})


def test_generated_files_hold_one_row_per_observation(tmp_path):
    records, _, truth = generate_world(records=125, seed=17)
    rows = observation_rows(records)
    csv_path, json_path, xml_path = write_outputs(records, tmp_path)
    truth_path, seeds_path = write_ground_truth(truth, tmp_path)
    assert len(json.loads(json_path.read_text())) == len(rows) > len(records)
    with csv_path.open(newline="", encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == len(rows)
    assert len(ET.parse(xml_path).getroot().findall("transaction")) == len(rows)
    assert json.loads(truth_path.read_text())["seed_wallets"] == seeds_path.read_text().split()
    for path in (csv_path, json_path, xml_path):  # ingestion puts the observations back together
        loaded, summary = load_dataset(path, include_summary=True)
        assert [record["txid"] for record in loaded] == [record["txid"] for record in records]
        assert summary["observations"] == len(rows) and summary["invalid_records"] == 0
        by_txid = {record["txid"]: record for record in loaded}
        for record in records[:40]:
            assert by_txid[record["txid"]]["src_ip"] == record["src_ip"]
            assert len(by_txid[record["txid"]].get("observations") or [1]) == len(record.get("observations") or [1])


def test_generated_ips_are_public_and_geoip_resolvable(small_world):
    records, _, _ = small_world
    for ip in {IPv4Address(row[key]) for row in observation_rows(records) for key in ("src_ip", "dst_ip")}:
        assert ip.is_global and not (ip.is_private or ip.is_reserved or ip.is_multicast or ip.is_loopback)
