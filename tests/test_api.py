import csv
import io
import time

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app


@pytest.fixture
def client():
    return TestClient(app)


def _stub_neo4j(monkeypatch):
    monkeypatch.setattr("backend.pipeline.orchestrator.AnalysisOrchestrator._persist_graph", lambda self, investigation, dataset_id: (False, "Neo4j stubbed in tests"))


def _upload(client, small_paths, key="csv"):
    with small_paths[key].open("rb") as handle:
        response = client.post("/datasets/upload", files={"file": (f"transactions.{key}", handle, "application/octet-stream")})
    assert response.status_code == 201, response.text
    return response.json()["dataset_id"]


@pytest.fixture
def analysed(client, small_paths, monkeypatch):
    """Upload and analyse the small dataset (graph persistence stubbed out) and return its id and seeds."""
    _stub_neo4j(monkeypatch)
    dataset_id = _upload(client, small_paths)
    seeds = small_paths["seeds"].read_text().split()
    response = client.post("/analyze", json={"dataset_id": dataset_id, "seed_wallets": seeds[:2]})
    assert response.status_code == 200, response.text
    return dataset_id, seeds


def test_health_and_ingest(client, small_paths):
    assert client.get("/health").json()["status"] == "ok"
    response = client.post("/ingest", json={"path": str(small_paths["csv"])})
    assert response.status_code == 200 and response.json()["records"] == 1500
    summary = response.json()["summary"]
    assert summary["rows"] > 1500 and summary["column_mapping"]["txid"] == "txid" and summary["amount_unit"] == "BTC"
    assert client.post("/ingest", json={"path": "missing.csv"}).status_code == 404


def test_every_route_is_also_served_under_the_api_prefix(client, analysed):
    dataset_id, _ = analysed
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/overview", params={"dataset_id": dataset_id}).json() == client.get("/overview", params={"dataset_id": dataset_id}).json()
    assert client.get("/api/alerts", params={"dataset_id": dataset_id, "limit": 3}).status_code == 200


def test_endpoints_require_a_known_analysed_dataset(client, small_paths):
    assert client.get("/alerts").status_code == 422
    assert client.get("/overview", params={"dataset_id": "dataset_missing"}).status_code == 404
    dataset_id = _upload(client, small_paths)
    for path in ("/overview", "/alerts", "/alerts/actors", "/alerts/ips", "/ingestion"):
        assert client.get(path, params={"dataset_id": dataset_id}).status_code == 409, path
    assert client.post("/analyze", json={}).status_code == 422
    assert client.get(f"/datasets/{dataset_id}/progress").json()["status"] == "idle"


def test_overview_and_ranked_leads(client, analysed):
    dataset_id, seeds = analysed
    overview = client.get("/overview", params={"dataset_id": dataset_id}).json()
    assert overview["dataset_id"] == dataset_id and overview["seeds"] == seeds[:2]
    assert overview["stats"]["transactions"] == 1500 and overview["graph_available"] is False
    assert overview["stage_seconds"]["analyse"] > 0 and overview["ingestion"]["invalid_records"] == 0
    alerts = client.get("/alerts", params={"dataset_id": dataset_id, "limit": 20}).json()
    assert [alert["rank"] for alert in alerts] == list(range(1, 21))
    assert alerts[0]["is_seed"] and alerts[0]["risk_level"] == "CRITICAL" and alerts[0]["confidence"] == 1.0
    for alert in alerts:
        assert abs(sum(alert["contributions"].values()) - alert["risk_score"]) < 0.05
        assert alert["reasons"] and {"category", "label", "detail", "contribution"} <= set(alert["reasons"][0])
        assert alert["actor_id"].startswith("A") and alert["entity_id"].startswith("E")
    assert all(alert["risk_level"] == "HIGH" for alert in client.get("/alerts", params={"dataset_id": dataset_id, "level": "HIGH"}).json())
    transactions = client.get("/alerts/transactions", params={"dataset_id": dataset_id, "limit": 5}).json()
    assert transactions[0]["rank"] == 1 and transactions[0]["reasons"] and "origin_confidence" in transactions[0]
    patterns = client.get("/patterns", params={"dataset_id": dataset_id}).json()
    assert {pattern["type"] for pattern in patterns} >= {"peeling_chain", "coinjoin"}
    assert all(item["type"] == "coinjoin" for item in client.get("/patterns", params={"dataset_id": dataset_id, "type": "coinjoin"}).json())
    clusters = client.get("/clusters", params={"dataset_id": dataset_id}).json()
    assert sum(cluster["wallet_count"] for cluster in clusters) == overview["stats"]["wallets"]
    assert client.get("/ingestion", params={"dataset_id": dataset_id}).json()["transactions"] == 1500


def test_actor_and_ip_leads(client, analysed):
    dataset_id, seeds = analysed
    actors = client.get("/alerts/actors", params={"dataset_id": dataset_id, "limit": 10}).json()
    assert [actor["rank"] for actor in actors] == list(range(1, 11)) and actors[0]["risk_score"] == 100.0 and actors[0]["seed_wallets"] >= 1
    detail = client.get(f"/actors/{actors[0]['actor_id']}", params={"dataset_id": dataset_id}).json()
    assert detail["top_wallets"] and detail["entity_list"] and detail["reasons"] and len(detail["detectors"]) == 3
    assert {wallet["wallet_id"] for wallet in detail["top_wallets"]} & set(seeds[:2])
    ips = client.get("/alerts/ips", params={"dataset_id": dataset_id, "limit": 10}).json()
    assert ips[0]["rank"] == 1 and ips[0]["originated"] > 0 and ips[0]["risk_score"] >= ips[-1]["risk_score"]
    assert {"country", "asn", "role", "wallets", "flagged_wallets", "flagged_share", "confidence", "top_reason"} <= set(ips[0])
    ip_detail = client.get(f"/ips/{ips[0]['ip']}", params={"dataset_id": dataset_id}).json()
    assert ip_detail["wallet_list"] and ip_detail["transactions"] and ip_detail["actor_list"] and ip_detail["ports"]
    relays = client.get("/alerts/ips", params={"dataset_id": dataset_id, "limit": 2000, "include_relays": True}).json()
    assert any(item["role"] == "relay node" for item in relays) and len(relays) > len(client.get("/alerts/ips", params={"dataset_id": dataset_id, "limit": 2000}).json())
    assert client.get("/actors/A999999", params={"dataset_id": dataset_id}).status_code == 404
    assert client.get("/ips/203.0.113.250", params={"dataset_id": dataset_id}).status_code == 404


def test_wallet_and_transaction_detail(client, analysed):
    dataset_id, _ = analysed
    alerts = client.get("/alerts", params={"dataset_id": dataset_id, "limit": 200}).json()
    flagged = next(alert for alert in alerts if not alert["is_seed"] and alert["signals"]["anomaly"] > 0)
    detail = client.get(f"/wallets/{flagged['wallet_id']}", params={"dataset_id": dataset_id}).json()
    assert [item["name"] for item in detail["detectors"]] == ["Isolation Forest", "Autoencoder", "ECOD"]
    assert detail["feature_attributions"] and detail["feature_attributions"][0]["label"]
    assert detail["entity"]["entity_id"] == flagged["entity_id"] and flagged["wallet_id"] in detail["entity"]["wallets"]
    assert detail["actor"]["actor_id"] == flagged["actor_id"] and detail["actor"]["top_wallets"]
    assert {"ips", "countries", "asns", "ports", "nonstandard_port_ratio", "shared_ip_wallets", "association", "actor"} <= set(detail["network"])
    assert detail["transactions"] and detail["transactions"][0]["direction"] in {"in", "out", "self"}
    tainted = next((alert for alert in alerts if alert["signals"]["taint"] > 0 and not alert["is_seed"]), None)
    assert tainted is not None
    evidence = client.get(f"/wallets/{tainted['wallet_id']}", params={"dataset_id": dataset_id}).json()
    assert any(reason["category"] == "taint" for reason in evidence["reasons"])
    assert (evidence["taint"] or {}).get("path") or (evidence["proceeds"] or {}).get("path") or evidence["upstream_exposure"] or evidence["entity"]["size"] > 1 or evidence["actor"]["wallets"] > 1
    txid = client.get("/alerts/transactions", params={"dataset_id": dataset_id, "limit": 1}).json()[0]["txid"]
    transaction = client.get(f"/transactions/{txid}", params={"dataset_id": dataset_id}).json()
    assert transaction["inputs"] and transaction["outputs"] and transaction["detectors"]
    assert transaction["observations"][0]["role"] == "first seen" and transaction["origin"]["ip"] == transaction["observations"][0]["src_ip"]
    assert transaction["origin"]["explanation"] and 0 <= transaction["origin"]["confidence"] <= 1
    assert client.get("/wallets/unknown", params={"dataset_id": dataset_id}).status_code == 404
    assert client.get("/transactions/unknown", params={"dataset_id": dataset_id}).status_code == 404


def test_graph_for_every_focus_type(client, analysed):
    dataset_id, seeds = analysed
    pattern_id = client.get("/patterns", params={"dataset_id": dataset_id}).json()[0]["pattern_id"]
    txid = client.get("/alerts/transactions", params={"dataset_id": dataset_id, "limit": 1}).json()[0]["txid"]
    cluster = client.get("/clusters", params={"dataset_id": dataset_id}).json()[0]["cluster_id"]
    actor = client.get("/alerts/actors", params={"dataset_id": dataset_id, "limit": 1}).json()[0]["actor_id"]
    ip = client.get("/alerts/ips", params={"dataset_id": dataset_id, "limit": 1}).json()[0]["ip"]
    for focus_type, focus_id in (("wallet", seeds[0]), ("transaction", txid), ("pattern", pattern_id), ("cluster", str(cluster)), ("actor", actor), ("ip", ip)):
        graph = client.get("/graph", params={"dataset_id": dataset_id, "focus_type": focus_type, "focus_id": focus_id, "depth": 2}).json()
        ids = {node["id"] for node in graph["nodes"]}
        edge_ids = [edge["id"] for edge in graph["edges"]]
        assert graph["nodes"] and graph["source"] == "local", focus_type
        assert len(edge_ids) == len(set(edge_ids))
        assert all(edge["source"] in ids and edge["target"] in ids for edge in graph["edges"])
        assert {edge["type"] for edge in graph["edges"]} <= {"INPUT_FROM", "OUTPUT_TO", "OBSERVED_IN", "RELAYED_BY"}
        assert {node["type"] for node in graph["nodes"]} <= {"Wallet", "Transaction", "IP"}
    wallet_graph = client.get("/graph", params={"dataset_id": dataset_id, "focus_type": "wallet", "focus_id": seeds[0]}).json()
    assert any(node["is_focus"] and node["is_seed"] for node in wallet_graph["nodes"])
    assert any(node["is_focus"] and node["type"] == "IP" for node in client.get("/graph", params={"dataset_id": dataset_id, "focus_type": "ip", "focus_id": ip}).json()["nodes"])
    assert client.get("/graph", params={"dataset_id": dataset_id, "focus_type": "pattern", "focus_id": "nope"}).status_code == 404
    assert client.get("/graph", params={"dataset_id": dataset_id, "focus_type": "bogus", "focus_id": "x"}).status_code == 422


def test_following_the_money_from_a_seed(client, analysed):
    dataset_id, seeds = analysed
    params = {"dataset_id": dataset_id, "focus_type": "wallet", "focus_id": seeds[0], "depth": 2}
    downstream = client.get("/graph", params={**params, "mode": "downstream"}).json()
    assert downstream["mode"] == "downstream" and len(downstream["nodes"]) > 3
    wallets = {node["id"]: node for node in downstream["nodes"] if node["type"] == "Wallet"}
    assert seeds[0] in wallets and any(node["risk_score"] >= 60 for key, node in wallets.items() if key != seeds[0])
    # The trace starts at the seed: it funds a transaction and never appears only as a recipient.
    assert any(edge["source"] == seeds[0] and edge["type"] == "INPUT_FROM" for edge in downstream["edges"])
    upstream = client.get("/graph", params={**params, "mode": "upstream"}).json()
    assert any(edge["target"] == seeds[0] and edge["type"] == "OUTPUT_TO" for edge in upstream["edges"])
    assert client.get("/graph", params={**params, "mode": "sideways"}).status_code == 422


def test_search_exports_and_reports(client, analysed):
    dataset_id, seeds = analysed
    hits = client.get("/search", params={"dataset_id": dataset_id, "q": seeds[0][:12]}).json()
    assert hits[0] == {"type": "wallet", "id": seeds[0], "label": "Wallet", "risk_score": 100.0, "risk_level": "CRITICAL"}
    ip = client.get("/alerts/ips", params={"dataset_id": dataset_id, "limit": 1}).json()[0]["ip"]
    assert any(hit["type"] == "ip" and hit["id"] == ip for hit in client.get("/search", params={"dataset_id": dataset_id, "q": ip}).json())
    assert client.get("/search", params={"dataset_id": dataset_id, "q": "zzzz-nothing"}).json() == []
    assert client.get("/search", params={"dataset_id": dataset_id, "q": "a"}).status_code == 422
    for kind in ("wallets", "transactions", "actors", "ips", "patterns"):
        response = client.get(f"/export/{kind}.csv", params={"dataset_id": dataset_id, "limit": 50})
        assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv"), kind
        rows = list(csv.reader(io.StringIO(response.text)))
        assert len(rows) > 2 and len(set(map(len, rows))) == 1, kind
    assert client.get("/export/secrets.csv", params={"dataset_id": dataset_id}).status_code == 404
    report = client.get(f"/report/wallet/{seeds[0]}", params={"dataset_id": dataset_id})
    assert report.status_code == 200 and report.text.startswith("# Wallet lead:") and "Known illicit seed wallet" in report.text
    assert "Not proof of identity" in report.text
    assert client.get(f"/report/ip/{ip}", params={"dataset_id": dataset_id}).text.startswith("# Ip lead:")
    assert client.get("/report/wallet/unknown", params={"dataset_id": dataset_id}).status_code == 404
    assert client.get(f"/report/pattern/{seeds[0]}", params={"dataset_id": dataset_id}).status_code == 404


def test_changing_seeds_rescores_and_persists(client, analysed):
    dataset_id, seeds = analysed
    before = client.get("/overview", params={"dataset_id": dataset_id}).json()["risk_distribution"]
    response = client.put(f"/datasets/{dataset_id}/seeds", json={"seed_wallets": seeds + ["not_in_dataset"]})
    assert response.status_code == 200
    body = response.json()
    assert body["unknown_seeds"] == ["not_in_dataset"] and body["overview"]["seeds"] == seeds
    assert body["overview"]["risk_distribution"]["CRITICAL"] >= before["CRITICAL"]
    from backend.api import main
    store = main._dataset_store()
    snapshot_written = store.snapshot_path(dataset_id).stat().st_mtime
    after_change = body["overview"]["risk_distribution"]
    main._cache.clear()  # the new seeds survive a restart: the stored analysis is re-scored with the saved seed list
    restarted = client.get("/overview", params={"dataset_id": dataset_id}).json()
    assert restarted["seeds"] == seeds and restarted["risk_distribution"] == after_change
    # Only the seed list was written; the large snapshot was left alone.
    assert store.load_seeds(dataset_id) == seeds and store.snapshot_path(dataset_id).stat().st_mtime == snapshot_written
    cleared = client.put(f"/datasets/{dataset_id}/seeds", json={"seed_wallets": []}).json()["overview"]
    assert cleared["seeds"] == [] and not client.get("/alerts", params={"dataset_id": dataset_id, "limit": 1}).json()[0]["is_seed"]
    main._cache.clear()  # "no seeds" is remembered too, not replaced by the seeds of the original analysis
    assert client.get("/overview", params={"dataset_id": dataset_id}).json()["seeds"] == []
    # A new analysis carries its own seeds and supersedes the saved list.
    assert client.post("/analyze", json={"dataset_id": dataset_id, "seed_wallets": seeds[:1]}).json()["overview"]["seeds"] == seeds[:1]
    assert store.load_seeds(dataset_id) is None
    main._cache.clear()
    assert client.get("/overview", params={"dataset_id": dataset_id}).json()["seeds"] == seeds[:1]


def test_background_analysis_reports_progress(client, small_paths, monkeypatch):
    _stub_neo4j(monkeypatch)
    dataset_id = _upload(client, small_paths, "json")
    started = client.post("/analyze", json={"dataset_id": dataset_id, "wait": False})
    assert started.status_code == 200 and started.json() == {"dataset_id": dataset_id, "status": "running"}
    assert client.post("/analyze", json={"dataset_id": dataset_id, "wait": False}).status_code == 409   # one analysis at a time per dataset
    seen = set()
    deadline = time.time() + 120
    while time.time() < deadline:
        progress = client.get(f"/datasets/{dataset_id}/progress").json()
        seen.add(progress["status"])
        assert 0.0 <= progress["fraction"] <= 1.0
        if progress["status"] != "running":
            break
        time.sleep(0.2)
    assert progress["status"] == "completed" and progress["fraction"] == 1.0, progress
    assert client.get("/overview", params={"dataset_id": dataset_id}).json()["stats"]["transactions"] == 1500
    assert client.get(f"/datasets/{dataset_id}").json()["analysis_status"] == "completed"


def test_failed_analysis_is_reported(client, tmp_path, monkeypatch):
    _stub_neo4j(monkeypatch)
    response = client.post("/datasets/upload", files={"file": ("empty.csv", b"timestamp,txid\n", "text/csv")})
    dataset_id = response.json()["dataset_id"]
    assert client.post("/analyze", json={"dataset_id": dataset_id}).status_code == 400
    metadata = client.get(f"/datasets/{dataset_id}").json()
    assert metadata["status"] == "failed" and "No valid records" in metadata["error_message"]
    assert client.get(f"/datasets/{dataset_id}/progress").json()["status"] == "failed"


def test_datasets_can_be_deleted(client, analysed):
    dataset_id, _ = analysed
    assert client.delete(f"/datasets/{dataset_id}").json() == {"dataset_id": dataset_id, "deleted": True}
    assert client.get(f"/datasets/{dataset_id}").status_code == 404
    assert client.get("/overview", params={"dataset_id": dataset_id}).status_code == 404
    assert dataset_id not in {item["dataset_id"] for item in client.get("/datasets").json()}
    assert client.delete(f"/datasets/{dataset_id}").status_code == 404
