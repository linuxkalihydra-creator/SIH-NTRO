"""Central orchestrator for the offline investigation pipeline."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from backend.enrichment.service import enrich_records
from backend.graph.graph_builder import build_transaction_graph
from backend.graph.neo4j_client import Neo4jClient, Neo4jUnavailableError
from backend.ingestion.service import load_dataset
from backend.pipeline.config import DEFAULT_CONTAMINATION, DEFAULT_OUTPUT_DIR, DEFAULT_RANDOM_STATE, SUPPORTED_FORMATS
from backend.pipeline.investigation import Investigation
from backend.pipeline.models import AnalysisResult

logger = logging.getLogger(__name__)


class AnalysisOrchestrator:
    """Coordinate ingestion, enrichment, correlation, ML, risk propagation and graph persistence."""

    def __init__(self, contamination: float = DEFAULT_CONTAMINATION, random_state: int = DEFAULT_RANDOM_STATE,
                 geoip_db_path: str | Path | None = None, asn_db_path: str | Path | None = None) -> None:
        self.contamination = contamination
        self.random_state = random_state
        # None resolves to $GEOIP_COUNTRY_DB / $GEOIP_ASN_DB, then data/geoip/*.mmdb.
        self.geoip_db_path = geoip_db_path
        self.asn_db_path = asn_db_path

    @staticmethod
    def _graph_statistics(investigation: Investigation) -> dict[str, Any]:
        txs = investigation.flows.txs
        ips = {tx.src_ip for tx in txs if tx.src_ip}
        return {
            "wallet_nodes": len(investigation.flows.first_seen),
            "transaction_nodes": len(txs),
            "ip_nodes": len(ips),
            "country_nodes": len({tx.country for tx in txs if tx.country}),
            "asn_nodes": len({tx.asn for tx in txs if tx.asn}),
            "input_edges": sum(len(tx.inputs) for tx in txs),
            "output_edges": sum(len(tx.outputs) for tx in txs),
        }

    def _persist_graph(self, records: list[dict[str, Any]], dataset_id: str) -> tuple[bool, str]:
        client = Neo4jClient()
        try:
            client.connect()
            cleared = client.clear_dataset_graph(dataset_id)
            if cleared:
                logger.info("Cleared %d previous graph nodes for dataset %s", cleared, dataset_id)
            persisted = client.persist_graph(build_transaction_graph(records), dataset_id=dataset_id)
            if persisted > 0:
                return True, f"Neo4j persistence verified with {persisted} records"
            return False, "Neo4j connected but persistence did not write any graph records"
        except Neo4jUnavailableError:
            return False, "Neo4j unavailable; the link-analysis graph is served from the stored analysis"
        finally:
            client.close()

    def _write_outputs(self, output_dir: Path, result: AnalysisResult) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        investigation = result.investigation
        summary = {
            "overview": investigation.overview,
            "ingestion_statistics": result.ingestion_statistics,
            "validation_statistics": result.validation_statistics,
            "graph_statistics": result.graph_statistics,
            "processing_duration_seconds": round(result.processing_duration, 3),
            "graph_available": result.graph_available,
            "warnings": result.warnings,
        }
        (output_dir / "analysis_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        investigation.risk_scores.to_json(output_dir / "wallet_risk_scores.json", orient="records", indent=1)
        investigation.wallet_features.to_csv(output_dir / "wallet_features.csv", index=False)
        investigation.wallets[["wallet_id", "entity_id", "entity_size", "cluster_id"]].to_csv(output_dir / "wallet_clusters.csv", index=False)
        (output_dir / "investigative_leads.json").write_text(json.dumps(investigation.wallet_alerts(200), indent=1), encoding="utf-8")
        (output_dir / "transaction_alerts.json").write_text(json.dumps(investigation.transaction_alerts(200), indent=1), encoding="utf-8")
        (output_dir / "patterns.json").write_text(json.dumps(investigation.pattern_list(), indent=1), encoding="utf-8")

    def run(self, input_path: str | Path, output_dir: str | None = None, contamination: float | None = None, random_state: int | None = None,
            dataset_id: str = "legacy", seed_wallets: list[str] | None = None, persist_graph: bool = True) -> AnalysisResult:
        """Execute the full analysis pipeline for one dataset."""
        start = time.perf_counter()
        file_path = Path(input_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Dataset not found: {file_path}")
        if file_path.suffix.lower() not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported dataset format for path: {file_path}")

        logger.info("[1/5] Loading and validating %s", file_path)
        records, ingestion_summary = load_dataset(file_path, include_summary=True)
        if not records:
            raise ValueError(f"No valid records in dataset: {file_path}")

        logger.info("[2/5] Enriching %d records with offline GeoIP/ASN", len(records))
        enriched = enrich_records(records, geoip_db_path=self.geoip_db_path, asn_db_path=self.asn_db_path)

        logger.info("[3/5] Correlating, detecting patterns, clustering entities, running models and propagating risk")
        investigation = Investigation.build(
            enriched, seeds=seed_wallets or [],
            contamination=contamination if contamination is not None else self.contamination,
            random_state=random_state if random_state is not None else self.random_state,
        )

        logger.info("[4/5] Persisting the entity/transaction graph")
        graph_available, graph_message = self._persist_graph(enriched, dataset_id) if persist_graph else (False, "Graph persistence skipped")
        warnings = [] if graph_available else [graph_message]

        duration = time.perf_counter() - start
        investigation.overview["processing_seconds"] = round(duration, 2)
        investigation.overview["graph_available"] = graph_available
        result = AnalysisResult(
            investigation=investigation,
            ingestion_statistics=ingestion_summary,
            validation_statistics={
                "valid_records": len(records),
                "invalid_records": ingestion_summary.get("invalid_records", 0),
                "duplicate_records": ingestion_summary.get("duplicates", 0),
                "missing_fields": ingestion_summary.get("missing_fields", {}),
            },
            graph_statistics=self._graph_statistics(investigation),
            processing_duration=duration,
            warnings=warnings,
            graph_available=graph_available,
            output_dir=str(Path(output_dir) if output_dir else Path(DEFAULT_OUTPUT_DIR)),
        )
        logger.info("[5/5] Writing outputs to %s", result.output_dir)
        self._write_outputs(Path(result.output_dir), result)
        stats = investigation.overview["stats"]
        logger.info("Transactions: %s | Wallets: %s | Entities: %s | Patterns: %s | HIGH+ wallets: %s | %.1fs",
                    stats["transactions"], stats["wallets"], stats["entities"], investigation.overview["patterns"],
                    investigation.overview["risk_distribution"]["CRITICAL"] + investigation.overview["risk_distribution"]["HIGH"], duration)
        return result


__all__ = ["AnalysisOrchestrator"]
