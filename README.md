# 🔎 Bitcoin Investigation Platform

> **AI-powered, fully offline monitoring and analysis of Bitcoin transaction traffic.** The platform ingests bulk transaction and network metadata (CSV/JSON/XML) and correlates network-layer observations (IP, port, timing) with blockchain-layer data (wallet, TXID, amount). Machine learning then detects anomalies, clusters entities, finds laundering patterns and propagates risk from known illicit wallets. The result is a ranked list of explainable investigative leads with confidence scores, on a single link-analysis dashboard.

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python\&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi\&logoColor=white)](https://fastapi.tiangolo.com/)
[![scikit-learn](https://img.shields.io/badge/scikit--learn-ML-F7931E?logo=scikitlearn\&logoColor=white)](https://scikit-learn.org/)
[![Neo4j](https://img.shields.io/badge/Neo4j-Graph%20Database-4581C3?logo=neo4j\&logoColor=white)](https://neo4j.com/)
[![React](https://img.shields.io/badge/React-Single--page%20UI-61DAFB?logo=react\&logoColor=black)](https://react.dev/)

Built for **SIH Problem Statement 26146: *AI-Powered Monitoring & Analysis of Bitcoin
Transaction Traffic*** (National Technical Research Organisation, theme
Cryptocurrency).

| Document | What it covers |
|---|---|
| This README | Problem mapping, complete setup, how the system works end to end, technical approach, results |
| [`docs/technical-writeup.md`](docs/technical-writeup.md) | The short technical write-up deliverable: approach, model choice, explainability method, evaluation, limitations |
| [`docs/api.md`](docs/api.md) | HTTP API reference |
| [`docs/dataset.md`](docs/dataset.md) | Synthetic dataset schema and simulation |
| [`docs/pipeline.md`](docs/pipeline.md) | Pipeline stages, CLI options, output files |

---

## Contents

1. [The problem and what we built](#1-the-problem-and-what-we-built)
2. [Architecture at a glance](#2-architecture-at-a-glance)
3. [Quick start](#3-quick-start)
4. [Complete setup, step by step](#4-complete-setup-step-by-step)
5. [Using the platform](#5-using-the-platform)
6. [How it works, end to end](#6-how-it-works-end-to-end)
7. [Technical approach](#7-technical-approach)
8. [The synthetic dataset](#8-the-synthetic-dataset)
9. [Results](#9-results)
10. [API, outputs and storage](#10-api-outputs-and-storage)
11. [Troubleshooting](#11-troubleshooting)
12. [Limitations](#12-limitations)
13. [Project structure](#13-project-structure)

---

## 1. The problem and what we built

Bitcoin's pseudonymous, peer-to-peer design lets criminals move, layer and cash
out illicit funds (ransomware payments, darknet-market proceeds, extortion)
while evading traditional financial surveillance. The problem statement asks for
a **complete offline system** that ingests bulk transaction/network metadata,
correlates the network and blockchain layers, and applies AI/ML to produce
**prioritised, explainable investigative leads**.

### Challenge objectives → implementation

| Objective | How it is met |
|---|---|
| Ingest & parse a bulk metadata dataset (timestamp, src/dst IP & port, TXID, input/output wallets, amounts, fee, script type) | CSV, JSON and XML parsers, a normaliser to one schema, and a validator that checks IPv4/IPv6, ports, timestamps, non-negative amounts, required fields and duplicate TXIDs (`backend/ingestion`) |
| Build an entity/transaction graph linking IPs, wallets and transactions | Neo4j graph with `Wallet`, `Transaction`, `IP`, `Country` and `ASN` nodes and value-flow edges, scoped per dataset (`backend/graph`). An identical in-memory flow graph keeps link analysis working when Neo4j is down |
| Implement the AI/ML detection use case with a working model, not just rules | All four Section-4 focus areas (below): three unsupervised anomaly models, graph embeddings with density clustering, pattern detectors and risk propagation, fused into one score |
| Ranked, explainable alert list (why a wallet/transaction was flagged, with a confidence score) | Ranked wallet **and** transaction leads. Each shows the risk points contributed by each signal (exact Shapley values), reasons tied to evidence, the features that drove the models, and a corroboration-based confidence |
| Dashboard or link-analysis visualisation | One page with ranked leads, a "why flagged" breakdown, evidence tabs and an interactive Cytoscape link graph |

### Suggested AI/ML focus areas → implementation

| Focus area | What to build (PDF) | What we built |
|---|---|---|
| **Entity clustering** | Group wallets likely owned by one entity using common-input-ownership + graph embeddings | CoinJoin-aware common-input ownership (union-find), then DeepWalk embeddings of the entity graph and HDBSCAN communities (`backend/ml/entities.py`) |
| **Anomaly detection** | Flag statistically unusual transactions/flows | An ensemble of Isolation Forest, a neural autoencoder and ECOD, fitted separately on wallets and on transactions, with model-faithful feature attributions (`backend/ml/anomaly.py`) |
| **Peeling-chain / mixing detection** | Detect laundering-pattern transaction sequences (peeling chains, CoinJoin-like structures) | Detectors for peeling chains, CoinJoins, fan-out/fan-in layering and rapid pass-through ("mule") wallets, each with per-wallet roles (`backend/ml/patterns.py`) |
| **Risk scoring** | Propagate risk scores from seed illicit wallets via algorithms | Time-ordered, value-weighted ("haircut") taint from seed wallets and their co-owned addresses, plus upstream exposure from pattern-confirmed wallets (`backend/ml/propagation.py`), then noisy-OR fusion (`backend/ml/scoring.py`) |

### Dataset requirements → implementation

| Requirement | How it is met |
|---|---|
| Synthetic dataset modelled on real Bitcoin P2P/transaction fields | `scripts/generate_dataset.py` simulates a two-month economy with users, exchanges, merchants, CoinJoins, a darknet vendor, mules and two ransomware groups |
| Minimum fields: `timestamp, src_ip, dst_ip, src_port, dst_port, txid, input_addresses[], output_addresses[], input_amounts[], output_amounts[]` | All generated. Datasets containing only these fields are fully supported, and tested |
| `geo_country`/`asn` from an open-source downloadable GeoIP database | Offline `.mmdb` lookups with `geoip2`: DB-IP Lite (CC BY 4.0, no account, downloaded by `scripts/download_geoip.py`) or MaxMind GeoLite2 |

### Expected deliverables → where to find them

| Deliverable | Where |
|---|---|
| Workable, complete offline solution for Linux | This repository; verified on Ubuntu 24.04 ([§4](#4-complete-setup-step-by-step)). After the one-time GeoIP download it needs no internet |
| Working prototype with ingestion, correlation and AI/ML model | `backend/`, `frontend/`, 67 automated tests, `demo.sh` |
| Short technical write-up: approach, model choice, explainability method | [`docs/technical-writeup.md`](docs/technical-writeup.md) |
| Dashboard showing flagged entities and the evidence for each flag | `frontend/` ([§5](#5-using-the-platform)) |

---

## 2. Architecture at a glance

```text
                    ┌───────────────────────────────────────────────┐
  CSV / JSON / XML  │  React single-page UI  (Vite · Cytoscape)      │
  upload ──────────►│  leads · why flagged · evidence · link graph   │
                    └───────────────▲───────────────────────────────┘
                                    │ HTTP (JSON)
                    ┌───────────────┴───────────────────────────────┐
                    │  FastAPI  (backend/api)                        │
                    │  datasets · analyze · seeds · leads · graph    │
                    └───────┬───────────────────────────┬───────────┘
                            │                           │
          ┌─────────────────▼───────────────┐   ┌───────▼────────────────┐
          │  Analysis pipeline               │   │  Neo4j (local)          │
          │  ingest → GeoIP → correlate →    │──►│  Wallet · Transaction   │
          │  patterns → entities → anomaly   │   │  IP · Country · ASN     │
          │  → propagation → fusion          │   │  (scoped by dataset_id) │
          └─────────────────┬───────────────┘   └────────────────────────┘
                            │
          ┌─────────────────▼───────────────┐   ┌────────────────────────┐
          │  Dataset store (JSON snapshot)   │   │  GeoIP .mmdb (local)    │
          │  data/raw/uploads/<dataset_id>/  │   │  data/geoip/            │
          └──────────────────────────────────┘   └────────────────────────┘
```

| Layer | Technology |
|---|---|
| Language / runtime | Python 3.12 (managed with `uv`), Node.js 22 |
| ML | scikit-learn (Isolation Forest, MLP autoencoder, HDBSCAN, PCA, truncated SVD), SciPy (sparse matrices), NumPy, pandas |
| API | FastAPI + Uvicorn |
| Graph database | Neo4j 5+/2026.x Community (Bolt, official Python driver) |
| GeoIP | `geoip2` reading DB-IP Lite or MaxMind GeoLite2 `.mmdb` files |
| Frontend | React 19, Vite 8, Cytoscape.js, axios; plain CSS with light/dark themes |
| Tests | pytest (unit, pipeline, API, and Neo4j integration tests) |

---

## 3. Quick start

On a machine that already has `uv`, Node.js 22 and a running Neo4j (full
instructions are in [§4](#4-complete-setup-step-by-step)):

```bash
git clone https://github.com/linuxkalihydra-creator/sih.git && cd sih
uv sync                                   # Python dependencies
(cd frontend && npm ci)                   # frontend dependencies
cp .env.example .env                      # then set NEO4J_PASSWORD
uv run python scripts/download_geoip.py   # one-time, needs internet

uv run uvicorn backend.api.main:app --host 127.0.0.1 --port 8000   # terminal 1
cd frontend && npm run dev                                          # terminal 2
```

Open **http://localhost:5173**, click **Upload dataset**, choose
`data/synthetic/transactions.csv` and then **Analyze** (about a minute).

To run everything non-interactively (generate data, analyse, test, build), use
`./demo.sh`.

---

## 4. Complete setup, step by step

These steps were verified on **Ubuntu 24.04 LTS** (5 CPU cores, 10 GB RAM).
Other recent Debian/Ubuntu releases work the same way. Budget about 2 GB of
disk space for dependencies, Neo4j and data.

### 4.1 System packages

```bash
sudo apt update
sudo apt install -y git curl ca-certificates gnupg build-essential
```

**Python 3.12 and `uv`.** Ubuntu 24.04 ships Python 3.12. `uv` installs the
exact dependencies from `uv.lock`, and can fetch Python 3.12 itself if the
system has an older version.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
exec $SHELL -l          # reload PATH
uv --version
```

**Node.js 22** (Vite 8 needs Node 20.19+ or 22.12+). For example, from NodeSource:

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
node --version
```

### 4.2 Neo4j (graph database)

Neo4j stores the entity/transaction graph. It is **recommended but optional**:
without it, analysis still completes and the link graph is served from the
stored analysis. Install Java 21 and Neo4j Community from Neo4j's apt
repository:

```bash
sudo apt install -y openjdk-21-jre-headless
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://debian.neo4j.com/neotechnology.gpg.key | sudo gpg --dearmor -o /etc/apt/keyrings/neotechnology.gpg
echo 'deb [signed-by=/etc/apt/keyrings/neotechnology.gpg] https://debian.neo4j.com stable latest' | sudo tee /etc/apt/sources.list.d/neo4j.list
sudo apt update && sudo apt install -y neo4j

sudo neo4j-admin dbms set-initial-password 'choose-a-password'   # before the first start
sudo systemctl enable --now neo4j
systemctl is-active neo4j                                        # → active
```

Neo4j listens on `bolt://localhost:7687`, with a browser UI at
http://localhost:7474.

<details>
<summary>Alternative: Neo4j in Docker</summary>

```bash
docker run -d --name neo4j -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/choose-a-password -v neo4j-data:/data neo4j:5
```
</details>

### 4.3 Get the code and install dependencies

```bash
git clone https://github.com/linuxkalihydra-creator/sih.git
cd sih
uv sync                    # creates .venv/ with the locked Python dependencies
cd frontend && npm ci && cd ..
```

### 4.4 Configure

```bash
cp .env.example .env
nano .env                  # set NEO4J_PASSWORD to the password chosen in 4.2
```

| Variable | Default | Purpose |
|---|---|---|
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection |
| `NEO4J_USERNAME` | `neo4j` | Neo4j user |
| `NEO4J_PASSWORD` | none | Neo4j password (`.env` is git-ignored) |
| `GEOIP_COUNTRY_DB` | first of `data/geoip/GeoLite2-Country.mmdb`, `data/geoip/dbip-country-lite.mmdb` | Country database |
| `GEOIP_ASN_DB` | first of `data/geoip/GeoLite2-ASN.mmdb`, `data/geoip/dbip-asn-lite.mmdb` | ASN database |
| `DATASET_STORE_DIR` | `data/raw/uploads` | Where uploads and analyses are stored |

The frontend reads its API address from `frontend/.env` or `frontend/.env.local`
(`VITE_API_URL`, default `http://127.0.0.1:8000`; template in
`frontend/.env.example`).

### 4.5 Download the GeoIP databases (the only online step)

```bash
uv run python scripts/download_geoip.py
# Downloaded .../dbip-country-lite-YYYY-MM.mmdb.gz -> data/geoip/dbip-country-lite.mmdb
# Downloaded .../dbip-asn-lite-YYYY-MM.mmdb.gz     -> data/geoip/dbip-asn-lite.mmdb
# Check: 8.8.8.8 -> US
```

- **Source and licence:** these are the free DB-IP Lite country and ASN
  databases (about 18 MB in total, CC BY 4.0, "IP Geolocation by DB-IP",
  https://db-ip.com). No account is needed.
- **MaxMind instead:** place `GeoLite2-Country.mmdb` and `GeoLite2-ASN.mmdb` in
  `data/geoip/`. GeoLite2 needs a free MaxMind account.
- **Lookups are local:** they never touch the network, and the `.mmdb` files
  are git-ignored.
- **Without a database:** analysis still runs using the dataset's own
  `geo_country`/`asn` values, and the dashboard shows which source each value
  came from.

### 4.6 Verify the installation

```bash
uv run pytest -q
# 67 passed   (the Neo4j integration tests are skipped if Neo4j is not running)
```

### 4.7 Run the platform

```bash
# terminal 1 — API
uv run uvicorn backend.api.main:app --host 127.0.0.1 --port 8000

# terminal 2 — UI
cd frontend && npm run dev
```

| URL | What |
|---|---|
| http://localhost:5173 | Investigation UI |
| http://127.0.0.1:8000/docs | Interactive API docs (Swagger) |
| http://127.0.0.1:8000/health | Health and Neo4j status |
| http://localhost:7474 | Neo4j Browser (query the stored graph directly) |

For a production-style build of the UI, run `cd frontend && npm run build` and
serve `frontend/dist/` with any static web server.

### 4.8 Going fully offline (checklist)

Once these are done, the machine can be disconnected:

- [ ] `uv sync` completed (`.venv/` present)
- [ ] `npm ci` completed (`frontend/node_modules/` present)
- [ ] Neo4j installed and `active` (optional)
- [ ] `data/geoip/*.mmdb` downloaded
- [ ] `uv run pytest -q` passes

Nothing at runtime calls an external service: no CDN fonts or scripts, no
blockchain APIs, no remote GeoIP lookups.

### 4.9 One-command demo

```bash
./demo.sh
```

The script runs these steps:
1. regenerates the synthetic dataset, identical to the committed one (seed 42);
2. downloads GeoIP if it is missing;
3. runs the full analysis with the seed list, writing to `data/processed/`;
4. runs the test suite;
5. builds the frontend.

It finishes by printing the commands that start the API and UI.

---

## 5. Using the platform

The whole investigation happens on **one page**.

```text
┌ Top bar ──────────────────────────────────────────────────────────────────────┐
│ dataset ▾ │ Upload dataset │ Seeds (n) │ Analyze │ ● Neo4j online              │
├ KPI strip ────────────────────────────────────────────────────────────────────┤
│ transactions · wallets · entities · communities · CRITICAL+HIGH · patterns ·   │
│ seeds · GeoIP source · wallet risk distribution                                │
├ Leads ─────────────────────────────┬ Investigation ───────────────────────────┤
│ [Wallets|Transactions|Patterns|    │ id · risk · level · confidence · seed    │
│  Communities]                      │ WHY FLAGGED: contribution bar, reasons,  │
│ level filter · search · sort       │   detectors, feature attributions        │
│ ranked table (click a row)         │ LINK ANALYSIS: interactive graph          │
│                                    │ EVIDENCE: transactions · network ·        │
│                                    │   entity · taint · patterns · features    │
├────────────────────────────────────┴──────────────────────────────────────────┤
│ Models & evaluation (collapsible)                                              │
└───────────────────────────────────────────────────────────────────────────────┘
```

### A typical investigation

1. **Upload** a CSV, JSON or XML dataset. For the demo, use
   `data/synthetic/transactions.csv`, or `data/mock_uploads/bitcoin_transactions.csv`.
2. **Analyze.** The full pipeline runs in about a minute for 10,000 transactions.
   The KPI strip fills in and the leads are ranked by risk.
3. **Add seeds.** Open **Seeds** and paste or load `data/synthetic/seed_wallets.txt`:
   known illicit wallets, such as addresses reported by ransomware victims. Risk
   is re-propagated through the transaction graph immediately (under a second),
   without refitting the models. You can also mark any selected wallet as a seed.
4. **Work the leads.** The page opens on the strongest lead that is not already a
   seed. For each lead:
   - **Why flagged:** a bar splitting the risk score into points from anomaly,
     pattern, taint and network signals; the reasons behind each; which of the
     three anomaly models flagged it; and which features drove them.
   - **Link analysis:** the wallet's neighbourhood along the money flow
     (`Wallet → Transaction → Wallet`, plus relaying IPs), coloured by risk, with
     seeds outlined. Click any wallet or transaction to investigate it next;
     **Back** returns.
   - **Evidence:** the wallet's transactions; its network footprint (IPs with
     country, ASN and association strength, ports, other wallets on the same
     IPs); the addresses co-owned with it; the taint path back to the seed; its
     patterns; and its raw features.
5. **Patterns and communities.** The Patterns tab lists every detected peeling
   chain, layering structure, pass-through wallet and CoinJoin, and opens its
   subgraph. The Communities tab lists the entity communities with average and
   maximum risk.
6. **Models & evaluation.** This section describes each model, what it flagged,
   and, for labelled synthetic data, precision/recall and ROC-AUC.

> Results are investigative leads, not proof of identity, ownership or criminal activity.

### Command-line use (no UI)

```bash
uv run python scripts/run_analysis.py --input data/synthetic/transactions.csv \
    --seeds data/synthetic/seed_wallets.txt --output-dir data/processed
uv run python scripts/evaluate_models.py          # wallet-level metrics vs ground truth
```

---

## 6. How it works, end to end

This section follows one dataset through the system. The ingestion, API and
orchestration code live in `backend/ingestion`, `backend/api` and
`backend/pipeline`.

```text
 upload ─► 1 ingest ─► 2 GeoIP ─► 3 flow index ─► 4 network correlation
                                                        │
      ┌──────────────────────┬─────────────────────┬────┴──────────────┐
      ▼                      ▼                     ▼                   ▼
 5 patterns           6 entity clustering   7 features        (transactions)
 CoinJoin · peeling   CIO → embeddings      wallet · tx              │
 layering · mules     → communities              │                  │
      │                      │                   ▼                  │
      │                      │            8 anomaly ensemble ◄──────┘
      ▼                      ▼                   │
 9 risk propagation (seeds · upstream exposure)  │
      └──────────────────────┬───────────────────┘
                             ▼
       10 fusion → risk · level · confidence · Shapley contributions · reasons
                             ▼
       11 Neo4j graph   ·   12 snapshot + outputs   ·   API → UI
```

| # | Stage | What happens | Code |
|---|---|---|---|
| 1 | **Ingest** | The file is parsed by format and normalised to one schema: list fields decoded, amounts aligned per address, a missing fee derived as inputs − outputs. Invalid rows and duplicate TXIDs are dropped and counted | `ingestion/{csv,json,xml}_parser.py`, `normalizer.py`, `validator.py` |
| 2 | **Enrich** | Each transaction's relaying IP is resolved to a country and ASN from the local `.mmdb`. The source of every value is recorded | `enrichment/geoip.py`, `asn.py` |
| 3 | **Flow index** | Transactions are sorted in time. For every address it records when it received and when it spent, so money can be followed forward and backward in time | `correlation/flows.py` |
| 4 | **Network correlation** | Network observations are attributed to the addresses that *spend* in a transaction (the relaying node is the spender's). Per address: IPs, ASNs, countries, non-standard-port share, wallets sharing its IPs, and IP↔wallet association strength | `correlation/network.py` |
| 5 | **Pattern detection** | CoinJoins, peeling chains, layering and rapid pass-through are found in the flow graph. Each wallet in a pattern gets a role | `ml/patterns.py` |
| 6 | **Entity clustering** | Co-spent addresses are merged into entities (CoinJoins excluded). Entities are embedded with DeepWalk and grouped into communities with HDBSCAN | `ml/entities.py` |
| 7 | **Features** | 21 wallet model features (amounts, holding time, quick spends, counterparties, IP/ASN/country diversity, ports, shared IPs, fee rate, co-spending, entity size, timing) and 12 transaction features (shape, fee rate, equal/fresh/round outputs, age of funds, port, relaying-IP load) | `ml/features.py` |
| 8 | **Anomaly ensemble** | Isolation Forest, an autoencoder and ECOD score every wallet and every transaction. The top 10% get occlusion-based feature attributions | `ml/anomaly.py` |
| 9 | **Risk propagation** | Taint flows forward in time from seed wallets and their co-owned addresses. Exposure flows backward from pattern-confirmed wallets | `ml/propagation.py` |
| 10 | **Fusion & explanation** | The four signals are combined into a 0–100 risk score and level, with exact per-signal Shapley points, a confidence and evidence-based reasons, for wallets and transactions | `ml/scoring.py` |
| 11 | **Graph persistence** | The entity/transaction graph is written to Neo4j, tagged with `dataset_id`. Only this dataset's previous graph is replaced | `graph/neo4j_client.py` |
| 12 | **Store & serve** | The analysis is saved as a JSON snapshot, restored into memory on first request, and queried by the API. Seed changes re-run only stages 9–10 | `pipeline/investigation.py`, `api/main.py` |

For 10,000 transactions (20,481 addresses) stages 1–10 take about 25–45 s on the
development VM, and the Neo4j write about 25 s more.

---

## 7. Technical approach

This is a summary. The full reasoning, formulas and references are in
[`docs/technical-writeup.md`](docs/technical-writeup.md).

### 7.1 Correlating the network and blockchain layers

The first node to relay a transaction (`src_ip`) is usually the spender's own
node or proxy. The platform therefore ties each IP to the **input** addresses of
the transactions it relayed, never to the receivers. From that it derives
evidence an investigator can act on:

- **Infrastructure diversity:** the number of IPs, ASNs and countries a wallet
  spends from.
- **Protocol anomalies:** the share of relays sent to non-standard ports (not
  8333).
- **Co-location:** other wallets broadcast from the same IPs.
- **Attribution strength:** `share × exclusivity`. An IP that relays most of a
  wallet's spends and nobody else's points at the operator's infrastructure.
- **Timing:** holding time, quick spends, burstiness and activity rate link
  network timing to fund movement.

### 7.2 Entity clustering: common-input ownership + graph embeddings

1. **Common-input ownership (CIO).** Addresses spent together must be signed by
   one wallet, so union-find merges them into entities. **CoinJoins are detected
   first and excluded**, because their inputs belong to different people on
   purpose. On the synthetic data this yields 0 mixed-owner entities, against 33
   when CoinJoins are included.
2. **Graph embeddings.** Entities become nodes of a payment graph. DeepWalk is
   computed as a matrix factorisation (Qiu et al., WSDM 2018):
   - sample random walks;
   - count window co-occurrences;
   - build a positive PMI matrix;
   - factorise it with truncated SVD.

   It is deterministic and fast (15,000 entities in seconds) and needs no deep
   learning framework.
3. **Communities.** HDBSCAN clusters entities on the leading principal
   components of their embeddings. It finds groups in the same region of the
   money-flow graph, and leaves unplaceable entities unassigned instead of
   forcing them into a cluster.

### 7.3 Anomaly detection: an ensemble on wallets and transactions

| Model | How it finds outliers | Why it is included |
|---|---|---|
| Isolation Forest | Rows isolated in few random splits | Fast, robust baseline for extreme values |
| Autoencoder (3-layer MLP bottleneck) | Rows it cannot reconstruct | Catches unusual *combinations* of individually normal features |
| ECOD (Li et al., 2022) | Skew-aware empirical tail probabilities per feature | Parameter-free and per-feature interpretable |

- **Scoring:** features are log-compressed and standardised. Each model's
  score is robust-standardised, and the ensemble ranks on their mean.
- **Agreement:** the share of models that put a row in their own top 5%. The
  three models find outliers in different ways, so agreement is real evidence.
- **Fitting:** the ensemble is fitted twice, on wallets (flows through an
  address) and on transactions.

### 7.4 Peeling-chain and mixing detection

| Pattern | Detection rule on the time-ordered flow graph |
|---|---|
| **Peeling chain** | ≥ 5 consecutive transactions with ≤ 2 inputs and 2 outputs. Each peels ≤ 30% and sends the remainder to a fresh address that is spent in the next step within 72 h. Strength grows with length and speed |
| **CoinJoin** | ≥ 5 inputs and ≥ 5 outputs, with ≥ 3 outputs of exactly equal value (≥ 30% of the outputs) |
| **Layering** | A split into ≥ 3 fresh addresses whose funds are forwarded (≥ 80% per hop) and reconverge on one address within 96 h |
| **Rapid pass-through** | A wallet that forwards ≥ 90% of what it receives within 30 minutes, in a small spend with only dust left over |

Every wallet in a pattern gets a role. Hop wallets count fully; CoinJoin
participants and peel recipients count only partly, because privacy use and
receiving a payment are not crimes.

### 7.5 Risk scoring: propagation from seed illicit wallets

- **Downstream taint ("haircut").** Seeds, and addresses CIO-linked to a seed,
  start tainted. Transactions are replayed in time order, so taint moves only
  forward in time.
  - A transaction carries the value-weighted taint of its inputs, reduced by
    10% per hop.
  - A wallet's taint is tainted value received ÷ total value received. An
    exchange that receives a little dirty money stays low; a hop wallet stays
    near 1.
  - The path back to the seed is kept as evidence.
- **Upstream exposure.** The same replay runs backwards from wallets inside
  confirmed laundering structures. Addresses that sent ≥ 60% of their outgoing
  value into such a structure, within 2 hops, are exposed. This surfaces
  unreported ransomware collection addresses with **no seed at all**, while
  stopping before ordinary payers such as victims.

### 7.6 Fusion, confidence and explainability

The four signals, each in [0, 1] (anomaly, pattern, taint, network), are fused
with a noisy-OR:

```text
risk = 100 × (1 − Π (1 − wₖ · sₖ))        wallet weights: anomaly 0.55 · pattern 0.7 · taint 0.9 · network 0.35
levels: CRITICAL ≥ 80 · HIGH ≥ 60 · MEDIUM ≥ 35 · LOW
```

One strong signal raises the risk, agreeing signals push it towards 100, and it
can never exceed 100. Seeds are 100 by definition.

**Explainability works at three levels:**

1. **Signal level.** With four signals the exact Shapley value of each is cheap,
   so every score splits into points per signal that sum exactly to the score.
2. **Evidence level.** Every non-zero signal becomes a sentence tied to evidence:
   - "Hop wallet in an 18-hop peeling chain";
   - "76% of received value traces back to seed bc1q4ul7…";
   - "Sent 100% of its outgoing value into a laundering structure";
   - "Statistical outlier (3 of 3 detectors, top 0.6%)".
3. **Model level.** Feature attributions come from occlusion on the fitted
   models. For each feature, the attribution averages the score drop when that
   feature is reset to the median and the rise when only that feature is set
   (a Shapley approximation that still credits correlated features). Each is
   shown with its value and population percentile.

**Confidence** measures corroboration, not size:
`0.2 + 0.2 × (independent signal families present) + 0.2 × (detector agreement)`,
capped at 0.99, and 1.0 for seeds. A lead backed by a single anomaly model scores
about 0.47; a tainted peeling-chain hop flagged by all three models scores 0.99.

### 7.7 The graph

```text
(:Wallet)-[:INPUT_FROM]->(:Transaction)-[:OUTPUT_TO]->(:Wallet)
(:Transaction)-[:OBSERVED_IN]->(:IP)-[:BELONGS_TO_ASN]->(:ASN)
(:IP)-[:IP_COUNTRY]->(:Country)   (:Wallet)-[:LOCATED_IN]->(:Country)   (:Wallet)-[:HAS_ASN]->(:ASN)
```

- **Isolation:** every node carries `dataset_id`, uniqueness constraints are
  per dataset, and every query filters on it.
- **Neighbourhood queries** traverse only value-flow edges, nearest nodes first.
  Two wallets are never linked merely because they share a country.
- **Offline fallback:** if Neo4j is unavailable, the same graph is built from
  the stored analysis.

---

## 8. The synthetic dataset

No real seized or intercepted data is used. `scripts/generate_dataset.py`
simulates a two-month economy chronologically, with per-address balances, so
every transaction spends funds its inputs actually hold
(`sum(inputs) = sum(outputs) + fee`).

| Actor | Behaviour |
|---|---|
| 1,500 users | 1–4 addresses each; pay merchants, peers and exchanges; change to fresh addresses; 6% are benign VPN users |
| 4 exchanges, 14 merchants | Batch withdrawals, deposit sweeps, invoice addresses |
| CoinJoin coordinator | Equal-denomination mixing rounds |
| Darknet vendor | Payouts from 26 networks, partly through mules |
| 2 ransomware groups | 40 victims each → consolidation → two peeling chains, layering, CoinJoins → mules → exchange cash-out |

- **Network layer:** illicit actors favour an anonymising IP pool and
  non-standard ports.
- **Resolvable IPs:** addresses come from 64 fixed public networks, so GeoIP
  databases resolve them.
- **Seeds:** only 35% of ransomware group 1's collection addresses are
  "reported" as seeds. Group 2 has none, so the models must find it unaided.

| File (`data/synthetic/`) | Content |
|---|---|
| `transactions.csv` / `.json` / `.xml` | The same 10,000 transactions in each format |
| `seed_wallets.txt` | Known-illicit seed addresses |
| `ground_truth.json` | Every address's owner, role and illicit flag, plus pattern transactions. Used **only** for evaluation |

`behavior_type` is an optional per-transaction evaluation label that the models
never read. Regenerate the data with
`uv run python scripts/generate_dataset.py --records 10000 --seed 42`; the same
seed always produces identical files. Details are in
[`docs/dataset.md`](docs/dataset.md).

---

## 9. Results

Measured on the committed dataset against the synthetic ground truth, with seeds
excluded (`uv run python scripts/evaluate_models.py`).

| Wallet-level score | ROC-AUC | Average precision |
|---|---|---|
| **Fused risk score** | **0.873** | **0.564** |
| Anomaly ensemble alone | 0.711 | 0.132 |
| Pattern signal alone | 0.694 | 0.391 |
| Taint signal alone | 0.815 | 0.500 |

| Measure | Value |
|---|---|
| Precision of the top 25 / 50 / 100 / 200 leads | 0.96 / 0.98 / 0.99 / 0.91 |
| HIGH/CRITICAL wallets that are truly illicit | 0.88 (212 wallets) |
| Ransomware peeling-chain hops, layering nodes, consolidation addresses at HIGH+ | 100% |
| Mule wallets at HIGH+ | 80% |
| Unseeded ransomware group's collection addresses surfaced (MEDIUM+) | 100% |
| Ransomware victims flagged HIGH | 0 |

| Transaction-level detector | Precision | Recall |
|---|---|---|
| Peeling chain | 0.86 | 1.00 |
| Layering | 1.00 | 0.96 |
| Rapid pass-through | 0.98 | 1.00 |
| CoinJoin | 1.00 | 0.94 |

The fusion of signals is what makes the leads useful. No single model ranks
illicit wallets nearly as well as the combined score.

---

## 10. API, outputs and storage

**API** (full reference in [`docs/api.md`](docs/api.md), interactive at `/docs`):

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Service and Neo4j status |
| GET / POST | `/datasets`, `/datasets/upload` | List datasets / upload a CSV, JSON or XML file |
| POST | `/analyze` | Run the pipeline `{dataset_id, seed_wallets?}` |
| PUT | `/datasets/{id}/seeds` | Replace seeds and re-score (no refit) |
| GET | `/overview` | KPIs, risk distribution, patterns, models, evaluation |
| GET | `/alerts`, `/alerts/transactions` | Ranked wallet / transaction leads with reasons |
| GET | `/patterns`, `/clusters` | Detected patterns / entity communities |
| GET | `/wallets/{id}`, `/transactions/{txid}` | Full evidence for one lead |
| GET | `/graph` | Link-analysis graph for a wallet, transaction, pattern or community |

**CLI outputs** (`scripts/run_analysis.py --output-dir …`):
- `analysis_summary.json`
- `wallet_risk_scores.json`
- `wallet_features.csv`
- `wallet_clusters.csv`
- `investigative_leads.json` (top 200 wallets with reasons)
- `transaction_alerts.json`
- `patterns.json`

**Storage:**
- Each upload gets a `dataset_id` and a folder
  `data/raw/uploads/<dataset_id>/` (git-ignored) holding the source file,
  metadata, the analysis snapshot and outputs.
- Datasets stay isolated in Neo4j by `dataset_id`. Re-analysing a dataset
  replaces only its own graph.
- To delete one dataset's graph:
  `MATCH (n {dataset_id: $id}) DETACH DELETE n`.

---

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| UI chip shows **Neo4j offline** | Check `systemctl status neo4j` and the password in `.env`. Everything else keeps working; graphs come from the stored analysis |
| `/health` returns `"graph_available": false` although Neo4j is running | Wrong `NEO4J_PASSWORD` in `.env`, or Neo4j still starting (it takes about 20 s after boot); restart uvicorn after fixing `.env` |
| Overview shows GeoIP source `synthetic_fallback` | No `.mmdb` found. Run `uv run python scripts/download_geoip.py` or set `GEOIP_COUNTRY_DB` |
| A dataset says **not analysed** or **outdated** | Press **Analyze**. Analyses from before the ML rewrite (snapshot version 1) must be rebuilt |
| UI cannot reach the API | Start uvicorn on port 8000, or set `VITE_API_URL` in `frontend/.env.local` and restart `npm run dev` |
| `npm run dev` fails with an engine error | Node.js is too old; install Node 22 (§4.1) |
| Analysis feels slow | Normal: about 1 minute for 10,000 transactions including the Neo4j write. Seed changes take under a second |

---

## 12. Limitations

- **Synthetic evaluation.** The results measure the models against a simulation
  of known laundering typologies. They show that the method works as designed,
  not how it would perform on real case data.
- **Address-level linking.** Records carry addresses, not UTXO outpoints, so
  heavy address reuse (exchanges) blurs taint. The haircut rule keeps this
  conservative.
- **Heuristics can be evaded.** Common-input ownership can be fooled by
  undetected CoinJoin variants such as PayJoin, and adversaries can randomise
  peeling and layering structures.
- **Network-only actors.** An actor that looks ordinary on-chain is detected
  only weakly: the darknet vendor reaches MEDIUM for about a third of its
  addresses. IP/timing-based address linking is the natural next step.
- **Not proof.** Scores are leads for an investigator, not evidence of identity,
  ownership or criminal activity.

---

## 13. Project structure

```text
backend/
  api/main.py               FastAPI endpoints (docs/api.md)
  ingestion/                CSV/JSON/XML parsers, normaliser, validator, dataset store
  enrichment/               offline GeoIP country + ASN lookups (.mmdb)
  correlation/              flows.py (time-ordered flow index), network.py (IP/port/ASN correlation)
  ml/                       features, anomaly (ensemble), entities (CIO + embeddings), patterns,
                            propagation (taint + exposure), scoring (fusion, Shapley, reasons)
  graph/                    Neo4j client, graph payload builder
  pipeline/                 orchestrator.py, investigation.py (models + queries + snapshot)
frontend/src/
  App.jsx                   single-page state: datasets, leads, selection, seeds
  components/               TopBar, KpiStrip, LeadsPanel, SeedsDrawer, ModelsSection, DataTable
  components/investigation/ Wallet, Transaction, Pattern and Community views
  components/graph/         InvestigationGraph (Cytoscape link analysis)
scripts/
  generate_dataset.py       synthetic economy simulator (+ ground truth, seeds)
  run_analysis.py           CLI pipeline
  evaluate_models.py        metrics vs ground truth
  download_geoip.py         one-time DB-IP Lite download
data/synthetic/             committed demo dataset, seeds, ground truth
data/mock_uploads/          a second sample file for upload testing
docs/                       technical write-up, API, dataset, pipeline
tests/                      unit, pipeline, API and Neo4j integration tests
demo.sh                     one-command end-to-end demo
```

---

## ⚠️ Disclaimer

A research and educational prototype built for the Smart India Hackathon. All
transactions, wallets and activities in the included data are **synthetic**.
Generated IPs come from public ranges so that GeoIP enrichment can resolve them,
but they say nothing about the real hosts at those addresses. GeoIP data: "IP
Geolocation by DB-IP" (https://db-ip.com), CC BY 4.0. Scores are **investigative
leads, not proof** of identity, ownership or criminal activity.
