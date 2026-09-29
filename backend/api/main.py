"""FastAPI service for uploaded Bitcoin investigation datasets (see docs/api.md)."""

from __future__ import annotations

import os
import threading
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.graph.neo4j_client import Neo4jClient, Neo4jUnavailableError
from backend.ingestion.dataset_store import DatasetStore
from backend.ingestion.service import load_dataset
from backend.pipeline.investigation import Investigation
from backend.pipeline.orchestrator import AnalysisOrchestrator

app = FastAPI(title="Bitcoin Investigation Platform")
# The UI is served from localhost during development; any local port is accepted.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    dataset_id: str | None = None
    path: str | None = None
    seed_wallets: list[str] = Field(default_factory=list)
    output_dir: str | None = None
    contamination: float = 0.05
    random_state: int = 42


class SeedsRequest(BaseModel):
    seed_wallets: list[str] = Field(default_factory=list)


class IngestRequest(BaseModel):
    path: str


_cache: dict[str, tuple[float, Investigation]] = {}
_cache_lock = threading.Lock()


def _dataset_store() -> DatasetStore:
    store = getattr(app.state, "dataset_store", None)
    if store is None:
        store = DatasetStore(os.getenv("DATASET_STORE_DIR", "data/raw/uploads"))
        app.state.dataset_store = store
    return store


def _metadata(dataset_id: str) -> dict[str, Any]:
    metadata = _dataset_store().get(dataset_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail=f"Dataset not found: {dataset_id}")
    return metadata


def _investigation(dataset_id: str) -> Investigation:
    """The analysed dataset, restored from its snapshot once and then served from memory."""
    _metadata(dataset_id)
    store = _dataset_store()
    path = store.snapshot_path(dataset_id)
    if not path.exists():
        raise HTTPException(status_code=409, detail="Dataset has not been analysed yet")
    modified = path.stat().st_mtime
    with _cache_lock:
        cached = _cache.get(dataset_id)
        if cached and cached[0] == modified:
            return cached[1]
        snapshot = store.load_snapshot(dataset_id)
        try:
            investigation = Investigation.from_snapshot(snapshot or {})
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail=f"Stored analysis is outdated; analyse the dataset again ({exc})") from exc
        _cache[dataset_id] = (modified, investigation)
        return investigation


def _save(dataset_id: str, investigation: Investigation) -> None:
    store = _dataset_store()
    store.save_snapshot(dataset_id, investigation.to_snapshot())
    with _cache_lock:
        _cache[dataset_id] = (store.snapshot_path(dataset_id).stat().st_mtime, investigation)


def _overview(dataset_id: str, investigation: Investigation) -> dict[str, Any]:
    metadata = _metadata(dataset_id)
    return {
        "dataset_id": dataset_id,
        "filename": metadata.get("filename"),
        "analyzed_at": metadata.get("analyzed_at"),
        "processing_seconds": metadata.get("processing_seconds"),
        "graph_available": bool(metadata.get("graph_available", False)),
        **{key: value for key, value in investigation.overview.items() if key not in ("processing_seconds", "graph_available")},
    }


def _not_found(kind: str, identifier: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{kind} not found: {identifier}")


# ── datasets ────────────────────────────────────────────────────────────────
@app.get("/health")
def health() -> dict[str, Any]:
    client = Neo4jClient()
    try:
        client.connect()
        return {"status": "ok", "graph_available": True, "graph_status": "Neo4j connectivity verified"}
    except Neo4jUnavailableError:
        return {"status": "ok", "graph_available": False, "graph_status": "Neo4j unavailable; graphs are served from the stored analysis"}
    finally:
        client.close()


@app.post("/ingest")
def ingest(request: IngestRequest) -> dict[str, Any]:
    """Validate a local dataset file without analysing it."""
    try:
        records, summary = load_dataset(request.path, include_summary=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"records": len(records), "summary": summary}


@app.post("/datasets")
def register_dataset(request: IngestRequest) -> dict[str, Any]:
    try:
        return _dataset_store().register_file(request.path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/datasets/upload", status_code=201)
async def upload_dataset(file: UploadFile = File(...)) -> dict[str, Any]:
    try:
        metadata = _dataset_store().register_upload(file.filename or "", await file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {key: metadata[key] for key in ("dataset_id", "filename", "format", "size_bytes", "status", "analysis_status", "created_at")}


@app.get("/datasets")
def datasets() -> list[dict[str, Any]]:
    return _dataset_store().list()


@app.get("/datasets/{dataset_id}")
def dataset(dataset_id: str) -> dict[str, Any]:
    return _metadata(dataset_id)


@app.post("/analyze")
def analyze(request: AnalyzeRequest) -> dict[str, Any]:
    """Run the full pipeline on a stored dataset (or a local path) and keep the analysis."""
    store = _dataset_store()
    if request.dataset_id:
        metadata = _metadata(request.dataset_id)
    elif request.path:
        try:
            metadata = store.register_file(request.path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    else:
        raise HTTPException(status_code=422, detail="dataset_id is required for analysis")
    dataset_id = metadata["dataset_id"]
    store.update(dataset_id, status="analyzing", analysis_status="running", error_message=None)
    try:
        result = AnalysisOrchestrator(contamination=request.contamination, random_state=request.random_state).run(
            metadata["source_path"], output_dir=request.output_dir or str(store._directory(dataset_id) / "processed"),
            dataset_id=dataset_id, seed_wallets=request.seed_wallets,
        )
    except FileNotFoundError as exc:
        store.update(dataset_id, status="failed", analysis_status="failed", error_message=str(exc))
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        store.update(dataset_id, status="failed", analysis_status="failed", error_message=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - broad guard for runtime failures
        store.update(dataset_id, status="failed", analysis_status="failed", error_message=str(exc))
        raise HTTPException(status_code=500, detail=f"Pipeline failure: {exc}") from exc
    _save(dataset_id, result.investigation)
    store.update(dataset_id, status="ready", analysis_status="completed", record_count=len(result.records), error_message=None,
                 analyzed_at=store._now(), processing_seconds=round(result.processing_duration, 2), graph_available=result.graph_available)
    return {"dataset_id": dataset_id, "status": "completed", "overview": _overview(dataset_id, result.investigation), "warnings": result.warnings}


@app.put("/datasets/{dataset_id}/seeds")
def update_seeds(dataset_id: str, request: SeedsRequest) -> dict[str, Any]:
    """Replace the seed wallets and re-run risk propagation and fusion (the models are not refitted)."""
    investigation = _investigation(dataset_id)
    with _cache_lock:
        unknown = investigation.rescore(request.seed_wallets)
    _save(dataset_id, investigation)
    return {"overview": _overview(dataset_id, investigation), "unknown_seeds": unknown}


# ── overview & leads ────────────────────────────────────────────────────────
@app.get("/overview")
def overview(dataset_id: str = Query(...)) -> dict[str, Any]:
    return _overview(dataset_id, _investigation(dataset_id))


@app.get("/alerts")
def alerts(dataset_id: str = Query(...), limit: int = Query(200, ge=1, le=2000), level: str | None = None) -> list[dict[str, Any]]:
    return _investigation(dataset_id).wallet_alerts(limit, level)


@app.get("/alerts/transactions")
def transaction_alerts(dataset_id: str = Query(...), limit: int = Query(200, ge=1, le=2000), level: str | None = None) -> list[dict[str, Any]]:
    return _investigation(dataset_id).transaction_alerts(limit, level)


@app.get("/patterns")
def patterns(dataset_id: str = Query(...), type: str | None = None) -> list[dict[str, Any]]:
    return _investigation(dataset_id).pattern_list(type)


@app.get("/clusters")
def clusters(dataset_id: str = Query(...)) -> list[dict[str, Any]]:
    return _investigation(dataset_id).clusters()


# ── detail ──────────────────────────────────────────────────────────────────
@app.get("/wallets/{wallet_id}")
def wallet(wallet_id: str, dataset_id: str = Query(...)) -> dict[str, Any]:
    investigation = _investigation(dataset_id)
    if wallet_id not in investigation.wallets.index:
        raise _not_found("Wallet", wallet_id)
    return investigation.wallet_detail(wallet_id)


@app.get("/transactions/{txid}")
def transaction(txid: str, dataset_id: str = Query(...)) -> dict[str, Any]:
    investigation = _investigation(dataset_id)
    if txid not in investigation.transactions.index:
        raise _not_found("Transaction", txid)
    return investigation.transaction_detail(txid)


# ── link analysis ───────────────────────────────────────────────────────────
@app.get("/graph")
def graph(dataset_id: str = Query(...), focus_type: str = Query(..., pattern="^(wallet|transaction|pattern|cluster)$"),
          focus_id: str = Query(...), depth: int = Query(1, ge=1, le=2)) -> dict[str, Any]:
    investigation = _investigation(dataset_id)
    exists = {
        "wallet": lambda: focus_id in investigation.wallets.index,
        "transaction": lambda: focus_id in investigation.transactions.index,
        "pattern": lambda: focus_id in investigation.patterns,
        "cluster": lambda: focus_id.lstrip("-").isdigit() and int(focus_id) in set(investigation.wallets["cluster_id"]),
    }[focus_type]()
    if not exists:
        raise _not_found(focus_type.capitalize(), focus_id)
    if focus_type == "wallet" and _metadata(dataset_id).get("graph_available"):
        client = Neo4jClient()
        try:
            client.connect()
            stored = client.get_wallet_flow_graph(dataset_id, focus_id, depth=depth)
            if stored["nodes"]:
                return investigation.annotate_graph(stored, focus_id)
        except Exception:  # Neo4j down or query failure: the stored analysis has the same graph
            pass
        finally:
            client.close()
    return investigation.graph(focus_type, focus_id, depth=depth)
