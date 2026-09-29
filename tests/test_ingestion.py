from pathlib import Path

from backend.ingestion.csv_parser import parse_csv
from backend.ingestion.json_parser import parse_json
from backend.ingestion.normalizer import normalize_records
from backend.ingestion.service import load_dataset
from backend.ingestion.validator import summarize_records, validate_record
from backend.ingestion.xml_parser import parse_xml


def test_csv_parser_reads_records():
    records = parse_csv(Path("data/synthetic/transactions.csv"))
    assert len(records) > 0
    assert "txid" in records[0]


def test_json_parser_reads_records():
    records = parse_json(Path("data/synthetic/transactions.json"))
    assert len(records) > 0
    assert "txid" in records[0]


def test_xml_parser_reads_records():
    records = parse_xml(Path("data/synthetic/transactions.xml"))
    assert len(records) > 0
    assert "txid" in records[0]


def test_normalizer_canonicalizes_records():
    raw_records = parse_csv(Path("data/synthetic/transactions.csv"))
    normalized = normalize_records(raw_records[:5])
    assert normalized[0]["input_addresses"]
    assert normalized[0]["output_addresses"]
    assert "timestamp" in normalized[0]


def test_load_dataset_returns_valid_records():
    records = load_dataset(Path("data/synthetic/transactions.csv"))
    assert len(records) > 0
    assert all("txid" in record for record in records)


def test_summary_reports_metrics():
    records = parse_json(Path("data/synthetic/transactions.json"))
    summary = summarize_records(records)
    assert summary["total_records"] > 0
    assert "valid_records" in summary
    assert "invalid_records" in summary


def test_load_dataset_accepts_unlabeled_records_without_geo_or_asn(minimal_csv_path):
    records, summary = load_dataset(minimal_csv_path, include_summary=True)
    assert summary["invalid_records"] == 0
    assert len(records) == 1500
    assert all(record["behavior_type"] == "" for record in records)


def test_validator_still_rejects_unknown_behavior_label_when_present():
    record = normalize_records(parse_csv(Path("data/synthetic/transactions.csv"))[:1])[0]
    assert validate_record(record) == []
    record["behavior_type"] = "NOT_A_PROFILE"
    assert "Unsupported behavior_type" in validate_record(record)
