"""FastAPI service for uploaded Bitcoin investigation datasets (see docs/api.md).

The same routes are served at the root and under ``/api``. When the dashboard has
been built (``frontend/dist``), it is served from ``/`` by this process, so the
whole platform is one offline Python service with no Node.js at run time.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import FileResponse, PlainTextResponse, Response
from starlette.staticfiles import StaticFiles

from backend.graph.neo4j_client import Neo4jClient, Neo4jUnavailableError
from backend.ingestion.dataset_store import DatasetStore
from backend.ingestion.service import load_dataset
from backend.pipeline.investigation import Investigation
from backend.pipeline.orchestrator import EXPORT_KINDS, AnalysisOrchestrator

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FOCUS_TYPES = "wallet|transaction|pattern|cluster|actor|ip"

app = FastAPI(title="Bitcoin Investigation Platform", version="1.0.0",
              description="Offline monitoring and analysis of Bitcoin transaction traffic: ingestion, network/blockchain correlation, ML detection and explainable leads.")
# The UI is served from this process or from localhost during development; any local port is accepted.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Lead lists, graphs and exports are repetitive JSON/CSV: compressing them cuts the payload about tenfold.
app.add_middleware(GZipMiddleware, minimum_size=2048)
router = APIRouter()


class AnalyzeRequest(BaseModel):
    dataset_id: str | None = None
    path: str | None = None
    seed_wallets: list[str] = Field(default_factory=list)
    output_dir: str | None = None
    contamination: float = 0.05
    random_state: int = 42
    wait: bool = True  # False: start in the background and poll /datasets/{id}/progress


class SeedsRequest(BaseModel):
    seed_wallets: list[str] = Field(default_factory=list)


class IngestRequest(BaseModel):
    path: str


_cache: dict[str, tuple[float, Investigation]] = {}
_cache_lock = threading.RLock()
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


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
        seeds = store.load_seeds(dataset_id)
        if snapshot and seeds is not None:  # seeds changed since the analysis; from_snapshot re-scores with them
            snapshot["seeds"] = seeds
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
        "stage_seconds": metadata.get("stage_seconds"),
        "graph_available": bool(metadata.get("graph_available", False)),
        **{key: value for key, value in investigation.overview.items() if key not in ("processing_seconds", "graph_available")},
    }


def _not_found(kind: str, identifier: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{kind} not found: {identifier}")


# ── datasets ────────────────────────────────────────────────────────────────
@router.get("/health")
def health() -> dict[str, Any]:
    client = Neo4jClient()
    try:
        client.connect()
        return {"status": "ok", "graph_available": True, "graph_status": "Neo4j connectivity verified"}
    except Neo4jUnavailableError:
        return {"status": "ok", "graph_available": False, "graph_status": "Neo4j is not reachable (optional): analysis, leads and link graphs work without it"}
    finally:
        client.close()


@router.post("/ingest")
def ingest(request: IngestRequest) -> dict[str, Any]:
    """Validate a local dataset file without analysing it; returns the data-quality report."""
    try:
        records, summary = load_dataset(request.path, include_summary=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"records": len(records), "summary": summary}


@router.post("/datasets")
def register_dataset(request: IngestRequest) -> dict[str, Any]:
    try:
        return _dataset_store().register_file(request.path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/datasets/upload", status_code=201)
def upload_dataset(file: UploadFile = File(...)) -> dict[str, Any]:
    try:
        metadata = _dataset_store().register_upload(file.filename or "", file.file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {key: metadata[key] for key in ("dataset_id", "filename", "format", "size_bytes", "status", "analysis_status", "created_at")}


@router.get("/datasets")
def datasets() -> list[dict[str, Any]]:
    return _dataset_store().list()


@router.get("/datasets/{dataset_id}")
def dataset(dataset_id: str) -> dict[str, Any]:
    return _metadata(dataset_id)


@router.delete("/datasets/{dataset_id}")
def delete_dataset(dataset_id: str) -> dict[str, Any]:
    """Remove a dataset with its stored analysis, outputs and (if reachable) its Neo4j graph."""
    _metadata(dataset_id)
    with _jobs_lock:
        if _jobs.get(dataset_id, {}).get("status") == "running":
            raise HTTPException(status_code=409, detail="The dataset is being analysed; wait for it to finish.")
    with _cache_lock:
        _cache.pop(dataset_id, None)
    _dataset_store().delete(dataset_id)
    client = Neo4jClient()
    try:
        client.connect()
        client.clear_dataset_graph(dataset_id)
    except Exception:  # Neo4j is optional
        pass
    finally:
        client.close()
    return {"dataset_id": dataset_id, "deleted": True}


def _run_analysis(dataset_id: str, metadata: dict[str, Any], request: AnalyzeRequest) -> dict[str, Any]:
    """Run the pipeline, store the result and keep the progress record up to date."""
    store = _dataset_store()
    started = time.time()

    def progress(stage: str, fraction: float) -> None:
        with _jobs_lock:
            _jobs[dataset_id] = {"status": "running", "stage": stage, "fraction": round(float(fraction), 3), "started_at": started, "error": None}

    progress("Starting", 0.0)
    store.update(dataset_id, status="analyzing", analysis_status="running", error_message=None)
    try:
        result = AnalysisOrchestrator(contamination=request.contamination, random_state=request.random_state).run(
            metadata["source_path"], output_dir=request.output_dir or str(store.directory(dataset_id) / "processed"),
            dataset_id=dataset_id, seed_wallets=request.seed_wallets, progress=progress,
        )
        progress("Saving the analysis", 0.98)
        _save(dataset_id, result.investigation)
        store.update(dataset_id, status="ready", analysis_status="completed", record_count=len(result.records), error_message=None,
                     analyzed_at=store._now(), processing_seconds=round(result.processing_duration, 2), stage_seconds=result.stage_seconds,
                     graph_available=result.graph_available, rows=result.ingestion_statistics.get("rows"))
    except Exception as exc:
        store.update(dataset_id, status="failed", analysis_status="failed", error_message=str(exc))
        with _jobs_lock:
            _jobs[dataset_id] = {"status": "failed", "stage": "Failed", "fraction": 0.0, "started_at": started, "error": str(exc)}
        raise
    with _jobs_lock:
        _jobs[dataset_id] = {"status": "completed", "stage": "Done", "fraction": 1.0, "started_at": started, "error": None,
                             "seconds": round(time.time() - started, 1)}
    return {"dataset_id": dataset_id, "status": "completed", "overview": _overview(dataset_id, result.investigation), "warnings": result.warnings}


@router.post("/analyze")
def analyze(request: AnalyzeRequest) -> dict[str, Any]:
    """Run the full pipeline on a stored dataset (or a local path) and keep the analysis.

    With ``wait: false`` the analysis runs in the background; poll ``GET /datasets/{id}/progress``.
    """
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
    with _jobs_lock:
        if _jobs.get(dataset_id, {}).get("status") == "running":
            raise HTTPException(status_code=409, detail="This dataset is already being analysed.")
        _jobs[dataset_id] = {"status": "running", "stage": "Queued", "fraction": 0.0, "started_at": time.time(), "error": None}
    if not request.wait:
        def worker() -> None:
            try:
                _run_analysis(dataset_id, metadata, request)
            except Exception:  # recorded in the progress record and the dataset metadata
                logger.exception("Background analysis of %s failed", dataset_id)

        threading.Thread(target=worker, name=f"analysis-{dataset_id}", daemon=True).start()
        return {"dataset_id": dataset_id, "status": "running"}
    try:
        return _run_analysis(dataset_id, metadata, request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - broad guard for runtime failures
        raise HTTPException(status_code=500, detail=f"Pipeline failure: {exc}") from exc


@router.get("/datasets/{dataset_id}/progress")
def progress(dataset_id: str) -> dict[str, Any]:
    metadata = _metadata(dataset_id)
    with _jobs_lock:
        job = dict(_jobs.get(dataset_id) or {})
    if not job:
        status = {"completed": "completed", "failed": "failed"}.get(metadata.get("analysis_status"), "idle")
        return {"dataset_id": dataset_id, "status": status, "stage": "", "fraction": 1.0 if status == "completed" else 0.0, "elapsed_seconds": 0.0,
                "error": metadata.get("error_message")}
    return {"dataset_id": dataset_id, "status": job["status"], "stage": job["stage"], "fraction": job["fraction"],
            "elapsed_seconds": job.get("seconds", round(time.time() - job["started_at"], 1)), "error": job.get("error")}


def _refresh_graph_scores(dataset_id: str, rows: dict[str, list[dict[str, Any]]]) -> None:
    """Bring the Neo4j copy of the scores in line with a re-score, without holding up the request."""
    client = Neo4jClient()
    try:
        client.connect()
        client.update_scores(dataset_id, rows)
    except Exception as exc:  # Neo4j is optional; the stored analysis is the source of truth
        logger.warning("Scores of %s were not refreshed in Neo4j: %s", dataset_id, exc)
    finally:
        client.close()


@router.put("/datasets/{dataset_id}/seeds")
def update_seeds(dataset_id: str, request: SeedsRequest) -> dict[str, Any]:
    """Replace the seed wallets and re-run risk propagation and fusion (the models are not refitted)."""
    investigation = _investigation(dataset_id)
    with _cache_lock:
        unknown = investigation.rescore(request.seed_wallets)
        rows = Neo4jClient.score_rows(investigation) if _metadata(dataset_id).get("graph_available") else None
    _dataset_store().save_seeds(dataset_id, investigation.seeds)  # the stored analysis is re-scored with them on load
    if rows is not None:
        threading.Thread(target=_refresh_graph_scores, args=(dataset_id, rows), name=f"graph-scores-{dataset_id}", daemon=True).start()
    return {"overview": _overview(dataset_id, investigation), "unknown_seeds": unknown}


# ── overview & leads ────────────────────────────────────────────────────────
@router.get("/overview")
def overview(dataset_id: str = Query(...)) -> dict[str, Any]:
    return _overview(dataset_id, _investigation(dataset_id))


@router.get("/ingestion")
def ingestion_report(dataset_id: str = Query(...)) -> dict[str, Any]:
    """Data-quality report of the last analysis: rows read, rejected, repaired, column mapping."""
    return _investigation(dataset_id).ingestion


@router.get("/alerts")
def alerts(dataset_id: str = Query(...), limit: int = Query(200, ge=1, le=5000), level: str | None = None) -> list[dict[str, Any]]:
    return _investigation(dataset_id).wallet_alerts(limit, level)


@router.get("/alerts/transactions")
def transaction_alerts(dataset_id: str = Query(...), limit: int = Query(200, ge=1, le=5000), level: str | None = None) -> list[dict[str, Any]]:
    return _investigation(dataset_id).transaction_alerts(limit, level)


@router.get("/alerts/actors")
def actor_alerts(dataset_id: str = Query(...), limit: int = Query(200, ge=1, le=5000), level: str | None = None) -> list[dict[str, Any]]:
    return _investigation(dataset_id).actor_alerts(limit, level)


@router.get("/alerts/ips")
def ip_alerts(dataset_id: str = Query(...), limit: int = Query(200, ge=1, le=5000), level: str | None = None,
              include_relays: bool = False) -> list[dict[str, Any]]:
    return _investigation(dataset_id).ip_alerts(limit, level, include_relays)


@router.get("/patterns")
def patterns(dataset_id: str = Query(...), type: str | None = None) -> list[dict[str, Any]]:
    return _investigation(dataset_id).pattern_list(type)


@router.get("/clusters")
def clusters(dataset_id: str = Query(...)) -> list[dict[str, Any]]:
    return _investigation(dataset_id).clusters()


@router.get("/search")
def search(dataset_id: str = Query(...), q: str = Query(..., min_length=2), limit: int = Query(20, ge=1, le=100)) -> list[dict[str, Any]]:
    """Find wallets, transactions, IPs, actors and patterns by (part of) their identifier."""
    return _investigation(dataset_id).search(q, limit)


# ── detail ──────────────────────────────────────────────────────────────────
@router.get("/wallets/{wallet_id}")
def wallet(wallet_id: str, dataset_id: str = Query(...)) -> dict[str, Any]:
    investigation = _investigation(dataset_id)
    if wallet_id not in investigation.wallets.index:
        raise _not_found("Wallet", wallet_id)
    return investigation.wallet_detail(wallet_id)


@router.get("/transactions/{txid}")
def transaction(txid: str, dataset_id: str = Query(...)) -> dict[str, Any]:
    investigation = _investigation(dataset_id)
    if txid not in investigation.transactions.index:
        raise _not_found("Transaction", txid)
    return investigation.transaction_detail(txid)


@router.get("/actors/{actor_id}")
def actor(actor_id: str, dataset_id: str = Query(...)) -> dict[str, Any]:
    investigation = _investigation(dataset_id)
    if actor_id not in investigation.actor_frame.index:
        raise _not_found("Actor", actor_id)
    return investigation.actor_detail(actor_id)


@router.get("/ips/{ip:path}")
def ip_address_detail(ip: str, dataset_id: str = Query(...)) -> dict[str, Any]:
    investigation = _investigation(dataset_id)
    if ip not in investigation.ips.index:
        raise _not_found("IP", ip)
    return investigation.ip_detail(ip)


def _exists(investigation: Investigation, kind: str, identifier: str) -> bool:
    return {
        "wallet": lambda: identifier in investigation.wallets.index,
        "transaction": lambda: identifier in investigation.transactions.index,
        "pattern": lambda: identifier in investigation.patterns,
        "cluster": lambda: identifier.lstrip("-").isdigit() and int(identifier) in set(investigation.wallets["cluster_id"]),
        "actor": lambda: identifier in investigation.actor_frame.index,
        "ip": lambda: identifier in investigation.ips.index,
    }[kind]()


# ── link analysis ───────────────────────────────────────────────────────────
@router.get("/graph")
def graph(dataset_id: str = Query(...), focus_type: str = Query(..., pattern=f"^({FOCUS_TYPES})$"), focus_id: str = Query(...),
          depth: int = Query(1, ge=1, le=3), mode: str = Query("neighbourhood", pattern="^(neighbourhood|downstream|upstream)$")) -> dict[str, Any]:
    """Link-analysis graph around one lead, built from the stored analysis (the same whether or not Neo4j is running)."""
    investigation = _investigation(dataset_id)
    if not _exists(investigation, focus_type, focus_id):
        raise _not_found(focus_type.capitalize(), focus_id)
    return investigation.graph(focus_type, focus_id, depth=depth, mode=mode)


# ── exports ─────────────────────────────────────────────────────────────────
@router.get("/export/{kind}.csv")
def export_csv(kind: str, dataset_id: str = Query(...), limit: int = Query(5000, ge=1, le=50000)) -> Response:
    """Ranked leads as a spreadsheet. ``kind``: wallets | transactions | actors | ips | patterns."""
    if kind not in EXPORT_KINDS:
        raise HTTPException(status_code=404, detail=f"Unknown export: {kind}. Choose one of {', '.join(EXPORT_KINDS)}.")
    header, rows = _investigation(dataset_id).export_rows(kind, limit)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return Response(content=buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="leads_{kind}.csv"'})


@router.get("/report/{kind}/{identifier:path}", response_class=PlainTextResponse)
def report(kind: str, identifier: str, dataset_id: str = Query(...)) -> PlainTextResponse:
    """Markdown evidence sheet for one wallet, transaction, actor or IP lead."""
    if kind not in ("wallet", "transaction", "actor", "ip"):
        raise HTTPException(status_code=404, detail="Reports exist for wallet, transaction, actor and ip leads.")
    investigation = _investigation(dataset_id)
    if not _exists(investigation, kind, identifier):
        raise _not_found(kind.capitalize(), identifier)
    safe = "".join(char if char.isalnum() else "-" for char in identifier)[:40]
    return PlainTextResponse(investigation.report_markdown(kind, identifier), media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{kind}_{safe}.md"'})


app.include_router(router)
app.include_router(router, prefix="/api", include_in_schema=False)


# ── dashboard (served when it has been built) ───────────────────────────────
def _frontend_dist() -> Path | None:
    candidate = Path(os.getenv("FRONTEND_DIST", PROJECT_ROOT / "frontend" / "dist"))
    return candidate if (candidate / "index.html").is_file() else None


_dist = _frontend_dist()
if _dist is not None:
    if (_dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(_dist / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/favicon.svg", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(_dist / "favicon.svg") if (_dist / "favicon.svg").is_file() else FileResponse(_dist / "index.html")
