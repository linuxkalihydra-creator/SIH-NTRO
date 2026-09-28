"""DBSCAN clustering over wallet behavioral features."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

# Heavy-tailed amount, time, count and ratio features are log-compressed before
# scaling so a handful of extreme wallets do not dominate the distance metric.
SKEWED_COLUMNS = (
    "transaction_count",
    "incoming_transaction_count",
    "outgoing_transaction_count",
    "total_received",
    "total_sent",
    "average_received",
    "average_sent",
    "maximum_transaction",
    "amount_variance",
    "unique_counterparties",
    "unique_ips",
    "transactions_per_hour",
    "transactions_per_day",
    "average_time_between_transactions",
    "minimum_time_between_transactions",
    "fan_in_ratio",
    "fan_out_ratio",
    "graph_degree",
    "graph_in_degree",
    "graph_out_degree",
)


def _log_skewed(frame: pd.DataFrame) -> pd.DataFrame:
    transformed = frame.copy()
    for column in SKEWED_COLUMNS:
        if column in transformed.columns:
            transformed[column] = np.log1p(transformed[column].clip(lower=0))
    return transformed


def cluster_wallets(feature_frame: pd.DataFrame, eps: float = 1.25, min_samples: int = 5) -> tuple[Pipeline, pd.DataFrame]:
    """Cluster wallets on log-compressed, standardized behavioral features (wallet_id excluded).

    eps=1.25 was tuned on the standardized synthetic features (1k-10k records).
    """
    numeric_frame = feature_frame.copy()
    wallet_ids = numeric_frame["wallet_id"].copy() if "wallet_id" in numeric_frame.columns else pd.Series([f"wallet_{idx}" for idx in range(len(numeric_frame))], index=numeric_frame.index)
    feature_columns = [col for col in numeric_frame.columns if col != "wallet_id"]
    data = numeric_frame[feature_columns].fillna(0.0)

    pipeline = Pipeline([
        ("log_skewed", FunctionTransformer(_log_skewed)),
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("model", DBSCAN(eps=eps, min_samples=min_samples)),
    ])
    labels = pipeline.fit_predict(data)

    result = pd.DataFrame({
        "wallet_id": wallet_ids.values,
        "cluster_id": labels,
    })
    return pipeline, result


__all__ = ["cluster_wallets"]
