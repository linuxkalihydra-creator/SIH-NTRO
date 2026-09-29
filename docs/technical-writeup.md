# Technical write-up: approach, model choice and explainability

This platform ingests bulk Bitcoin transaction and network metadata, links the
network layer (IP, port, timing) to the blockchain layer (wallet, TXID, amount),
and produces a ranked, explainable list of investigative leads. It runs entirely
offline on Linux: Python (FastAPI, scikit-learn, SciPy), a local Neo4j, and a
React single-page UI. The only network access is a one-time download of an
open GeoIP database.

## 1. Pipeline

```
CSV / JSON / XML
  → parse, normalise, validate, de-duplicate            backend/ingestion
  → GeoIP country + ASN from a local .mmdb              backend/enrichment
  → time-ordered flow index + network correlation       backend/correlation
  → pattern detectors (CoinJoin, peeling, layering,     backend/ml/patterns.py
    rapid pass-through)
  → entity clustering: common-input ownership           backend/ml/entities.py
    + DeepWalk embeddings + HDBSCAN communities
  → wallet and transaction features                     backend/ml/features.py
  → anomaly ensemble (wallet level and transaction      backend/ml/anomaly.py
    level)
  → risk propagation from seeds, upstream exposure      backend/ml/propagation.py
  → noisy-OR fusion, Shapley contributions,             backend/ml/scoring.py
    confidence, reasons
  → Neo4j graph (Wallet, Transaction, IP, Country, ASN) backend/graph
  → API + single-page UI                                backend/api, frontend
```

`backend/pipeline/investigation.py` holds one analysed dataset. It runs every
model, saves itself as a JSON snapshot, and answers the API's queries. When the
investigator changes the seed wallets, only propagation and fusion re-run
(under a second for 10,000 transactions).

## 2. Correlating the network and blockchain layers

A transaction's `src_ip` is the node that relayed it first, which is usually
the spender's own node or proxy. Network observations are therefore attributed
to the addresses that **spend** in a transaction, not to those that receive.
For every address the platform keeps:

- **Relay footprint:** the IPs, ASNs and countries seen when it spent.
- **Ports:** the share of its broadcasts that went to non-standard destination
  ports (not 8333).
- **Shared IPs:** how many other addresses were broadcast from the same IPs.
- **IP associations:** each IP scored as `share × exclusivity`. An IP that
  relays most of a wallet's spends and nobody else's is a strong pointer to the
  operator's infrastructure.

Timing enters through several features: holding time (receive → next spend),
the share of receipts spent within an hour, burstiness, and activity rate. The
transaction-level anomaly model also sees fee rate, the relaying IP's address
count and the non-standard-port flag.

## 3. The four Section-4 focus areas

### Entity clustering: common-input ownership + graph embeddings

1. **Common-input ownership.** Union-find merges addresses spent together as
   inputs of one transaction. CoinJoin transactions are detected first and
   excluded, because their inputs belong to different people on purpose. This
   is the heuristic's best-known failure mode, and the synthetic data shows the
   effect: 0 mixed-owner entities with the exclusion, against 33 without it.
2. **Graph embeddings.** Entities become nodes of a graph weighted by the
   payments between them. DeepWalk is computed as an explicit matrix
   factorisation (Qiu et al., WSDM 2018):
   - sample truncated random walks;
   - count co-occurrences within a window;
   - build the positive PMI matrix;
   - factorise it with truncated SVD.

   The result is deterministic, needs no deep-learning framework, and embeds
   15,000 entities in a few seconds.
3. **Communities.** HDBSCAN runs on the leading 8 principal components of the
   embeddings. It finds groups of entities that occupy the same region of the
   money-flow graph, and leaves unplaceable entities unassigned (`-1`) instead
   of forcing them into a cluster.

### Anomaly detection: statistically unusual flows

The same ensemble is fitted twice:
- on 21 wallet features;
- on 12 transaction features: inputs/outputs, value, fee rate, largest-output
  share, equal outputs, fresh outputs, round amounts, age of spent funds, prior
  activity, non-standard port, and addresses on the relaying IP.

| Detector | Why it is in the ensemble |
|---|---|
| Isolation Forest | Fast, robust, finds rows isolated by a few extreme features. |
| Autoencoder (MLP 3-layer bottleneck) | Learns the joint structure of normal behaviour; flags rows whose *combination* of features breaks it even if each feature is ordinary. |
| ECOD (empirical-CDF outlier detection, Li et al., TKDE 2022) | Parameter-free, skew-aware tail probabilities; its per-feature terms are directly interpretable. |

Features are log-compressed and standardised. Each detector's raw score is
robust-standardised (median / IQR), and the ensemble score is the percentile of
their mean. `agreement` is the share of detectors that put a row in their own
top 5%. The three models find outliers in different ways (isolation,
reconstruction, tail probability), so agreement between them is real evidence
rather than a repeated vote.

### Peeling-chain and mixing detection

Pattern detectors run over a time-ordered flow index that follows funds by
address:

- **Peeling chain:** a transaction with at most 2 inputs and exactly 2 outputs,
  where the peel is ≤ 30% and the remainder goes to a fresh address that is
  spent in the next peel step within 72 hours. It needs at least 5 hops.
  Strength grows with length and speed.
- **CoinJoin:** at least 5 inputs and 5 outputs, with at least 3 outputs of
  exactly equal value making up at least 30% of the outputs.
- **Layering:** a split into at least 3 fresh addresses whose funds are
  forwarded (≥ 80% per hop) and reconverge on one address within 96 hours.
- **Rapid pass-through:** a wallet that forwards at least 90% of a receipt
  within 30 minutes, in a spend with at most 2 inputs and only dust left over.

Each pattern records every transaction and every wallet's role (hop, peel
recipient, source, intermediate, sink, mule, CoinJoin participant). The role
decides how much the pattern counts against that wallet: hop wallets count
fully, while CoinJoin participants and peel recipients count only partly,
because privacy use and receiving a payment are not crimes.

### Risk scoring: propagation from seed illicit wallets

- **Downstream taint (haircut).** Seeds are known-illicit addresses supplied by
  the investigator; addresses CIO-linked to a seed count as co-owned (0.9).
  Transactions are replayed in time order, so taint only moves forward in time.
  - A transaction's taint is the value-weighted taint of its inputs.
  - Each output receives that share, times 0.9 per hop.
  - An address's taint is tainted value received ÷ total value received. A
    busy exchange that receives a little tainted money stays low; a hop wallet
    that only ever moved seed funds stays close to 1.
  - The strongest upstream contributor is kept, so the path back to the seed
    can be shown.
- **Upstream exposure.** The same replay runs backwards in time from wallets
  the pattern detectors place inside a laundering structure. An address that
  sent at least 60% of its outgoing value into such a structure, within 2 hops,
  is exposed. This finds unreported ransomware collection addresses with no
  seed at all. The hop limit stops at ordinary payers: victims, who pay into a
  collection address one hop further upstream, stay LOW.

## 4. Fusion, confidence and explainability

Four signals, each in [0, 1], feed the score:
- `anomaly`: the ensemble percentile, with only the top 10% counting;
- `pattern`: the strongest pattern × role weight;
- `taint`: the maximum of downstream taint and 0.9 × upstream exposure;
- `network`: non-standard ports and ASN diversity.

They are combined with a noisy-OR:

`risk = 100 × (1 − Π (1 − wₖ·sₖ))`, with wallet weights anomaly 0.55,
pattern 0.7, taint 0.9 and network 0.35.

Any single strong signal raises the risk, agreeing signals push it towards 100,
and it can never exceed 100. A seed is 100 by definition. Levels are CRITICAL
≥ 80, HIGH ≥ 60 and MEDIUM ≥ 35.

**Why each wallet was flagged** is explained at three levels:

1. **Signal level.** With only four signals, the exact Shapley value of each is
   cheap, so the score splits into points per signal that sum exactly to the
   risk score. That is the stacked bar in the UI.
2. **Evidence level.** Each non-zero signal becomes a reason written from real
   evidence, for example:
   - "Hop wallet in an 18-hop peeling chain";
   - "76% of received value traces back to seed bc1q4ul7…";
   - "Sent 100% of its outgoing value into a laundering structure";
   - "Statistical outlier (3 of 3 detectors, top 0.6%)".

   Each reason links to the pattern, the taint path or the transactions behind
   it.
3. **Model level.** Anomaly attributions are computed on the fitted models by
   occlusion. For each feature, the attribution averages two effects: the drop
   in the ensemble score when the feature is reset to the median, and the rise
   when only that feature is set on an otherwise median row. This is the
   first/last-position approximation of a Shapley value. Unlike one-sided
   occlusion, it still credits correlated features such as the different
   transaction counts of an exchange hot wallet. The UI shows each feature's
   value, its population percentile and its share of the score.

**Confidence** measures corroboration, not magnitude:
`0.2 + 0.2 × (signal families with wₖ·sₖ ≥ 0.2) + 0.2 × detector agreement`,
capped at 0.99, and 1.0 for seeds. A lead supported only by one anomaly
detector scores about 0.47; a peeling-chain hop that is also tainted and
flagged by all three detectors scores 0.99.

## 5. Synthetic data

`scripts/generate_dataset.py` simulates a two-month economy chronologically,
with per-address balances. Every transaction spends funds its inputs actually
hold (inputs = outputs + fee).

**Actors:**
- 1,500 users, 6% of them VPN users with several IPs and some odd ports;
- 4 exchanges with batch withdrawals and deposit sweeps, and 14 merchants;
- a CoinJoin coordinator;
- a darknet vendor broadcasting from 26 networks, cashing out partly through
  mules;
- two ransomware groups.

**Ransomware flow:** each group collects 40 victim payments, consolidates them,
and launders the proceeds through two peeling chains, a layering structure, and
CoinJoins joined with its own funds. Mules forward peels to exchanges within
minutes. Illicit actors use an anonymising IP pool and non-standard ports more
often. Only 35% of group 1's collection addresses are "reported" as seeds;
group 2 has none.

IPs come from 64 fixed public /24 networks, so the open DB-IP Lite database
resolves 100% of the countries and 96% of the ASNs. `ground_truth.json` records
every address's owner, role and illicit flag, plus each pattern's transactions.
It is used only for evaluation.

## 6. Results on the committed dataset (10,000 transactions, 20,481 addresses)

Produced by `scripts/evaluate_models.py`, with seeds excluded from every
wallet-level metric.

| Wallet-level score | ROC-AUC | Average precision |
|---|---|---|
| **Fused risk score** | **0.873** | **0.564** |
| Anomaly ensemble only | 0.711 | 0.132 |
| Pattern signal only | 0.694 | 0.391 |
| Taint signal only | 0.815 | 0.500 |

- Precision of the top 25 / 50 / 100 / 200 leads: 0.96 / 0.98 / 0.99 / 0.91.
- HIGH or CRITICAL: 212 wallets at precision 0.88. That covers 42% of all
  illicit addresses (most of the rest are change addresses and vendor wallets,
  reached at MEDIUM).
- Every ransomware peeling-chain hop, layering node and consolidation address
  is HIGH or CRITICAL, as are 80% of mule wallets.
- All ransomware collection addresses are MEDIUM or above. That includes group
  2's, which had no seed and are found by upstream exposure.
- No victim is HIGH (14% are MEDIUM).

| Transaction-level detector | Precision | Recall |
|---|---|---|
| Peeling chain | 0.86 | 1.00 |
| Layering | 1.00 | 0.96 |
| Rapid pass-through | 0.98 | 1.00 |
| CoinJoin | 1.00 | 0.94 |

The fused transaction risk has a ROC-AUC of 0.83, against 0.81 for the
transaction anomaly ensemble alone.

## 7. Limitations

- **Synthetic evaluation.** The numbers above measure the models against a
  simulation whose laundering typologies we designed. They show the method
  works as intended, not how it performs on real, seized data.
- **Address-level linking.** Records carry addresses, not UTXO outpoints, so
  flows are followed by address. Heavy address reuse (exchanges) blurs taint;
  the haircut rule keeps it conservative.
- **Heuristics that can be fooled.** Common-input ownership fails on
  undetected CoinJoin variants (for example PayJoin). The peeling and layering
  detectors can be evaded by adversaries who randomise their structure.
- **Network-only actors.** An actor whose on-chain behaviour looks ordinary is
  only weakly detected. The darknet vendor, identifiable mostly by its network
  footprint, reaches MEDIUM for about a third of its addresses. Linking
  addresses that broadcast from the same IP within a short time would be the
  next heuristic to add.
- **Not proof.** Scores are investigative leads, not proof of identity,
  ownership or criminal activity.
