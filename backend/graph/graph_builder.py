"""Graph-building utilities for the synthetic investigative prototype."""

from __future__ import annotations

from typing import Any


def build_transaction_graph(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create a lightweight graph representation from transactions.

    This keeps the graph payload ready for efficient batched Neo4j writes while
    preserving local in-memory graph exports for analysis and testing.
    """
    graph_nodes: list[dict[str, Any]] = []
    for record in records:
        txid = str(record.get("txid", ""))
        src_ip = str(record.get("src_ip", ""))
        asn = record.get("asn")
        country = record.get("geo_country")

        if txid:
            graph_nodes.append({"type": "Transaction", "id": txid, "relationship": "OBSERVED_IN", "ip": src_ip})
            graph_nodes.append({"type": "IP", "id": src_ip, "relationship": "OBSERVED_IN", "txid": txid})
            if asn:
                graph_nodes.append({"type": "ASN", "id": str(asn), "relationship": "IP_BELONGS_TO_ASN", "ip": src_ip, "txid": txid})
            if country:
                graph_nodes.append({"type": "Country", "id": str(country), "relationship": "IP_COUNTRY", "ip": src_ip, "txid": txid})

        for wallet in record.get("input_addresses", []):
            wallet_id = str(wallet)
            graph_nodes.append({"type": "Wallet", "id": wallet_id, "relationship": "INPUT_FROM", "txid": txid})
            if country:
                graph_nodes.append({"type": "Country", "id": str(country), "relationship": "LOCATED_IN", "wallet": wallet_id})
            if asn:
                graph_nodes.append({"type": "ASN", "id": str(asn), "relationship": "HAS_ASN", "wallet": wallet_id})

        for wallet in record.get("output_addresses", []):
            wallet_id = str(wallet)
            graph_nodes.append({"type": "Wallet", "id": wallet_id, "relationship": "OUTPUT_TO", "txid": txid})
            if country:
                graph_nodes.append({"type": "Country", "id": str(country), "relationship": "LOCATED_IN", "wallet": wallet_id})
            if asn:
                graph_nodes.append({"type": "ASN", "id": str(asn), "relationship": "HAS_ASN", "wallet": wallet_id})

    return graph_nodes
