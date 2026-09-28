"""FastAPI service for uploaded Bitcoin investigation datasets."""

from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.graph.neo4j_client import Neo4jClient, Neo4jUnavailableError
from backend.ingestion.service import load_dataset
from backend.ingestion.dataset_store import DatasetStore
from backend.pipeline.orchestrator import AnalysisOrchestrator

app = FastAPI(title="Bitcoin Investigation Platform")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AnalyzeRequest(BaseModel):
    path: str | None = None
    output_dir: str | None = None
    contamination: float = 0.05
    random_state: int = 42
    dataset_id: str | None = None


class IngestRequest(BaseModel):
    path: str


def _dataset_store() -> DatasetStore:
    store = app.state.__dict__.get("dataset_store")
    if store is None:
        store = DatasetStore()
        app.state.dataset_store = store
    return store


def _dataset_snapshot(dataset_id: str) -> dict[str, Any]:
    if _dataset_store().get(dataset_id) is None:
        raise HTTPException(status_code=404, detail=f"Dataset not found: {dataset_id}")
    snapshot = _dataset_store().load_snapshot(dataset_id)
    if snapshot is None:
        raise HTTPException(status_code=409, detail="Dataset has not been analysed yet")
    return snapshot


def _anomaly_confidences(snapshot: dict[str, Any]) -> dict[str, float]:
    """Model confidence per wallet: the percentile of its Isolation Forest anomaly_score_norm in the dataset."""
    rows = snapshot.get("anomaly_results", [])
    if not rows:
        return {}
    scores = pd.Series({str(row["wallet_id"]): float(row.get("anomaly_score_norm", 0.0)) for row in rows})
    return scores.rank(pct=True).round(3).to_dict()


def _alerts_from_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    cluster_ids = {str(row["wallet_id"]): row.get("cluster_id") for row in snapshot.get("cluster_results", [])}
    risks = sorted(snapshot.get("wallet_risk_scores", []), key=lambda row: row.get("risk_score", 0), reverse=True)[:20]
    explanation_rows = {str(row.get("wallet_id")): row for row in snapshot.get("explanations", [])}
    confidences = _anomaly_confidences(snapshot)
    alerts = []
    for row in risks:
        wallet_id = str(row["wallet_id"])
        reasons = explanation_rows.get(wallet_id, {}).get("reasons", [])
        alerts.append({
            "wallet_id": wallet_id,
            "risk_score": float(row["risk_score"]),
            "risk_level": str(row["risk_level"]),
            "cluster_id": cluster_ids.get(wallet_id),
            "confidence": float(confidences.get(wallet_id, 0.0)),
            "top_reasons": [
                {
                    "label": str(reason["label"]),
                    "confidence": float(reason.get("confidence", 0.0)),
                    "evidence": str(reason.get("evidence", "")),
                    "investigative_lead": str(reason.get("investigative_lead", "")),
                }
                for reason in reasons
                if reason.get("label")
            ],
        })
    return alerts


def _snapshot_wallet_row(snapshot: dict[str, Any], key: str, wallet_id: str) -> dict[str, Any] | None:
    return next((row for row in snapshot.get(key, []) if str(row.get("wallet_id")) == wallet_id), None)


def _snapshot_entity(snapshot: dict[str, Any], wallet_id: str) -> dict[str, Any]:
    feature_row = _snapshot_wallet_row(snapshot, "wallet_features", wallet_id)
    if feature_row is None:
        raise HTTPException(status_code=404, detail=f"Wallet not found: {wallet_id}")

    risk_row = _snapshot_wallet_row(snapshot, "wallet_risk_scores", wallet_id) or {}
    cluster_row = _snapshot_wallet_row(snapshot, "cluster_results", wallet_id) or {}
    anomaly_row = _snapshot_wallet_row(snapshot, "anomaly_results", wallet_id) or {}
    related_ips = {
        str(ip)
        for record in snapshot.get("records", [])
        if wallet_id in {str(item) for item in record.get("input_addresses", []) + record.get("output_addresses", [])}
        for ip in (record.get("src_ip"), record.get("dst_ip"))
        if ip
    }
    risk_score = float(risk_row.get("risk_score", 0.0))
    cluster_id = cluster_row.get("cluster_id")
    return {
        "wallet_id": wallet_id,
        "risk_score": risk_score,
        "risk_level": str(risk_row.get("risk_level", "LOW")),
        "confidence": float(_anomaly_confidences(snapshot).get(wallet_id, 0.0)),
        "cluster_id": cluster_id,
        "transaction_statistics": {
            "transaction_count": int(feature_row.get("transaction_count", 0)),
            "incoming_transaction_count": int(feature_row.get("incoming_transaction_count", 0)),
            "outgoing_transaction_count": int(feature_row.get("outgoing_transaction_count", 0)),
        },
        "network_statistics": {
            "unique_ips": int(feature_row.get("unique_ips", 0)),
            "unique_counterparties": int(feature_row.get("unique_counterparties", 0)),
            "graph_degree": int(feature_row.get("graph_degree", 0)),
        },
        "ml_information": {
            "anomaly_score": float(anomaly_row.get("anomaly_score", 0.0)),
            "anomaly_label": int(anomaly_row.get("anomaly_label", 0)),
        },
        "cluster_information": {"cluster_id": cluster_id},
        "explanations": [row for row in snapshot.get("explanations", []) if str(row.get("wallet_id")) == wallet_id],
        "evidence": snapshot.get("evidence", {}).get(wallet_id, {}),
        "related_entities": sorted(related_ips),
    }


@app.get("/health")
def health() -> dict[str, Any]:
    """Return a health indicator for the offline platform."""
    from backend.graph.neo4j_client import Neo4jClient, Neo4jUnavailableError

    try:
        client = Neo4jClient()
        client.connect()
        client._driver.verify_connectivity()
        graph_available = True
        message = "Neo4j connectivity verified"
    except Neo4jUnavailableError:
        graph_available = False
        message = "Neo4j unavailable"
    else:
        client.close()

    return {
        "status": "ok",
        "mode": "uploaded_dataset",
        "graph_available": graph_available,
        "graph_status": message,
    }


@app.post("/ingest")
def ingest(request: IngestRequest) -> dict[str, Any]:
    """Ingest a dataset and return the canonical records count."""
    try:
        records = load_dataset(request.path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"records_loaded": len(records), "source": request.path}


@app.post("/datasets")
def register_dataset(request: IngestRequest) -> dict[str, Any]:
    """Copy a local source into the persistent, offline dataset registry."""
    try:
        return _dataset_store().register_file(request.path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/datasets/upload", status_code=201)
async def upload_dataset(file: UploadFile = File(...)) -> dict[str, Any]:
    """Accept a browser multipart upload and register it in the local store."""
    try:
        metadata = _dataset_store().register_upload(file.filename or "", await file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {key: metadata[key] for key in ("dataset_id", "filename", "format", "size_bytes", "status", "created_at")}


@app.get("/datasets")
def datasets() -> list[dict[str, Any]]:
    return _dataset_store().list()


@app.get("/datasets/{dataset_id}")
def dataset(dataset_id: str) -> dict[str, Any]:
    metadata = _dataset_store().get(dataset_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail=f"Dataset not found: {dataset_id}")
    return metadata


@app.post("/analyze")
def analyze(request: AnalyzeRequest) -> dict[str, Any]:
    """Run the real end-to-end analysis pipeline for a local dataset."""
    try:
        store = _dataset_store()
        metadata = store.get(request.dataset_id) if request.dataset_id else None
        if request.dataset_id and metadata is None:
            raise HTTPException(status_code=404, detail=f"Dataset not found: {request.dataset_id}")
        if metadata is None:
            if not request.path:
                raise HTTPException(status_code=422, detail="dataset_id is required for analysis")
            metadata = store.register_file(request.path)
        dataset_id = metadata["dataset_id"]
        store.update(dataset_id, status="analyzing", analysis_status="running", error_message=None)
        orchestrator = AnalysisOrchestrator(contamination=request.contamination, random_state=request.random_state)
        result = orchestrator.run(metadata["source_path"], output_dir=request.output_dir or str(store._directory(dataset_id) / "processed"), contamination=request.contamination, random_state=request.random_state, dataset_id=dataset_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Neo4jUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - broad guard for runtime failures
        if request.dataset_id:
            _dataset_store().update(request.dataset_id, status="failed", analysis_status="failed", error_message=str(exc))
        raise HTTPException(status_code=500, detail=f"Pipeline failure: {exc}") from exc

    snapshot = {
        "dataset_statistics": result.dataset_statistics, "ingestion_statistics": result.ingestion_statistics,
        "graph_statistics": result.graph_statistics, "graph_available": result.graph_available,
        "wallet_risk_scores": result.risk_scores[["wallet_id", "risk_score", "risk_level"]].to_dict(orient="records"),
        "wallet_features": result.wallet_features.to_dict(orient="records"), "cluster_results": result.cluster_results.to_dict(orient="records"),
        "anomaly_results": result.anomaly_results.to_dict(orient="records"), "explanations": result.explanations.to_dict(orient="records"),
        "evidence": result.evidence, "records": result.records,
    }
    store.save_snapshot(dataset_id, snapshot)
    store.update(dataset_id, status="ready", analysis_status="completed", record_count=len(result.records), error_message=None)

    return {"dataset_id": dataset_id,
        "dataset_statistics": result.dataset_statistics,
        "ingestion_statistics": result.ingestion_statistics,
        "validation_statistics": result.validation_statistics,
        "correlation_statistics": result.correlation_statistics,
        "graph_statistics": result.graph_statistics,
        "graph_available": result.graph_available,
        "wallet_risk_scores": result.risk_scores[["wallet_id", "risk_score", "risk_level"]].to_dict(orient="records"),
        "wallet_features": result.wallet_features.to_dict(orient="records"),
        "cluster_results": result.cluster_results.to_dict(orient="records"),
        "anomaly_results": result.anomaly_results.to_dict(orient="records"),
        "explanations": result.explanations.to_dict(orient="records"),
        "evaluation": result.evaluation,
        "processing_duration": round(result.processing_duration, 6),
    }


@app.get("/stats")
def stats(dataset_id: str = Query(...)) -> dict[str, Any]:
    """Return statistics only for an analysed uploaded dataset."""
    return _dataset_snapshot(dataset_id)["dataset_statistics"]


@app.get("/alerts")
def alerts(dataset_id: str = Query(...)) -> list[dict[str, Any]]:
    """Return alerts only for an analysed uploaded dataset."""
    return _alerts_from_snapshot(_dataset_snapshot(dataset_id))


@app.get("/alerts/{wallet_id}")
def alert_for_wallet(wallet_id: str, dataset_id: str = Query(...)) -> dict[str, Any]:
    """Return one alert from the requested uploaded dataset."""
    alert = next((item for item in _alerts_from_snapshot(_dataset_snapshot(dataset_id)) if item["wallet_id"] == wallet_id), None)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Wallet not found in dataset: {wallet_id}")
    return alert


@app.get("/entities/{wallet_id}")
def entity(wallet_id: str, dataset_id: str = Query(...)) -> dict[str, Any]:
    """Return a wallet summary from the requested uploaded dataset."""
    return _snapshot_entity(_dataset_snapshot(dataset_id), wallet_id)


@app.get("/entities/{wallet_id}/evidence")
def entity_evidence(wallet_id: str, dataset_id: str = Query(...)) -> dict[str, Any]:
    """Return evidence from the requested uploaded dataset."""
    evidence = _dataset_snapshot(dataset_id).get("evidence", {})
    if wallet_id not in evidence:
        raise HTTPException(status_code=404, detail=f"Evidence not found for wallet: {wallet_id}")
    return evidence[wallet_id]


@app.get("/entities/{wallet_id}/graph")
def entity_graph(
    wallet_id: str,
    dataset_id: str = Query(...),
    depth: int = Query(default=2, ge=1, le=3),
    max_nodes: int = Query(default=150, ge=1, le=500),
    max_edges: int = Query(default=300, ge=1, le=1000),
) -> dict[str, Any]:
    """Return a bounded Neo4j neighborhood for a wallet in one dataset."""
    snapshot = _dataset_snapshot(dataset_id)
    if _snapshot_wallet_row(snapshot, "wallet_features", wallet_id) is None:
        raise HTTPException(status_code=404, detail=f"Wallet not found: {wallet_id}")

    client = Neo4jClient()
    try:
        client.connect()
        graph = client.get_neighborhood(dataset_id, wallet_id, depth=depth, max_nodes=max_nodes, max_edges=max_edges)
    except Neo4jUnavailableError:
        return {"wallet_id": wallet_id, "graph_available": False, "nodes": [], "edges": [], "depth": depth}
    finally:
        client.close()

    return {"wallet_id": wallet_id, **graph}


@app.get("/entities/{wallet_id}/transactions")
def entity_transactions(wallet_id: str, dataset_id: str = Query(...)) -> list[dict[str, Any]]:
    """Return records associated with a wallet in one uploaded dataset."""
    snapshot = _dataset_snapshot(dataset_id)
    result = []
    for record in snapshot.get("records", []):
        wallet_set = {str(item) for item in record.get("input_addresses", [])} | {str(item) for item in record.get("output_addresses", [])}
        if wallet_id in wallet_set:
            result.append({
                "txid": record.get("txid"),
                "timestamp": record.get("timestamp"),
                "src_ip": record.get("src_ip"),
                "dst_ip": record.get("dst_ip"),
                "input_addresses": record.get("input_addresses", []),
                "output_addresses": record.get("output_addresses", []),
                "fee": record.get("fee"),
            })
    return result


@app.get("/clusters")
def clusters(dataset_id: str = Query(...)) -> list[dict[str, Any]]:
    """Return cluster assignments for one uploaded dataset."""
    return _dataset_snapshot(dataset_id).get("cluster_results", [])


@app.get("/datasets/{dataset_id}/clusters/{cluster_id}/graph")
def cluster_graph(dataset_id: str, cluster_id: int) -> dict[str, Any]:
    """Fetch only the selected cluster's Neo4j neighborhood for Cytoscape."""
    snapshot = _dataset_snapshot(dataset_id)
    wallets = [str(row["wallet_id"]) for row in snapshot.get("cluster_results", []) if int(row.get("cluster_id", -999999)) == cluster_id]
    if not wallets:
        raise HTTPException(status_code=404, detail=f"Cluster {cluster_id} not found in dataset {dataset_id}")
    client = Neo4jClient()
    try:
        client.connect()
        graph = client.get_cluster_graph(dataset_id, wallets, max_nodes=200, max_edges=500)
    except Neo4jUnavailableError:
        graph = {"graph_available": False, "nodes": [], "edges": []}
    finally:
        client.close()
    return {"dataset_id": dataset_id, "cluster_id": cluster_id, "max_nodes": 200, "max_edges": 500,
            "limited": len(graph["nodes"]) >= 200 or len(graph["edges"]) >= 500, **graph}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
