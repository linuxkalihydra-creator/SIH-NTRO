"""Central orchestrator for the offline investigation pipeline."""

from __future__ import annotations

import csv
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

from backend.enrichment.service import enrich_dataset
from backend.graph.neo4j_client import Neo4jClient, Neo4jUnavailableError
from backend.ingestion.readers import is_supported
from backend.ingestion.service import load_dataset
from backend.pipeline.config import DEFAULT_CONTAMINATION, DEFAULT_OUTPUT_DIR, DEFAULT_RANDOM_STATE
from backend.pipeline.investigation import Investigation
from backend.pipeline.models import AnalysisResult

logger = logging.getLogger(__name__)
Progress = Callable[[str, float], None]
EXPORT_KINDS = ("wallets", "transactions", "actors", "ips", "patterns")


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
        return {
            "wallet_nodes": len(investigation.flows.first_seen),
            "transaction_nodes": len(txs),
            "ip_nodes": len(investigation.ips),
            "entity_nodes": len(investigation.clustering.members),
            "actor_nodes": len(investigation.actors.members),
            "country_nodes": len({tx.country for tx in txs if tx.country}),
            "asn_nodes": len({tx.asn for tx in txs if tx.asn}),
            "input_edges": sum(len(tx.inputs) for tx in txs),
            "output_edges": sum(len(tx.outputs) for tx in txs),
            "observation_edges": sum(tx.relay_count for tx in txs),
        }

    def _persist_graph(self, investigation: Investigation, dataset_id: str) -> tuple[bool, str]:
        client = Neo4jClient()
        try:
            client.connect()
            written = client.persist_investigation(investigation, dataset_id=dataset_id)
            if written > 0:
                return True, f"Neo4j persistence verified with {written} nodes and relationships"
            return False, "Neo4j connected but persistence did not write any graph records"
        except Neo4jUnavailableError:
            return False, "Neo4j is not reachable, so the graph was not written to it (optional; everything else is unaffected)"
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
            "stage_seconds": result.stage_seconds,
            "graph_available": result.graph_available,
            "warnings": result.warnings,
        }
        (output_dir / "analysis_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        investigation.risk_scores.to_json(output_dir / "wallet_risk_scores.json", orient="records", indent=1)
        investigation.wallet_features.to_csv(output_dir / "wallet_features.csv", index=False)
        investigation.wallets[["wallet_id", "entity_id", "entity_size", "actor_id", "cluster_id"]].to_csv(output_dir / "wallet_clusters.csv", index=False)
        (output_dir / "investigative_leads.json").write_text(json.dumps(investigation.wallet_alerts(200), indent=1), encoding="utf-8")
        (output_dir / "transaction_alerts.json").write_text(json.dumps(investigation.transaction_alerts(200), indent=1), encoding="utf-8")
        (output_dir / "actor_leads.json").write_text(json.dumps(investigation.actor_alerts(200), indent=1), encoding="utf-8")
        (output_dir / "ip_leads.json").write_text(json.dumps(investigation.ip_alerts(200), indent=1), encoding="utf-8")
        (output_dir / "patterns.json").write_text(json.dumps(investigation.pattern_list(), indent=1), encoding="utf-8")
        for kind in EXPORT_KINDS:  # the same ranked leads as spreadsheets
            header, rows = investigation.export_rows(kind)
            with (output_dir / f"leads_{kind}.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(header)
                writer.writerows(rows)
        reports = output_dir / "reports"
        reports.mkdir(exist_ok=True)
        for alert in investigation.wallet_alerts(40):
            if not alert["is_seed"] and alert["risk_score"] >= 60:
                (reports / f"wallet_{alert['rank']:04d}_{alert['wallet_id'][:16]}.md").write_text(investigation.report_markdown("wallet", alert["wallet_id"]), encoding="utf-8")
        for alert in investigation.ip_alerts(10):
            if alert["risk_score"] >= 60:
                (reports / f"ip_{alert['rank']:04d}_{alert['ip'].replace(':', '-')}.md").write_text(investigation.report_markdown("ip", alert["ip"]), encoding="utf-8")

    def run(self, input_path: str | Path, output_dir: str | None = None, contamination: float | None = None, random_state: int | None = None,
            dataset_id: str = "legacy", seed_wallets: list[str] | None = None, persist_graph: bool = True,
            progress: Progress | None = None) -> AnalysisResult:
        """Execute the full analysis pipeline for one dataset."""
        start = time.perf_counter()
        stages: dict[str, float] = {}

        def report(label: str, fraction: float) -> None:
            if progress is not None:
                progress(label, fraction)

        file_path = Path(input_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Dataset not found: {file_path}")
        if not is_supported(file_path):
            raise ValueError(f"Unsupported dataset format for path: {file_path}")

        logger.info("[1/5] Loading and validating %s", file_path)
        report("Reading and validating the dataset", 0.02)
        mark = time.perf_counter()
        records, ingestion_summary = load_dataset(file_path, include_summary=True)
        stages["ingest"] = time.perf_counter() - mark
        if not records:
            raise ValueError(f"No valid records in dataset: {file_path}")

        logger.info("[2/5] Enriching %d transactions with offline GeoIP/ASN", len(records))
        report("Resolving IPs with the offline GeoIP database", 0.1)
        mark = time.perf_counter()
        enriched, ip_info = enrich_dataset(records, geoip_db_path=self.geoip_db_path, asn_db_path=self.asn_db_path)
        stages["enrich"] = time.perf_counter() - mark

        logger.info("[3/5] Correlating, detecting patterns, clustering entities, running models and propagating risk")
        mark = time.perf_counter()
        investigation = Investigation.build(
            enriched, seeds=seed_wallets or [],
            contamination=contamination if contamination is not None else self.contamination,
            random_state=random_state if random_state is not None else self.random_state,
            ip_info=ip_info, ingestion=ingestion_summary,
            progress=(lambda label, fraction: report(label, 0.12 + 0.73 * fraction)) if progress is not None else None,
        )
        stages["analyse"] = time.perf_counter() - mark

        logger.info("[4/5] Persisting the entity/transaction graph")
        report("Writing the graph to Neo4j", 0.88)
        mark = time.perf_counter()
        graph_available, graph_message = self._persist_graph(investigation, dataset_id) if persist_graph else (False, "Graph persistence skipped")
        stages["graph"] = time.perf_counter() - mark
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
            stage_seconds={name: round(value, 2) for name, value in stages.items()},
        )
        logger.info("[5/5] Writing outputs to %s", result.output_dir)
        report("Writing reports", 0.95)
        mark = time.perf_counter()
        self._write_outputs(Path(result.output_dir), result)
        result.stage_seconds["outputs"] = round(time.perf_counter() - mark, 2)
        stats = investigation.overview["stats"]
        logger.info("Transactions: %s | Wallets: %s | Entities: %s | Actors: %s | Patterns: %s | HIGH+ wallets: %s | %.1fs",
                    stats["transactions"], stats["wallets"], stats["entities"], stats["actors"], investigation.overview["patterns"],
                    investigation.overview["risk_distribution"]["CRITICAL"] + investigation.overview["risk_distribution"]["HIGH"], duration)
        report("Done", 1.0)
        return result


__all__ = ["AnalysisOrchestrator"]
