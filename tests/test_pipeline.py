from __future__ import annotations

import csv
import gzip
import json
import shutil
from pathlib import Path

import pytest

from backend.pipeline.investigation import SNAPSHOT_VERSION, Investigation
from backend.pipeline.orchestrator import AnalysisOrchestrator

SIGNAL_NAMES = ("anomaly", "pattern", "taint", "network")
OUTPUT_FILES = ("analysis_summary.json", "wallet_risk_scores.json", "wallet_features.csv", "wallet_clusters.csv", "investigative_leads.json",
                "transaction_alerts.json", "actor_leads.json", "ip_leads.json", "patterns.json", "leads_wallets.csv", "leads_transactions.csv",
                "leads_actors.csv", "leads_ips.csv", "leads_patterns.csv")


def _run(path, tmp_path, **kwargs):
    return AnalysisOrchestrator().run(str(path), output_dir=str(tmp_path / "out"), persist_graph=False, **kwargs)


@pytest.fixture(scope="module")
def csv_result(small_paths, tmp_path_factory):
    seeds = small_paths["seeds"].read_text().split()
    stages = []
    result = AnalysisOrchestrator().run(str(small_paths["csv"]), output_dir=str(tmp_path_factory.mktemp("out")), persist_graph=False, seed_wallets=seeds,
                                        progress=lambda label, fraction: stages.append((label, fraction)))
    result.progress_log = stages
    return result


def test_pipeline_produces_every_model_output(csv_result):
    overview = csv_result.overview
    stats = overview["stats"]
    assert stats["transactions"] == 1500 and stats["observations"] > 1500
    assert stats["actors"] <= stats["entities"] < stats["wallets"]
    assert overview["patterns"]["peeling_chain"] >= 1 and overview["patterns"]["coinjoin"] >= 1
    assert overview["seeds"] and overview["risk_distribution"]["CRITICAL"] >= len(overview["seeds"])
    families = {model["family"] for model in overview["models"]}
    assert families == {"anomaly detection", "entity clustering", "pattern detection", "network correlation", "risk propagation", "risk scoring"}
    wallets = csv_result.investigation.wallets
    assert {"risk_score", "confidence", "entity_id", "actor_id", "cluster_id", "signal_taint", "contrib_anomaly"} <= set(wallets.columns)
    contributions = wallets[[f"contrib_{name}" for name in SIGNAL_NAMES]].sum(axis=1)
    assert (abs(contributions - wallets["risk_score"]) < 0.05).all()
    transactions = csv_result.investigation.transactions
    assert (abs(transactions[[f"contrib_{name}" for name in SIGNAL_NAMES]].sum(axis=1) - transactions["risk_score"]) < 0.05).all()
    assert sum(overview["actor_risk_distribution"].values()) == stats["actors"]
    assert overview["network"]["attributed_share"] > 0.8 and overview["network"]["relay_nodes"] > 0
    assert overview["entity_links"]["co-spend"] > 0 and any(name.startswith("change") for name in overview["entity_links"])
    assert overview["timeline"] and sum(bucket["transactions"] for bucket in overview["timeline"]) == 1500
    assert overview["geography"]["countries"] and overview["ingestion"]["rows"] == stats["observations"]


def test_progress_is_reported_in_order(csv_result):
    fractions = [fraction for _, fraction in csv_result.progress_log]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0 and len(fractions) >= 8
    assert set(csv_result.stage_seconds) == {"ingest", "enrich", "analyse", "graph", "outputs"}


def test_pipeline_evaluates_against_synthetic_labels(csv_result):
    evaluation = csv_result.evaluation
    assert evaluation["labels_available"] is True
    assert evaluation["transaction_auc"] > 0.7
    detectors = {item["detector"]: item for item in evaluation["detectors"]}
    assert detectors["coinjoin"]["precision"] == 1.0
    assert detectors["peeling_chain"]["recall"] > 0.9


def test_pipeline_writes_output_files(csv_result):
    out = Path(csv_result.output_dir)
    for name in OUTPUT_FILES:
        assert (out / name).exists(), name
    leads = json.loads((out / "investigative_leads.json").read_text())
    assert leads[0]["rank"] == 1 and leads[0]["reasons"]
    with (out / "leads_ips.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows and {"ip", "risk_score", "role", "flagged_wallets"} <= set(rows[0])
    reports = list((out / "reports").glob("wallet_*.md"))
    assert reports and "## Why flagged" in reports[0].read_text(encoding="utf-8")


def test_formats_are_equivalent(small_paths, tmp_path, csv_result):
    for key in ("json", "xml"):
        result = _run(small_paths[key], tmp_path / key)
        assert result.overview["stats"] == csv_result.overview["stats"]
        assert result.overview["patterns"] == csv_result.overview["patterns"]


def test_gzip_input_gives_the_same_analysis(small_paths, tmp_path, csv_result):
    packed = tmp_path / "transactions.csv.gz"
    with small_paths["csv"].open("rb") as source, gzip.open(packed, "wb") as target:
        shutil.copyfileobj(source, target)
    result = _run(packed, tmp_path)
    assert result.overview["stats"] == csv_result.overview["stats"] and result.ingestion_statistics["format"] == "csv (gzip)"


def test_pipeline_is_deterministic(small_paths, tmp_path):
    first = _run(small_paths["csv"], tmp_path / "a").investigation.wallets["risk_score"]
    second = _run(small_paths["csv"], tmp_path / "b").investigation.wallets["risk_score"]
    assert first.equals(second)


def test_pipeline_runs_on_the_minimum_problem_statement_fields(minimal_csv_path, tmp_path):
    result = _run(minimal_csv_path, tmp_path)
    assert result.overview["stats"]["transactions"] == 1500
    assert result.evaluation["labels_available"] is False
    assert result.overview["risk_distribution"] and result.overview["patterns"]["peeling_chain"] >= 1
    assert result.overview["geo_sources"] == {"unresolved": 1500}  # no GeoIP database and no country column


def test_snapshot_restores_the_same_analysis_without_refitting(csv_result):
    investigation = csv_result.investigation
    snapshot = json.loads(json.dumps(investigation.to_snapshot(), default=str))
    assert snapshot["version"] == SNAPSHOT_VERSION
    restored = Investigation.from_snapshot(snapshot)
    assert restored.wallets["risk_score"].tolist() == investigation.wallets["risk_score"].tolist()
    assert restored.transactions["risk_score"].tolist() == investigation.transactions["risk_score"].tolist()
    assert restored.overview["risk_distribution"] == investigation.overview["risk_distribution"]
    lead = investigation.wallet_alerts(1)[0]["wallet_id"]
    assert restored.wallet_detail(lead)["reasons"] == investigation.wallet_detail(lead)["reasons"]
    with pytest.raises(ValueError):
        Investigation.from_snapshot({**snapshot, "version": SNAPSHOT_VERSION - 1})


def test_changing_seeds_only_rescores(csv_result, small_paths):
    investigation = csv_result.investigation
    seeds = small_paths["seeds"].read_text().split()
    anomaly = investigation.wallets["anomaly_percentile"].copy()
    with_seeds = investigation.overview["risk_distribution"]
    assert investigation.rescore([]) == [] and investigation.seeds == []
    assert not investigation.wallets["is_seed"].any()
    without = investigation.overview["risk_distribution"]
    assert without["CRITICAL"] + without["HIGH"] < with_seeds["CRITICAL"] + with_seeds["HIGH"]
    assert without["CRITICAL"] + without["HIGH"] > 0                       # structures are found without any seed
    assert investigation.rescore(seeds + ["not_in_dataset"]) == ["not_in_dataset"]
    assert investigation.overview["risk_distribution"] == with_seeds and investigation.wallets["anomaly_percentile"].equals(anomaly)


def test_pipeline_rejects_missing_empty_and_unsupported_input(tmp_path):
    with pytest.raises(FileNotFoundError):
        _run(tmp_path / "missing.csv", tmp_path)
    empty = tmp_path / "empty.csv"
    empty.write_text("timestamp,txid\n", encoding="utf-8")
    with pytest.raises(ValueError):
        _run(empty, tmp_path)
    other = tmp_path / "data.txt"
    other.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        _run(other, tmp_path)


def test_a_tiny_dataset_still_produces_leads(tmp_path):
    rows = [
        {"timestamp": f"2024-01-01T00:0{index}:00Z", "src_ip": "8.8.8.8", "dst_ip": "1.1.1.1", "src_port": 40000, "dst_port": 8333, "txid": f"t{index}",
         "input_addresses": [f"w{index}"], "output_addresses": [f"w{index + 1}", f"c{index}"], "input_amounts": [1.0], "output_amounts": [0.9, 0.0999]}
        for index in range(6)
    ]
    path = tmp_path / "tiny.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    result = _run(path, tmp_path, seed_wallets=["w0"])
    assert result.overview["stats"]["transactions"] == 6
    top = result.investigation.wallet_alerts(3)
    assert top[0]["wallet_id"] == "w0" and top[0]["is_seed"] and top[1]["signals"]["taint"] > 0
