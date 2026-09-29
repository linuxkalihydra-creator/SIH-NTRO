# HTTP API

The FastAPI backend (`uv run uvicorn backend.api.main:app`) serves everything the
single-page investigation UI needs. All analysis endpoints are scoped to one
uploaded dataset through `dataset_id`.

Errors use FastAPI's `{"detail": "..."}` body: `404` when the dataset, wallet,
transaction, pattern or cluster does not exist, `409` when the dataset has not
been analysed yet, `400` for invalid input.

Risk levels are `CRITICAL` (score >= 80), `HIGH` (>= 60), `MEDIUM` (>= 35) and
`LOW`. Scores are 0-100, confidences and signals are 0-1.

## Datasets

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | | `{status, graph_available, graph_status}` |
| GET | `/datasets` | | `DatasetMeta[]`, newest first |
| POST | `/datasets/upload` | multipart `file` (.csv/.json/.xml) | `201 DatasetMeta` |
| POST | `/analyze` | `{dataset_id, seed_wallets?: string[]}` | `{dataset_id, status: "completed", overview: Overview}` |
| PUT | `/datasets/{dataset_id}/seeds` | `{seed_wallets: string[]}` | `{overview: Overview, unknown_seeds: string[]}` (re-scores without re-running the models) |

`DatasetMeta`: `{dataset_id, filename, format, size_bytes, status, analysis_status, record_count, created_at, updated_at, error_message}`.
`status` is `uploaded | analyzing | ready | failed`.

## Overview

`GET /overview?dataset_id=...` returns `Overview`:

```json
{
  "dataset_id": "dataset_...", "filename": "transactions.csv", "analyzed_at": "2026-09-29T10:00:00+00:00",
  "processing_seconds": 21.4, "graph_available": true,
  "stats": {"transactions": 10000, "wallets": 6120, "ips": 540, "entities": 2900, "multi_address_entities": 610,
            "communities": 14, "countries": 31, "asns": 57, "start_time": "...", "end_time": "..."},
  "geo_sources": {"dbip": 10000},
  "risk_distribution": {"CRITICAL": 40, "HIGH": 120, "MEDIUM": 600, "LOW": 5360},
  "transaction_risk_distribution": {"CRITICAL": 30, "HIGH": 200, "MEDIUM": 900, "LOW": 8870},
  "patterns": {"peeling_chain": 6, "coinjoin": 25, "layering": 8, "rapid_passthrough": 30},
  "seeds": ["bc1q..."],
  "models": [{"name": "Isolation Forest", "family": "anomaly", "level": "wallet", "description": "...", "flagged": 306}],
  "evaluation": {"labels_available": true, "transaction_auc": 0.93,
                 "detectors": [{"label": "PEELING_CHAIN", "detector": "peeling_chain", "precision": 0.97, "recall": 0.91, "support": 180}],
                 "notes": "..."}
}
```

## Leads

| Method | Path | Query | Returns |
|---|---|---|---|
| GET | `/alerts` | `dataset_id`, `limit` (default 200), `level` (optional) | `WalletAlert[]` ranked by risk |
| GET | `/alerts/transactions` | `dataset_id`, `limit`, `level` | `TransactionAlert[]` ranked by risk |
| GET | `/patterns` | `dataset_id`, `type` (optional) | `Pattern[]` strongest first |
| GET | `/clusters` | `dataset_id` | `Cluster[]` highest average risk first |

`Signals`: `{anomaly, pattern, taint, network}` (each 0-1).
`Contributions`: the same keys, in risk points; they sum to `risk_score`.
`Reason`: `{category: "seed"|"anomaly"|"pattern"|"taint"|"network"|"entity", label, detail, contribution}`.

`WalletAlert`:

```json
{"rank": 1, "wallet_id": "bc1q...", "risk_score": 91.2, "risk_level": "CRITICAL", "confidence": 0.93,
 "is_seed": false, "entity_id": "E12", "entity_size": 7, "cluster_id": 3,
 "signals": {"anomaly": 0.8, "pattern": 0.9, "taint": 0.6, "network": 0.4},
 "contributions": {"anomaly": 22.1, "pattern": 35.0, "taint": 28.4, "network": 5.7},
 "patterns": ["peeling_chain"], "top_reason": "Hop 4 of a 9-hop peeling chain",
 "reasons": [{"category": "pattern", "label": "Hop 4 of a 9-hop peeling chain", "detail": "...", "contribution": 35.0}]}
```

`TransactionAlert`: `{rank, txid, timestamp, risk_score, risk_level, confidence, signals, contributions, patterns, input_count, output_count, total_input, total_output, fee, src_ip, dst_ip, dst_port, country, top_reason, reasons}`.

`Pattern`: `{pattern_id, type: "peeling_chain"|"coinjoin"|"layering"|"rapid_passthrough", strength, summary, wallets: string[], transactions: string[], start_time, end_time, total_value, details: {}}`.

`Cluster`: `{cluster_id, label, wallet_count, entity_count, avg_risk, max_risk, flagged_count, top_wallets: [{wallet_id, risk_score, risk_level}], pattern_counts: {type: n}}`.
`cluster_id` `-1` groups entities HDBSCAN left unassigned.

## Detail

`GET /wallets/{wallet_id}?dataset_id=...` returns the `WalletAlert` fields plus:

```json
{
  "detectors": [{"name": "Isolation Forest", "percentile": 0.998, "flagged": true}],
  "feature_attributions": [{"feature": "unique_ips", "label": "Unique IPs", "value": 41, "percentile": 0.997, "contribution": 0.31}],
  "features": {"transaction_count": 12, "...": 0},
  "entity": {"entity_id": "E12", "size": 7, "wallets": ["..."]},
  "cluster": {"cluster_id": 3, "label": "..."},
  "taint": {"score": 0.62, "tainted_value": 3.1, "hops": 2, "seed": "bc1q...", "path": [{"wallet_id": "...", "txid": "..."}]},
  "patterns": ["Pattern"],
  "network": {"ips": [{"ip": "...", "country": "DE", "asn": 3320, "count": 4}], "countries": [{"country": "DE", "count": 4}],
              "asns": [{"asn": 3320, "count": 4}], "ports": [{"port": 8333, "count": 3}],
              "nonstandard_port_ratio": 0.25, "shared_ip_wallets": 3},
  "transactions": [{"txid": "...", "timestamp": "...", "direction": "in"|"out"|"self", "amount": 0.5,
                    "counterparties": ["..."], "src_ip": "...", "dst_port": 8333, "risk_level": "HIGH", "patterns": []}]
}
```

`feature_attributions[].contribution` is the share of the anomaly-ensemble score
removed when that feature is reset to the dataset median (0-1, largest first).

`GET /transactions/{txid}?dataset_id=...` returns the `TransactionAlert` fields
plus `inputs: [{wallet_id, amount, risk_level}]`, `outputs` (same shape),
`detectors`, `feature_attributions` and `patterns: Pattern[]`.

## Graph

`GET /graph?dataset_id=...&focus_type=wallet|transaction|pattern|cluster&focus_id=...&depth=1|2`

```json
{"graph_available": true, "source": "neo4j"|"local", "limited": false,
 "nodes": [{"id": "bc1q...", "type": "Wallet"|"Transaction"|"IP", "label": "bc1q…9x", "risk_score": 91.2,
            "risk_level": "CRITICAL", "is_seed": false, "is_focus": true}],
 "edges": [{"id": "...", "source": "bc1q...", "target": "tx...", "type": "INPUT_FROM"|"OUTPUT_TO"|"OBSERVED_IN", "amount": 0.5}]}
```

Edges follow value flow: `Wallet -INPUT_FROM-> Transaction -OUTPUT_TO-> Wallet`,
and `Transaction -OBSERVED_IN-> IP`. Wallet neighbourhoods come from Neo4j when it
is reachable and from the stored analysis otherwise, so the graph works offline
without Neo4j.
