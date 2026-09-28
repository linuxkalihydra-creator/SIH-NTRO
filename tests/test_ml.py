import pandas as pd

from backend.ml.anomaly import train_isolation_forest
from backend.ml.clustering import cluster_wallets
from backend.ml.features import build_wallet_feature_frame
from backend.ml.risk_score import compute_risk_scores


def test_ml_pipeline_runs_on_wallet_features():
    records = [{
        "timestamp": "2024-01-01T00:00:00+00:00",
        "src_ip": "203.0.113.10",
        "dst_ip": "198.51.100.10",
        "src_port": 8333,
        "dst_port": 8333,
        "txid": "tx_1",
        "input_addresses": ["wallet_a"],
        "output_addresses": ["wallet_b"],
        "input_amounts": [1.0],
        "output_amounts": [0.95],
        "fee": 0.05,
        "script_type": "P2WPKH",
        "geo_country": "US",
        "asn": 64512,
        "behavior_type": "NORMAL",
    }, {
        "timestamp": "2024-01-01T00:05:00+00:00",
        "src_ip": "203.0.113.11",
        "dst_ip": "198.51.100.11",
        "src_port": 8333,
        "dst_port": 8333,
        "txid": "tx_2",
        "input_addresses": ["wallet_b"],
        "output_addresses": ["wallet_c"],
        "input_amounts": [0.95],
        "output_amounts": [0.9],
        "fee": 0.05,
        "script_type": "P2WPKH",
        "geo_country": "US",
        "asn": 64512,
        "behavior_type": "NORMAL",
    }, {
        "timestamp": "2024-01-01T00:10:00+00:00",
        "src_ip": "203.0.113.12",
        "dst_ip": "198.51.100.12",
        "src_port": 8333,
        "dst_port": 8333,
        "txid": "tx_3",
        "input_addresses": ["wallet_c"],
        "output_addresses": ["wallet_d"],
        "input_amounts": [0.9],
        "output_amounts": [0.85],
        "fee": 0.05,
        "script_type": "P2WPKH",
        "geo_country": "US",
        "asn": 64512,
        "behavior_type": "NORMAL",
    }]

    features = build_wallet_feature_frame(records)
    model, anomalies = train_isolation_forest(features)
    assert model is not None
    assert set(anomalies.columns) >= {"wallet_id", "anomaly_score", "anomaly_label"}

    _, clusters = cluster_wallets(features)
    assert set(clusters.columns) >= {"wallet_id", "cluster_id"}

    scored = compute_risk_scores(features, anomalies, clusters)
    assert isinstance(scored, pd.DataFrame)
    assert "risk_score" in scored.columns
    assert "risk_level" in scored.columns


def test_clustering_standard_synthetic_dataset_finds_multiple_clusters():
    from backend.ingestion.service import load_dataset

    features = build_wallet_feature_frame(load_dataset("data/synthetic/transactions.csv"))
    _, clusters = cluster_wallets(features)
    non_noise = set(clusters["cluster_id"]) - {-1}
    assert len(non_noise) > 1
    assert clusters["cluster_id"].eq(-1).mean() < 0.5


def test_risk_scores_flag_more_than_one_wallet_above_low():
    from scripts.generate_dataset import generate_dataset
    from backend.ingestion.normalizer import normalize_records

    records, _ = generate_dataset(records=2000, seed=7)
    features = build_wallet_feature_frame(normalize_records(records))
    _, anomalies = train_isolation_forest(features)
    _, clusters = cluster_wallets(features)
    scored = compute_risk_scores(features, anomalies, clusters)
    assert scored["risk_level"].ne("LOW").sum() > 1
    # max()-normalization let one outlier push ~99% of wallets to LOW.
    assert scored["risk_level"].ne("LOW").mean() > 0.05
    assert scored["risk_score"].between(0, 100).all()
