"""Plain transaction-graph rows from raw records (no models, no scores).

``Neo4jClient.persist_investigation`` writes the full analysed graph. This builder
is the light-weight alternative for loading raw records as a graph, and the
shape the graph tests use.
"""

from __future__ import annotations

from typing import Any


def build_transaction_graph(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Wallet / Transaction / IP / ASN / Country nodes and the links between them.

    The relaying IP is linked to the transaction, never to the wallets that
    *receive* an output: the network position of a transaction says nothing about
    where its recipients are.
    """
    rows: list[dict[str, Any]] = []
    for record in records:
        txid = str(record.get("txid", ""))
        if not txid:
            continue
        src_ip = str(record.get("src_ip", "") or "")
        asn = record.get("asn")
        country = record.get("geo_country")
        rows.append({"type": "Transaction", "id": txid, "relationship": "OBSERVED_IN", "ip": src_ip})
        if src_ip:
            rows.append({"type": "IP", "id": src_ip, "relationship": "OBSERVED_IN", "txid": txid})
            if asn:
                rows.append({"type": "ASN", "id": str(asn), "relationship": "IP_BELONGS_TO_ASN", "ip": src_ip, "txid": txid})
            if country:
                rows.append({"type": "Country", "id": str(country), "relationship": "IP_COUNTRY", "ip": src_ip, "txid": txid})
        for wallet in record.get("input_addresses", []):
            rows.append({"type": "Wallet", "id": str(wallet), "relationship": "INPUT_FROM", "txid": txid})
        for wallet in record.get("output_addresses", []):
            rows.append({"type": "Wallet", "id": str(wallet), "relationship": "OUTPUT_TO", "txid": txid})
    return rows
