from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from backend.pipeline.orchestrator import AnalysisOrchestrator


@pytest.fixture
def synthetic_csv_path() -> Path:
    return Path("data/synthetic/transactions.csv")


@pytest.fixture
def synthetic_json_path() -> Path:
    return Path("data/synthetic/transactions.json")


@pytest.fixture
def synthetic_xml_path() -> Path:
    return Path("data/synthetic/transactions.xml")


def test_pipeline_processes_csv(synthetic_csv_path: Path):
    result = AnalysisOrchestrator().run(str(synthetic_csv_path), random_state=42)
    assert result.dataset_statistics["total_records"] > 0
    assert not result.wallet_features.empty
    assert not result.anomaly_results.empty
    assert not result.cluster_results.empty
    assert not result.risk_scores.empty
    assert not result.explanations.empty


def test_pipeline_processes_json(synthetic_json_path: Path):
    result = AnalysisOrchestrator().run(str(synthetic_json_path), random_state=42)
    assert result.dataset_statistics["total_records"] > 0
    assert not result.wallet_features.empty


def test_pipeline_processes_xml(synthetic_xml_path: Path):
    result = AnalysisOrchestrator().run(str(synthetic_xml_path), random_state=42)
    assert result.dataset_statistics["total_records"] > 0
    assert not result.wallet_features.empty


def test_pipeline_writes_output_files(tmp_path):
    output_dir = tmp_path / "analysis"
    result = AnalysisOrchestrator().run("data/synthetic/transactions.csv", output_dir=str(output_dir), random_state=42)
    assert output_dir.exists()
    assert (output_dir / "analysis_summary.json").exists()
    assert (output_dir / "wallet_risk_scores.json").exists()
    assert (output_dir / "wallet_features.csv").exists()
    assert (output_dir / "wallet_clusters.csv").exists()
    assert (output_dir / "investigative_leads.json").exists()
    assert result.output_dir == str(output_dir)


def test_pipeline_handles_invalid_input(tmp_path):
    invalid_path = tmp_path / "invalid.csv"
    invalid_path.write_text("timestamp,src_ip\n2024-01-01,invalid\n")

    with pytest.raises(ValueError, match="invalid|empty|record"):
        AnalysisOrchestrator().run(str(invalid_path), random_state=42)


def test_pipeline_handles_empty_input(tmp_path):
    empty_path = tmp_path / "empty.json"
    empty_path.write_text(json.dumps([]))

    with pytest.raises(ValueError, match="empty"):
        AnalysisOrchestrator().run(str(empty_path), random_state=42)


def test_pipeline_deterministic_with_seed(synthetic_csv_path: Path):
    result_a = AnalysisOrchestrator().run(str(synthetic_csv_path), random_state=42)
    result_b = AnalysisOrchestrator().run(str(synthetic_csv_path), random_state=42)
    pd.testing.assert_frame_equal(result_a.wallet_features, result_b.wallet_features)
    pd.testing.assert_frame_equal(result_a.risk_scores, result_b.risk_scores)
    assert result_a.anomaly_results.equals(result_b.anomaly_results)
    assert result_a.cluster_results.equals(result_b.cluster_results)


def test_pipeline_runs_on_unlabeled_dataset_and_skips_label_metrics(minimal_csv_path, tmp_path):
    result = AnalysisOrchestrator().run(str(minimal_csv_path), output_dir=str(tmp_path / "out"), random_state=42)
    assert result.dataset_statistics["total_records"] == 300
    assert result.dataset_statistics["labels_available"] is False
    assert result.dataset_statistics["behavior_distribution"] == {}
    assert result.evaluation["labels_available"] is False
    assert "anomaly_rate_by_profile" not in result.evaluation
    assert not result.risk_scores.empty
