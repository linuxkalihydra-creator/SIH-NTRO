# Bitcoin Investigation Platform

**Offline, AI-powered monitoring and analysis of Bitcoin transaction traffic.**
The platform reads a bulk capture of transaction and network metadata (CSV,
JSON or XML), ties the network layer (IP, port, timing) to the blockchain layer
(wallet, TXID, amount), and returns a ranked list of leads for wallets,
transactions, actors and IPs. Every lead comes with a risk score, a confidence
and the evidence behind it, on one link-analysis dashboard.

Built for Smart India Hackathon problem statement 26146, *AI-Powered Monitoring
& Analysis of Bitcoin Transaction Traffic* (National Technical Research
Organisation).

```bash
git clone https://github.com/linuxkalihydra-creator/SIH-NTRO.git && cd SIH-NTRO
bash run.sh --demo          # then open http://127.0.0.1:8000
```

| Document | What it covers |
|---|---|
| This README | What was built, setup, use, results |
| [`docs/technical-writeup.md`](docs/technical-writeup.md) | The technical write-up: approach, model choice, explainability, evaluation, limitations |
| [`docs/pipeline.md`](docs/pipeline.md) | Stages, commands, output files, settings, run time |
| [`docs/dataset.md`](docs/dataset.md) | Input fields and formats, the synthetic capture |
| [`docs/api.md`](docs/api.md) | HTTP API |
| [`docs/neo4j.md`](docs/neo4j.md) | Graph schema and example Cypher queries |

## 1. What was asked, and what was built

| Challenge objective | How it is met |
|---|---|
| Ingest and parse a bulk metadata dataset | Streaming CSV, JSON and XML readers (optionally gzip). Column names, list encodings, timestamps and amount units are recognised automatically. Several sightings of one transaction are merged. Rejected and repaired rows are counted by reason and shown in a data-quality report |
| Build an entity/transaction graph linking IPs, wallets and transactions | A flow graph of wallets, transactions and IPs, grouped into entities and actors. Served to the dashboard from the stored analysis, and written to Neo4j when it is available |
| Implement an AI/ML detection use case with a working model, not just rules | All four suggested focus areas, fused into one score (table below) |
| Ranked, explainable alert list with a confidence score | Ranked wallet, transaction, actor and IP leads. Each score splits exactly into points per signal, each point has a reason tied to evidence, and the confidence reflects how many independent signals agree |
| Dashboard or link-analysis visualisation | One page: leads, "why flagged", an interactive link graph that can follow the money forward or backward, evidence tabs, activity and origin charts |

| Focus area | What was built |
|---|---|
| Entity clustering (common-input ownership + graph embeddings) | Common-input ownership with CoinJoins excluded, three change-address rules and laundering-structure continuity give **entities**. Entities operated together are joined into **actors**. DeepWalk embeddings and HDBSCAN give **communities** |
| Anomaly detection | An ensemble of Isolation Forest, an autoencoder and ECOD, fitted on actor behaviour and on transactions, with per-feature attributions |
| Peeling-chain / mixing detection | Detectors for peeling chains, CoinJoins, fan-out/fan-in layering and rapid pass-through wallets, each with wallet roles and a strength |
| Risk scoring by propagation from seed wallets | Value-weighted taint from seeds, proceeds of detected structures (needs no seed), upstream exposure, and association through shared IPs |

| Expected deliverable | Where |
|---|---|
| Workable, complete offline solution for Linux | `bash run.sh`; verified on Kali Linux. [Section 3](#3-going-offline) covers air-gapped machines |
| Working prototype: ingestion, correlation, AI/ML model | `backend/`, `scripts/`, 144 automated tests |
| Short technical write-up | [`docs/technical-writeup.md`](docs/technical-writeup.md) |
| Dashboard showing flagged entities and the evidence for each flag | `frontend/`, served by the same process at `/` |

**Dataset requirements.** The minimum fields of the problem statement are all
that a file needs. `geo_country` and `asn` come from an open, downloadable
GeoIP database (DB-IP Lite, or MaxMind GeoLite2) that is read locally. No real
seized data is used: `scripts/generate_dataset.py` simulates the capture.

## 2. Setup

### Requirements

- Linux (verified on Kali with Python 3.12 and 3.13; any recent Debian or Ubuntu works).
- Python 3.12 or newer, and one of: [`uv`](https://docs.astral.sh/uv/), or the
  `venv` module (`sudo apt install python3-venv`).
- Nothing else. The built dashboard is part of the repository, so Node.js is
  needed only to change the dashboard. Neo4j and GeoIP databases are optional.

### Start

```bash
bash run.sh --demo
```

`run.sh` creates the Python environment on first use (with `uv` and the lock
file when `uv` is installed, otherwise with `python3 -m venv` and
`requirements.txt`), analyses the bundled capture, and starts one process that
serves both the API and the dashboard:

| URL | What |
|---|---|
| http://127.0.0.1:8000 | Dashboard |
| http://127.0.0.1:8000/docs | Interactive API reference |
| http://127.0.0.1:8000/api/health | Status, and whether Neo4j is reachable |

`bash run.sh` without `--demo` starts with whatever datasets are already
stored. `HOST=0.0.0.0 PORT=9000 bash run.sh` changes the address. The service
has no login, so keep it on `127.0.0.1` unless it sits behind a proxy that
authenticates.

### Optional: GeoIP databases (the only online step)

```bash
uv run python scripts/download_geoip.py     # about 18 MB into data/geoip/
```

This fetches the free DB-IP Lite country and ASN databases (CC BY 4.0, no
account). To use MaxMind GeoLite2 instead, place `GeoLite2-Country.mmdb` and
`GeoLite2-ASN.mmdb` in `data/geoip/`. Lookups are local. Without a database the
platform uses the dataset's own `geo_country` and `asn` columns and says so in
the overview.

### Optional: Neo4j

```bash
cp .env.example .env            # set NEO4J_PASSWORD
bash run.sh --demo --neo4j      # starts the neo4j container from docker-compose.yml
```

With Neo4j running, every analysed dataset is also written as a property graph
that can be queried in Cypher ([`docs/neo4j.md`](docs/neo4j.md)). Without it,
everything else works and the link graph is served from the stored analysis.

### Optional: everything in containers

```bash
cp .env.example .env && docker compose up -d --build     # platform + Neo4j on 127.0.0.1:8000
```

### Settings

Copy `.env.example` to `.env`. Every value is optional.

| Variable | Default | Purpose |
|---|---|---|
| `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` | `bolt://localhost:7687`, `neo4j`, none | Optional graph database |
| `GEOIP_COUNTRY_DB`, `GEOIP_ASN_DB` | first `.mmdb` of each kind in `data/geoip/` | Offline GeoIP databases |
| `DATASET_STORE_DIR` | `data/raw/uploads` | Uploads, analysis snapshots, per-dataset outputs |
| `BITCOIN_STANDARD_PORTS` | `8333` | Ports that count as standard |

## 3. Going offline

Nothing calls an external service at run time: no CDN fonts or scripts, no
blockchain API, no remote GeoIP lookup, no telemetry.

**A machine that was online once** stays usable after it is disconnected, as
soon as `bash run.sh` has created the environment (and, if wanted, the GeoIP
databases and the Neo4j image have been downloaded).

**An air-gapped machine** gets a bundle built on a connected Linux machine with
the same Python minor version and CPU architecture:

```bash
bash scripts/make_offline_bundle.sh      # -> offline-bundle/bitcoin-investigation-platform-offline.tar.gz
```

The archive holds the source, every Python dependency as a wheel, the built
dashboard, the GeoIP databases and the demo capture. On the target:

```bash
tar xzf bitcoin-investigation-platform-offline.tar.gz
cd bitcoin-investigation-platform && bash run.sh --demo
```

The target needs only Python. `run.sh` installs from the bundled wheels without
touching the network, and it also works where the `venv` module and `pip` are
missing. This path was tested inside a network namespace with no route out.

## 4. Using the dashboard

Everything happens on one page.

```text
┌ Top bar ─────────────────────────────────────────────────────────────────────────┐
│ search │ dataset ▾ │ Delete │ Upload dataset │ Seeds │ Analyze │ Export │ Neo4j   │
├ Key figures ─────────────────────────────────────────────────────────────────────┤
│ transactions · value · sightings · wallets · actors · flagged · structures · seeds │
│ leads by risk level for wallets, transactions, actors and origin IPs               │
├ Activity & origin ───────────────────────────────────────────────────────────────┤
│ transactions over time · origin countries · origin networks · origin confidence    │
├ Leads ────────────────────────────┬ Investigation ───────────────────────────────┤
│ Wallets │ Transactions │ Actors │  │ risk · level · confidence · entity · actor   │
│ IPs │ Patterns │ Communities      │ WHY FLAGGED  points per signal, reasons,      │
│ level filter · text filter        │              detectors, feature attributions  │
│ ranked table (click a row)        │ LINK ANALYSIS  neighbourhood / follow the     │
│                                   │              money / where it came from       │
│                                   │ EVIDENCE  transactions · network · ownership  │
│                                   │           · taint · patterns · features       │
├───────────────────────────────────┴──────────────────────────────────────────────┤
│ Data quality · Models & evaluation                                                │
└───────────────────────────────────────────────────────────────────────────────────┘
```

A typical investigation:

1. **Upload** a capture, or open the demo. **Analyze** shows its progress stage
   by stage.
2. **Check the data quality** panel: rows read, rows rejected or repaired and
   why, how the columns were understood, the amount unit that was detected.
3. **Add seeds.** Paste known illicit addresses (the demo uses
   `data/synthetic/seed_wallets.txt`). Risk is re-propagated in about a second;
   no model is refitted. Seeds are optional: laundering structures, their
   proceeds and their cash-out points are found without any.
4. **Work the leads.** For each wallet lead:
   - *Why flagged* splits the score into points from the anomaly, pattern,
     taint and network signals, with the reason behind each.
   - *Link analysis* draws its transactions, counterparties and the IPs they
     were first seen from. **Follow the money** traces the funds forward in
     time, **Where it came from** backward. Tracing stops at services such as
     exchanges, which are marked. Click any node to investigate it next.
   - *Evidence* lists its transactions, its network footprint, who owns and
     operates it and why the platform thinks so, the taint path back to the
     seed, its patterns and its raw features.
5. **Switch the unit.** The Actors tab ranks whole operations (wallets operated
   together). The IPs tab ranks the infrastructure transactions were first seen
   from. The Transactions tab shows every network sighting of a transaction in
   time order and why its origin was or was not attributed. Patterns lists each
   peeling chain, layering structure, pass-through wallet and CoinJoin.
6. **Export.** Ranked leads as CSV, and a Markdown evidence sheet for any lead.

Leads are investigative leads, not proof of identity, ownership or crime.

## 5. Command line

```bash
uv run python scripts/run_analysis.py --input data/synthetic/transactions.csv \
    --seeds data/synthetic/seed_wallets.txt --output-dir data/processed     # leads, CSVs, evidence sheets
uv run python scripts/evaluate_models.py       # the committed capture against its ground truth
uv run python scripts/benchmark.py             # the same metrics over 10 newly generated captures
uv run pytest -q                               # tests
bash demo.sh                                   # generate, analyse, evaluate, test, build
```

`make help` lists these as make targets. Options and output files are in
[`docs/pipeline.md`](docs/pipeline.md).

## 6. How it works

```text
capture file ─► ingest ─► GeoIP ─► flow index ─► patterns ─► origin IP per transaction
                                                    │               │
                               ownership: address → entity → actor ◄┘
                                                    │
              anomaly ensemble (actors, transactions) + risk propagation (seeds, structures, IPs)
                                                    │
        fusion ─► risk · level · confidence · points per signal · reasons ─► leads, graph, exports
```

- **A row is a sighting.** The same transaction appears once per relay that was
  captured. The earliest sighting is taken as the origin only when its lead
  over the next relay is clear and the IP is not a relay node. 91% of
  transactions are attributed this way, 99.4% of them correctly.
- **Three levels of "who".** Addresses are merged into *entities* by ownership
  rules that refuse to decide when the evidence is ambiguous. Entities are
  joined into *actors* when two independent clues agree: the same broadcast IP,
  a direct payment, or deposits into the same exchange account.
- **Four signals.** Statistical anomaly of the actor's behaviour, the strongest
  laundering pattern the wallet takes part in, taint from seeds or from
  detected structures, and the network footprint.
- **Weak evidence needs corroboration.** Being unusual or using a VPN is not a
  crime. Those signals count in full only when another signal supports them.
- **Every score is explained.** With four signals the exact Shapley value of
  each is computed, so the points always add up to the score.

The reasoning, formulas and references are in
[`docs/technical-writeup.md`](docs/technical-writeup.md).

## 7. Results

The models never see the ground truth. Seed wallets are left out of every
metric, because they are given, not detected. A "true lead" is an address of an
illicit actor, or an exchange deposit address an illicit actor cashes out
through.

**Ten independently generated captures** (10,000 transactions and about 20,000
addresses each; `scripts/benchmark.py`):

| Metric | Mean | Worst capture |
|---|---|---|
| Average precision of the wallet ranking | 0.967 | 0.959 |
| ROC-AUC | 0.996 | 0.993 |
| Precision of the top 100 leads | 1.000 | 1.000 |
| HIGH or CRITICAL wallets: precision / recall | 0.972 / 0.866 | 0.956 / 0.833 |
| MEDIUM or above: precision / recall | 0.858 / 0.948 | 0.801 / 0.931 |
| Entities: share of merged pairs with one true owner | 1.000 | 1.000 |
| Actors: share of merged pairs in one true operation | 1.000 | 0.998 |
| Origin IP attributions that are correct | 99.4% | 99.3% |

| Found at HIGH or above | Share |
|---|---|
| Ransom collection addresses, including a group with no seed at all | 100% |
| Peeling-chain hops and layering wallets | 99 to 100% |
| Mule wallets | 94% |
| Darknet vendor wallets (no seed, no laundering structure) | 95% |
| Ransomware victims, who are licit and paid those addresses | 0% |

| Pattern detector | Precision | Recall |
|---|---|---|
| Peeling chain | 0.79 | 1.00 |
| Layering | 1.00 | 1.00 |
| Rapid pass-through | 0.98 | 1.00 |
| CoinJoin | 1.00 | 1.00 |

No single signal comes close to the fused score (average precision: anomaly
0.82, pattern 0.91, taint 0.86, network 0.79, fused 0.97). The peeling-chain
figure, the ablations and the failure cases are discussed in the write-up.

**The committed capture** (`data/synthetic/`, `scripts/evaluate_models.py`):
432 wallets at HIGH or above with precision 0.97 and recall 0.89; the top 200
leads are all correct; 91.0% of transactions attributed to an origin IP.

**Run time** of the analysis on a laptop CPU:

| Transactions | Capture rows | Wallets | Time |
|---|---|---|---|
| 10,000 | 20,341 | 20,342 | 14 s |
| 50,000 | 102,264 | 100,372 | 45 s |
| 100,000 | 205,177 | 200,115 | 100 s |

These numbers measure the method against a simulation. They show that it works
as designed across many random economies, not how it would perform on real
case data.

## 8. Project layout

```text
backend/
  ingestion/     readers (CSV/JSON/XML, gzip), schema and aliases, normaliser, dataset store
  enrichment/    offline GeoIP country and ASN lookups
  correlation/   flows.py (time-ordered flow index), network.py (origin inference, IP footprints)
  ml/            patterns, entities (ownership, actors, embeddings), features, anomaly,
                 propagation, scoring (fusion, Shapley, reasons)
  graph/         Neo4j client
  pipeline/      orchestrator, investigation (models and snapshot), views (queries, graphs, exports)
  api/main.py    FastAPI: routes at / and /api, built dashboard at /
frontend/        React + Vite + Cytoscape dashboard; dist/ is the committed build
scripts/         run_analysis, generate_dataset, evaluate_models, benchmark, load_demo,
                 download_geoip, make_offline_bundle.sh
data/synthetic/  demo capture (CSV, JSON, XML), seed list, ground truth
docs/            technical write-up, pipeline, dataset, API, Neo4j
tests/           ingestion, correlation, models, pipeline, API, Neo4j, quality floors
run.sh  demo.sh  Makefile  Dockerfile  docker-compose.yml
```

To change the dashboard: `cd frontend && npm ci && npm run dev` (it proxies
`/api` to the API on port 8000), then `npm run build` to refresh `dist/`.

## 9. Troubleshooting

| Symptom | What to do |
|---|---|
| `run.sh` says it cannot create a Python environment | Install `uv`, or `sudo apt install python3-venv`. Python must be 3.12 or newer |
| The page at `/` is the API reference, not the dashboard | `frontend/dist` is missing. Run `cd frontend && npm ci && npm run build`, or use the offline bundle |
| The Neo4j chip says offline | Start it (`docker compose up -d neo4j`) and check `NEO4J_PASSWORD` in `.env`. Everything else keeps working |
| A dataset says it must be analysed again | It was analysed by an older version. Press **Analyze** |
| Countries or networks look wrong or generic | No GeoIP database was found, so the dataset's own columns were used. Run `scripts/download_geoip.py` |
| Many rows rejected on upload | Open the data-quality panel: it lists the reasons, example rows and how each column was understood |

## 10. Limitations

- The evaluation is synthetic. The simulator and its laundering typologies are
  ours.
- Flows are followed by address, because the input has addresses and not UTXO
  outpoints.
- Ownership and pattern heuristics can be evaded: PayJoin, slow peeling,
  randomised layering.
- The origin of a transaction is the last hop the sensors saw. Behind Tor or a
  VPN that is the exit, not the person.
- Linking wallets through a shared exchange deposit address assumes one account
  per deposit address. The clues behind every link are shown so an analyst can
  reject one.

More detail is in section 8 of the write-up.

## Disclaimer

A research prototype built for the Smart India Hackathon. All transactions,
wallets and activities in the included data are synthetic. Generated IPs lie in
public ranges so that GeoIP databases can resolve them, and say nothing about
the real hosts at those addresses. GeoIP data: "IP Geolocation by DB-IP"
(https://db-ip.com), CC BY 4.0. Scores are investigative leads, not proof of
identity, ownership or criminal activity.
