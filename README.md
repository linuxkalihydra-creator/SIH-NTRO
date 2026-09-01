# 🔎 Bitcoin Investigation Platform

> **An offline, graph-powered platform for analyzing Bitcoin transaction patterns, identifying suspicious behavioral clusters, and generating explainable investigative leads.**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python\&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi\&logoColor=white)](https://fastapi.tiangolo.com/)
[![Neo4j](https://img.shields.io/badge/Neo4j-Graph%20Database-4581C3?logo=neo4j\&logoColor=white)](https://neo4j.com/)
[![React](https://img.shields.io/badge/React-Frontend-61DAFB?logo=react\&logoColor=black)](https://react.dev/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## 📌 Overview

**Bitcoin Investigation Platform** is a local, offline investigation environment designed to help analysts explore Bitcoin-like transaction activity through **data ingestion, behavioral analysis, clustering, alerts, and graph-based investigation**.

The platform transforms transaction records into an interconnected investigation graph containing entities such as:

* 💰 Wallet addresses
* 🔗 Transactions
* 🌐 IP addresses
* 🛰️ Autonomous Systems (ASNs)
* 🌍 Countries
* 🔄 Transaction relationships

The system is intentionally designed around **synthetic data**, allowing investigation workflows to be developed and tested without connecting to the real Bitcoin network or using real-world criminal datasets.

---

## 🎯 Problem Statement

Cryptocurrency transactions are transparent, but large transaction networks can become extremely difficult to investigate manually.

A single wallet may interact with hundreds of addresses, IPs, transactions, exchanges, and geographic locations. Traditional tabular analysis makes it difficult to understand these relationships.

This platform addresses that challenge by combining:

**Data Ingestion → Normalization → Behavioral Analysis → Clustering → Graph Investigation → Explainable Leads**

The goal is to provide investigators with a unified environment for understanding complex transaction networks.

---

## ✨ Key Features

### 📂 1. Dataset-Driven Investigation

Upload a transaction dataset and start an investigation directly from the platform.

Supported formats include:

* CSV
* JSON
* XML

Each uploaded dataset receives a unique `dataset_id`, allowing the investigation pipeline and Neo4j graph to remain isolated.

---

### 🧪 2. Synthetic Dataset Generation

The project includes a configurable synthetic Bitcoin-like dataset generator.

Generate 1,000 records:

```bash
uv run python scripts/generate_dataset.py --records 1000
```

Generate 10,000 records:

```bash
uv run python scripts/generate_dataset.py --records 10000
```

Generated datasets are available as:

```text
data/synthetic/
├── transactions.csv
├── transactions.json
└── transactions.xml
```

The generator produces realistic investigation-oriented fields including:

```text
timestamp
src_ip
dst_ip
src_port
dst_port
txid
input_addresses
output_addresses
input_amounts
output_amounts
fee
script_type
geo_country
asn
behavior_type
```

---

## 🧠 Behavioral Profiles

The synthetic generator supports multiple behavioral patterns for testing the investigation pipeline.

| Profile                  | Description                                             |
| ------------------------ | ------------------------------------------------------- |
| `NORMAL`                 | Baseline, low-volume activity                           |
| `EXCHANGE_LIKE`          | High-volume activity with many counterparties           |
| `RAPID_TRANSFER`         | Rapid movement between related wallets                  |
| `LAYERING_LIKE`          | Multi-hop transaction chains                            |
| `MIXING_LIKE`            | Multi-input/multi-output splitting behavior             |
| `HIGH_NETWORK_DIVERSITY` | Wallets associated with diverse IPs, ASNs and countries |

> ⚠️ These profiles are **synthetic behavioral patterns** used exclusively for experimentation and evaluation. They are not labels for real criminal activity.

---

## 🔬 Investigation Pipeline

The platform follows a structured investigation workflow:

```text
                 ┌──────────────────┐
                 │  Upload Dataset  │
                 └────────┬─────────┘
                          │
                          ▼
                ┌────────────────────┐
                │ Data Validation &  │
                │   Normalization    │
                └─────────┬──────────┘
                          │
                          ▼
                ┌────────────────────┐
                │ Behavioral /      │
                │ Statistical        │
                │ Analysis           │
                └─────────┬──────────┘
                          │
                          ▼
                ┌────────────────────┐
                │ Clustering &       │
                │ Anomaly Detection  │
                └─────────┬──────────┘
                          │
                          ▼
                ┌────────────────────┐
                │ Neo4j Graph        │
                │ Construction       │
                └─────────┬──────────┘
                          │
                          ▼
                ┌────────────────────┐
                │ Interactive Graph  │
                │ Investigation      │
                └─────────┬──────────┘
                          │
                          ▼
                ┌────────────────────┐
                │ Investigative      │
                │ Leads & Alerts     │
                └────────────────────┘
```

---

## 🕸️ Graph-Based Investigation

One of the core components of the platform is its **Neo4j-powered investigation graph**.

The graph represents relationships between different entities rather than treating transactions as isolated rows.

### Nodes

The graph can contain:

* `Wallet`
* `Transaction`
* `IP`
* `ASN`
* `Country`

### Relationships

Examples include:

```text
INPUT_FROM
OUTPUT_TO
OBSERVED_IN
LOCATED_IN
HAS_ASN
```

This allows investigators to explore relationships such as:

```text
Wallet
   │
   ├── Transaction
   │       │
   │       └── Wallet
   │
   ├── IP Address
   │       │
   │       └── ASN
   │
   └── Country
```

The frontend provides interactive graph exploration including:

* Pan
* Zoom
* Node dragging
* Fit-to-screen
* Layout reset
* Cluster-based graph exploration

---

## 🧩 Cluster Investigation

The **Clusters** page groups related entities and provides an investigation-oriented view of each cluster.

When a cluster is expanded, the platform retrieves its corresponding Neo4j neighborhood and renders it as an interactive graph.

To keep visualization responsive:

* Up to **200 nodes** are displayed per cluster.
* Up to **500 relationships** are displayed per cluster.
* Graph loading is performed lazily.
* Stale graph requests are cancelled.
* Graph instances are cleaned up properly.
* Neo4j failures do not prevent the rest of the cluster interface from working.

---

## 🗄️ Dataset Isolation

Each investigation is isolated using a unique `dataset_id`.

When a new dataset is uploaded:

```text
Dataset A
   ↓
Existing investigation data
   ↓
Upload Dataset B
   ↓
Dataset A graph data removed
   ↓
Dataset B analyzed
   ↓
Dataset B persisted to Neo4j
```

This prevents records from different investigations from accidentally appearing in the same graph.

### Reset behavior

| Action             | Reset Neo4j Investigation Data? |
| ------------------ | ------------------------------- |
| Upload new dataset | ✅ Yes                           |
| Browser refresh    | ❌ No                            |
| Open Clusters      | ❌ No                            |
| Fetch statistics   | ❌ No                            |
| Fetch alerts       | ❌ No                            |
| Fetch graph        | ❌ No                            |

The deletion is **dataset-aware**, meaning only records associated with the relevant investigation are removed.

---

## 📊 Data Quality & Validation

Generated datasets are validated before being written to disk.

Validation includes:

* Required field validation
* Unique transaction IDs
* Valid timestamps
* Valid IPv4 addresses
* Valid port ranges
* Non-negative transaction amounts
* Non-negative fees
* Input/output consistency
* Valid behavioral profiles

---

## ⚙️ Technology Stack

| Layer               | Technology       |
| ------------------- | ---------------- |
| Frontend            | React            |
| Backend             | Python + FastAPI |
| Graph Database      | Neo4j            |
| Graph Visualization | Cytoscape.js     |
| Package Management  | `uv`             |
| Data Formats        | CSV / JSON / XML |
| Testing             | Pytest           |

---

## 📁 Project Structure

```text
sih/
│
├── backend/                         # FastAPI backend
│
├── frontend/                        # Frontend application
│
├── data/
│   ├── synthetic/                   # Generated datasets
│   └── processed/                   # Analysis output
│
├── docs/                            # Documentation
│
├── scripts/
│   ├── generate_dataset.py          # Synthetic dataset generator
│   └── run_analysis.py              # Complete analysis pipeline
│
├── src/
│   └── bitcoin_investigation_platform/
│                                    # Core analysis modules
│
├── tests/                           # Automated tests
│
├── demo.sh                          # Demo helper
├── pyproject.toml                   # Project configuration
├── uv.lock                          # Dependency lockfile
└── README.md
```

---

# 🚀 Getting Started

## Prerequisites

Make sure the following are installed:

* Linux
* Python 3.11+
* `uv`
* Neo4j
* Node.js / npm

---

## 1. Clone the Repository

```bash
git clone https://github.com/linuxkalihydra-creator/sih.git
cd sih
```

---

## 2. Install Python Dependencies

```bash
uv sync
```

---

## 3. Generate Synthetic Data

```bash
uv run python scripts/generate_dataset.py --records 10000
```

---

## 4. Run the Analysis Pipeline

```bash
uv run python scripts/run_analysis.py \
  --input data/synthetic/transactions.csv \
  --output-dir data/processed
```

---

## 5. Start the Backend

```bash
uv run python -m uvicorn backend.api.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --reload
```

The backend will be available at:

```text
http://127.0.0.1:8000
```

---

## 6. Start the Frontend

From the frontend directory:

```bash
cd frontend
npm install
npm run dev
```

Then open the development URL shown by Vite.

---

# 🧪 Testing

Run the complete test suite:

```bash
uv run pytest -q
```

---

# 🔄 Typical Usage

### Step 1 — Upload

Upload a CSV, JSON, or XML transaction dataset.

### Step 2 — Analyze

Start the investigation pipeline.

### Step 3 — Inspect Statistics

Review transaction and behavioral statistics.

### Step 4 — Investigate Alerts

Inspect suspicious or unusual behavioral patterns.

### Step 5 — Explore Clusters

Open a cluster to inspect related entities.

### Step 6 — Investigate the Graph

Explore relationships between:

```text
Wallets ↔ Transactions ↔ IPs ↔ ASNs ↔ Countries
```

### Step 7 — Generate Investigative Leads

Use the resulting patterns and relationships as explainable leads for further analysis.

---

# 🔐 Offline & Privacy-First Design

This prototype is intentionally designed for **offline experimentation**.

It does not require:

* Real Bitcoin network access
* Real criminal datasets
* Live blockchain monitoring
* External transaction intelligence services

This makes the platform suitable for:

* Development
* Demonstrations
* Academic research
* SIH prototyping
* Algorithm experimentation
* Controlled investigation simulations

---

# ⚠️ Disclaimer

This project is a research and educational prototype.

All transaction records generated by the included dataset generator are **synthetic** and do not represent real Bitcoin transactions, real wallets, real IP addresses, or real criminal activity.

The behavioral categories are synthetic testing profiles and should **not** be interpreted as evidence of criminal behavior.

---

# 🛣️ Roadmap

### Current

* [x] Synthetic transaction generation
* [x] CSV / JSON / XML support
* [x] Data validation
* [x] Offline analysis pipeline
* [x] Behavioral profiling
* [x] Clustering
* [x] FastAPI backend
* [x] Neo4j graph persistence
* [x] Interactive graph visualization
* [x] Dataset-aware investigation isolation
* [x] Cluster investigation interface

### Future

* [ ] Advanced anomaly detection
* [ ] Explainable risk scoring
* [ ] Improved graph-based clustering
* [ ] Temporal transaction analysis
* [ ] Investigation case management
* [ ] Evidence and report generation
* [ ] More sophisticated entity correlation
* [ ] Multi-chain investigation support
* [ ] Production-scale graph optimization

---

# 🤝 Contributing

Contributions, ideas, bug reports, and improvements are welcome.

A typical contribution workflow:

```bash
git checkout -b feature/your-feature
```

Make your changes, run the tests:

```bash
uv run pytest -q
```

Then commit and push:

```bash
git add .
git commit -m "feat: add your feature"
git push origin feature/your-feature
```

Open a Pull Request describing:

* What changed
* Why it was needed
* How it was tested

---

# 📜 License

This project is distributed under the terms of the license included in this repository.

---

## ⭐ Why This Project?

Bitcoin transactions are transparent, but **transparency does not automatically mean simplicity**.

The Bitcoin Investigation Platform aims to turn large, disconnected transaction datasets into an **interactive investigation environment** where analysts can move from raw data to behavioral patterns, clusters, relationships, and explainable investigative leads.

> **From transaction data → to connected intelligence.** 🔎
