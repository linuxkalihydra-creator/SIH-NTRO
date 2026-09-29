from __future__ import annotations

import json

import pytest

from backend.pipeline.orchestrator import AnalysisOrchestrator


def _run(path, tmp_path, **kwargs):
    return AnalysisOrchestrator().run(str(path), output_dir=str(tmp_path / "out"), persist_graph=False, **kwargs)


@pytest.fixture(scope="module")
def csv_result(small_paths, tmp_path_factory):
    seeds = small_paths["seeds"].read_text().split()
    return AnalysisOrchestrator().run(str(small_paths["csv"]), output_dir=str(tmp_path_factory.mktemp("out")), persist_graph=False, seed_wallets=seeds)


def test_pipeline_produces_every_model_output(csv_result):
    overview = csv_result.overview
    assert overview["stats"]["transactions"] == 1500
    assert overview["stats"]["entities"] < overview["stats"]["wallets"]
    assert overview["patterns"]["peeling_chain"] >= 1 and overview["patterns"]["coinjoin"] >= 1
    assert overview["seeds"] and overview["risk_distribution"]["CRITICAL"] >= len(overview["seeds"])
    families = {model["family"] for model in overview["models"]}
    assert families == {"anomaly detection", "entity clustering", "pattern detection", "risk propagation", "risk scoring"}
    wallets = csv_result.investigation.wallets
    assert {"risk_score", "confidence", "entity_id", "cluster_id", "signal_taint", "contrib_anomaly"} <= set(wallets.columns)
    contributions = wallets[[f"contrib_{name}" for name in ("anomaly", "pattern", "taint", "network")]].sum(axis=1)
    assert (abs(contributions - wallets["risk_score"]) < 0.05).all()


def test_pipeline_evaluates_against_synthetic_labels(csv_result):
    evaluation = csv_result.evaluation
    assert evaluation["labels_available"] is True
    assert evaluation["transaction_auc"] > 0.7
    detectors = {item["detector"]: item for item in evaluation["detectors"]}
    assert detectors["coinjoin"]["precision"] == 1.0
    assert detectors["peeling_chain"]["recall"] > 0.9


def test_pipeline_writes_output_files(csv_result):
    out = csv_result.output_dir
    for name in ("analysis_summary.json", "wallet_risk_scores.json", "wallet_features.csv", "wallet_clusters.csv", "investigative_leads.json", "transaction_alerts.json", "patterns.json"):
        assert (__import__("pathlib").Path(out) / name).exists(), name
    leads = json.loads((__import__("pathlib").Path(out) / "investigative_leads.json").read_text())
    assert leads[0]["rank"] == 1 and leads[0]["reasons"]


def test_formats_are_equivalent(small_paths, tmp_path, csv_result):
    for key in ("json", "xml"):
        result = _run(small_paths[key], tmp_path / key)
        assert result.overview["stats"] == csv_result.overview["stats"]
        assert result.overview["patterns"] == csv_result.overview["patterns"]


def test_pipeline_is_deterministic(small_paths, tmp_path):
    first = _run(small_paths["csv"], tmp_path / "a").investigation.wallets["risk_score"]
    second = _run(small_paths["csv"], tmp_path / "b").investigation.wallets["risk_score"]
    assert first.equals(second)


def test_pipeline_runs_on_the_minimum_problem_statement_fields(minimal_csv_path, tmp_path):
    result = _run(minimal_csv_path, tmp_path)
    assert result.overview["stats"]["transactions"] == 1500
    assert result.evaluation["labels_available"] is False
    assert result.overview["risk_distribution"]


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
