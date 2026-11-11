import uuid

import pytest

from backend.enrichment.service import enrich_dataset
from backend.graph.graph_builder import build_transaction_graph
from backend.graph.neo4j_client import NODE_KEYS, RELATIONSHIPS, Neo4jClient, Neo4jUnavailableError
from backend.pipeline.investigation import Investigation
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
    assert {("Wallet", "INPUT_FROM"), ("Wallet", "OUTPUT_TO"), ("IP", "OBSERVED_IN"), ("Transaction", "OBSERVED_IN"), ("Country", "IP_COUNTRY")} <= kinds
    # Geography belongs to the relaying IP, never to the wallets that merely receive an output.
    assert not any(row["type"] in ("Country", "ASN") and "wallet" in row for row in graph)


def test_graph_schema_covers_every_relationship_endpoint():
    assert all(source in NODE_KEYS and target in NODE_KEYS for source, target in RELATIONSHIPS.values())


def test_graph_is_available_only_after_a_real_write(monkeypatch):
    class FakeClient:
        written = 0

        def connect(self):
            return None

        def persist_investigation(self, investigation, dataset_id="legacy"):
            return self.written

        def close(self):
            return None

    monkeypatch.setattr("backend.pipeline.orchestrator.Neo4jClient", FakeClient)
    orchestrator = AnalysisOrchestrator()
    assert orchestrator._persist_graph(object(), "d")[0] is False
    FakeClient.written = 5
    available, message = orchestrator._persist_graph(object(), "d")
    assert available and "persistence" in message.lower()


def test_unreachable_neo4j_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:1")  # nothing listens here
    available, message = AnalysisOrchestrator()._persist_graph(object(), "d")
    assert available is False and "not reachable" in message.lower()


@pytest.fixture
def neo4j():
    client = Neo4jClient()
    try:
        client.connect()
    except Neo4jUnavailableError:
        pytest.skip("Neo4j is not reachable")
    dataset_id = f"test_{uuid.uuid4().hex}"
    yield client, dataset_id
    client.clear_dataset_graph(dataset_id)
    client.close()


def test_persisted_edges_follow_value_flow(neo4j):
    client, dataset_id = neo4j
    client.persist_graph(build_transaction_graph(RECORDS), dataset_id=dataset_id)
    outputs = client.run_query("MATCH (t:Transaction {dataset_id: $d})-[:OUTPUT_TO]->(w:Wallet {dataset_id: $d}) RETURN t.txid AS t, w.wallet_id AS w", d=dataset_id)
    inputs = client.run_query("MATCH (:Wallet {dataset_id: $d})-[r:INPUT_FROM]->(:Transaction {dataset_id: $d}) RETURN count(r) AS n", d=dataset_id)[0]["n"]
    assert {(row["t"], row["w"]) for row in outputs} == {("tx_1", "wallet_b"), ("tx_1", "wallet_c"), ("tx_2", "wallet_d"), ("tx_3", "stranger_2")}
    assert inputs == 3


def test_wallet_flow_graph_follows_money_not_geography(neo4j):
    client, dataset_id = neo4j
    client.persist_graph(build_transaction_graph(RECORDS), dataset_id=dataset_id)
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


def test_analysed_graph_carries_scores_entities_actors_and_relays(neo4j, small_world):
    client, dataset_id = neo4j
    records, _, truth = small_world
    enriched, ip_info = enrich_dataset(records)
    investigation = Investigation.build(enriched, truth["seed_wallets"], ip_info=ip_info)
    written = client.persist_investigation(investigation, dataset_id=dataset_id)
    assert written > len(investigation.wallets) + len(investigation.transactions)
    counts = {row["label"]: row["n"] for row in client.run_query("MATCH (n {dataset_id: $d}) RETURN labels(n)[0] AS label, count(n) AS n", d=dataset_id)}
    assert counts["Wallet"] == len(investigation.wallets) and counts["Transaction"] == len(investigation.transactions)
    assert counts["Entity"] == len(investigation.clustering.members) and counts["Actor"] == len(investigation.actors.members)
    assert counts["Pattern"] == len(investigation.patterns) and counts["IP"] == len(investigation.ips)
    seed = truth["seed_wallets"][0]
    row = client.run_query("MATCH (w:Wallet {dataset_id: $d, wallet_id: $w})-[:BELONGS_TO]->(e:Entity)-[:PART_OF]->(a:Actor) "
                           "RETURN w.risk_score AS risk, w.is_seed AS seed, e.entity_id AS entity, a.actor_id AS actor", d=dataset_id, w=seed)[0]
    assert row["risk"] == 100.0 and row["seed"] is True
    assert row["entity"] == investigation.clustering.entity_of[seed] and row["actor"] == investigation.wallets.at[seed, "actor_id"]
    # A question that needs both layers: IPs that originated transactions for HIGH-risk wallets.
    hot = client.run_query("MATCH (w:Wallet {dataset_id: $d})-[:BROADCAST_FROM]->(ip:IP) WHERE w.risk_score >= 60 "
                           "RETURN ip.ip AS ip, count(DISTINCT w) AS wallets ORDER BY wallets DESC LIMIT 3", d=dataset_id)
    assert hot and hot[0]["wallets"] >= 2
    assert client.run_query("MATCH (:Transaction {dataset_id: $d})-[r:RELAYED_BY]->(:IP) RETURN count(r) AS n", d=dataset_id)[0]["n"] > 0
    # A seed change only moves scores: they are refreshed in place and the structure is left alone.
    assert investigation.rescore([]) == []
    assert client.update_scores(dataset_id, Neo4jClient.score_rows(investigation)) == \
        len(investigation.wallets) + len(investigation.transactions) + len(investigation.ips) + len(investigation.actor_frame)
    refreshed = client.run_query("MATCH (w:Wallet {dataset_id: $d, wallet_id: $w})-[:BELONGS_TO]->(e:Entity) RETURN w.risk_score AS risk, w.is_seed AS seed, "
                                 "e.entity_id AS entity", d=dataset_id, w=seed)[0]
    assert refreshed["seed"] is False and refreshed["risk"] == float(investigation.wallets.at[seed, "risk_score"]) < 100.0
    assert refreshed["entity"] == row["entity"]
    assert client.update_scores("no_such_dataset", Neo4jClient.score_rows(investigation)) == 0   # nothing is created by a refresh
    investigation.rescore(truth["seed_wallets"])
    # Re-writing replaces the dataset's graph instead of duplicating it.
    assert client.persist_investigation(investigation, dataset_id=dataset_id) == written
    assert client.run_query("MATCH (n:Wallet {dataset_id: $d}) RETURN count(n) AS n", d=dataset_id)[0]["n"] == len(investigation.wallets)
