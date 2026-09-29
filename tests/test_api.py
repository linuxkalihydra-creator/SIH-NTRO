import pytest
from fastapi.testclient import TestClient

from backend.api.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def analysed(client, small_paths, monkeypatch):
    """Upload and analyse the small dataset (graph persistence stubbed out) and return its id and seeds."""
    monkeypatch.setattr("backend.pipeline.orchestrator.AnalysisOrchestrator._persist_graph", lambda self, records, dataset_id: (False, "Neo4j stubbed in tests"))
    with small_paths["csv"].open("rb") as handle:
        dataset_id = client.post("/datasets/upload", files={"file": ("transactions.csv", handle, "text/csv")}).json()["dataset_id"]
    seeds = small_paths["seeds"].read_text().split()
    response = client.post("/analyze", json={"dataset_id": dataset_id, "seed_wallets": seeds[:2]})
    assert response.status_code == 200, response.text
    return dataset_id, seeds


def test_health_and_ingest(client, small_paths):
    assert client.get("/health").json()["status"] == "ok"
    response = client.post("/ingest", json={"path": str(small_paths["csv"])})
    assert response.status_code == 200 and response.json()["records"] == 1500
    assert client.post("/ingest", json={"path": "missing.csv"}).status_code == 404


def test_endpoints_require_a_known_analysed_dataset(client, small_paths):
    assert client.get("/alerts").status_code == 422
    assert client.get("/overview", params={"dataset_id": "dataset_missing"}).status_code == 404
    with small_paths["csv"].open("rb") as handle:
        dataset_id = client.post("/datasets/upload", files={"file": ("t.csv", handle, "text/csv")}).json()["dataset_id"]
    assert client.get("/overview", params={"dataset_id": dataset_id}).status_code == 409
    assert client.post("/analyze", json={}).status_code == 422


def test_overview_and_ranked_leads(client, analysed):
    dataset_id, seeds = analysed
    overview = client.get("/overview", params={"dataset_id": dataset_id}).json()
    assert overview["dataset_id"] == dataset_id and overview["seeds"] == seeds[:2]
    assert overview["stats"]["transactions"] == 1500 and overview["graph_available"] is False
    alerts = client.get("/alerts", params={"dataset_id": dataset_id, "limit": 20}).json()
    assert [alert["rank"] for alert in alerts] == list(range(1, 21))
    assert alerts[0]["is_seed"] and alerts[0]["risk_level"] == "CRITICAL" and alerts[0]["confidence"] == 1.0
    for alert in alerts:
        assert abs(sum(alert["contributions"].values()) - alert["risk_score"]) < 0.05
        assert alert["reasons"] and {"category", "label", "detail", "contribution"} <= set(alert["reasons"][0])
    assert all(alert["risk_level"] == "HIGH" for alert in client.get("/alerts", params={"dataset_id": dataset_id, "level": "HIGH"}).json())
    transactions = client.get("/alerts/transactions", params={"dataset_id": dataset_id, "limit": 5}).json()
    assert transactions[0]["rank"] == 1 and transactions[0]["reasons"]
    patterns = client.get("/patterns", params={"dataset_id": dataset_id}).json()
    assert {pattern["type"] for pattern in patterns} >= {"peeling_chain", "coinjoin"}
    assert all(item["type"] == "coinjoin" for item in client.get("/patterns", params={"dataset_id": dataset_id, "type": "coinjoin"}).json())
    clusters = client.get("/clusters", params={"dataset_id": dataset_id}).json()
    assert sum(cluster["wallet_count"] for cluster in clusters) == overview["stats"]["wallets"]


def test_wallet_and_transaction_detail(client, analysed):
    dataset_id, _ = analysed
    alerts = client.get("/alerts", params={"dataset_id": dataset_id, "limit": 200}).json()
    flagged = next(alert for alert in alerts if not alert["is_seed"] and alert["signals"]["anomaly"] > 0)
    detail = client.get(f"/wallets/{flagged['wallet_id']}", params={"dataset_id": dataset_id}).json()
    assert [item["name"] for item in detail["detectors"]] == ["Isolation Forest", "Autoencoder", "ECOD"]
    assert detail["feature_attributions"] and detail["feature_attributions"][0]["label"]
    assert detail["entity"]["entity_id"] == flagged["entity_id"] and flagged["wallet_id"] in detail["entity"]["wallets"]
    assert {"ips", "countries", "asns", "ports", "nonstandard_port_ratio", "shared_ip_wallets"} <= set(detail["network"])
    assert detail["transactions"] and detail["transactions"][0]["direction"] in {"in", "out", "self"}
    tainted = next((alert for alert in alerts if alert["signals"]["taint"] > 0 and not alert["is_seed"]), None)
    if tainted:
        taint = client.get(f"/wallets/{tainted['wallet_id']}", params={"dataset_id": dataset_id}).json()["taint"]
        assert taint is None or taint["path"]
    txid = client.get("/alerts/transactions", params={"dataset_id": dataset_id, "limit": 1}).json()[0]["txid"]
    transaction = client.get(f"/transactions/{txid}", params={"dataset_id": dataset_id}).json()
    assert transaction["inputs"] and transaction["outputs"] and transaction["detectors"]
    assert client.get("/wallets/unknown", params={"dataset_id": dataset_id}).status_code == 404
    assert client.get("/transactions/unknown", params={"dataset_id": dataset_id}).status_code == 404


def test_graph_for_every_focus_type(client, analysed):
    dataset_id, seeds = analysed
    pattern_id = client.get("/patterns", params={"dataset_id": dataset_id}).json()[0]["pattern_id"]
    txid = client.get("/alerts/transactions", params={"dataset_id": dataset_id, "limit": 1}).json()[0]["txid"]
    cluster = client.get("/clusters", params={"dataset_id": dataset_id}).json()[0]["cluster_id"]
    for focus_type, focus_id in (("wallet", seeds[0]), ("transaction", txid), ("pattern", pattern_id), ("cluster", str(cluster))):
        graph = client.get("/graph", params={"dataset_id": dataset_id, "focus_type": focus_type, "focus_id": focus_id, "depth": 2}).json()
        ids = {node["id"] for node in graph["nodes"]}
        edge_ids = [edge["id"] for edge in graph["edges"]]
        assert graph["nodes"] and graph["source"] == "local"
        assert len(edge_ids) == len(set(edge_ids))
        assert all(edge["source"] in ids and edge["target"] in ids for edge in graph["edges"])
        assert {edge["type"] for edge in graph["edges"]} <= {"INPUT_FROM", "OUTPUT_TO", "OBSERVED_IN"}
    wallet_graph = client.get("/graph", params={"dataset_id": dataset_id, "focus_type": "wallet", "focus_id": seeds[0]}).json()
    assert any(node["is_focus"] and node["is_seed"] for node in wallet_graph["nodes"])
    assert client.get("/graph", params={"dataset_id": dataset_id, "focus_type": "pattern", "focus_id": "nope"}).status_code == 404
    assert client.get("/graph", params={"dataset_id": dataset_id, "focus_type": "bogus", "focus_id": "x"}).status_code == 422


def test_changing_seeds_rescores_and_persists(client, analysed):
    dataset_id, seeds = analysed
    before = client.get("/overview", params={"dataset_id": dataset_id}).json()["risk_distribution"]
    response = client.put(f"/datasets/{dataset_id}/seeds", json={"seed_wallets": seeds + ["not_in_dataset"]})
    assert response.status_code == 200
    body = response.json()
    assert body["unknown_seeds"] == ["not_in_dataset"] and body["overview"]["seeds"] == seeds
    assert body["overview"]["risk_distribution"]["CRITICAL"] >= before["CRITICAL"]
    from backend.api import main
    main._cache.clear()  # the new seeds survive a restart (reloaded from the snapshot)
    assert client.get("/overview", params={"dataset_id": dataset_id}).json()["seeds"] == seeds
    cleared = client.put(f"/datasets/{dataset_id}/seeds", json={"seed_wallets": []}).json()["overview"]
    assert cleared["seeds"] == [] and not client.get("/alerts", params={"dataset_id": dataset_id, "limit": 1}).json()[0]["is_seed"]
