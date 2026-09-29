# 🔎 Bitcoin Investigation Platform

> **An offline platform that ingests bulk Bitcoin transaction and network metadata, correlates the network layer (IP/port/timing) with the blockchain layer (wallet/TXID/amount), and uses machine learning to detect anomalies, cluster entities, detect laundering patterns and propagate risk into a ranked, explainable list of investigative leads.**

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python\&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi\&logoColor=white)](https://fastapi.tiangolo.com/)
[![scikit-learn](https://img.shields.io/badge/scikit--learn-ML-F7931E?logo=scikitlearn\&logoColor=white)](https://scikit-learn.org/)
[![Neo4j](https://img.shields.io/badge/Neo4j-Graph%20Database-4581C3?logo=neo4j\&logoColor=white)](https://neo4j.com/)
[![React](https://img.shields.io/badge/React-Single--page%20UI-61DAFB?logo=react\&logoColor=black)](https://react.dev/)

Built for SIH problem statement 5, *AI-Powered Monitoring & Analysis of Bitcoin
Transaction Traffic* (NTRO). Everything runs locally on Linux. The only network
access is a one-time download of an open GeoIP database.

📄 **[Technical write-up](docs/technical-writeup.md)** covers the approach, the
model choice, the explainability method and the evaluation results.
**[API reference](docs/api.md)** · **[Dataset](docs/dataset.md)** · **[Pipeline](docs/pipeline.md)**

---

## ✅ Requirements coverage

| Requirement (problem statement) | Where it is implemented |
|---|---|
| Ingest & parse bulk metadata (timestamp, src/dst IP & port, TXID, input/output wallets, amounts, fee, script type) in CSV/JSON/XML | `backend/ingestion`: parsers, normaliser, validator (IPv4/IPv6, ports, amounts, duplicates) |
| Correlate network-layer (IP/port/timing) with blockchain-layer (wallet/TXID/amount) data | `backend/correlation`: spender-side IP/ASN/country footprint, non-standard ports, shared IPs, IP↔wallet association strength, holding time and burst timing |
| Entity/transaction graph linking IPs, wallets and transactions | Neo4j (`backend/graph`, dataset-scoped `Wallet`/`Transaction`/`IP`/`Country`/`ASN` nodes) plus an in-memory flow graph used when Neo4j is down |
| **Entity clustering**: common-input-ownership + graph embeddings | `backend/ml/entities.py`: CoinJoin-aware union-find CIO, DeepWalk-as-matrix-factorisation embeddings, HDBSCAN communities |
| **Anomaly detection**: statistically unusual transactions/flows | `backend/ml/anomaly.py`: Isolation Forest + autoencoder + ECOD ensemble, fitted on wallets *and* on transactions |
| **Peeling-chain / mixing detection** | `backend/ml/patterns.py`: peeling chains, CoinJoins, fan-out/fan-in layering, rapid pass-through (mules) |
| **Risk scoring**: propagate risk from seed illicit wallets | `backend/ml/propagation.py`: time-ordered haircut taint from seeds (and co-owned addresses) plus upstream exposure from pattern-confirmed wallets |
| Working model, not just rules | Three unsupervised models, graph embeddings and density clustering; rules only describe the laundering structures the models are fused with |
| Ranked, explainable alerts with a confidence score | `backend/ml/scoring.py`: noisy-OR fusion, exact Shapley contribution per signal, model attributions per feature, corroboration-based confidence, for wallets **and** transactions |
| Dashboard / link-analysis visualisation of flagged entities and evidence | `frontend/`: one page with ranked leads, a "why flagged" breakdown, evidence tabs and a Cytoscape link graph |
| Geo/ASN from an open-source downloadable GeoIP database | `backend/enrichment` + `scripts/download_geoip.py`: DB-IP Lite (CC BY 4.0, no account) or MaxMind GeoLite2 `.mmdb` |
| Synthetic dataset with the minimum fields | `scripts/generate_dataset.py`: chronological economy simulation with ground truth and seeds |
| Offline Linux solution, working repo, write-up | `demo.sh`, 67 tests, [`docs/technical-writeup.md`](docs/technical-writeup.md) |

---

## 🧠 How it works

```text
CSV / JSON / XML ─► parse · normalise · validate ─► GeoIP/ASN (local .mmdb)
        │
        ▼
time-ordered flow index + network correlation (IP · port · ASN · timing)
        │
        ├─► pattern detectors ──── CoinJoin · peeling chain · layering · pass-through
        ├─► entity clustering ──── common-input ownership (CoinJoins excluded)
        │                          → DeepWalk embeddings → HDBSCAN communities
        ├─► anomaly ensemble ───── Isolation Forest · autoencoder · ECOD
        │                          (wallet level and transaction level)
        └─► risk propagation ───── haircut taint from seed wallets
                                   + upstream exposure from pattern wallets
        │
        ▼
noisy-OR fusion → risk score, level, confidence, Shapley contributions, reasons
        │
        ├─► Neo4j graph (dataset-scoped)          ├─► JSON snapshot per dataset
        └─► FastAPI  ─────────────────────────────►  single-page investigation UI
```

Each wallet and each transaction gets four signals (anomaly, pattern, taint,
network) that are fused into a 0–100 risk score. Levels are CRITICAL ≥ 80,
HIGH ≥ 60 and MEDIUM ≥ 35. Every lead says:

* **how many points each signal contributed** (exact Shapley values that sum to the score);
* **why**, in words tied to evidence, for example "Hop wallet in an 18-hop peeling
  chain", "76% of received value traces back to seed bc1q4ul7…" or "Statistical
  outlier (3 of 3 detectors, top 0.6%)";
* **which features drove the anomaly models**, with each feature's value and
  population percentile;
* **how well corroborated the flag is** (confidence).

Changing the seed wallets re-runs only propagation and fusion, in under a second.

### Results on the synthetic dataset

Full details are in the [write-up](docs/technical-writeup.md#6-results-on-the-committed-dataset-10000-transactions-20481-addresses).
Reproduce them with `uv run python scripts/evaluate_models.py`.

| Measure | Value |
|---|---|
| Wallet ROC-AUC, fused score (anomaly models alone) | 0.87 (0.71) |
| Precision of the top 100 leads | 0.99 |
| HIGH/CRITICAL wallets that are illicit | 0.88 |
| Unseeded ransomware group's collection addresses surfaced | 100% at MEDIUM or above |
| Ransomware victims flagged HIGH | 0 |
| Detector precision / recall: peeling · layering · pass-through · CoinJoin | 0.86/1.00 · 1.00/0.96 · 0.98/1.00 · 1.00/0.94 |
| Mixed-owner entities, CIO with CoinJoin exclusion (naive CIO) | 0 (33) |

---

## 🖥️ The investigation page

The frontend is a single page, with no separate dashboard, alerts or clusters screens:

* **Top bar:** dataset selector, upload (CSV/JSON/XML), analyse, seed wallets, Neo4j status.
* **KPI strip:** transactions, wallets, entities, communities, CRITICAL/HIGH counts,
  detected patterns, seeds, GeoIP source and the risk distribution.
* **Leads:** ranked tables of wallets, transactions, patterns and communities, with
  level filter, search and sorting.
* **Investigation panel** for the selected lead:
  * risk, confidence and the contribution bar;
  * reasons, detector percentiles and feature attributions;
  * the link-analysis graph, following value flow `Wallet → Transaction → Wallet`
    plus relaying IPs, coloured by risk, with seeds marked; click a node to investigate it;
  * evidence tabs: transactions, network (IPs/ASNs/ports), co-owned wallets,
    taint path back to the seed, patterns, features.
* **Seeds:** paste or load known-illicit wallets, or mark the selected wallet as a
  seed. Risk is re-propagated immediately.
* **Models & evaluation:** every model with what it flagged, and the metrics
  against the synthetic labels when the dataset has them.

---

## 🚀 Getting started

### Prerequisites

Linux, Python 3.12+ with [`uv`](https://docs.astral.sh/uv/), Node.js 20+ / npm, and
Neo4j 5+ (optional: without it the graph is served from the stored analysis).

```bash
git clone https://github.com/linuxkalihydra-creator/sih.git
cd sih
uv sync
cp .env.example .env   # then set your Neo4j password
```

`.env` (git-ignored):

```text
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=<your password>
```

### Offline GeoIP (once, while you still have internet)

```bash
uv run python scripts/download_geoip.py     # DB-IP Lite country + ASN → data/geoip/*.mmdb
```

DB-IP Lite is CC BY 4.0 ("IP Geolocation by DB-IP", https://db-ip.com) and needs
no account. MaxMind GeoLite2 also works: put `GeoLite2-Country.mmdb` /
`GeoLite2-ASN.mmdb` in `data/geoip/`. Other locations can be set with
`GEOIP_COUNTRY_DB` / `GEOIP_ASN_DB`. The `.mmdb` files are git-ignored.

Without a database the pipeline still runs, using the dataset's own `geo_country`
and `asn` fields; the overview reports the source of every value.

### Run everything

```bash
./demo.sh
```

It regenerates the synthetic dataset (identical to the committed one), downloads
GeoIP if missing, runs the analysis with the seed list, runs the tests and builds
the frontend. Or run the steps by hand:

```bash
uv run python scripts/generate_dataset.py --records 10000 --seed 42      # data/synthetic/
uv run python scripts/run_analysis.py --input data/synthetic/transactions.csv \
    --seeds data/synthetic/seed_wallets.txt --output-dir data/processed
uv run uvicorn backend.api.main:app --host 127.0.0.1 --port 8000        # API
cd frontend && npm ci && npm run dev                                     # UI on http://localhost:5173
```

In the UI: **Upload dataset** → `data/synthetic/transactions.csv` → **Analyze**
(about a minute for 10,000 transactions). Then open **Seeds** and load
`data/synthetic/seed_wallets.txt` to propagate risk from the "reported" ransomware
addresses.

### Tests and evaluation

```bash
uv run pytest -q                              # 67 tests; the Neo4j tests skip when it is not running
uv run python scripts/evaluate_models.py      # wallet-level metrics vs data/synthetic/ground_truth.json
```

---

## 🧪 Synthetic data

`scripts/generate_dataset.py` simulates a two-month economy chronologically, with
per-address balances:

* users who own several addresses and send change to fresh ones;
* exchanges (batch withdrawals, deposit sweeps) and merchants;
* CoinJoin rounds;
* a darknet vendor broadcasting from many networks;
* two ransomware groups that collect victim payments, consolidate them and launder
  the proceeds through peeling chains, layering, CoinJoins and fast mules before
  cashing out.

Illicit actors favour an anonymising IP pool and non-standard ports. IPs come
from fixed public networks, so GeoIP databases resolve them.

Outputs in `data/synthetic/`:

| File | Content |
|---|---|
| `transactions.csv` / `.json` / `.xml` | The same 10,000 transactions in the three formats |
| `seed_wallets.txt` | Known-illicit seeds: 35% of ransomware group 1's collection addresses (group 2 has none) |
| `ground_truth.json` | Every address's owner, role and illicit flag, plus pattern transactions; used **only** for evaluation |

`behavior_type` is an optional per-transaction evaluation label (`NORMAL`,
`EXCHANGE_LIKE`, `MIXING_LIKE`, `RANSOMWARE`, `PEELING_CHAIN`, `LAYERING_LIKE`,
`RAPID_TRANSFER`, `HIGH_NETWORK_DIVERSITY`). The models never read it. Datasets
with only the minimum fields (`timestamp, src_ip, dst_ip, src_port, dst_port, txid,
input/output addresses and amounts`) are fully supported.

---

## 🗄️ Datasets, snapshots and graph isolation

* Every upload gets a `dataset_id`. Its source file, metadata, analysis snapshot
  and CLI-style outputs live in `data/raw/uploads/<dataset_id>/` (git-ignored).
  Set `DATASET_STORE_DIR` to use another location.
* Every Neo4j node is tagged with its `dataset_id`, uniqueness constraints are
  scoped per dataset, and every query filters on it. Re-analysing dataset X
  replaces only X's nodes, so other datasets stay viewable and graph data
  accumulates. To delete one dataset's graph:
  `MATCH (n {dataset_id: $dataset_id}) DETACH DELETE n`.
* Snapshots from before the ML rewrite (version 1) are reported as outdated; press
  **Analyze** to rebuild them.

---

## 📁 Project structure

```text
backend/
  api/main.py               FastAPI endpoints (see docs/api.md)
  ingestion/                CSV/JSON/XML parsers, normaliser, validator, dataset store
  enrichment/               offline GeoIP country + ASN (.mmdb)
  correlation/              flow index, network-layer correlation
  ml/                       features, anomaly ensemble, entities, patterns, propagation, scoring
  graph/                    Neo4j client and graph payload builder
  pipeline/                 orchestrator and the Investigation (models + queries + snapshot)
frontend/                   React single-page investigation UI (Vite, Cytoscape)
scripts/                    generate_dataset, run_analysis, evaluate_models, download_geoip
data/synthetic/             committed demo dataset, seeds and ground truth
docs/                       technical write-up, API, dataset and pipeline notes
tests/                      unit, pipeline, API and Neo4j integration tests
```

---

## ⚠️ Disclaimer

A research and educational prototype. All generated transactions, wallets and
activities are **synthetic**. Generated IPs come from public ranges so that GeoIP
enrichment can resolve them, but they say nothing about the real hosts at those
addresses. Scores are **investigative leads, not proof** of identity, ownership or
criminal activity.
