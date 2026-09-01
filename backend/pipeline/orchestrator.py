"""Central orchestrator for the offline investigation pipeline."""

from __future__ import annotations

import json
import logging
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

from backend.correlation.service import build_correlation_index, get_related_ips
from backend.enrichment.service import enrich_records
from backend.explainability.evidence import summarize_evidence
from backend.explainability.service import build_explanations
from backend.graph.graph_builder import build_transaction_graph
from backend.graph.neo4j_client import Neo4jClient, Neo4jUnavailableError
from backend.ingestion.service import load_dataset
from backend.ml.anomaly import train_isolation_forest
from backend.ml.clustering import cluster_wallets
from backend.ml.features import build_wallet_feature_frame
from backend.ml.risk_score import compute_risk_scores
from backend.pipeline.config import DEFAULT_CONTAMINATION, DEFAULT_EPS, DEFAULT_MIN_SAMPLES, DEFAULT_OUTPUT_DIR, DEFAULT_RANDOM_STATE, SUPPORTED_FORMATS
from backend.pipeline.models import AnalysisResult

logger = logging.getLogger(__name__)


class AnalysisOrchestrator:
    """Coordinate ingestion, enrichment, correlation, ML, risk, and explanations."""

    def __init__(self, contamination: float = DEFAULT_CONTAMINATION, random_state: int = DEFAULT_RANDOM_STATE) -> None:
        self.contamination = contamination
        self.random_state = random_state

    def _graph_status(self, graph_records: list[dict[str, Any]] | None = None) -> tuple[bool, str]:
        client = Neo4jClient()
        try:
            client.connect()
            if not graph_records:
                return True, "Neo4j connectivity verified"
            persisted = client.persist_graph(graph_records)
            if persisted > 0:
                return True, f"Neo4j persistence verified with {persisted} records"
            return False, "Neo4j connected but persistence did not write any graph records"
        except Neo4jUnavailableError:
            return False, "Neo4j unavailable; continuing with local graph-only analysis"
        finally:
            client.close()

    def _build_dataset_statistics(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        total_records = len(records)
        wallets = sorted({wallet for record in records for wallet in record.get("input_addresses", []) + record.get("output_addresses", [])})
        ips = sorted({record.get("src_ip") for record in records} | {record.get("dst_ip") for record in records})
        behaviors = Counter(str(record.get("behavior_type", "UNKNOWN")) for record in records)
        return {
            "total_records": total_records,
            "unique_wallets": len(wallets),
            "unique_ips": len([ip for ip in ips if ip]),
            "behavior_distribution": dict(sorted(behaviors.items())),
        }

    def _build_correlation_statistics(self, correlation_index: dict[str, Any]) -> dict[str, Any]:
        wallet_map = correlation_index.get("wallet_map", {})
        ip_map = correlation_index.get("ip_map", {})
        return {
            "wallet_links": len(wallet_map),
            "ip_links": len(ip_map),
            "max_wallet_related_transactions": max((len(ids) for ids in wallet_map.values()), default=0),
        }

    def _build_graph_statistics(self, graph_records: list[dict[str, Any]]) -> dict[str, Any]:
        node_types = Counter(str(node.get("type", "UNKNOWN")) for node in graph_records)
        return {
            "node_count": len(graph_records),
            "node_types": dict(sorted(node_types.items())),
            "wallet_nodes": node_types.get("Wallet", 0),
            "transaction_nodes": node_types.get("Transaction", 0),
            "ip_nodes": node_types.get("IP", 0),
        }

    def _build_evaluation_report(self, records: list[dict[str, Any]], wallet_features: pd.DataFrame, anomaly_results: pd.DataFrame, cluster_results: pd.DataFrame) -> dict[str, Any]:
        wallet_behavior: dict[str, str] = defaultdict(str)
        for record in records:
            for wallet in record.get("input_addresses", []) + record.get("output_addresses", []):
                wallet_behavior[str(wallet)] = str(record.get("behavior_type", "UNKNOWN"))

        merged = wallet_features.merge(anomaly_results[["wallet_id", "anomaly_label"]], on="wallet_id", how="left")
        merged = merged.merge(cluster_results[["wallet_id", "cluster_id"]], on="wallet_id", how="left")
        merged["expected_normal"] = merged["wallet_id"].map(lambda wallet: wallet_behavior.get(wallet, "UNKNOWN") == "NORMAL")
        merged["is_anomalous"] = merged["anomaly_label"].eq(-1)

        normal_entities = int((merged["expected_normal"]).sum())
        anomalous_entities = int(merged["is_anomalous"].sum())
        false_positives = int(((merged["expected_normal"]) & (merged["is_anomalous"])).sum())
        false_positive_rate = (false_positives / normal_entities * 100.0) if normal_entities else 0.0

        profile_counts = defaultdict(lambda: {"normal": 0, "anomalous": 0})
        for _, row in merged.iterrows():
            wallet = str(row["wallet_id"])
            profile = wallet_behavior.get(wallet, "UNKNOWN")
            if profile == "UNKNOWN":
                continue
            profile_counts[profile]["anomalous" if bool(row["is_anomalous"]) else "normal"] += 1

        behavior_profile_rates = {
            profile: {
                "normal_entities": counts["normal"],
                "anomalous_entities": counts["anomalous"],
                "anomaly_rate": (counts["anomalous"] / max(counts["normal"] + counts["anomalous"], 1)) * 100.0,
            }
            for profile, counts in sorted(profile_counts.items())
        }

        return {
            "synthetic_ground_truth": True,
            "normal_entities": normal_entities,
            "anomalous_entities": anomalous_entities,
            "anomaly_rate_by_profile": behavior_profile_rates,
            "normal_profile_false_positive_rate": round(false_positive_rate, 4),
            "cluster_distribution": dict(sorted(cluster_results["cluster_id"].value_counts().astype(int).to_dict().items())),
        }

    def _write_outputs(self, output_dir: Path, result: AnalysisResult) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        summary = {
            "dataset_statistics": result.dataset_statistics,
            "ingestion_statistics": result.ingestion_statistics,
            "validation_statistics": result.validation_statistics,
            "correlation_statistics": result.correlation_statistics,
            "graph_statistics": result.graph_statistics,
            "processing_duration_seconds": round(result.processing_duration, 6),
            "graph_available": result.graph_available,
            "warnings": result.warnings,
            "errors": result.errors,
            "evaluation": result.evaluation,
        }
        (output_dir / "analysis_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        (output_dir / "wallet_risk_scores.json").write_text(json.dumps(result.risk_scores[["wallet_id", "risk_score", "risk_level"]].to_dict(orient="records"), indent=2), encoding="utf-8")
        result.wallet_features.to_csv(output_dir / "wallet_features.csv", index=False)
        result.cluster_results.to_csv(output_dir / "wallet_clusters.csv", index=False)
        investigative = []
        for wallet_id, evidence in result.evidence.items():
            wallet_row = result.risk_scores[result.risk_scores["wallet_id"] == wallet_id]
            wallet_entry = {
                "wallet_id": wallet_id,
                "risk_score": float(wallet_row["risk_score"].iloc[0]) if not wallet_row.empty else 0.0,
                "risk_level": str(wallet_row["risk_level"].iloc[0]) if not wallet_row.empty else "LOW",
                "evidence": evidence,
            }
            investigative.append(wallet_entry)
        (output_dir / "investigative_leads.json").write_text(json.dumps(investigative, indent=2, default=str), encoding="utf-8")

    def run(self, input_path: str | Path, output_dir: str | None = None, contamination: float | None = None, random_state: int | None = None, dataset_id: str = "legacy") -> AnalysisResult:
        """Execute the full analysis pipeline for one uploaded dataset."""
        start = time.perf_counter()
        logger.info("[1/9] Loading dataset...")
        file_path = Path(input_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Dataset not found: {file_path}")
        if file_path.suffix.lower() not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported dataset format for path: {file_path}")

        records, ingestion_summary = load_dataset(file_path, include_summary=True)
        if not records:
            raise ValueError(f"Input dataset is empty: {file_path}")

        logger.info("[2/9] Validating records...")
        invalid = ingestion_summary.get("invalid_records", 0)
        if invalid and len(records) == 0:
            raise ValueError("No valid records remain after validation.")

        logger.info("[3/9] Normalizing records...")
        enriched_records = enrich_records(records)
        logger.info("[4/9] Building correlations...")
        correlation_index = build_correlation_index(enriched_records)
        logger.info("[5/9] Building graph...")
        graph_records = build_transaction_graph(enriched_records)

        logger.info("[6/9] Engineering wallet features...")
        wallet_features = build_wallet_feature_frame(enriched_records).sort_values("wallet_id").reset_index(drop=True)
        if wallet_features.empty:
            raise ValueError("Wallet feature engineering produced no rows.")

        logger.info("[7/9] Running anomaly detection...")
        model, anomaly_results = train_isolation_forest(
            wallet_features,
            contamination=contamination if contamination is not None else self.contamination,
            random_state=random_state if random_state is not None else self.random_state,
        )
        logger.info("[8/9] Running clustering...")
        _, cluster_results = cluster_wallets(wallet_features, eps=DEFAULT_EPS, min_samples=DEFAULT_MIN_SAMPLES)

        logger.info("[9/9] Generating risk and explanations...")
        risk_scores = compute_risk_scores(wallet_features, anomaly_results, cluster_results)
        explanations = build_explanations(wallet_features, risk_scores[["wallet_id", "risk_score", "risk_level"]])

        evidence: dict[str, Any] = {}
        for _, row in wallet_features.iterrows():
            evidence[str(row["wallet_id"])] = summarize_evidence(row.to_dict())

        graph_available = False
        graph_message = "Neo4j unavailable; continuing with local graph-only analysis"
        client = Neo4jClient()
        try:
            client.connect()
            # Clear previous investigation data for this dataset to ensure isolation
            cleared = client.clear_dataset_graph(dataset_id)
            if cleared > 0:
                logger.info(f"Cleared previous investigation data: {cleared} nodes for dataset {dataset_id}")
            # Persist new dataset investigation data
            persisted = client.persist_graph(graph_records, dataset_id=dataset_id)
            graph_available = persisted > 0
            graph_message = f"Neo4j persistence verified with {persisted} records" if graph_available else "Neo4j connected but persistence did not write any graph records"
        except Neo4jUnavailableError:
            graph_available = False
            graph_message = "Neo4j unavailable; continuing with local graph-only analysis"
        finally:
            client.close()
        warnings = []
        if not graph_available:
            warnings.append(graph_message)

        evaluation = self._build_evaluation_report(enriched_records, wallet_features, anomaly_results, cluster_results)

        final_output_dir = Path(output_dir) if output_dir else Path(DEFAULT_OUTPUT_DIR)
        result = AnalysisResult(
            dataset_statistics=self._build_dataset_statistics(enriched_records),
            ingestion_statistics=ingestion_summary,
            validation_statistics={
                "valid_records": len(records),
                "invalid_records": ingestion_summary.get("invalid_records", 0),
                "duplicate_records": ingestion_summary.get("duplicates", 0),
                "missing_fields": ingestion_summary.get("missing_fields", {}),
            },
            correlation_statistics=self._build_correlation_statistics(correlation_index),
            graph_statistics=self._build_graph_statistics(graph_records),
            wallet_features=wallet_features,
            anomaly_results=anomaly_results,
            cluster_results=cluster_results,
            risk_scores=risk_scores,
            explanations=explanations,
            evidence=evidence,
            processing_duration=time.perf_counter() - start,
            warnings=warnings,
            errors=[],
            graph_available=graph_available,
            evaluation=evaluation,
            output_dir=str(final_output_dir),
            graph_records=graph_records,
            records=enriched_records,
        )
        self._write_outputs(final_output_dir, result)
        logger.info("Analysis completed successfully.")
        logger.info(
            "Transactions: %s | Wallets: %s | IPs: %s | Anomalies: %s | Clusters: %s | High-risk entities: %s | Processing time: %.2fs",
            result.dataset_statistics["total_records"],
            result.dataset_statistics["unique_wallets"],
            result.dataset_statistics["unique_ips"],
            int(result.anomaly_results["anomaly_label"].eq(-1).sum()),
            int(result.cluster_results["cluster_id"].nunique()),
            int((result.risk_scores["risk_score"] >= 60).sum()),
            result.processing_duration,
        )
        return result


__all__ = ["AnalysisOrchestrator"]
