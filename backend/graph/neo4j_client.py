"""Neo4j client wrapper for offline-safe analysis tooling."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.graph.graph_builder import build_transaction_graph


class Neo4jUnavailableError(RuntimeError):
    """Raised when a Neo4j dependency is required but not available."""


def _load_env() -> None:
    """Load the workspace .env file before reading local Neo4j settings."""
    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env", override=False)


_load_env()


logger = logging.getLogger(__name__)
_GRAPH_WRITE_LOCK = threading.RLock()


class Neo4jClient:
    """Wrapper around a Neo4j driver with safe batch writes and validation."""

    def __init__(self, uri: str | None = None, username: str | None = None, password: str | None = None):
        self.uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.username = username or os.getenv("NEO4J_USERNAME", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "password")
        self._driver = None

    def connect(self) -> None:
        """Connect to Neo4j if the driver is installed and reachable."""
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:  # pragma: no cover - environment-specific.
            raise Neo4jUnavailableError(
                "Neo4j is not installed in this environment. Install it locally with Ubuntu packages or Docker if needed for graph testing."
            ) from exc

        self._driver = GraphDatabase.driver(self.uri, auth=(self.username, self.password))
        try:
            self._driver.verify_connectivity()
        except Exception as exc:  # pragma: no cover - environment-specific.
            self.close()
            raise Neo4jUnavailableError(f"Neo4j connectivity check failed for {self.uri}: {exc}") from exc

    def close(self) -> None:
        """Close the underlying driver if it exists."""
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def ensure_schema(self) -> None:
        """Create dataset-scoped uniqueness constraints for the graph schema."""
        if self._driver is None:
            raise Neo4jUnavailableError("Neo4j connection is not available; call connect() first.")
        with self._driver.session() as session:
            # Earlier versions keyed graph nodes globally. Drop only those known legacy
            # constraints so equal wallet/transaction identifiers can coexist by dataset.
            for constraint in ("wallet_id_unique", "transaction_txid_unique", "ip_unique", "country_unique", "asn_unique"):
                session.run(f"DROP CONSTRAINT {constraint} IF EXISTS").consume()
            session.run("CREATE CONSTRAINT wallet_dataset_id_unique IF NOT EXISTS FOR (w:Wallet) REQUIRE (w.dataset_id, w.wallet_id) IS UNIQUE").consume()
            session.run("CREATE CONSTRAINT transaction_dataset_id_unique IF NOT EXISTS FOR (t:Transaction) REQUIRE (t.dataset_id, t.txid) IS UNIQUE").consume()
            session.run("CREATE CONSTRAINT ip_dataset_id_unique IF NOT EXISTS FOR (i:IP) REQUIRE (i.dataset_id, i.ip) IS UNIQUE").consume()
            session.run("CREATE CONSTRAINT country_dataset_id_unique IF NOT EXISTS FOR (c:Country) REQUIRE (c.dataset_id, c.country) IS UNIQUE").consume()
            session.run("CREATE CONSTRAINT asn_dataset_id_unique IF NOT EXISTS FOR (a:ASN) REQUIRE (a.dataset_id, a.asn) IS UNIQUE").consume()

    def clear_graph(self) -> None:
        """Remove all existing graph data in the database."""
        if self._driver is None:
            raise Neo4jUnavailableError("Neo4j connection is not available; call connect() first.")
        with self._driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n").consume()

    def clear_dataset_graph(self, dataset_id: str) -> int:
        """Remove all graph data for a specific dataset, ensuring investigation isolation."""
        if self._driver is None:
            raise Neo4jUnavailableError("Neo4j connection is not available; call connect() first.")
        with self._driver.session() as session:
            result = session.run(
                "MATCH (n {dataset_id: $dataset_id}) DETACH DELETE n RETURN count(n) as count",
                dataset_id=dataset_id
            )
            record = result.single()
            count = record["count"] if record else 0
            if count > 0:
                logger.info(f"Cleared {count} nodes for dataset {dataset_id}")
            return count

    def _normalize_graph_records(self, graph_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize raw record dictionaries into graph node entries for batch writes."""
        if not graph_records:
            return []
        if all("type" in record for record in graph_records):
            return list(graph_records)
        return build_transaction_graph(graph_records)

    def persist_graph(self, graph_records: list[dict[str, Any]], dataset_id: str = "legacy") -> int:
        """Serialize graph replacement writes within this application process."""
        with _GRAPH_WRITE_LOCK:
            return self._persist_graph_unlocked(graph_records, dataset_id)

    def _persist_graph_unlocked(self, graph_records: list[dict[str, Any]], dataset_id: str) -> int:
        """Persist a graph payload into Neo4j using batched Cypher statements."""
        if not graph_records:
            return 0
        if self._driver is None:
            self.connect()
        self.ensure_schema()

        normalized = self._normalize_graph_records(graph_records)
        if not normalized:
            return 0

        node_rows = []
        rel_rows = {"INPUT_FROM": [], "OUTPUT_TO": [], "OBSERVED_IN": [], "LOCATED_IN": [], "HAS_ASN": [], "IP_BELONGS_TO_ASN": [], "IP_COUNTRY": []}
        for record in normalized:
            node_type = str(record.get("type", "")).strip()
            node_id = record.get("id")
            if node_type and node_id is not None:
                node_rows.append({"type": node_type, "id": str(node_id), "dataset_id": str(dataset_id)})

            relationship = str(record.get("relationship", "")).strip()
            if not relationship:
                continue

            if node_type == "Wallet" and record.get("txid") is not None:
                rel_rows.setdefault(relationship, []).append({"source": str(node_id), "target": str(record.get("txid"))})
            elif node_type == "IP" and record.get("txid") is not None:
                rel_rows.setdefault("OBSERVED_IN", []).append({"source": str(record.get("txid")), "target": str(node_id)})
            elif node_type == "Country" and record.get("wallet") is not None:
                rel_rows.setdefault("LOCATED_IN", []).append({"source": str(record.get("wallet")), "target": str(node_id)})
            elif node_type == "ASN" and record.get("wallet") is not None:
                rel_rows.setdefault("HAS_ASN", []).append({"source": str(record.get("wallet")), "target": str(node_id)})
            elif node_type == "ASN" and record.get("ip") is not None:
                rel_rows.setdefault("IP_BELONGS_TO_ASN", []).append({"source": str(record.get("ip")), "target": str(node_id)})
            elif node_type == "Country" and record.get("ip") is not None:
                rel_rows.setdefault("IP_COUNTRY", []).append({"source": str(record.get("ip")), "target": str(node_id)})

        start = time.perf_counter()
        with self._driver.session() as session:
            node_start = time.perf_counter()
            node_types = [
                ("Wallet", "MERGE (w:Wallet {dataset_id: node.dataset_id, wallet_id: node.id}) SET w.id = node.id"),
                ("Transaction", "MERGE (t:Transaction {dataset_id: node.dataset_id, txid: node.id}) SET t.id = node.id"),
                ("IP", "MERGE (ip:IP {dataset_id: node.dataset_id, ip: node.id}) SET ip.id = node.id"),
                ("ASN", "MERGE (asn:ASN {dataset_id: node.dataset_id, asn: toInteger(node.id)})"),
                ("Country", "MERGE (country:Country {dataset_id: node.dataset_id, country: node.id})"),
            ]
            for node_type_name, merge_clause in node_types:
                filtered = [row for row in node_rows if row["type"] == node_type_name]
                if not filtered:
                    continue
                for index in range(0, len(filtered), 500):
                    session.run(
                        f"""
                        UNWIND $nodes AS node
                        {merge_clause}
                        """,
                        nodes=filtered[index:index + 500],
                    ).consume()
            logger.info("Neo4j node batch elapsed %.3fs", time.perf_counter() - node_start)

            relationship_start = time.perf_counter()
            for kind, rows in rel_rows.items():
                if not rows:
                    continue
                rows = sorted(rows, key=lambda row: (row["source"], row["target"]))
                if kind == "INPUT_FROM":
                    query = """
                    UNWIND $rels AS rel
                    MATCH (source:Wallet {dataset_id: $dataset_id, wallet_id: rel.source})
                    MATCH (target:Transaction {dataset_id: $dataset_id, txid: rel.target})
                    MERGE (source)-[:INPUT_FROM]->(target)
                    """
                elif kind == "OUTPUT_TO":
                    query = """
                    UNWIND $rels AS rel
                    MATCH (source:Transaction {dataset_id: $dataset_id, txid: rel.source})
                    MATCH (target:Wallet {dataset_id: $dataset_id, wallet_id: rel.target})
                    MERGE (source)-[:OUTPUT_TO]->(target)
                    """
                elif kind == "OBSERVED_IN":
                    query = """
                    UNWIND $rels AS rel
                    MATCH (source:Transaction {dataset_id: $dataset_id, txid: rel.source})
                    MATCH (target:IP {dataset_id: $dataset_id, ip: rel.target})
                    MERGE (source)-[:OBSERVED_IN]->(target)
                    """
                elif kind == "LOCATED_IN":
                    query = """
                    UNWIND $rels AS rel
                    MATCH (source:Wallet {dataset_id: $dataset_id, wallet_id: rel.source})
                    MATCH (target:Country {dataset_id: $dataset_id, country: rel.target})
                    MERGE (source)-[:LOCATED_IN]->(target)
                    """
                elif kind == "HAS_ASN":
                    query = """
                    UNWIND $rels AS rel
                    MATCH (source:Wallet {dataset_id: $dataset_id, wallet_id: rel.source})
                    MATCH (target:ASN {dataset_id: $dataset_id, asn: toInteger(rel.target)})
                    MERGE (source)-[:HAS_ASN]->(target)
                    """
                elif kind == "IP_BELONGS_TO_ASN":
                    query = """
                    UNWIND $rels AS rel
                    MATCH (source:IP {dataset_id: $dataset_id, ip: rel.source})
                    MATCH (target:ASN {dataset_id: $dataset_id, asn: toInteger(rel.target)})
                    MERGE (source)-[:BELONGS_TO_ASN]->(target)
                    """
                elif kind == "IP_COUNTRY":
                    query = """
                    UNWIND $rels AS rel
                    MATCH (source:IP {dataset_id: $dataset_id, ip: rel.source})
                    MATCH (target:Country {dataset_id: $dataset_id, country: rel.target})
                    MERGE (source)-[:IP_COUNTRY]->(target)
                    """
                else:
                    continue
                for index in range(0, len(rows), 500):
                    session.run(query, rels=rows[index:index + 500], dataset_id=str(dataset_id)).consume()
            logger.info("Neo4j relationship batch elapsed %.3fs", time.perf_counter() - relationship_start)

        elapsed = time.perf_counter() - start
        logger.info("Neo4j persist_graph total elapsed %.3fs for %d graph records", elapsed, len(normalized))
        return len(normalized)

    def get_status(self) -> dict[str, Any]:
        """Return a status dictionary that is safe for local diagnostics."""
        return {
            "uri": self.uri,
            "username": self.username,
            "connected": self._driver is not None,
            "availability": "local_neo4j_available" if self._driver is not None else "requires_local_neo4j",
        }

    def get_neighborhood(self, dataset_id: str, wallet_id: str, depth: int = 2, max_nodes: int = 150, max_edges: int = 300) -> dict[str, Any]:
        """Return a bounded Neo4j neighborhood without exposing driver objects."""
        if self._driver is None:
            self.connect()

        depth = max(1, min(int(depth), 3))
        max_nodes = max(1, min(int(max_nodes), 500))
        max_edges = max(1, min(int(max_edges), 1000))
        traversal = f"[*1..{depth}]"

        with self._driver.session() as session:
            node_result = session.run(
                f"""
                MATCH (root:Wallet {{dataset_id: $dataset_id, wallet_id: $wallet_id}})
                MATCH (root)-[*0..{depth}]-(node)
                WHERE node.dataset_id = $dataset_id
                WITH DISTINCT node
                LIMIT $max_nodes
                RETURN elementId(node) AS internal_id, labels(node) AS labels, properties(node) AS properties
                """,
                wallet_id=str(wallet_id),
                dataset_id=str(dataset_id),
                max_nodes=max_nodes,
            )
            node_rows = list(node_result)
            if not node_rows:
                return {"graph_available": True, "nodes": [], "edges": [], "depth": depth}

            internal_ids = [row["internal_id"] for row in node_rows]
            edge_result = session.run(
                """
                UNWIND $internal_ids AS node_id
                MATCH (source)-[relationship]-(target)
                WHERE elementId(source) IN $internal_ids AND elementId(target) IN $internal_ids
                WITH DISTINCT relationship, source, target
                LIMIT $max_edges
                RETURN elementId(relationship) AS relationship_id,
                       elementId(source) AS source_internal_id,
                       elementId(target) AS target_internal_id,
                       type(relationship) AS relationship_type,
                       properties(relationship) AS relationship_properties
                """,
                internal_ids=internal_ids,
                max_edges=max_edges,
            )
            edge_rows = list(edge_result)

        def public_id(row: Any) -> str:
            properties = dict(row["properties"] or {})
            labels = row["labels"] or []
            node_type = str(labels[0]) if labels else "Entity"
            identifier = {
                "Wallet": properties.get("wallet_id"),
                "Transaction": properties.get("txid"),
                "IP": properties.get("ip"),
                "ASN": properties.get("asn"),
                "Country": properties.get("country"),
            }.get(node_type)
            return str(identifier if identifier is not None else row["internal_id"])

        nodes = []
        ids_by_internal = {}
        for row in node_rows:
            properties = dict(row["properties"] or {})
            node_type = str((row["labels"] or ["Entity"])[0])
            node_id = public_id(row)
            ids_by_internal[row["internal_id"]] = node_id
            nodes.append({"id": node_id, "type": node_type, "label": node_id, "properties": properties})

        edges = []
        for row in edge_rows:
            source = ids_by_internal.get(row["source_internal_id"])
            target = ids_by_internal.get(row["target_internal_id"])
            if source is None or target is None:
                continue
            edges.append({
                "id": str(row["relationship_id"]),
                "source": source,
                "target": target,
                "type": str(row["relationship_type"]),
                "properties": dict(row["relationship_properties"] or {}),
            })

        return {"graph_available": True, "nodes": nodes, "edges": edges, "depth": depth}

    def get_cluster_graph(self, dataset_id: str, wallet_ids: list[str], max_nodes: int = 200, max_edges: int = 500) -> dict[str, Any]:
        """Return a bounded graph seeded only by the selected cluster's wallets."""
        if self._driver is None:
            self.connect()
        seeds = [str(wallet_id) for wallet_id in wallet_ids]
        if not seeds:
            return {"graph_available": True, "nodes": [], "edges": []}
        max_nodes, max_edges = min(max(1, int(max_nodes)), 200), min(max(1, int(max_edges)), 500)
        with self._driver.session() as session:
            node_rows = list(session.run("""
                MATCH (wallet:Wallet {dataset_id: $dataset_id}) WHERE wallet.wallet_id IN $wallet_ids
                MATCH (wallet)-[*0..2]-(node)
                WHERE node.dataset_id = $dataset_id
                WITH DISTINCT node LIMIT $max_nodes
                RETURN elementId(node) AS internal_id, labels(node) AS labels, properties(node) AS properties
                """, dataset_id=str(dataset_id), wallet_ids=seeds, max_nodes=max_nodes))
            if not node_rows:
                return {"graph_available": True, "nodes": [], "edges": []}
            internal_ids = [row["internal_id"] for row in node_rows]
            edge_rows = list(session.run("""
                UNWIND $internal_ids AS node_id
                MATCH (source)-[relationship]-(target)
                WHERE elementId(source) IN $internal_ids AND elementId(target) IN $internal_ids
                WITH DISTINCT relationship, source, target LIMIT $max_edges
                RETURN elementId(relationship) AS relationship_id, elementId(source) AS source_internal_id,
                       elementId(target) AS target_internal_id, type(relationship) AS relationship_type,
                       properties(relationship) AS relationship_properties
                """, internal_ids=internal_ids, max_edges=max_edges))

        identifiers = {"Wallet": "wallet_id", "Transaction": "txid", "IP": "ip", "ASN": "asn", "Country": "country"}
        ids = {}
        nodes = []
        for row in node_rows:
            properties, labels = dict(row["properties"] or {}), row["labels"] or ["Entity"]
            node_type = str(labels[0])
            node_id = str(properties.get(identifiers.get(node_type), row["internal_id"]))
            ids[row["internal_id"]] = node_id
            nodes.append({"id": node_id, "type": node_type, "label": node_id, "properties": properties})
        edges = [{"id": str(row["relationship_id"]), "source": ids[row["source_internal_id"]], "target": ids[row["target_internal_id"]], "type": str(row["relationship_type"]), "properties": dict(row["relationship_properties"] or {})} for row in edge_rows if row["source_internal_id"] in ids and row["target_internal_id"] in ids]
        return {"graph_available": True, "nodes": nodes, "edges": edges}
