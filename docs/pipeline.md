# End-to-end pipeline

`AnalysisOrchestrator.run` (backend/pipeline/orchestrator.py) runs one dataset
through every stage, offline. The model details are explained in
[technical-writeup.md](technical-writeup.md).

## Stages

1. **Load and validate** CSV, JSON or XML. Records are normalised to one
   schema; invalid rows (bad IP, port, timestamp, negative amounts, missing
   fields) and duplicate TXIDs are dropped and counted.
2. **Enrich** country and ASN from a local `.mmdb` (DB-IP Lite or GeoLite2).
   The dataset's own values are kept when the database is missing or has no
   entry.
3. **Build the flow index**: transactions in time order, with per-address
   receives and spends and aligned per-address amounts.
4. **Correlate the network layer**: each address's IP/ASN/country footprint
   and non-standard-port share, shared IPs, and IP↔wallet association
   strength.
5. **Detect patterns**: CoinJoin, peeling chain, layering and rapid
   pass-through.
6. **Cluster entities**: common-input ownership (CoinJoins excluded), then
   DeepWalk embeddings of the entity graph and HDBSCAN communities.
7. **Engineer features** at wallet level (21 model features) and transaction
   level (12). `behavior_type` is never a feature.
8. **Run the anomaly ensemble** (Isolation Forest, autoencoder, ECOD) on
   wallets and on transactions, plus occlusion attributions for the top 10% of
   rows.
9. **Propagate risk**: haircut taint from the seed wallets, and upstream
   exposure from pattern-confirmed wallets.
10. **Fuse signals** with a noisy-OR into risk, level and confidence; compute
    exact Shapley contributions and reasons.
11. **Persist the graph** to Neo4j, scoped to the dataset. This is optional:
    without Neo4j the graph is served from the stored analysis.
12. **Write outputs** and, through the API, a JSON snapshot that the UI
    queries.

With 10,000 transactions (20,481 addresses) the stages take about 25–45 s on
the 5-core development VM, plus about 25 s for the Neo4j write. Changing the seed wallets
afterwards re-runs only stages 9–10, in under a second.

## CLI

```bash
uv run python scripts/generate_dataset.py --records 10000 --seed 42
uv run python scripts/run_analysis.py --input data/synthetic/transactions.csv \
    --seeds data/synthetic/seed_wallets.txt --output-dir data/processed
uv run python scripts/evaluate_models.py      # metrics vs data/synthetic/ground_truth.json
```

`run_analysis.py` options:
- `--seeds FILE`: one known-illicit wallet per line;
- `--contamination`: the share of rows each anomaly detector flags, default 0.05;
- `--random-state`: default 42.

## Output files (`--output-dir`)

| File | Content |
|---|---|
| `analysis_summary.json` | Overview (stats, risk distribution, patterns, models, evaluation), ingestion and graph statistics |
| `wallet_risk_scores.json` | Every wallet's risk score, level, confidence, entity, community, signals and contributions |
| `wallet_features.csv` | The wallet feature table |
| `wallet_clusters.csv` | Wallet → common-input-ownership entity → community |
| `investigative_leads.json` | The top 200 wallet leads with reasons |
| `transaction_alerts.json` | The top 200 transaction leads with reasons |
| `patterns.json` | Every detected pattern with transactions, wallet roles and details |

## API

```bash
uv run uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
```

See [api.md](api.md). Analyses are stored per dataset in
`data/raw/uploads/<dataset_id>/analysis.json` (override the store location with
`DATASET_STORE_DIR`). They are restored in memory on first use.
