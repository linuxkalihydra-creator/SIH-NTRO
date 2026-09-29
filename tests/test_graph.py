import uuid

import pytest

from backend.graph.graph_builder import build_transaction_graph
from backend.graph.neo4j_client import Neo4jClient, Neo4jUnavailableError
from backend.pipeline.orchestrator import AnalysisOrchestrator

RECORDS = [
    {"timestamp": "2024-01-01T00:00:00+00:00", "src_ip": "8.8.8.8", "dst_ip": "1.1.1.1", "src_port": 40000, "dst_port": 8333, "txid": "tx_1",
     "input_addresses": ["wallet_a"], "output_addresses": ["wallet_b", "wallet_c"], "input_amounts": [1.0], "output_amounts": [0.5, 0.49],
     "fee": 0.01, "geo_country": "US", "asn": 15169},
    {"timestamp": "2024-01-01T00:05:00+00:00", "src_ip": "8.8.4.4", "dst_ip": "1.1.1.1", "src_port": 40001, "dst_port": 8333, "txid": "tx_2",
     "input_addresses": ["wallet_b"], "output_addresses": ["wallet_d"], "input_amounts": [0.5], "output_amounts": [0.49],
     "fee": 0.01, "geo_country": "US", "asn": 15169},
    # an unrelated wallet in the same country and ASN must never appear in wallet_a's neighbourhood
    {"timestamp": "2024-01-01T00:09:00+00:00", "src_ip": "9.9.9.9", "dst_ip": "1.1.1.1", "src_port": 40002, "dst_port": 8333, "txid": "tx_3",
     "input_addresses": ["stranger"], "output_addresses": ["stranger_2"], "input_amounts": [0.2], "output_amounts": [0.19],
     "fee": 0.01, "geo_country": "US", "asn": 15169},
]


def test_build_transaction_graph_links_ips_wallets_and_transactions():
    graph = build_transaction_graph(RECORDS)
    kinds = {(row["type"], row.get("relationship")) for row in graph}
    assert {("Wallet", "INPUT_FROM"), ("Wallet", "OUTPUT_TO"), ("IP", "OBSERVED_IN"), ("Transaction", "OBSERVED_IN")} <= kinds


def test_graph_is_available_only_after_a_real_write(monkeypatch, small_paths, tmp_path):
    class FakeClient:
        written = 0

        def connect(self):
            return None

        def clear_dataset_graph(self, dataset_id):
            return 0

        def persist_graph(self, graph_records, dataset_id="legacy"):
            return self.written

        def close(self):
            return None

    monkeypatch.setattr("backend.pipeline.orchestrator.Neo4jClient", FakeClient)
    orchestrator = AnalysisOrchestrator()
    assert orchestrator._persist_graph(RECORDS, "d")[0] is False
    FakeClient.written = 5
    available, message = orchestrator._persist_graph(RECORDS, "d")
    assert available and "persistence" in message.lower()


@pytest.fixture
def neo4j():
    client = Neo4jClient()
    try:
        client.connect()
    except Neo4jUnavailableError:
        pytest.skip("Neo4j is not reachable")
    dataset_id = f"test_{uuid.uuid4().hex}"
    client.persist_graph(build_transaction_graph(RECORDS), dataset_id=dataset_id)
    yield client, dataset_id
    client.clear_dataset_graph(dataset_id)
    client.close()


def test_persisted_edges_follow_value_flow(neo4j):
    client, dataset_id = neo4j
    with client._driver.session() as session:
        outputs = session.run("MATCH (t:Transaction {dataset_id: $d})-[:OUTPUT_TO]->(w:Wallet {dataset_id: $d}) RETURN t.txid AS t, w.wallet_id AS w", d=dataset_id).data()
        inputs = session.run("MATCH (:Wallet {dataset_id: $d})-[r:INPUT_FROM]->(:Transaction {dataset_id: $d}) RETURN count(r) AS n", d=dataset_id).single()["n"]
    assert {(row["t"], row["w"]) for row in outputs} == {("tx_1", "wallet_b"), ("tx_1", "wallet_c"), ("tx_2", "wallet_d"), ("tx_3", "stranger_2")}
    assert inputs == 3


def test_wallet_flow_graph_follows_money_not_geography(neo4j):
    client, dataset_id = neo4j
    graph = client.get_wallet_flow_graph(dataset_id, "wallet_a", depth=2)
    ids = {node["id"] for node in graph["nodes"]}
    assert {"wallet_a", "tx_1", "wallet_b", "wallet_c", "tx_2", "wallet_d", "8.8.8.8"} <= ids
    assert "stranger" not in ids and not any(node["type"] in {"Country", "ASN"} for node in graph["nodes"])
    edge_ids = [edge["id"] for edge in graph["edges"]]
    assert len(edge_ids) == len(set(edge_ids))
    directed = {(edge["type"], edge["source"], edge["target"]) for edge in graph["edges"]}
    assert ("INPUT_FROM", "wallet_a", "tx_1") in directed and ("OUTPUT_TO", "tx_1", "wallet_b") in directed
    assert ("OBSERVED_IN", "tx_1", "8.8.8.8") in directed
    assert client.get_wallet_flow_graph(dataset_id, "nobody")["nodes"] == []
