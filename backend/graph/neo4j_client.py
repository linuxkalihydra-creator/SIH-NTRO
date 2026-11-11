"""Neo4j persistence for the entity/transaction graph (optional: everything also works without it).

``persist_investigation`` writes one analysed dataset as a property graph an
investigator can query with Cypher::

    (:Wallet)-[:INPUT_FROM {amount}]->(:Transaction)-[:OUTPUT_TO {amount}]->(:Wallet)
    (:Transaction)-[:OBSERVED_IN {confidence, attributed}]->(:IP)      first-seen relay (inferred origin)
    (:Transaction)-[:RELAYED_BY {delay}]->(:IP)                        later relays
    (:Wallet)-[:BROADCAST_FROM {count}]->(:IP)                         attributed spending footprint
    (:Wallet)-[:BELONGS_TO]->(:Entity)-[:PART_OF]->(:Actor)
    (:Wallet)-[:IN_PATTERN {role}]->(:Pattern)<-[:PART_OF_PATTERN]-(:Transaction)
    (:IP)-[:BELONGS_TO_ASN]->(:ASN)      (:IP)-[:IP_COUNTRY]->(:Country)

Wallet, transaction, IP and actor nodes carry their risk score and level, so
queries such as "IPs that relayed for HIGH-risk wallets in two different actors"
need no application code. Every node carries ``dataset_id``; constraints, writes
and reads are scoped to it, so datasets never mix.

``docs/neo4j.md`` lists example queries; ``docker-compose.yml`` starts a local instance.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from dotenv import load_dotenv

from backend.graph.graph_builder import build_transaction_graph

if TYPE_CHECKING:  # pragma: no cover
    from backend.pipeline.investigation import Investigation


class Neo4jUnavailableError(RuntimeError):
    """Raised when a Neo4j dependency is required but not available."""


def _load_env() -> None:
    """Load the workspace .env file before reading local Neo4j settings."""
    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env", override=False)


_load_env()


logger = logging.getLogger(__name__)
_GRAPH_WRITE_LOCK = threading.RLock()
BATCH = 5000
CONNECT_TIMEOUT_SECONDS = 3.0

NODE_KEYS = {"Wallet": "wallet_id", "Transaction": "txid", "IP": "ip", "Entity": "entity_id", "Actor": "actor_id", "Pattern": "pattern_id",
             "ASN": "asn", "Country": "country"}
# (relationship type, source label, target label)
RELATIONSHIPS = {
    "INPUT_FROM": ("Wallet", "Transaction"), "OUTPUT_TO": ("Transaction", "Wallet"), "OBSERVED_IN": ("Transaction", "IP"),
    "RELAYED_BY": ("Transaction", "IP"), "BROADCAST_FROM": ("Wallet", "IP"), "BELONGS_TO": ("Wallet", "Entity"), "PART_OF": ("Entity", "Actor"),
    "IN_PATTERN": ("Wallet", "Pattern"), "PART_OF_PATTERN": ("Transaction", "Pattern"), "BELONGS_TO_ASN": ("IP", "ASN"), "IP_COUNTRY": ("IP", "Country"),
}


def _chunks(rows: list[Any], size: int = BATCH) -> Iterable[list[Any]]:
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


class Neo4jClient:
    """Wrapper around a Neo4j driver with batched, dataset-scoped writes."""

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
            raise Neo4jUnavailableError("The neo4j Python driver is not installed in this environment.") from exc

        try:
            self._driver = GraphDatabase.driver(self.uri, auth=(self.username, self.password), connection_timeout=CONNECT_TIMEOUT_SECONDS,
                                                connection_acquisition_timeout=CONNECT_TIMEOUT_SECONDS + 2, max_transaction_retry_time=5.0)
            self._driver.verify_connectivity()
        except Exception as exc:  # pragma: no cover - environment-specific.
            self.close()
            raise Neo4jUnavailableError(f"Neo4j connectivity check failed for {self.uri}: {exc}") from exc

    def close(self) -> None:
        """Close the underlying driver if it exists."""
        if self._driver is not None:
            try:
                self._driver.close()
            finally:
                self._driver = None

    def _require(self):
        if self._driver is None:
            raise Neo4jUnavailableError("Neo4j connection is not available; call connect() first.")
        return self._driver

    def ensure_schema(self) -> None:
        """Create dataset-scoped uniqueness constraints (they also index every lookup the writes use)."""
        with self._require().session() as session:
            # Earlier versions keyed graph nodes globally. Drop only those known legacy
            # constraints so equal wallet/transaction identifiers can coexist by dataset.
            for constraint in ("wallet_id_unique", "transaction_txid_unique", "ip_unique", "country_unique", "asn_unique"):
                session.run(f"DROP CONSTRAINT {constraint} IF EXISTS").consume()
            for label, key in NODE_KEYS.items():
                session.run(f"CREATE CONSTRAINT {label.lower()}_dataset_id_unique IF NOT EXISTS FOR (n:{label}) REQUIRE (n.dataset_id, n.{key}) IS UNIQUE").consume()

    def clear_graph(self) -> None:
        """Remove all existing graph data in the database."""
        with self._require().session() as session:
            session.run("MATCH (n) DETACH DELETE n").consume()

    def clear_dataset_graph(self, dataset_id: str) -> int:
        """Remove all graph data for a specific dataset, in batches so large graphs do not exhaust memory."""
        total = 0
        with self._require().session() as session:
            while True:
                record = session.run(
                    "MATCH (n {dataset_id: $dataset_id}) WITH n LIMIT 20000 DETACH DELETE n RETURN count(n) AS count", dataset_id=str(dataset_id),
                ).single()
                deleted = record["count"] if record else 0
                total += deleted
                if deleted < 20000:
                    break
        if total:
            logger.info("Cleared %d nodes for dataset %s", total, dataset_id)
        return total

    # ── writes ──────────────────────────────────────────────────────────────
    def _write(self, dataset_id: str, nodes: dict[str, list[dict[str, Any]]], relationships: dict[str, list[dict[str, Any]]]) -> int:
        """Replace one dataset's graph. ``nodes[label]`` rows hold the key property plus any others;
        ``relationships[type]`` rows hold ``source``, ``target`` and optional ``props``."""
        with _GRAPH_WRITE_LOCK:
            if self._driver is None:
                self.connect()
            self.ensure_schema()
            self.clear_dataset_graph(dataset_id)
            start = time.perf_counter()
            written = 0
            with self._require().session() as session:
                for label, rows in nodes.items():
                    key = NODE_KEYS[label]
                    for chunk in _chunks(rows):
                        session.run(f"UNWIND $rows AS row MERGE (n:{label} {{dataset_id: $dataset_id, {key}: row.{key}}}) SET n += row, n.id = toString(row.{key})",
                                    rows=chunk, dataset_id=str(dataset_id)).consume()
                    written += len(rows)
                logger.info("Neo4j nodes written in %.2fs", time.perf_counter() - start)
                mark = time.perf_counter()
                for kind, rows in relationships.items():
                    source_label, target_label = RELATIONSHIPS[kind]
                    query = (f"UNWIND $rows AS row "
                             f"MATCH (source:{source_label} {{dataset_id: $dataset_id, {NODE_KEYS[source_label]}: row.source}}) "
                             f"MATCH (target:{target_label} {{dataset_id: $dataset_id, {NODE_KEYS[target_label]}: row.target}}) "
                             f"MERGE (source)-[link:{kind}]->(target) SET link += row.props")
                    for chunk in _chunks(rows):
                        session.run(query, rows=chunk, dataset_id=str(dataset_id)).consume()
                    written += len(rows)
                logger.info("Neo4j relationships written in %.2fs", time.perf_counter() - mark)
            logger.info("Neo4j graph for dataset %s: %d nodes and relationships in %.2fs", dataset_id, written, time.perf_counter() - start)
            return written

    def persist_investigation(self, investigation: "Investigation", dataset_id: str = "legacy") -> int:
        """Write an analysed dataset with risk scores, entities, actors, patterns and network observations."""
        flows, network = investigation.flows, investigation.network
        wallets, transactions = investigation.wallets, investigation.transactions
        nodes: dict[str, list[dict[str, Any]]] = {label: [] for label in NODE_KEYS}
        links: dict[str, list[dict[str, Any]]] = {kind: [] for kind in RELATIONSHIPS}

        for row in wallets[["wallet_id", "risk_score", "risk_level", "confidence", "is_seed", "entity_id", "actor_id", "cluster_id", "total_received", "total_sent",
                            "signal_anomaly", "signal_pattern", "signal_taint", "signal_network"]].itertuples(index=False):
            nodes["Wallet"].append({"wallet_id": row.wallet_id, "risk_score": float(row.risk_score), "risk_level": row.risk_level, "confidence": float(row.confidence),
                                    "is_seed": bool(row.is_seed), "entity_id": row.entity_id, "actor_id": row.actor_id, "community": int(row.cluster_id),
                                    "total_received": float(row.total_received), "total_sent": float(row.total_sent), "anomaly": float(row.signal_anomaly),
                                    "pattern": float(row.signal_pattern), "taint": float(row.signal_taint), "network": float(row.signal_network)})
            links["BELONGS_TO"].append({"source": row.wallet_id, "target": row.entity_id, "props": {}})
        risk = dict(zip(transactions.index, zip(transactions["risk_score"], transactions["risk_level"])))
        for tx in flows.txs:
            origin = network.origins[tx.index]
            score, level = risk[tx.txid]
            nodes["Transaction"].append({"txid": tx.txid, "timestamp": tx.time.isoformat(), "total_input": round(tx.total_in, 8), "total_output": round(tx.total_out, 8),
                                         "fee": round(tx.fee, 8), "input_count": len(tx.inputs), "output_count": len(tx.outputs), "risk_score": float(score),
                                         "risk_level": level, "relay_count": tx.relay_count, "origin_ip": origin.ip, "origin_confidence": round(origin.confidence, 3),
                                         "script_type": tx.script_type})
            merged: dict[str, float] = {}
            for address, amount in tx.inputs:
                merged[address] = merged.get(address, 0.0) + amount
            links["INPUT_FROM"].extend({"source": address, "target": tx.txid, "props": {"amount": round(amount, 8)}} for address, amount in merged.items())
            merged = {}
            for address, amount in tx.outputs:
                merged[address] = merged.get(address, 0.0) + amount
            links["OUTPUT_TO"].extend({"source": tx.txid, "target": address, "props": {"amount": round(amount, 8)}} for address, amount in merged.items())
            if tx.src_ip:
                links["OBSERVED_IN"].append({"source": tx.txid, "target": tx.src_ip,
                                             "props": {"confidence": round(origin.confidence, 3), "attributed": origin.attributed, "port": tx.dst_port}})
            seen = {tx.src_ip}
            for observation in tx.observations[1:]:
                if observation.src_ip and observation.src_ip not in seen:
                    seen.add(observation.src_ip)
                    links["RELAYED_BY"].append({"source": tx.txid, "target": observation.src_ip, "props": {"delay": round(observation.ts - tx.observations[0].ts, 3)}})
        asns: dict[int, str] = {}
        countries: set[str] = set()
        for row in investigation.ips.itertuples(index=False):
            nodes["IP"].append({"ip": row.ip, "country": row.country, "asn": int(row.asn or 0), "as_org": row.as_org, "risk_score": float(row.risk_score),
                                "risk_level": row.risk_level, "originated": int(row.originated), "relayed": int(row.relayed_count), "wallets": int(row.wallets),
                                "relay_ratio": float(row.relay_ratio)})
            if row.asn:
                asns.setdefault(int(row.asn), row.as_org)
                links["BELONGS_TO_ASN"].append({"source": row.ip, "target": int(row.asn), "props": {}})
            if row.country:
                countries.add(row.country)
                links["IP_COUNTRY"].append({"source": row.ip, "target": row.country, "props": {}})
        nodes["ASN"] = [{"asn": number, "org": org} for number, org in asns.items()]
        nodes["Country"] = [{"country": code} for code in sorted(countries)]
        for address, footprint in network.wallets.items():
            links["BROADCAST_FROM"].extend({"source": address, "target": ip, "props": {"count": int(uses)}} for ip, uses in footprint.ips.items())
        for row in investigation.entities[["entity_id", "address_count", "service_score", "counterparty_entities"]].itertuples(index=False):
            nodes["Entity"].append({"entity_id": row.entity_id, "wallets": int(row.address_count), "service_score": float(row.service_score),
                                    "counterparties": int(row.counterparty_entities)})
            links["PART_OF"].append({"source": row.entity_id, "target": investigation.actors.actor_of[row.entity_id], "props": {}})
        for row in investigation.actor_frame[["actor_id", "address_count", "entity_count", "risk_score", "risk_level", "relay_ips", "relay_asns"]].itertuples(index=False):
            nodes["Actor"].append({"actor_id": row.actor_id, "wallets": int(row.address_count), "entities": int(row.entity_count), "risk_score": float(row.risk_score),
                                   "risk_level": row.risk_level, "ips": int(row.relay_ips), "asns": int(row.relay_asns)})
        for pattern in investigation.patterns.values():
            nodes["Pattern"].append({"pattern_id": pattern["pattern_id"], "type": pattern["type"], "strength": float(pattern["strength"]), "summary": pattern["summary"],
                                     "total_value": float(pattern["total_value"])})
            links["IN_PATTERN"].extend({"source": address, "target": pattern["pattern_id"], "props": {"role": role}} for address, role in pattern["roles"].items())
            links["PART_OF_PATTERN"].extend({"source": txid, "target": pattern["pattern_id"], "props": {}} for txid in pattern["txids"])
        known_ips = {row["ip"] for row in nodes["IP"]}
        for kind in ("OBSERVED_IN", "RELAYED_BY", "BROADCAST_FROM"):
            links[kind] = [row for row in links[kind] if row["target"] in known_ips]
        return self._write(dataset_id, nodes, links)

    @staticmethod
    def score_rows(investigation: "Investigation") -> dict[str, list[dict[str, Any]]]:
        """The properties a re-score changes (risk, level, confidence, signals), per node label."""
        wallets, transactions = investigation.wallets, investigation.transactions
        rows: dict[str, list[dict[str, Any]]] = {"Wallet": [], "Transaction": [], "IP": [], "Actor": []}
        for row in wallets[["wallet_id", "risk_score", "risk_level", "confidence", "is_seed", "signal_anomaly", "signal_pattern", "signal_taint",
                            "signal_network"]].itertuples(index=False):
            rows["Wallet"].append({"wallet_id": row.wallet_id, "risk_score": float(row.risk_score), "risk_level": row.risk_level, "confidence": float(row.confidence),
                                   "is_seed": bool(row.is_seed), "anomaly": float(row.signal_anomaly), "pattern": float(row.signal_pattern),
                                   "taint": float(row.signal_taint), "network": float(row.signal_network)})
        for txid, score, level in zip(transactions.index, transactions["risk_score"], transactions["risk_level"]):
            rows["Transaction"].append({"txid": txid, "risk_score": float(score), "risk_level": level})
        for ip, score, level in zip(investigation.ips.index, investigation.ips["risk_score"], investigation.ips["risk_level"]):
            rows["IP"].append({"ip": ip, "risk_score": float(score), "risk_level": level})
        for actor, score, level in zip(investigation.actor_frame.index, investigation.actor_frame["risk_score"], investigation.actor_frame["risk_level"]):
            rows["Actor"].append({"actor_id": actor, "risk_score": float(score), "risk_level": level})
        return rows

    def update_scores(self, dataset_id: str, rows: dict[str, list[dict[str, Any]]]) -> int:
        """Refresh risk properties in place (``rows`` from :meth:`score_rows`); the graph structure is left untouched.

        Used after the seed wallets change: only scores move, so nodes are matched and updated instead of the
        dataset's graph being rewritten. Returns the number of nodes updated.
        """
        with _GRAPH_WRITE_LOCK:
            if self._driver is None:
                self.connect()
            updated = 0
            with self._require().session() as session:
                for label, items in rows.items():
                    key = NODE_KEYS[label]
                    query = f"UNWIND $rows AS row MATCH (n:{label} {{dataset_id: $dataset_id, {key}: row.{key}}}) SET n += row RETURN count(n) AS count"
                    for chunk in _chunks(items):
                        record = session.run(query, rows=chunk, dataset_id=str(dataset_id)).single()
                        updated += record["count"] if record else 0
            return updated

    def persist_graph(self, graph_records: list[dict[str, Any]], dataset_id: str = "legacy") -> int:
        """Write a plain transaction graph (no scores) from records or from ``build_transaction_graph`` rows."""
        if not graph_records:
            return 0
        rows = list(graph_records) if all("type" in record for record in graph_records) else build_transaction_graph(graph_records)
        nodes: dict[str, dict[Any, dict[str, Any]]] = {label: {} for label in NODE_KEYS}
        links: dict[str, dict[tuple[Any, Any], dict[str, Any]]] = {kind: {} for kind in RELATIONSHIPS}
        for record in rows:
            label, identifier, relationship = str(record.get("type", "")), record.get("id"), str(record.get("relationship", ""))
            if label not in NODE_KEYS or identifier is None:
                continue
            identifier = int(identifier) if label == "ASN" else str(identifier)
            nodes[label].setdefault(identifier, {NODE_KEYS[label]: identifier})
            if label == "Wallet" and record.get("txid") is not None:
                pair = (str(record["txid"]), identifier) if relationship == "OUTPUT_TO" else (identifier, str(record["txid"]))
                links["OUTPUT_TO" if relationship == "OUTPUT_TO" else "INPUT_FROM"].setdefault(pair, {})
            elif label == "IP" and record.get("txid") is not None:
                links["OBSERVED_IN"].setdefault((str(record["txid"]), identifier), {})
            elif label == "ASN" and record.get("ip") is not None:
                links["BELONGS_TO_ASN"].setdefault((str(record["ip"]), identifier), {})
            elif label == "Country" and record.get("ip") is not None:
                links["IP_COUNTRY"].setdefault((str(record["ip"]), identifier), {})
        written = self._write(dataset_id, {label: list(items.values()) for label, items in nodes.items()},
                              {kind: [{"source": source, "target": target, "props": props} for (source, target), props in items.items()] for kind, items in links.items()})
        return written

    def get_status(self) -> dict[str, Any]:
        """Return a status dictionary that is safe for local diagnostics."""
        return {
            "uri": self.uri,
            "username": self.username,
            "connected": self._driver is not None,
            "availability": "local_neo4j_available" if self._driver is not None else "requires_local_neo4j",
        }

    # ── reads ───────────────────────────────────────────────────────────────
    def get_wallet_flow_graph(self, dataset_id: str, wallet_id: str, depth: int = 1, max_nodes: int = 160, max_edges: int = 400) -> dict[str, Any]:
        """Wallet neighbourhood along value flow only, nearest nodes first, plus the IPs that relayed the wallet's transactions.

        ``depth`` counts transaction hops (Wallet -> Transaction -> Wallet is one hop).
        Country/ASN nodes are never traversed: two wallets in the same country are not linked.
        """
        if self._driver is None:
            self.connect()
        hops = 2 * max(1, min(int(depth), 2))
        with self._require().session() as session:
            node_rows = list(session.run(
                f"""
                MATCH (root:Wallet {{dataset_id: $dataset_id, wallet_id: $wallet_id}})
                MATCH path = (root)-[:INPUT_FROM|OUTPUT_TO*0..{hops}]-(node)
                WHERE node.dataset_id = $dataset_id
                WITH node, min(length(path)) AS distance
                ORDER BY distance
                LIMIT $max_nodes
                RETURN elementId(node) AS internal_id, labels(node)[0] AS label, properties(node) AS properties
                UNION
                MATCH (root:Wallet {{dataset_id: $dataset_id, wallet_id: $wallet_id}})-[:INPUT_FROM|OUTPUT_TO]-(:Transaction)-[:OBSERVED_IN]->(node:IP)
                WHERE node.dataset_id = $dataset_id
                RETURN DISTINCT elementId(node) AS internal_id, labels(node)[0] AS label, properties(node) AS properties
                """,
                dataset_id=str(dataset_id), wallet_id=str(wallet_id), max_nodes=max_nodes,
            ))
            if not node_rows:
                return {"nodes": [], "edges": [], "limited": False}
            internal_ids = [row["internal_id"] for row in node_rows]
            edge_rows = list(session.run(
                """
                MATCH (source)-[relationship:INPUT_FROM|OUTPUT_TO|OBSERVED_IN]->(target)
                WHERE elementId(source) IN $internal_ids AND elementId(target) IN $internal_ids
                RETURN elementId(source) AS source, elementId(target) AS target, type(relationship) AS type
                LIMIT $max_edges
                """,
                internal_ids=internal_ids, max_edges=max_edges,
            ))
        ids = {row["internal_id"]: str(row["properties"].get(NODE_KEYS.get(row["label"], "id"), row["internal_id"])) for row in node_rows}
        nodes = [{"id": ids[row["internal_id"]], "type": row["label"]} for row in node_rows]
        edges = [{"id": f"{ids[row['source']]}>{ids[row['target']]}", "source": ids[row["source"]], "target": ids[row["target"]], "type": row["type"]}
                 for row in edge_rows]
        return {"nodes": nodes, "edges": edges, "limited": len(node_rows) >= max_nodes or len(edge_rows) >= max_edges}

    def run_query(self, query: str, **parameters: Any) -> list[dict[str, Any]]:
        """Run a read-only Cypher query (used by the example queries in docs/neo4j.md and by the tests)."""
        with self._require().session(default_access_mode="READ") as session:
            return session.run(query, **parameters).data()
