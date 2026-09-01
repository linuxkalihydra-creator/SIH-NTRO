# Bitcoin Investigation Platform

This project is an offline, Linux-only prototype for exploring synthetic Bitcoin investigation workflows. It is designed for early-stage local development and experimentation without connecting to real Bitcoin networks or real-world criminal datasets.

## Synthetic Dataset Generator

This repository includes a synthetic data generator for Bitcoin-like transaction and network observations. The data is generated locally and is intentionally not tied to any real blockchain records or seized data.

### Why use synthetic data?

The goal is to create a realistic, reproducible, offline dataset that can support later phases such as:

- ingestion from CSV/JSON/XML
- normalization and enrichment
- IP/wallet/TX correlation
- anomaly detection
- clustering and evaluation
- explainable investigative lead generation
- dashboard and link-analysis prototyping

Synthetic data allows the project to remain fully offline, deterministic, and safe for testing without relying on real Bitcoin network observations.

### Important fields

Each generated record contains fields such as:

- `timestamp`: UTC timestamp for the network/transaction event
- `src_ip` and `dst_ip`: synthetic IPv4 addresses in documentation/test ranges
- `src_port` and `dst_port`: synthetic network ports, with Bitcoin P2P port 8333 used often for realism
- `txid`: synthetic transaction identifier
- `input_addresses` and `output_addresses`: wallet-like addresses
- `input_amounts` and `output_amounts`: BTC-like transfer amounts
- `fee`: transaction fee approximation
- `script_type`: transaction script type
- `geo_country`: synthetic country code
- `asn`: synthetic autonomous system number
- `behavior_type`: the synthetic generation profile used for evaluation only

The `behavior_type` field is only a generation label. It is not intended to be used as a model feature unless explicitly requested later.

### Behavioral profiles

The generator creates multiple synthetic behavior groups:

- `NORMAL`: baseline, low-volume and low-diversity activity
- `EXCHANGE_LIKE`: high-activity exchange-like flows with more counterparties
- `RAPID_TRANSFER`: quick movement between related wallets
- `LAYERING_LIKE`: multi-hop synthetic chains for layering patterns
- `MIXING_LIKE`: multiple-input, multiple-output transfers with balanced splits
- `HIGH_NETWORK_DIVERSITY`: one wallet associated with many IPs/ASNs/countries

These are synthetic behavioral patterns for experimentation only; they are not labels of real criminal activity.

### Generate the dataset

From the project root, run:

```bash
uv run python scripts/generate_dataset.py --records 1000
```

For a larger dataset:

```bash
uv run python scripts/generate_dataset.py --records 10000
```

### Output files

The generated files are written to:

- `data/synthetic/transactions.csv`
- `data/synthetic/transactions.json`
- `data/synthetic/transactions.xml`

These three files represent the same synthetic dataset in different formats.

### Data quality

The generator validates the created records before writing them. It checks for:

- required fields
- unique transaction identifiers
- valid timestamps
- valid IPv4 addresses
- valid port ranges
- non-negative amounts and fees
- consistent input/output totals
- valid behavior labels

### Synthetic data notice

This project is intentionally designed for offline experimentation. The dataset is synthetic, clearly labeled as such, and does not represent real Bitcoin transactions or criminal activity.

## Run complete analysis

Generate a fresh synthetic dataset:

```bash
uv run python scripts/generate_dataset.py --records 10000
```

Run the complete offline analysis pipeline:

```bash
uv run python scripts/run_analysis.py --input data/synthetic/transactions.csv --output-dir data/processed
```

Run the full test suite:

```bash
uv run pytest -q
```

## Run the FastAPI service

```bash
uv run python -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000 --reload
```

## Graph Visualization

The application includes a **Graph Visualization feature** within the Clusters page. When analyzing a dataset:

1. Navigate to **Clusters** after analysis completes
2. Click a cluster card to expand it
3. The **Graph** section displays a real-time Neo4j visualization showing:
   - **Nodes**: Wallets, Transactions, IP addresses, ASNs, and Countries (real entities from Neo4j)
   - **Edges**: Relationships like INPUT_FROM, OUTPUT_TO, OBSERVED_IN, LOCATED_IN, HAS_ASN
   - **Node Types**: Color-coded by entity type for easy visual identification
   - **Interactions**: Pan, zoom, drag nodes, fit-to-screen, and reset layout

### Graph Performance

The graph is optimized for performance:
- Maximum 200 nodes displayed per cluster
- Maximum 500 edges displayed per cluster
- Stale requests are cancelled when cluster changes
- Graph is destroyed during cleanup to prevent memory leaks
- Graph only loads when cluster is expanded (lazy loading)

### Graph Failure Handling

If Neo4j is unavailable, the application gracefully shows:
- "Graph data unavailable" message with a Retry button
- Clusters list remains fully functional
- Alerts and statistics work independently

## Neo4j Investigation Isolation

The application ensures **strict investigation isolation** through automatic Neo4j data reset:

### Reset Behavior

When a **new dataset is uploaded**:
1. Previous investigation graph data is cleared from Neo4j
2. New dataset analysis runs
3. New graph data persists to Neo4j
4. No mixing of Dataset A and Dataset B records

### When Reset Occurs

Neo4j data is reset **only** when starting a genuinely new investigation:
- ✅ Uploading a new dataset triggers reset
- ❌ Browser refresh does NOT reset (restores same dataset)
- ❌ Opening Clusters does NOT reset
- ❌ Fetching stats/alerts/graph does NOT reset
- ❌ Refreshing page does NOT reset (previous dataset remains active)

### Dataset-Aware Deletion

The reset is dataset-aware:
- Only removes nodes tagged with the current `dataset_id`
- Leaves Neo4j running (never restarts the service)
- Safe if database contains multiple datasets
- Fast operation (targeted deletion, not full database clear)

## Workflow

### Upload a Dataset

```
User
  ↓
[Upload Dataset]
  ↓
FormData POST /datasets/upload
  ↓
Backend creates dataset entry
  ↓
Returns dataset_id
```

### Analyze and Persist to Neo4j

```
POST /analyze with dataset_id
  ↓
Existing pipeline runs
  ↓
Neo4j previous data cleared (dataset-aware reset)
  ↓
New investigation graph persisted to Neo4j
  ↓
Dashboard displays real data
```

### View Graph in Clusters

```
Clusters page
  ↓
Click cluster card to expand
  ↓
Graph section loads
  ↓
Fetches real Neo4j neighborhood
  ↓
Cytoscape renders interactive visualization
```

### Upload Another Dataset

```
Dataset A active
  ↓
[Upload Dataset B]
  ↓
Dataset A cleared from Neo4j
  ↓
Dataset B analyzed and persisted
  ↓
Graph shows only Dataset B
```

## Project layout

```text
backend/
data/
docs/
frontend/
models/
scripts/
src/
tests/
```
