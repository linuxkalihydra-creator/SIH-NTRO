# The analysis pipeline

One run takes a capture file and produces ranked, explained leads for wallets,
transactions, actors and IPs. This page is the operator's view: stages,
commands, outputs, settings and run time. The reasoning behind each model is in
[`technical-writeup.md`](technical-writeup.md).

## Stages

```text
capture file (CSV / JSON / XML, optionally .gz)
  1  ingest         parse, map columns, merge sightings per transaction, reject / repair, report
  2  enrich         country + ASN of every IP from the offline GeoIP database
  3  flow index     transactions in time order; per address: what it received and spent, and when
  4  patterns       CoinJoin, peeling chain, layering, rapid pass-through
  5  network        first-seen origin of each transaction with a confidence; IP footprints of spenders
  6  entities       who owns which addresses (co-spend, change, laundering structures)
  7  actors         which entities are operated together (two agreeing clues)
  8  communities    DeepWalk embeddings of the entity payment graph + HDBSCAN
  9  anomaly        Isolation Forest + autoencoder + ECOD, on actor behaviour and on transactions
 10  propagation    seed taint, structure proceeds, upstream exposure, network association
 11  fusion         risk 0-100, level, confidence, exact per-signal contributions, reasons
 12  graph          optional write to Neo4j
 13  outputs        JSON / CSV leads and Markdown evidence sheets; analysis snapshot for the API
```

| Stage | Code |
|---|---|
| 1 | `backend/ingestion/` (`readers.py`, `schema.py`, `normalizer.py`, `service.py`) |
| 2 | `backend/enrichment/` |
| 3, 5 | `backend/correlation/flows.py`, `network.py` |
| 4 | `backend/ml/patterns.py` |
| 6 to 8 | `backend/ml/entities.py` |
| 9 | `backend/ml/features.py`, `anomaly.py` |
| 10 | `backend/ml/propagation.py` |
| 11 | `backend/ml/scoring.py` |
| 12 | `backend/graph/neo4j_client.py` |
| orchestration, queries, snapshot | `backend/pipeline/` (`orchestrator.py`, `investigation.py`, `views.py`) |

Stages 10 and 11 are the only ones that depend on the seed wallets. Changing
the seeds re-runs just those two (about a second for 10,000 transactions); no
model is refitted.

## Running it

```bash
# full run from the command line
uv run python scripts/run_analysis.py \
    --input data/synthetic/transactions.csv \
    --seeds data/synthetic/seed_wallets.txt \
    --output-dir data/processed
```

| Option | Default | Meaning |
|---|---|---|
| `--input` | required | Capture file: `.csv`, `.tsv`, `.json`, `.jsonl`, `.xml`, optionally `.gz` |
| `--seeds` | none | File of known illicit addresses, one per line (or comma / space separated) |
| `--output-dir` | `data/processed` | Where leads and reports are written |
| `--contamination` | `0.05` | Share of rows each anomaly detector calls an outlier |
| `--random-state` | `42` | Seed of the ML routines; the same input gives the same output |
| `--neo4j` | off | Also write the graph to Neo4j |
| `--dataset-id` | `cli` | Label of the graph in Neo4j |
| `--top` | `10` | How many leads to print |

Other entry points:

| Command | What it does |
|---|---|
| `bash run.sh` | Start the API and dashboard on http://127.0.0.1:8000 |
| `bash run.sh --demo` | Analyse the bundled capture first, then start |
| `bash demo.sh` | Regenerate the capture, analyse, evaluate, test, build the dashboard |
| `uv run python scripts/evaluate_models.py` | Full evaluation report against the ground truth |
| `uv run python scripts/benchmark.py` | The same metrics over 10 independently generated captures |
| `uv run python scripts/benchmark.py --scale 25000 50000 100000` | Run time by dataset size |
| `uv run python scripts/generate_dataset.py --records 10000 --seed 42` | Regenerate the synthetic capture |
| `uv run python scripts/download_geoip.py` | One-time download of the DB-IP Lite databases |
| `bash scripts/make_offline_bundle.sh` | Build the bundle for an air-gapped machine |

`make help` lists the same actions as make targets.

## Outputs

Written to `--output-dir` (and, for datasets analysed through the API, to
`data/raw/uploads/<dataset_id>/processed/`):

| File | Content |
|---|---|
| `analysis_summary.json` | Overview, ingestion report, graph statistics, run time per stage |
| `investigative_leads.json` | Top 200 wallet leads with reasons and contributions |
| `transaction_alerts.json` | Top 200 transaction leads |
| `actor_leads.json`, `ip_leads.json` | Top 200 actor and IP leads |
| `patterns.json` | Every detected structure with its transactions and wallet roles |
| `leads_wallets.csv`, `leads_transactions.csv`, `leads_actors.csv`, `leads_ips.csv`, `leads_patterns.csv` | The ranked lists as spreadsheets |
| `wallet_risk_scores.json` | Every wallet's score, level, confidence and signals |
| `wallet_features.csv` | The per-address features |
| `wallet_clusters.csv` | Address → entity, actor, community |
| `reports/*.md` | A Markdown evidence sheet for each of the top wallet and IP leads |

The API additionally keeps `analysis.json.gz` per dataset: the whole analysis,
restored into memory on first request. Seeds changed afterwards are kept in a
small `seeds.json` beside it; the scores are recomputed from them on load.

## Settings

Environment variables (or `.env`, copied from `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` | `bolt://localhost:7687`, `neo4j`, none | Optional graph database |
| `GEOIP_COUNTRY_DB`, `GEOIP_ASN_DB` | first `.mmdb` of each kind in `data/geoip/` | Offline GeoIP databases |
| `DATASET_STORE_DIR` | `data/raw/uploads` | Uploads, snapshots, per-dataset outputs |
| `FRONTEND_DIST` | `frontend/dist` | Built dashboard served at `/` |
| `BITCOIN_STANDARD_PORTS` | `8333` | Ports that count as standard (comma separated) |
| `HOST`, `PORT` | `127.0.0.1`, `8000` | Read by `run.sh` |

Model constants are named at the top of the module that uses them:
`backend/ml/scoring.py` (signal weights, levels, inheritance),
`backend/ml/patterns.py` (detector thresholds as function arguments),
`backend/ml/entities.py` (service limit, deposit-address cap) and
`backend/pipeline/investigation.py` (structure thresholds, hand-over decay).

## Run time

Measured on a 16-thread laptop CPU while other jobs were running; analysis
only, seed 42 (`scripts/benchmark.py --scale ...`).

| Transactions | Capture rows | Wallets | Analysis |
|---|---|---|---|
| 10,000 | 20,341 | 20,342 | 14 s |
| 25,000 | 51,322 | 50,351 | 35 s |
| 50,000 | 102,264 | 100,372 | 45 s |
| 100,000 | 205,177 | 200,115 | 100 s |

Around that: ingestion reads on the order of 10,000 rows per second for CSV
and JSON and about half that for XML (the 20,341-row demo CSV takes 2 s); GeoIP
enrichment takes well under a second (one lookup per distinct IP); writing the
10,000-transaction graph to Neo4j takes about 16 s; writing the output files
about 3 s. Re-scoring after a seed change takes under a second at 10,000
transactions and about 4 s at 50,000.

What keeps it bounded: files are parsed as a stream; the density clustering is
fitted on at most 12,000 best-connected entities and the rest are assigned to
the nearest community; feature attributions are computed for the top-ranked
rows only; lead lists are served from array-backed lookups, not row-by-row
DataFrame access.

## Reproducibility

- The same file, seeds and `--random-state` give the same scores.
- `scripts/generate_dataset.py --seed N` regenerates a capture byte for byte.
- The ground truth is read only by `scripts/evaluation.py`; nothing in
  `backend/` opens it.
