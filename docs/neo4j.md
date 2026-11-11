# The graph in Neo4j

Neo4j is **optional**. Without it the platform analyses, scores and draws link
graphs from the stored analysis. With it, every analysed dataset is also
written as a property graph, so an investigator can ask their own questions in
Cypher, across the network layer and the blockchain layer at once.

## Start it

```bash
# with Docker (the compose file binds Neo4j to 127.0.0.1 only and switches telemetry off)
cp .env.example .env            # set NEO4J_PASSWORD; compose and the platform read the same value
docker compose up -d neo4j      # or: ./run.sh --neo4j
```

Or install Neo4j 5 Community natively (Debian/Ubuntu/Kali):

```bash
sudo apt install -y openjdk-21-jre-headless
curl -fsSL https://debian.neo4j.com/neotechnology.gpg.key | sudo gpg --dearmor -o /etc/apt/keyrings/neotechnology.gpg
echo 'deb [signed-by=/etc/apt/keyrings/neotechnology.gpg] https://debian.neo4j.com stable 5' | sudo tee /etc/apt/sources.list.d/neo4j.list
sudo apt update && sudo apt install -y neo4j
sudo neo4j-admin dbms set-initial-password 'choose-a-password'   # before the first start
sudo systemctl enable --now neo4j
```

The platform connects with `NEO4J_URI` (default `bolt://localhost:7687`),
`NEO4J_USERNAME` and `NEO4J_PASSWORD` from `.env`. `GET /health` says whether it
is reachable. Neo4j Browser is at http://localhost:7474.

## When the graph is written

| Action | Effect on Neo4j |
|---|---|
| `POST /analyze` (dashboard: **Analyze**) | The dataset's graph is replaced, if Neo4j is reachable |
| `python scripts/run_analysis.py --neo4j --dataset-id case42 ...` | Written under the label `case42` |
| `./run.sh --demo --neo4j` | The demo capture is analysed and written |
| Seed wallets changed (`PUT /datasets/{id}/seeds`) | Only the scores are refreshed in place (in the background) |
| `DELETE /datasets/{id}` | The dataset's subgraph is removed |

Every node carries `dataset_id`. Uniqueness constraints are per
`(dataset_id, key)`, and every write and read is scoped to one dataset, so
captures never mix and re-analysing one replaces only its own subgraph.

The 10,000-transaction demo capture becomes about 52,000 nodes and 106,000
relationships and is written in 15 to 20 seconds. Every example query below
returns in under two seconds on it.

## Schema

```text
(:Wallet)-[:INPUT_FROM {amount}]->(:Transaction)-[:OUTPUT_TO {amount}]->(:Wallet)       money flow
(:Transaction)-[:OBSERVED_IN {confidence, attributed, port}]->(:IP)                     first-seen relay = inferred origin
(:Transaction)-[:RELAYED_BY {delay}]->(:IP)                                             later relays, seconds after the first
(:Wallet)-[:BROADCAST_FROM {count}]->(:IP)                                              attributed spending footprint
(:Wallet)-[:BELONGS_TO]->(:Entity)-[:PART_OF]->(:Actor)                                 who controls / operates it
(:Wallet)-[:IN_PATTERN {role}]->(:Pattern)<-[:PART_OF_PATTERN]-(:Transaction)           laundering structures
(:IP)-[:BELONGS_TO_ASN]->(:ASN)          (:IP)-[:IP_COUNTRY]->(:Country)
```

| Node | Key | Other properties |
|---|---|---|
| `Wallet` | `wallet_id` | `risk_score`, `risk_level`, `confidence`, `is_seed`, `anomaly`, `pattern`, `taint`, `network` (the four signals, 0-1), `entity_id`, `actor_id`, `community`, `total_received`, `total_sent` |
| `Transaction` | `txid` | `timestamp`, `total_input`, `total_output`, `fee`, `input_count`, `output_count`, `risk_score`, `risk_level`, `relay_count`, `origin_ip`, `origin_confidence`, `script_type` |
| `IP` | `ip` | `country`, `asn`, `as_org`, `risk_score`, `risk_level`, `originated`, `relayed`, `wallets`, `relay_ratio` |
| `Entity` | `entity_id` | `wallets`, `service_score`, `counterparties` |
| `Actor` | `actor_id` | `wallets`, `entities`, `risk_score`, `risk_level`, `ips`, `asns` |
| `Pattern` | `pattern_id` | `type`, `strength`, `summary`, `total_value` |
| `ASN` | `asn` | `org` |
| `Country` | `country` | |

Geography belongs to IPs only. A wallet that merely receives an output reveals
nothing about where its owner is, so wallets are never linked to countries or
ASNs directly.

## Example queries

Set the dataset once in Neo4j Browser, then paste any query:

```cypher
:param d => 'dataset_3f0c9c1e5b7a4c0f9d2e61a07b5c8e44'
```

**IPs that originated transactions for high-risk wallets** (network layer joined
to risk scoring):

```cypher
MATCH (w:Wallet {dataset_id: $d})-[b:BROADCAST_FROM]->(ip:IP)
WHERE w.risk_score >= 60
RETURN ip.ip AS ip, ip.country AS country, ip.as_org AS network,
       count(DISTINCT w) AS risky_wallets, sum(b.count) AS broadcasts
ORDER BY risky_wallets DESC LIMIT 20
```

**Infrastructure shared by two or more risky actors**:

```cypher
MATCH (a:Actor {dataset_id: $d})<-[:PART_OF]-(:Entity)<-[:BELONGS_TO]-(w:Wallet)-[:BROADCAST_FROM]->(ip:IP)
WHERE a.risk_score >= 60
WITH ip, collect(DISTINCT a.actor_id) AS actors
WHERE size(actors) >= 2
RETURN ip.ip AS ip, ip.country AS country, actors
ORDER BY size(actors) DESC
```

**Follow the money out of a wallet, three hops forward in time**:

```cypher
MATCH path = (w:Wallet {dataset_id: $d, wallet_id: 'bc1q...'})
             (()-[:INPUT_FROM]->(:Transaction)-[:OUTPUT_TO]->()){1,3}(end:Wallet)
RETURN path LIMIT 200
```

**Where does a laundering structure cash out?** Wallets of services (exchanges)
that received funds straight from a structure's wallets:

```cypher
MATCH (p:Pattern {dataset_id: $d})<-[:IN_PATTERN]-(hop:Wallet)-[:INPUT_FROM]->(t:Transaction)-[o:OUTPUT_TO]->(out:Wallet)
MATCH (out)-[:BELONGS_TO]->(e:Entity)
WHERE p.type IN ['peeling_chain', 'layering', 'rapid_passthrough'] AND e.service_score >= 0.5
RETURN p.pattern_id AS structure, out.wallet_id AS deposit_address, e.entity_id AS service_entity,
       round(sum(o.amount) * 1e8) / 1e8 AS btc, count(DISTINCT t) AS payments
ORDER BY btc DESC LIMIT 25
```

**Every wallet operated by the same actor as a known seed**:

```cypher
MATCH (seed:Wallet {dataset_id: $d, is_seed: true})-[:BELONGS_TO]->(:Entity)-[:PART_OF]->(a:Actor)
MATCH (a)<-[:PART_OF]-(:Entity)<-[:BELONGS_TO]-(w:Wallet)
RETURN a.actor_id AS actor, count(DISTINCT w) AS wallets, round(avg(w.risk_score)) AS mean_risk,
       collect(DISTINCT w.wallet_id)[0..10] AS sample
ORDER BY wallets DESC
```

**Transactions whose origin is uncertain** (first seen from a node that mostly
relays other people's transactions):

```cypher
MATCH (t:Transaction {dataset_id: $d})-[o:OBSERVED_IN]->(ip:IP)
WHERE NOT o.attributed AND ip.relay_ratio >= 0.5
RETURN t.txid AS txid, ip.ip AS first_seen_from, ip.relay_ratio AS relay_ratio, t.relay_count AS sightings
ORDER BY t.risk_score DESC LIMIT 50
```

**Relay timing of one transaction** (who announced it, and how many seconds
after the first sighting):

```cypher
MATCH (t:Transaction {dataset_id: $d, txid: '9f2c...'})-[o:OBSERVED_IN]->(first:IP)
OPTIONAL MATCH (t)-[r:RELAYED_BY]->(relay:IP)
RETURN first.ip AS origin, o.confidence AS origin_confidence, relay.ip AS relay, r.delay AS seconds_later
ORDER BY seconds_later
```

**Countries by risk-weighted traffic**:

```cypher
MATCH (ip:IP {dataset_id: $d})-[:IP_COUNTRY]->(c:Country)
WHERE ip.originated > 0
RETURN c.country AS country, count(ip) AS ips, sum(ip.originated) AS transactions,
       sum(CASE WHEN ip.risk_score >= 60 THEN 1 ELSE 0 END) AS high_risk_ips
ORDER BY high_risk_ips DESC, transactions DESC
```

## Housekeeping

```cypher
// datasets present, with their size
MATCH (n) RETURN n.dataset_id AS dataset, count(n) AS nodes ORDER BY nodes DESC;

// remove one dataset by hand (the API's DELETE /datasets/{id} does this for you);
// in Neo4j Browser put :auto in front of this statement, because it commits in batches
MATCH (n {dataset_id: $d}) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 20000 ROWS;
```
