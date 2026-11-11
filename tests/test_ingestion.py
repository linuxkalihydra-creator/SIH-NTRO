import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.ingestion.csv_parser import parse_csv
from backend.ingestion.json_parser import parse_json
from backend.ingestion.normalizer import normalize_record, normalize_records, normalize_row
from backend.ingestion.readers import detect_format, read_json, read_rows, read_xml
from backend.ingestion.schema import address_type, map_columns, parse_list, parse_timestamp, split_endpoint
from backend.ingestion.service import build_dataset, load_dataset
from backend.ingestion.validator import record_warnings, summarize_records, validate_record
from backend.ingestion.xml_parser import parse_xml

SYNTHETIC = Path("data/synthetic")


def row(**overrides):
    base = {"timestamp": "2024-01-01T00:00:00+00:00", "src_ip": "8.8.8.8", "dst_ip": "1.1.1.1", "src_port": 40000, "dst_port": 8333, "txid": "t1",
            "input_addresses": ["a"], "output_addresses": ["b", "c"], "input_amounts": [1.0], "output_amounts": [0.6, 0.39]}
    base.update(overrides)
    return base


# ── the committed dataset in all three formats ──────────────────────────────
@pytest.mark.parametrize("parser, name", [(parse_csv, "transactions.csv"), (parse_json, "transactions.json"), (parse_xml, "transactions.xml")])
def test_parsers_read_the_committed_dataset(parser, name):
    records = parser(SYNTHETIC / name)
    assert len(records) > 0 and "txid" in records[0]


def test_committed_formats_load_to_the_same_transactions():
    loaded = {name: load_dataset(SYNTHETIC / name, include_summary=True) for name in ("transactions.csv", "transactions.json", "transactions.xml")}
    reference, summary = loaded["transactions.csv"]
    assert summary["invalid_records"] == 0 and summary["rows"] > summary["transactions"] == len(reference)
    assert summary["observations"] == summary["rows"] - summary["duplicates"]
    for name in ("transactions.json", "transactions.xml"):
        records, other = loaded[name]
        assert [record["txid"] for record in records] == [record["txid"] for record in reference]
        assert other["observations"] == summary["observations"]
        assert records[0]["output_amounts"] == reference[0]["output_amounts"] and records[0]["src_ip"] == reference[0]["src_ip"]


def test_normalizer_canonicalizes_records():
    normalized = normalize_records(parse_csv(SYNTHETIC / "transactions.csv")[:5])
    assert normalized[0]["input_addresses"] and normalized[0]["output_addresses"]
    assert validate_record(normalized[0]) == []
    with pytest.raises(ValueError):
        normalize_record({"txid": "x", "timestamp": "not a time", "input_addresses": ["a"], "output_addresses": ["b"]})


def test_summary_reports_metrics():
    summary = summarize_records(normalize_records(parse_json(SYNTHETIC / "transactions.json")[:50]))
    assert summary["total_records"] == 50 and summary["valid_records"] == 50 and summary["invalid_records"] == 0


def test_load_dataset_accepts_unlabeled_records_without_geo_or_asn(minimal_csv_path):
    records, summary = load_dataset(minimal_csv_path, include_summary=True)
    assert summary["invalid_records"] == 0 and len(records) == 1500
    assert all(record["behavior_type"] == "" for record in records)
    assert summary["missing_fields"].get("fee") == summary["rows"]  # absent, so derived from inputs - outputs
    assert all(record["fee"] >= 0 for record in records)


# ── schema flexibility ──────────────────────────────────────────────────────
def test_column_aliases_units_and_endpoints_are_recognised(tmp_path):
    path = tmp_path / "capture.csv"
    path.write_text(
        "Time;Source IP;Destination IP;Tx Hash;Inputs;Outputs;input_values_sat;output_values_sat;fee_sat;Country;AS Number;Label\n"
        "1704067637123;8.8.8.8:51234;1.1.1.1:8333;aaa;addr1|addr2;addr3;60000000|40000000;99990000;10000;in;AS15169;some_label\n",
        encoding="utf-8")
    (record,), summary = load_dataset(path, include_summary=True)
    assert record["txid"] == "aaa" and record["timestamp"] == "2024-01-01T00:07:17.123+00:00"
    assert (record["src_ip"], record["src_port"], record["dst_ip"], record["dst_port"]) == ("8.8.8.8", 51234, "1.1.1.1", 8333)
    assert record["input_addresses"] == ["addr1", "addr2"] and record["input_amounts"] == [0.6, 0.4]
    assert record["output_amounts"] == [0.9999] and record["fee"] == 0.0001
    assert record["geo_country"] == "IN" and record["asn"] == 15169 and record["behavior_type"] == "some_label"
    assert summary["amount_unit"].startswith("satoshi") and summary["column_mapping"]["Tx Hash"] == "txid" and not summary["unmapped_columns"]


def test_satoshi_amounts_are_detected_without_a_column_hint():
    records, summary = build_dataset([row(input_amounts=[100000000], output_amounts=[60000000, 39000000])])
    assert summary["amount_unit"].startswith("satoshi (detected")
    assert records[0]["input_amounts"] == [1.0] and records[0]["output_amounts"] == [0.6, 0.39] and records[0]["fee"] == 0.01


def test_mapping_prefers_exact_names_and_joins_date_and_time():
    mapping = map_columns(["hash", "txid", "date", "time", "note"])
    assert mapping.source["txid"] == "txid" and mapping.source["timestamp"] == "date" and mapping.time_part == "time"
    assert mapping.unmapped == ["hash", "note"]
    result = normalize_row({"date": "2024-01-01", "time": "00:00:03.100", "txid": "t", "input_addresses": "a", "output_addresses": "b",
                            "input_amounts": "1", "output_amounts": "0.9"})
    assert result.record["timestamp"] == "2024-01-01T00:00:03.100+00:00"


@pytest.mark.parametrize("value, expected", [
    ("2024-01-01T00:00:00Z", datetime(2024, 1, 1, tzinfo=timezone.utc)),
    ("2024-01-01 05:30:00+05:30", datetime(2024, 1, 1, tzinfo=timezone.utc)),
    ("2024-01-01 00:00:00", datetime(2024, 1, 1, tzinfo=timezone.utc)),
    (1704067200, datetime(2024, 1, 1, tzinfo=timezone.utc)),
    ("1704067200000", datetime(2024, 1, 1, tzinfo=timezone.utc)),
    ("01/01/2024 00:00:00", datetime(2024, 1, 1, tzinfo=timezone.utc)),
])
def test_timestamps_are_parsed_to_utc(value, expected):
    assert parse_timestamp(value) == expected


def test_unparseable_timestamps_and_lists():
    assert parse_timestamp("yesterday") is None and parse_timestamp("") is None and parse_timestamp(-5) is None
    assert parse_list('["a", "b"]') == ["a", "b"] and parse_list("['a', 'b']") == ["a", "b"]
    assert parse_list("a;b") == ["a", "b"] and parse_list("a|b") == ["a", "b"] and parse_list("0.5, 0.25") == ["0.5", "0.25"]
    assert parse_list("solo") == ["solo"] and parse_list("") == [] and parse_list(None) == [] and parse_list(0.5) == [0.5]


def test_endpoints_and_address_types():
    assert split_endpoint("1.2.3.4:8333") == ("1.2.3.4", 8333) and split_endpoint("[2001:db8::1]:8333") == ("2001:db8::1", 8333)
    assert split_endpoint("2001:db8::1") == ("2001:db8::1", None) and split_endpoint("not-an-ip") == ("", None)
    assert address_type("bc1q" + "q" * 38) == "P2WPKH" and address_type("bc1p" + "q" * 58) == "P2TR"
    assert address_type("1" + "A" * 33) == "P2PKH" and address_type("3" + "A" * 33) == "P2SH" and address_type("wallet_000001") == ""


def test_nested_inputs_and_outputs_carry_their_own_amounts():
    records, _ = build_dataset([{
        "hash": "j1", "time": "2024-01-01T00:00:00Z", "source_ip": "8.8.8.8", "dest_ip": "1.1.1.1", "sport": 1, "dport": 8333,
        "vin": [{"address": "a", "value": 1.0}, {"addr": "b", "value_sat": 50000000}], "vout": [{"address": "c", "value": 1.49}],
    }])
    assert records[0]["input_addresses"] == ["a", "b"] and records[0]["input_amounts"] == [1.0, 0.5]
    assert records[0]["output_amounts"] == [1.49] and records[0]["fee"] == 0.01


# ── tolerance ───────────────────────────────────────────────────────────────
def test_unusable_rows_are_rejected_with_reasons_and_examples():
    records, summary = build_dataset([
        row(), row(txid=""), row(txid="t3", timestamp="garbage"), row(txid="t4", input_addresses=[], output_addresses=[]),
        row(txid="t5", input_amounts=["abc"]), row(txid="t6", output_amounts=[-1.0, 0.5]), "not a record",
    ])
    assert [record["txid"] for record in records] == ["t1"]
    assert summary["invalid_records"] == 6 and summary["total_records"] == 7
    assert set(summary["rejected"]) == {"No transaction id", "Timestamp could not be parsed", "No input or output addresses", "Amounts are not numeric",
                                         "Negative amount or fee", "Row is not a record"}
    assert summary["examples"][0]["row"] == 2 and summary["examples"][0]["problems"] == ["No transaction id"]


def test_repairable_problems_keep_the_transaction():
    records, summary = build_dataset([
        row(txid="bad_ip", src_ip="999.1.1.1", dst_port=99999),
        row(txid="more_out", input_amounts=[1.0], output_amounts=[0.7, 0.7]),
        row(txid="unaligned", output_amounts=[0.9]),
        row(txid="no_inputs", input_amounts=[]),
        row(txid="odd_label", behavior_type="NOT_A_PROFILE"),
    ])
    by_txid = {record["txid"]: record for record in records}
    assert len(records) == 5 and summary["invalid_records"] == 0
    assert by_txid["bad_ip"]["src_ip"] == "" and by_txid["bad_ip"]["dst_port"] == 0
    assert by_txid["more_out"]["fee"] == 0.0
    assert by_txid["unaligned"]["output_amounts"] == [0.45, 0.45]
    assert by_txid["no_inputs"]["input_amounts"] == [0.99] and by_txid["no_inputs"]["fee"] == 0.0
    assert by_txid["odd_label"]["behavior_type"] == "NOT_A_PROFILE"
    assert {"Source IP is not a valid address (kept without it)", "Destination port out of range (set to 0)", "Outputs exceed inputs (kept; fee set to 0)",
            "Output amounts not aligned with output addresses (total split evenly)", "Input amounts missing (reconstructed from outputs + fee)"} <= set(summary["warnings"])


def test_validator_separates_fatal_problems_from_warnings():
    record = normalize_record(row(behavior_type="NOT_A_PROFILE", src_ip="bogus"))
    assert validate_record(record) == []
    warnings = record_warnings(record)
    assert "Missing src_ip" in warnings and any(text.startswith("Unrecognised behavior_type") for text in warnings)
    assert "Missing required field: txid" in validate_record({**record, "txid": ""})
    assert "Amount values cannot be negative" in validate_record({**record, "output_amounts": [-1.0]})


# ── several network observations of one transaction ─────────────────────────
def test_observations_of_one_transaction_are_merged_first_seen_first():
    records, summary = build_dataset([
        row(timestamp="2024-01-01T00:00:05.250Z", src_ip="5.5.5.5", geo_country="DE", asn=64501),
        row(timestamp="2024-01-01T00:00:03.100Z", src_ip="6.6.6.6", geo_country="NL", asn=64502),
        row(timestamp="2024-01-01T00:00:03.100Z", src_ip="6.6.6.6", geo_country="NL", asn=64502),   # exact duplicate
        row(timestamp="2024-01-01T00:00:09Z", src_ip="7.7.7.7", output_addresses=["zzz"], output_amounts=[0.99]),  # conflicting content
        row(txid="t2", timestamp="2024-01-01T00:01:00Z"),
    ])
    assert [record["txid"] for record in records] == ["t1", "t2"]
    first = records[0]
    assert (first["timestamp"], first["src_ip"], first["geo_country"], first["asn"]) == ("2024-01-01T00:00:03.100+00:00", "6.6.6.6", "NL", 64502)
    assert [item["src_ip"] for item in first["observations"]] == ["6.6.6.6", "5.5.5.5", "7.7.7.7"]
    assert first["output_addresses"] == ["b", "c"]  # the first version of the transaction is kept
    assert "observations" not in records[1]
    assert (summary["rows"], summary["transactions"], summary["observations"], summary["duplicates"]) == (5, 2, 4, 1)
    assert summary["multi_observation_transactions"] == 1 and summary["conflicting_rows"] == 1


# ── file formats ────────────────────────────────────────────────────────────
def test_json_wrappers_lines_and_gzip(tmp_path):
    rows = [row(), row(txid="t2", timestamp="2024-01-01T00:01:00Z")]
    variants = {
        "array.json": json.dumps(rows), "wrapped.json": json.dumps({"meta": {"tool": "x"}, "transactions": rows}),
        "other_key.json": json.dumps({"capture": rows}), "lines.jsonl": "\n".join(json.dumps(item) for item in rows), "single.json": json.dumps(rows[0]),
    }
    for name, text in variants.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
        assert [record["txid"] for record in load_dataset(tmp_path / name)] == (["t1"] if name == "single.json" else ["t1", "t2"]), name
    with gzip.open(tmp_path / "packed.json.gz", "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(rows))
    records, summary = load_dataset(tmp_path / "packed.json.gz", include_summary=True)
    assert len(records) == 2 and summary["format"] == "json (gzip)"
    (tmp_path / "broken.json").write_text('[{"txid": "t1"', encoding="utf-8")
    with pytest.raises(ValueError):
        list(read_json(tmp_path / "broken.json"))
    (tmp_path / "scalar.json").write_text("42", encoding="utf-8")
    with pytest.raises(ValueError):
        list(read_json(tmp_path / "scalar.json"))


def test_json_is_streamed_across_chunk_boundaries(tmp_path, monkeypatch):
    from backend.ingestion import readers

    monkeypatch.setattr(readers._JsonStream, "CHUNK", 37)  # far smaller than one record
    rows = [row(txid=f"t{index}", output_amounts=[0.123456789, 0.5]) for index in range(40)]
    path = tmp_path / "big.json"
    path.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    assert [item["txid"] for item in read_json(path)] == [f"t{index}" for index in range(40)]


def test_xml_variants(tmp_path):
    path = tmp_path / "capture.xml"
    path.write_text("""<?xml version='1.0'?><capture><meta><tool>x</tool></meta><records>
      <tx txid="x1" timestamp="2024-01-01 00:00:00" src_ip="8.8.8.8" dst_ip="1.1.1.1" src_port="5" dst_port="8333">
        <input address="a" amount="1.0"/><input address="b" amount="0.5"/><output address="c" amount="1.49"/>
      </tx>
      <tx><txid>x2</txid><timestamp>2024-01-01T00:10:00+00:00</timestamp><src_ip>8.8.8.8</src_ip><dst_ip>1.1.1.1</dst_ip>
        <inputs><input><address>c</address><amount>1.49</amount></input></inputs>
        <outputs><output><address>d</address><amount>1.0</amount></output><output><address>e</address><amount>0.48</amount></output></outputs>
      </tx></records></capture>""", encoding="utf-8")
    first, second = load_dataset(path)
    assert first["input_addresses"] == ["a", "b"] and first["input_amounts"] == [1.0, 0.5] and first["output_amounts"] == [1.49]
    assert second["output_addresses"] == ["d", "e"] and second["output_amounts"] == [1.0, 0.48] and second["fee"] == 0.01
    (tmp_path / "broken.xml").write_text("<transactions><transaction><txid>t1</txid>", encoding="utf-8")
    with pytest.raises(ValueError):
        list(read_xml(tmp_path / "broken.xml"))


def test_csv_delimiters_and_format_detection(tmp_path):
    header = ["timestamp", "src_ip", "dst_ip", "txid", "input_addresses", "output_addresses", "input_amounts", "output_amounts"]
    values = ["2024-01-01T00:00:00Z", "8.8.8.8", "1.1.1.1", "t1", "a", "b", "1.0", "0.9"]
    for name, delimiter in (("tabs.tsv", "\t"), ("pipes.csv", "|"), ("commas.csv", ",")):
        (tmp_path / name).write_text(delimiter.join(header) + "\n" + delimiter.join(values) + "\n\n", encoding="utf-8")
        assert [item["txid"] for item in read_rows(tmp_path / name)] == ["t1"], name
    assert detect_format("x.csv.gz") == "csv" and detect_format("x.NDJSON") == "json" and detect_format("x.xml") == "xml"
    with pytest.raises(ValueError):
        detect_format("x.txt")
    with pytest.raises(FileNotFoundError):
        load_dataset(tmp_path / "missing.csv")
