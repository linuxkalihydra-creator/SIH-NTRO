#!/usr/bin/env python3
"""Register and analyse a dataset in the dashboard's store, exactly as an upload through the UI would.

Used by ``./run.sh --demo`` so the dashboard opens on finished results:

    uv run python scripts/load_demo.py                                   # the committed synthetic capture + its seeds
    uv run python scripts/load_demo.py --input my_capture.csv.gz --seeds known_bad.txt
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.ingestion.dataset_store import DatasetStore  # noqa: E402
from backend.pipeline.orchestrator import AnalysisOrchestrator  # noqa: E402
from scripts.run_analysis import read_seeds  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", default="data/synthetic/transactions.csv")
    parser.add_argument("--seeds", default="data/synthetic/seed_wallets.txt")
    parser.add_argument("--neo4j", action="store_true", help="Also write the graph to Neo4j.")
    parser.add_argument("--force", action="store_true", help="Analyse again even if this file is already in the store.")
    args = parser.parse_args()

    store = DatasetStore(os.getenv("DATASET_STORE_DIR", "data/raw/uploads"))
    source = Path(args.input)
    if not args.force:
        for known in store.list():
            if known.get("filename") == source.name and known.get("analysis_status") == "completed" and known.get("size_bytes") == source.stat().st_size:
                print(f"{source.name} is already analysed as {known['dataset_id']}; use --force to analyse it again.")
                return
    metadata = store.register_file(source)
    dataset_id = metadata["dataset_id"]
    seeds = read_seeds(args.seeds) if args.seeds and Path(args.seeds).exists() else []
    store.update(dataset_id, status="analyzing", analysis_status="running")
    try:
        result = AnalysisOrchestrator().run(metadata["source_path"], output_dir=str(store.directory(dataset_id) / "processed"), dataset_id=dataset_id,
                                            seed_wallets=seeds, persist_graph=args.neo4j)
    except Exception as exc:
        store.update(dataset_id, status="failed", analysis_status="failed", error_message=str(exc))
        raise
    store.save_snapshot(dataset_id, result.investigation.to_snapshot())
    store.update(dataset_id, status="ready", analysis_status="completed", record_count=len(result.records), error_message=None, analyzed_at=store._now(),
                 processing_seconds=round(result.processing_duration, 2), stage_seconds=result.stage_seconds, graph_available=result.graph_available,
                 rows=result.ingestion_statistics.get("rows"))
    risk = result.overview["risk_distribution"]
    print(f"Loaded {source.name} as {dataset_id}: {len(result.records):,} transactions, {risk['CRITICAL'] + risk['HIGH']} wallets at HIGH or above, "
          f"{len(seeds)} seeds, {result.processing_duration:.1f}s.")


if __name__ == "__main__":
    main()
