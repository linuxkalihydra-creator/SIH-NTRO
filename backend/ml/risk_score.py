"""Risk scoring for wallet entities using synthetic behavioral indicators."""

from __future__ import annotations

from typing import Any

import pandas as pd


def compute_risk_scores(wallet_features: pd.DataFrame, anomaly_scores: pd.DataFrame, cluster_results: pd.DataFrame) -> pd.DataFrame:
    """Combine anomaly and network/transaction signals into a wallet risk score.

    This is intentionally explicit and beginner-friendly; weights are visible and can
    be adjusted in one place later.
    """
    merged = wallet_features.merge(anomaly_scores[["wallet_id", "anomaly_score_norm"]], on="wallet_id", how="left")
    merged = merged.merge(cluster_results[["wallet_id", "cluster_id"]], on="wallet_id", how="left")

    df = merged.copy()
    score_columns = [
        "transaction_count",
        "unique_ips",
        "unique_counterparties",
        "average_time_between_transactions",
        "fan_in_ratio",
        "fan_out_ratio",
        "maximum_transaction",
        "graph_degree",
    ]

    for column in score_columns:
        df[column] = df[column].fillna(0.0)

    df["ml_component"] = df["anomaly_score_norm"].fillna(0.0) * 0.45
    # Percentile rank keeps one extreme wallet from compressing everyone else toward zero.
    df["network_component"] = (df["unique_ips"].rank(pct=True) * 100) * 0.15
    df["transaction_component"] = (df["transaction_count"].rank(pct=True) * 100) * 0.15
    df["temporal_component"] = ((1 / (1 + df["average_time_between_transactions"])) * 100) * 0.10
    df["graph_component"] = (df["graph_degree"].rank(pct=True) * 100) * 0.15
    df["risk_score"] = df["ml_component"] + df["network_component"] + df["transaction_component"] + df["temporal_component"] + df["graph_component"]
    df["risk_score"] = df["risk_score"].clip(0, 100)

    def level_for(score: float) -> str:
        if score >= 80:
            return "CRITICAL"
        if score >= 60:
            return "HIGH"
        if score >= 35:
            return "MEDIUM"
        return "LOW"

    df["risk_level"] = df["risk_score"].apply(level_for)
    return df


__all__ = ["compute_risk_scores"]
