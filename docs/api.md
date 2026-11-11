# HTTP API

The platform is one FastAPI process. Start it with `./run.sh` (or
`uv run uvicorn backend.api.main:app`); the interactive reference is at
`http://127.0.0.1:8000/docs`.

- Every route is served at the root **and** under `/api` (the dashboard uses
  `/api`, so it can be served from the same process).
- Every analysis route takes the dataset as a `dataset_id` query parameter.
  Datasets never mix: each has its own folder, snapshot and Neo4j subgraph.
- Responses are JSON unless stated otherwise, and are gzip-compressed above 2 KB.
- Errors use the usual codes: `400` unusable file, `404` unknown dataset or
  identifier, `409` dataset not analysed yet (or already being analysed),
  `422` invalid parameters.
- There is no authentication: the service binds to `127.0.0.1` and is meant for
  an offline analyst workstation. Put it behind a reverse proxy with
  authentication before exposing it on a network.

## Datasets

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Service status and whether Neo4j is reachable |
| `POST` | `/datasets/upload` | Upload a capture (multipart field `file`). Returns `201` with `dataset_id` |
| `POST` | `/datasets` | Register a file already on disk: `{"path": "data/synthetic/transactions.csv"}` |
| `GET` | `/datasets` | All datasets with their status |
| `GET` | `/datasets/{dataset_id}` | One dataset's metadata |
| `DELETE` | `/datasets/{dataset_id}` | Remove the dataset, its analysis, its outputs and its Neo4j subgraph |
| `POST` | `/ingest` | Validate a local file without analysing it: `{"path": "..."}` returns the data-quality report |

Accepted formats: `.csv`, `.tsv`, `.json`, `.jsonl` / `.ndjson`, `.xml`, each
optionally gzip-compressed (`.csv.gz`, ...). See [`dataset.md`](dataset.md) for
the fields and the accepted column names.

## Analysis

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/analyze` | Run the whole pipeline on a dataset |
| `GET` | `/datasets/{dataset_id}/progress` | Stage and fraction done of a running analysis |
| `PUT` | `/datasets/{dataset_id}/seeds` | Replace the seed wallets and re-score (no model is refitted) |

`POST /analyze` body:

```json
{
  "dataset_id": "dataset_3f0c9c1e5b7a4c0f9d2e61a07b5c8e44",
  "seed_wallets": ["bc1q...", "1A1z..."],
  "wait": false,
  "contamination": 0.05,
  "random_state": 42
}
```

- `wait: true` (default) returns when the analysis is finished, with the
  overview. `wait: false` returns `{"status": "running"}` at once; poll the
  progress route.
- `seed_wallets` are known illicit addresses. They are optional: without any
  seed the platform still finds laundering structures, their proceeds and
  cash-out points.
- Progress: `{"status": "running" | "completed" | "failed" | "idle", "stage": "Fitting the anomaly ensemble on actor behaviour", "fraction": 0.62, "elapsed_seconds": 8.4, "error": null}`.

`PUT /datasets/{id}/seeds` body `{"seed_wallets": [...]}` returns the new
overview and `unknown_seeds` (addresses that do not occur in the dataset).
It takes about a second for 10,000 transactions: only the seed list is
stored, and the scores are recomputed from it. When Neo4j is in use, the
scores stored there are refreshed in the background.

## Overview and data quality

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/overview?dataset_id=` | Counts, risk distributions, pattern counts, model list, timeline, geography, evaluation |
| `GET` | `/ingestion?dataset_id=` | Data-quality report of the last analysis |

Main keys of the overview:

| Key | Content |
|---|---|
| `stats` | transactions, network observations, wallets, IPs, entities, actors, communities, time range, total value |
| `network` | observations, multi-observed transactions, share attributed to an origin IP, mean origin confidence, relay nodes, `standard_ports` |
| `entity_links`, `actor_links` | how many links each ownership heuristic / actor rule produced |
| `risk_distribution`, `transaction_risk_distribution`, `actor_risk_distribution`, `ip_risk_distribution` | counts per level |
| `patterns` | detected structures per type |
| `models` | every model with what it does and how much it flagged |
| `timeline` | transactions, value and HIGH-or-above transactions per bucket; buckets start on the hour (or at 00:00 UTC) and state their length in `seconds` |
| `geography` | the 20 origin countries and networks with the most HIGH-or-above transactions, plus `country_count`, `asn_count` and `attributed` (how many transactions the figures cover) |
| `evaluation` | precision/recall against `behavior_type` labels when the dataset has them |
| `ingestion` | same content as `/ingestion` |
| `stage_seconds`, `processing_seconds` | run time per stage |

The ingestion report lists rows read, transactions, observations, rejected rows
by reason, repaired rows by reason, example row numbers, the column mapping
that was applied, unmapped columns, the detected amount unit and the time range.

## Leads

Each route returns a list ranked by risk. Optional parameters: `limit`
(default 200, maximum 5000) and `level` (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`).

| Method | Path | One row is |
|---|---|---|
| `GET` | `/alerts` | a wallet address |
| `GET` | `/alerts/transactions` | a transaction |
| `GET` | `/alerts/actors` | an actor (wallets operated together) |
| `GET` | `/alerts/ips` | an IP that originated transactions (`include_relays=true` adds relay-only IPs) |
| `GET` | `/patterns` | a detected structure (`type=peeling_chain`, `layering`, `rapid_passthrough` or `coinjoin`) |
| `GET` | `/clusters` | an entity community |
| `GET` | `/search?q=` | wallets, transactions, IPs, actors and patterns whose identifier contains `q` |

A wallet lead:

```json
{
  "rank": 3,
  "wallet_id": "bc1q...",
  "risk_score": 93.4,
  "risk_level": "CRITICAL",
  "confidence": 0.99,
  "is_seed": false,
  "entity_id": "E412", "entity_size": 9,
  "actor_id": "A7", "actor_size": 141,
  "cluster_id": 12,
  "signals":       {"anomaly": 0.96, "pattern": 1.0, "taint": 0.81, "network": 0.72},
  "contributions": {"anomaly": 18.2, "pattern": 31.5, "taint": 29.9, "network": 13.8},
  "patterns": ["peeling_chain"],
  "tags": ["structure"],
  "top_reason": "Hop wallet in a 17-hop peeling chain",
  "reasons": [
    {"category": "pattern", "label": "Hop wallet in a 17-hop peeling chain", "detail": "...", "pattern_id": "peeling-3", "contribution": 31.5},
    {"category": "taint", "label": "81% of received value traces back to seed bc1q4ul7…", "detail": "...", "contribution": 29.9}
  ]
}
```

- `signals` are the four inputs of the score, each between 0 and 1.
- `contributions` are their exact Shapley values in risk points; they sum to
  `risk_score`.
- `reasons` are sentences tied to evidence, ordered by the points they explain.
  Reasons with `contribution: 0` are context (entity membership, service-like
  entity).
- `tags`: `seed`, `cash-out` (dirty funds entering a service), `service`,
  `structure` (inside a laundering structure).

Transaction, actor and IP leads have the same `rank`, `risk_score`,
`risk_level` and `top_reason` fields, plus their own facts (amounts and origin
confidence; wallets, entities and IP footprint; role, originated/relayed counts
and the share of traffic from flagged wallets).

## Evidence for one lead

| Method | Path | Returns |
|---|---|---|
| `GET` | `/wallets/{wallet_id}` | The lead plus detectors, feature attributions, features (with `feature_labels`), entity, actor, community, taint path, proceeds path, upstream exposure, patterns with the wallet's role, network footprint, transactions |
| `GET` | `/transactions/{txid}` | The lead plus inputs and outputs with their risk, detectors, feature attributions, patterns, the inferred origin with an explanation, and every network observation in time order |
| `GET` | `/actors/{actor_id}` | The lead plus reasons, member wallets, member entities with the link that attached each one, and the IPs it broadcast from |
| `GET` | `/ips/{ip}` | The lead plus reasons, wallets and actors using it, ports, and the transactions it originated |

How an entity was attached to its actor is reported as a `link`:

```json
{"kind": "payment+deposit", "description": "one pays the other directly, and both pay into the same service deposit address",
 "txid": "9f2c...", "with": "E88", "ip": "", "address": "bc1q552c..."}
```

`kind` is `payment` (same IP and a direct payment), `deposit` (same IP and the
same service deposit address) or `payment+deposit` (both money ties, no IP
needed, so `ip` is empty).

## Link analysis

`GET /graph?dataset_id=&focus_type=&focus_id=&depth=1&mode=neighbourhood`

| Parameter | Values |
|---|---|
| `focus_type` | `wallet`, `transaction`, `pattern`, `cluster`, `actor`, `ip` |
| `depth` | 1 to 3 hops |
| `mode` | `neighbourhood` (everything the wallet took part in), `downstream` or `upstream` (follow the money forward or backward in time along the largest flows; tracing stops at services) |

```json
{
  "graph_available": true, "source": "local", "mode": "downstream", "limited": false,
  "nodes": [
    {"id": "bc1q...", "type": "Wallet", "label": "bc1qabcd…wxyz", "risk_score": 88.1, "risk_level": "CRITICAL",
     "is_seed": false, "is_focus": true, "entity_id": "E412", "actor_id": "A7", "service": false},
    {"id": "9f2c...", "type": "Transaction", "label": "9f2c41aa…07be", "risk_score": 71.0, "risk_level": "HIGH", "timestamp": "2024-01-19T08:14:02+00:00"},
    {"id": "185.220.101.7", "type": "IP", "country": "DE", "asn": 60729, "risk_level": "HIGH", "is_relay": false}
  ],
  "edges": [
    {"id": "bc1q...>9f2c...", "source": "bc1q...", "target": "9f2c...", "type": "INPUT_FROM", "amount": 1.25},
    {"id": "9f2c...@185.220.101.7", "source": "9f2c...", "target": "185.220.101.7", "type": "OBSERVED_IN"}
  ]
}
```

Edge types: `INPUT_FROM` (wallet to transaction), `OUTPUT_TO` (transaction to
wallet), `OBSERVED_IN` (transaction to its first-seen IP), `RELAYED_BY`
(transaction to a later relay). `limited` is true when the node cap cut the
graph. Wallet nodes also carry `service` (a service-like entity) and
`trace_stopped` (a trace was not followed past this service); IP nodes carry
`is_relay`. The graph is always built from the stored analysis, so it is the
same whether or not Neo4j is running.

## Exports

| Method | Path | Returns |
|---|---|---|
| `GET` | `/export/{kind}.csv?dataset_id=` | Ranked leads as CSV. `kind`: `wallets`, `transactions`, `actors`, `ips`, `patterns`. `limit` up to 50,000 |
| `GET` | `/report/{kind}/{identifier}?dataset_id=` | A Markdown evidence sheet for one `wallet`, `transaction`, `actor` or `ip` |

## Example session

```bash
API=http://127.0.0.1:8000/api

# upload and analyse in the background
ID=$(curl -s -F file=@data/synthetic/transactions.csv $API/datasets/upload | python3 -c 'import json,sys; print(json.load(sys.stdin)["dataset_id"])')
curl -s -X POST $API/analyze -H 'Content-Type: application/json' -d "{\"dataset_id\": \"$ID\", \"wait\": false}"
curl -s $API/datasets/$ID/progress

# give it the reported ransom addresses, then read the top leads
SEEDS=$(python3 -c 'import json; print(json.dumps(open("data/synthetic/seed_wallets.txt").read().split()))')
curl -s -X PUT $API/datasets/$ID/seeds -H 'Content-Type: application/json' -d "{\"seed_wallets\": $SEEDS}" > /dev/null
curl -s "$API/alerts?dataset_id=$ID&limit=5" | python3 -m json.tool | head -40
curl -s "$API/export/wallets.csv?dataset_id=$ID" -o leads_wallets.csv
```
