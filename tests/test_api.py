from fastapi.testclient import TestClient

from backend.api.main import app


client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ingest_endpoint_with_synthetic_csv():
    response = client.post("/ingest", json={"path": "data/synthetic/transactions.csv"})
    assert response.status_code == 200
    assert response.json()["records_loaded"] > 0


def test_analyze_endpoint():
    response = client.post("/analyze", json={"path": "data/synthetic/transactions.csv"})
    assert response.status_code == 200
    assert response.json()["dataset_statistics"]["total_records"] > 0
    assert "wallet_risk_scores" in response.json()


def test_stats_requires_an_uploaded_dataset():
    response = client.get("/stats")
    assert response.status_code == 422


def test_alerts_requires_an_uploaded_dataset():
    response = client.get("/alerts")
    assert response.status_code == 422


def test_alerts_for_wallet_requires_an_uploaded_dataset():
    response = client.get("/alerts/does-not-exist")
    assert response.status_code == 422


def test_entity_evidence_requires_an_uploaded_dataset():
    response = client.get("/entities/unknown-wallet/evidence")
    assert response.status_code == 422


def _snapshot_from_ml_pipeline():
    from backend.explainability.service import build_explanations
    from backend.ingestion.normalizer import normalize_records
    from backend.ml.anomaly import train_isolation_forest
    from backend.ml.clustering import cluster_wallets
    from backend.ml.features import build_wallet_feature_frame
    from backend.ml.risk_score import compute_risk_scores
    from scripts.generate_dataset import generate_dataset

    records, _ = generate_dataset(records=1500, seed=11)
    features = build_wallet_feature_frame(normalize_records(records))
    _, anomalies = train_isolation_forest(features)
    _, clusters = cluster_wallets(features)
    risks = compute_risk_scores(features, anomalies, clusters)[["wallet_id", "risk_score", "risk_level"]]
    explanations = build_explanations(features, risks)
    return {
        "wallet_risk_scores": risks.to_dict(orient="records"),
        "cluster_results": clusters.to_dict(orient="records"),
        "anomaly_results": anomalies.to_dict(orient="records"),
        "explanations": explanations.to_dict(orient="records"),
    }


def test_alert_confidence_comes_from_anomaly_score_and_reasons_are_objects():
    from backend.api.main import _alerts_from_snapshot

    snapshot = _snapshot_from_ml_pipeline()
    alerts = _alerts_from_snapshot(snapshot)
    assert alerts
    assert [alert["risk_score"] for alert in alerts] == sorted((alert["risk_score"] for alert in alerts), reverse=True)

    anomaly_norm = {row["wallet_id"]: row["anomaly_score_norm"] for row in snapshot["anomaly_results"]}
    ranked = sorted(anomaly_norm, key=anomaly_norm.get)
    for alert in alerts:
        # Confidence is the wallet's anomaly_score_norm percentile, not risk_score / 100.
        assert 0.0 < alert["confidence"] <= 1.0
        assert alert["confidence"] != round(alert["risk_score"] / 100, 3)
        assert abs(alert["confidence"] - (ranked.index(alert["wallet_id"]) + 1) / len(ranked)) < 0.01
        assert alert["top_reasons"]
        for reason in alert["top_reasons"]:
            assert set(reason) == {"label", "confidence", "evidence", "investigative_lead"}
            assert reason["label"] and reason["evidence"] and reason["investigative_lead"]
            assert isinstance(reason["confidence"], float)
            assert reason["confidence"] != alert["confidence"]
