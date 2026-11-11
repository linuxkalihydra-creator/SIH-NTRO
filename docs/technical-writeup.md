# Technical write-up: approach, model choice and explainability

**Problem statement:** AI-Powered Monitoring & Analysis of Bitcoin Transaction
Traffic (NTRO, Smart India Hackathon).

The platform reads a bulk capture of Bitcoin transaction traffic, ties the
network layer (IP, port, timing) to the blockchain layer (wallet, TXID,
amount), and returns a ranked list of leads, each with a risk score, a
confidence and the evidence behind it. It runs as one offline Python process on
Linux. The only network access it ever needs is a one-time download of an open
GeoIP database.

Measured on ten independently generated captures, 97% of the wallets it rates
HIGH or CRITICAL are true leads, it finds 87% of all true leads at that level,
and the top 100 leads of every capture are all correct. Section 6 has the
numbers, the ablations and the failure cases.

## 1. Approach

```text
capture rows ──► transactions with their network sightings
                   │
   ┌───────────────┼────────────────────────────────────────────┐
   ▼               ▼                                            ▼
origin IP       laundering patterns                        ownership
(first seen,    (CoinJoin, peeling, layering,              address → entity → actor
 confidence)     pass-through)
   │               │                                            │
   └───────┬───────┴──────────────┬─────────────────────────────┘
           ▼                      ▼
   anomaly ensemble        risk propagation
   (actor behaviour,       (seed taint, structure proceeds,
    transactions)           upstream exposure, shared IPs)
           └──────────┬───────────┘
                      ▼
   fusion ──► risk 0-100 · level · confidence · points per signal · reasons
                      ▼
        ranked wallet, transaction, actor and IP leads ──► dashboard, API, Neo4j
```

Three decisions shape everything else.

1. **A row is a sighting, not a fact about the sender.** The capture shows who
   *relayed* a transaction. Who *created* it has to be inferred, with a
   confidence, before any IP is tied to a wallet.
2. **Three levels of "who".** An *address* is what an alert is raised on. An
   *entity* is the set of addresses one party controls. An *actor* is the set
   of entities one party operates. Behaviour is measured per actor, because one
   address shows too little to judge.
3. **Four independent signals, and weak ones need corroboration.** Being
   unusual, or using a VPN, is not a crime. Those signals count in full only
   when something else supports them, and every score splits exactly into the
   points each signal contributed.

## 2. Correlating the network and blockchain layers

**Origin inference.** A transaction enters the peer-to-peer network at its
creator's node and is relayed onward seconds later, so a monitor sees the
creator first (the first-seen heuristic of Koshy et al. and Biryukov et al.).
Taking the first sighting at face value is wrong whenever the creator's link
was not monitored, so each origin gets a confidence:

```text
confidence = (0.6 + 0.35 × (1 − e^(−lead / 2 s))) × (1 − 0.9 × relay_ratio)
```

- `lead` is how many seconds the first sighting is ahead of the next one. A
  clear lead points at the creator; a near tie means two relays were heard.
- `relay_ratio` is the share of an IP's sightings in which it passed on a
  transaction that someone else announced first. An IP that mostly relays is a
  relay node, not a wallet.
- An origin is *attributed* to the spending wallets only at confidence ≥ 0.5.

This attributes 90.6% of transactions at 99.4% precision. The plain first-seen
rule attributes all of them and is right 93.3% of the time.

**What is attributed to whom.** Network evidence goes only to the addresses
that *spend* in a transaction. Receiving an output says nothing about where the
receiver is. CoinJoins are broadcast by a coordinator, so they are attributed
to no one.

**What the correlation feeds.** Per actor: the IPs, networks (ASN) and
countries it broadcast from, and its share of non-standard ports. Per IP: which
wallets and actors use it, how risky they are, and whether it is a relay node.
These drive actor linking (section 3.1), the network signal and network
association (3.4), and the IP leads in the dashboard.

## 3. Models and why they were chosen

No model is supervised. A seized capture has no labels, and the labels in the
synthetic data are never read by the pipeline. scikit-learn, SciPy and NumPy
are the only ML dependencies, so the system installs offline and its results
are reproducible.

### 3.1 Entity clustering: ownership heuristics and graph embeddings

**Entities (who owns which addresses).** Addresses are merged with union-find
on four kinds of evidence, and each address records which rule and transaction
attached it:

| Rule | Why it holds | Guard |
|---|---|---|
| Common-input ownership | Inputs of one transaction are signed by one wallet | CoinJoins are detected first and excluded |
| Change, only new output | The other outputs already existed, so they are payees | The candidate must be a one-time address |
| Change, only output of the spender's script type | Wallets return change in the type they spend from | Nothing is linked when two outputs qualify |
| Change by elimination | The other new outputs were later swept by a service, so they were payments to it | The candidate must not be co-owned with anyone else |
| Structure continuity | The remainder of a peel is the spender's change; funds that fan out and reconverge never left their owner | Only for detected structures with strength ≥ 0.5 |

A wrong merge poisons every later conclusion, so every rule refuses to decide
when the evidence is ambiguous. Across ten captures no entity mixes two true
owners (pair precision 1.000). Without the CoinJoin exclusion, 33 entities of
the demo capture do.

**Actors (who operates which entities).** Ownership rules cannot join wallets
that never spend together. Two entities are joined when two of three
independent clues agree: they broadcast from the same IP, one pays the other
directly, or both pay into the same deposit address of a service (the same
exchange account). No clue is used alone, services are excluded, and IPs used
by more than 50 entities are ignored. Pair precision is 1.000 (0.998 in the
worst capture). This is what reassembles an operator who hops between
unrelated networks: its wallets share no IP, but they pay each other and cash
out through one account.

**Communities (where an entity sits in the money flow).** Entities become
nodes of a payment graph. DeepWalk embeddings are computed as an explicit
matrix factorisation (random walks, window co-occurrence counts, positive PMI,
truncated SVD; Qiu et al. 2018), reduced with PCA and clustered with HDBSCAN.
The result is deterministic, needs no deep-learning framework, and leaves
entities that fit nowhere unassigned. Communities are context for the analyst
and are not part of the score.

### 3.2 Anomaly detection: an ensemble on actor behaviour

| Detector | Finds | Why it is in the ensemble |
|---|---|---|
| Isolation Forest | Rows isolated by a few extreme values | Fast, robust baseline |
| Autoencoder (MLP with a bottleneck) | Rows it cannot reconstruct | Catches unusual *combinations* of ordinary values |
| ECOD | Rows in the empirical tails, feature by feature | Parameter-free, and each feature's share is readable |

The three find outliers in different ways, so agreement between them is
evidence and not a repeated vote. Each raw score is robust-standardised, the
ensemble ranks on their mean, and `agreement` is the share of detectors that
put a row in their own top 5%.

The ensemble is fitted twice:

- on **actors** (21 features: volume, flow-through, holding time, quick
  forwarding, counterparties, IPs, networks, countries, non-standard port
  share, fee rates, lifetime, activity rate, single-use addresses, widest
  co-spend and payout);
- on **transactions** (12 features: shape, value, fee rate, equal, new and
  round outputs, age of the spent funds, port, load on the relaying IP).

Only the top 10% of the ensemble ranking produce a signal at all.

### 3.3 Peeling-chain and mixing detection

Each detector walks the time-ordered flow graph and returns the transactions,
every wallet's role, a strength between 0 and 1, and a sentence an analyst can
check against the raw data.

| Pattern | Rule | What keeps it precise |
|---|---|---|
| CoinJoin | ≥ 5 inputs and outputs, ≥ 3 outputs of exactly equal value making up ≥ 30% of the outputs | At least as many distinct inputs as equal outputs |
| Peeling chain | ≥ 5 consecutive steps with 1 or 2 inputs and 2 outputs, the smaller ≤ 30%, the remainder going to a new address that is peeled again within 72 h | Strength grows with length and falls with slowness: a shopper makes the same shape over weeks |
| Layering | A split into ≥ 3 new addresses whose funds are forwarded (≥ 80%, up to 3 hops, within 96 h) and reconverge on one address holding ≥ 50% of the value | A branch counts only when its *own* funds make up the spend, so an exchange sweep never qualifies |
| Rapid pass-through | A wallet forwards ≥ 90% of a receipt within 30 minutes, in one output, leaving only dust | The receipt itself must fund the onward transaction |

The role decides the weight. A hop wallet counts fully; someone who received a
peel, or joined a CoinJoin, counts for a third or less, because receiving a
payment and wanting privacy are not crimes.

### 3.4 Risk scoring by propagation

| Propagation | Starts from | Rule |
|---|---|---|
| Seed taint | Known illicit wallets and the addresses co-owned with them | Transactions are replayed in time order. A transaction carries the value-weighted taint of its inputs. A wallet's taint is tainted value received ÷ total value received |
| Structure proceeds | Wallets inside detected laundering structures | The same replay. Finds cash-out points and recipients with **no seed at all** |
| Upstream exposure | The same structure wallets | Backwards in time, at most 2 hops: who sent ≥ 60% of their outgoing value into a structure |
| Network association | Wallets that already carry evidence | A wallet inherits the share of flagged traffic on the IPs it broadcast from, leaving its own traffic out, for two rounds |

The value-weighted ("haircut") rule was chosen over the alternatives for a
reason. Poison taint marks everything a dirty coin ever touches, so an exchange
turns the whole capture red. PageRank-style scores ignore time and amounts.
Haircut respects both, and it keeps a busy exchange low while a hop wallet that
only ever moved seed funds stays near 1.

Three refinements stop taint from spreading too far:

- Inside one entity taint does not decay (moving money between your own
  addresses does not clean it). Between entities it keeps 90% per hop.
- Services pool everyone's funds. What they pay out is discounted by how
  service-like they are, and money arriving at a service is recorded as a
  **cash-out point**, the address an investigator takes to the exchange.
- Once dirty money is spent by a wallet that is not part of any operation, an
  unwitting seller for example, it keeps only 60% per change of hands. That
  seller is a lead; the shops the seller pays are not.

## 4. Fusion: one score, a level, a confidence

Every wallet gets four signals between 0 and 1:

| Signal | Value | Weight |
|---|---|---|
| anomaly | Ensemble percentile of its actor, top 10% only | 0.25 alone, up to 0.60 when corroborated |
| pattern | Strongest structure × role weight; or inherited from its entity or actor; or a cash-out point | 0.70 |
| taint | Strongest of seed taint, 0.9 × upstream exposure, 0.7 × structure proceeds; or inherited | 0.90 |
| network | Non-standard ports and rotation across networks of its actor; IPs shared with flagged wallets | 0.25 |

```text
risk = 100 × (1 − Π (1 − wₖ · sₖ))
w_anomaly = 0.25 + 0.35 × (strongest other signal)
CRITICAL ≥ 80 · HIGH ≥ 60 · MEDIUM ≥ 35 · LOW
```

- **Noisy-OR.** One strong signal raises the risk, agreeing signals push it
  towards 100, and it never exceeds 100. A seed is 100 by definition.
- **Gating.** An outlier with nothing else against it stays a low-priority
  lead; the same oddness on a wallet tied to a laundering structure counts in
  full. Network oddities get a low weight for the same reason, and count mostly
  by opening the anomaly gate and through shared IPs.
- **Inheritance with coverage.** A wallet inherits evidence from the wallets
  it is owned (×0.8) or operated (×0.6) with, scaled by the share of the group
  that is implicated, reaching full strength at a quarter. A few tainted
  deposits therefore never condemn an exchange's thousands of addresses.
- **Services.** Behaviour-based signals are discounted for service-like
  entities, and nothing is inherited across a service's address pool.

**Confidence** measures corroboration, not size:
`0.2 + 0.2 × (signal families with weighted evidence ≥ 0.2) + 0.2 × detector agreement − 0.1 if the main evidence is inherited`,
capped at 0.99, and 1.0 for seeds. Actors and IPs are ranked by rolling wallet
risk up: an actor by its riskiest wallet scaled by how widespread the evidence
is, an IP by the risk-weighted traffic it originated.

## 5. Explainability

Every lead answers "why was this flagged?" at three levels, and each level
points at data the analyst can open.

1. **Points per signal (exact Shapley values).** With four signals the Shapley
   value of each one is computed exactly over all 16 coalitions, so the points
   sum to the risk score with no approximation. The synergy between the
   anomaly signal and whatever corroborates it is split between the two. This
   is the stacked bar in the dashboard.
2. **Reasons tied to evidence.** Each signal becomes a sentence built from the
   underlying facts, with the points it accounts for and a link to the
   evidence: the pattern's subgraph, the taint path back to the seed, the IP
   and the other wallets on it, the transaction and heuristic that attached an
   address to its entity or an entity to its actor.
3. **Feature attributions on the fitted models.** For the anomaly signal, each
   feature is credited by occlusion: the average of the score drop when it is
   reset to the median and the rise when only it is set on an otherwise median
   row. This is the first-and-last-position approximation of a Shapley value.
   Unlike one-sided occlusion, it still credits correlated features. The
   dashboard shows the value, its percentile and its share.

A real lead from the demo capture:

```text
bc1qc2a5…69l8    risk 96.9 CRITICAL    confidence 0.99

 +34.5  pattern   Intermediate in a layering structure (6 branches reconverge)
                  split across 7 fresh addresses, 6 reconverge with 97% of the value
 +33.6  taint     76% of received value traces back to seed bc1qntmc…5t5d
                  0.7948 of 1.0479 BTC received, 3 hops away (path shown)
 +16.6  anomaly   Its actor (A3, 89 wallets) is an outlier for 3 of 3 detectors, top 0.1%
                  drivers: non-standard port share, largest transfer, value received
 +12.2  network   43% of its actor's broadcasts used non-standard ports; 6 networks in
                  7 countries; shares 54.162.102.139 with 24 flagged wallets
   0    context   One of 20 addresses of entity E21, attached by layering-1
```

The same content is exported as a Markdown evidence sheet per lead
(`GET /report/...`), and transaction leads list every network sighting in time
order with the reason the origin was or was not attributed.

## 6. Evaluation

**Method.** One committed dataset says little, because detectors can be tuned
to it by accident. `scripts/benchmark.py` generates a new economy per random
seed, runs the unchanged pipeline, and reports the mean and the **worst**
capture. Nothing is fitted to the ground truth. Seed wallets are excluded from
every metric, because they are given, not detected. Two definitions of a true
lead are reported: *strict* (the address belongs to an illicit actor) and
*investigative* (also the exchange deposit addresses that illicit actors cash
out through).

**Ten captures of 10,000 transactions (about 20,000 addresses each):**

| Metric | Mean | Worst capture |
|---|---|---|
| Average precision, strict | 0.967 | 0.958 |
| ROC-AUC, strict | 0.996 | 0.993 |
| Average precision, investigative | 0.967 | 0.959 |
| Precision of the top 100 leads | 1.000 | 1.000 |
| Precision of the top 400 leads | 0.983 | 0.950 |
| HIGH or above: precision / recall | 0.972 / 0.866 | 0.956 / 0.833 |
| MEDIUM or above: precision / recall | 0.858 / 0.948 | 0.801 / 0.931 |
| Entities: pair precision / recall | 1.000 / 0.643 | 1.000 / 0.592 |
| Actors: pair precision / recall | 1.000 / 0.660 | 0.998 / 0.606 |
| Origin IP: attributed share, precision | 90.6%, 99.4% | 89.3%, 99.3% |
| Transaction risk ROC-AUC | 0.862 | 0.843 |

**Fusion beats every single signal.** Average precision of each signal on its
own: anomaly 0.82, pattern 0.91, taint 0.86, network 0.79. Fused: 0.97.

**Pattern detectors** (transactions, pooled over the ten captures):

| Detector | Precision | Recall |
|---|---|---|
| Peeling chain | 0.79 | 1.00 |
| Layering | 1.00 | 1.00 |
| Rapid pass-through | 0.98 | 1.00 |
| CoinJoin | 1.00 | 1.00 |

Peeling-chain precision is measured against the chains the simulator labels as
such. Of the other detected chain steps, a third are the darknet vendor's own
payout chains, which are illicit but not labelled as chains, and the rest are
ordinary users paying several bills from one balance. Those chains are short
and slow (five steps, one every day or two, against 12 to 24 steps an hour or
two apart), so their strength is about 0.2 against 0.84 to 0.97 for the
ransomware chains, and none of their wallets reaches HIGH.

**Who is found** (mean share of addresses per role):

| True role | Addresses per capture | MEDIUM or above | HIGH or above |
|---|---|---|---|
| Ransom collection addresses (most of them unreported) | 66 | 100% | 100% |
| Peeling-chain hop wallets | 76 | 100% | 99% |
| Layering source, legs and sink | 26 | 100% | 100% |
| Mule wallets | 54 | 100% | 94% |
| Mule change addresses | 54 | 100% | 84% |
| Darknet vendor: receiving wallets | 29 | 96% | 95% |
| Darknet vendor: change addresses | 67 | 95% | 95% |
| Ransomware operator: change addresses | 44 | 81% | 77% |
| Ransomware operator: inputs to a CoinJoin | 8 | 100% | 64% |
| Ransomware operator: outputs of a CoinJoin | 6 | 0% | 0% |
| Exchange deposit addresses used to cash out | 45 | 86% | 39% |
| **Ransomware victims (licit, must not be flagged)** | 111 | **1%** | **0%** |

Two rows matter most. The second ransomware group is never reported, so it has
no seed, and all of its collection addresses are still found, because the
structures their funds are laundered through expose them. And the victims, who
paid those very addresses, are not flagged.

**What each component contributes** (same ten captures, one component removed
at a time):

| Variant | Average precision | HIGH+ precision | HIGH+ recall | MEDIUM+ precision | Most visible effect |
|---|---|---|---|---|---|
| **Full system** | **0.967** | **0.972** | **0.866** | **0.858** | |
| No change-address rules (co-spend and structures only) | 0.876 | 0.977 | 0.620 | 0.882 | Mule change addresses at HIGH: 84% → 24% |
| No actor linking | 0.898 | 0.995 | 0.658 | 0.876 | Darknet vendor at HIGH: 95% → 35% |
| No deposit-address clue for actors | 0.953 | 0.972 | 0.830 | 0.847 | Worst capture: HIGH recall 0.83 → 0.65 |
| No change by elimination | 0.966 | 0.973 | 0.857 | 0.850 | Mule change addresses at HIGH: 84% → 77% |
| Anomaly counted in full on its own (no gating) | 0.954 | 0.949 | 0.882 | 0.506 | Victims at MEDIUM: 1% → 9% |
| No structure-proceeds propagation | 0.947 | 0.971 | 0.789 | 0.867 | Cash-out points found: 86% → 45% |
| No hand-over decay | 0.968 | 0.960 | 0.875 | 0.834 | Worst capture: MEDIUM precision 0.80 → 0.75 |
| No inheritance within entity or actor | 0.959 | 0.975 | 0.845 | 0.870 | |
| No upstream exposure | 0.962 | 0.972 | 0.855 | 0.857 | |
| No taint signal | 0.936 | 0.996 | 0.697 | 0.917 | |
| No pattern signal | 0.954 | 0.973 | 0.774 | 0.871 | |
| No anomaly signal | 0.960 | 0.973 | 0.843 | 0.910 | Worst capture: HIGH recall 0.83 → 0.76 |

Three things stand out. Knowing who owns and operates what matters more than
any single detector: without the change-address rules or without actor
linking, a fifth to a quarter of the true leads drop out of the HIGH tier. Gating is what
keeps the MEDIUM tier usable: counted at full weight, the anomaly signal alone
halves its precision. And the anomaly signal earns its place in the hard
captures, where an operator has neither a seed nor a laundering structure; its
price is MEDIUM-tier precision, because privacy-minded users are outliers too.

**Where it is wrong.**

- *False positives.* Most are licit wallets that really did receive dirty
  money: unwitting recipients of a peel, and to a lesser degree the shops they
  pay. They are the right place for an investigator to look, but their owner
  is not the criminal. The remainder are privacy-minded users whose network
  footprint resembles an operator's; they stay at MEDIUM.
- *Missed.* Outputs that an operator takes out of a CoinJoin (the mix works as
  intended), and leftover change addresses that no heuristic ties to their
  owner.
- *By design.* Cash-out deposit addresses are mostly MEDIUM, not HIGH. They
  belong to an exchange, so they are flagged as cash-out points and not as
  criminal wallets.

## 7. Offline operation and performance

- One Python process serves the API and the built dashboard. No CDN, no
  blockchain API, no remote GeoIP lookup. Neo4j is optional; without it the
  link graph is served from the stored analysis.
- `scripts/make_offline_bundle.sh` packs the source, every dependency as a
  wheel, the built dashboard and the GeoIP databases into one archive for an
  air-gapped machine, which then needs only Python.
- Run time for the analysis: 14 s for 10,000 transactions, 45 s for 50,000,
  100 s for 100,000 (205,000 capture rows, 200,000 addresses) on a laptop CPU.
  Changing the seed wallets re-scores in under a second at 10,000 transactions.
- 144 automated tests cover ingestion, correlation, every model, the pipeline,
  the API, Neo4j persistence, and quality floors on a generated capture.

## 8. Limitations

- **Synthetic evaluation.** The numbers measure the method against a
  simulation whose laundering typologies we designed. They show it works as
  intended across many random economies, not how it performs on seized data.
- **Address-level flows.** Rows carry addresses, not UTXO outpoints, so funds
  are followed by address. The haircut rule keeps this conservative.
- **Heuristics can be evaded.** PayJoin defeats common-input ownership. A
  patient launderer can peel slowly or randomise a layering structure.
- **The origin is the last hop the sensors saw.** Behind Tor or a VPN, the
  origin IP is the exit, and inference is only as good as sensor coverage.
- **Deposit-address linking assumes one account per deposit address.** A
  criminal who hands his deposit address to payers would have them joined to
  him. The clues used are shown for every link so an analyst can reject one.
- **Small fast forwarders look like mules.** A payment processor below the
  service threshold would be flagged. An allow-list is the practical remedy.
- **Not proof.** Scores are leads for an investigator, not evidence of
  identity, ownership or criminal activity.

## References

- Meiklejohn et al., *A Fistful of Bitcoins*, IMC 2013 (common-input ownership, change addresses).
- Koshy, Koshy, McDaniel, *An Analysis of Anonymity in Bitcoin Using P2P Network Traffic*, FC 2014; Biryukov, Khovratovich, Pustogarov, *Deanonymisation of Clients in Bitcoin P2P Network*, CCS 2014 (first-seen origin).
- Möser, Böhme, Breuker, *Towards Risk Scoring of Bitcoin Transactions*, FC Workshops 2014 (poison and haircut taint).
- Victor, *Address Clustering Heuristics for Ethereum*, FC 2020 (deposit-address reuse).
- Perozzi et al., *DeepWalk*, KDD 2014; Qiu et al., *Network Embedding as Matrix Factorization*, WSDM 2018.
- Campello, Moulavi, Sander, *Density-Based Clustering Based on Hierarchical Density Estimates*, PAKDD 2013 (HDBSCAN).
- Liu, Ting, Zhou, *Isolation Forest*, ICDM 2008; Li et al., *ECOD*, IEEE TKDE 2022.
- Shapley, *A Value for n-Person Games*, 1953; Lundberg and Lee, *A Unified Approach to Interpreting Model Predictions*, NeurIPS 2017.
