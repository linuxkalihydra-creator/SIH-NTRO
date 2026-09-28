from backend.graph.graph_builder import build_transaction_graph
from backend.graph.service import build_graph
from backend.pipeline.orchestrator import AnalysisOrchestrator


def test_build_transaction_graph_returns_records():
    records = [{
        "timestamp": "2024-01-01T00:00:00+00:00",
        "src_ip": "203.0.113.10",
        "dst_ip": "198.51.100.10",
        "src_port": 8333,
        "dst_port": 8333,
        "txid": "tx_1",
        "input_addresses": ["wallet_a"],
        "output_addresses": ["wallet_b"],
        "input_amounts": [1.0],
        "output_amounts": [1.0],
        "fee": 0.0,
        "script_type": "P2WPKH",
        "geo_country": "US",
        "asn": 64512,
        "behavior_type": "NORMAL",
    }]
    graph = build_transaction_graph(records)
    assert len(graph) > 0
    assert build_graph(records) == graph


def test_orchestrator_requires_real_persistence_before_graph_available(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.persisted = 0

        def connect(self):
            return None

        def persist_graph(self, graph_records):
            self.persisted = len(graph_records)
            return len(graph_records)

        def close(self):
            return None

    monkeypatch.setattr("backend.pipeline.orchestrator.Neo4jClient", FakeClient)

    graph_records = [{"type": "Wallet", "id": "wallet_a", "relationship": "INPUT_FROM", "txid": "tx_1"}]
    status, message = AnalysisOrchestrator()._graph_status(graph_records)

    assert status is True
    assert "persistence" in message.lower()


def test_persist_graph_writes_output_to_edges_in_neo4j():
    import uuid

    import pytest

    from backend.graph.neo4j_client import Neo4jClient, Neo4jUnavailableError

    client = Neo4jClient()
    try:
        client.connect()
    except Neo4jUnavailableError:
        pytest.skip("Neo4j is not reachable")

    dataset_id = f"test_output_to_{uuid.uuid4().hex}"
    records = [
        {"timestamp": "2024-01-01T00:00:00+00:00", "src_ip": "203.0.113.10", "dst_ip": "198.51.100.10", "txid": "tx_1",
         "input_addresses": ["wallet_a"], "output_addresses": ["wallet_b", "wallet_c"], "input_amounts": [1.0], "output_amounts": [0.5, 0.49],
         "geo_country": "US", "asn": 64512},
        {"timestamp": "2024-01-01T00:05:00+00:00", "src_ip": "203.0.113.11", "dst_ip": "198.51.100.11", "txid": "tx_2",
         "input_addresses": ["wallet_b"], "output_addresses": ["wallet_d"], "input_amounts": [0.5], "output_amounts": [0.49],
         "geo_country": "US", "asn": 64512},
    ]
    try:
        client.persist_graph(build_transaction_graph(records), dataset_id=dataset_id)
        with client._driver.session() as session:
            output_edges = session.run(
                "MATCH (t:Transaction {dataset_id: $d})-[:OUTPUT_TO]->(w:Wallet {dataset_id: $d}) RETURN t.txid AS txid, w.wallet_id AS wallet",
                d=dataset_id,
            ).data()
            input_edges = session.run(
                "MATCH (:Wallet {dataset_id: $d})-[r:INPUT_FROM]->(:Transaction {dataset_id: $d}) RETURN count(r) AS n", d=dataset_id,
            ).single()["n"]
        assert len(output_edges) > 0
        assert {(edge["txid"], edge["wallet"]) for edge in output_edges} == {("tx_1", "wallet_b"), ("tx_1", "wallet_c"), ("tx_2", "wallet_d")}
        assert input_edges == 2
    finally:
        client.clear_dataset_graph(dataset_id)
        client.close()
