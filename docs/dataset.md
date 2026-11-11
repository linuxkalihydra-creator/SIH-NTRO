# Datasets: what the platform reads, and the synthetic capture

## 1. What one row means

A dataset is a **capture of Bitcoin transaction traffic**. Each row is one
*sighting* of a transaction on the peer-to-peer network: at `timestamp`, the
peer `src_ip:src_port` announced transaction `txid` to `dst_ip:dst_port`. The
row repeats the transaction's blockchain fields (addresses, amounts, fee).

The same transaction therefore appears in several rows, once per relay that was
captured. Ingestion merges them: one transaction, with its sightings in time
order. The earliest sighting is the *first-seen relay*, which origin inference
later uses to decide where the transaction entered the network. A dataset with
one row per transaction works too.

## 2. Fields

| Field | Required | Meaning |
|---|---|---|
| `timestamp` | yes | When the sighting was captured |
| `src_ip`, `src_port` | yes | The peer that announced the transaction |
| `dst_ip`, `dst_port` | yes | The peer it was announced to |
| `txid` | yes | Transaction id |
| `input_addresses[]` | yes* | Wallet addresses spent from |
| `output_addresses[]` | yes* | Wallet addresses paid |
| `input_amounts[]` | yes | BTC per input address |
| `output_amounts[]` | yes | BTC per output address |
| `fee` | no | Derived as inputs minus outputs when absent |
| `script_type` | no | `P2PKH`, `P2SH`, `P2WPKH`, `P2TR`, ... |
| `geo_country`, `asn` | no | Used only when no offline GeoIP database resolves the IP |
| `behavior_type` | no | Evaluation label. The models never read it |
| `block_height`, `transaction_size` | no | Kept when present; size gives the fee rate |

\* A row needs at least one input or output address.

These are the minimum fields of the problem statement. A file that has only
those ten columns is fully supported.

### Formats

| Format | Accepted layouts |
|---|---|
| CSV / TSV | Delimiter `,` tab `;` or `\|`, detected from the header line |
| JSON | A top-level array, an object wrapping the list under `transactions` / `records` / `data` / ..., or JSON Lines (`.jsonl`, `.ndjson`) |
| XML | Any element that carries a transaction id is a record, whatever its tag or depth. List fields may be nested elements, repeated tags or attributes |

Every format may be gzip-compressed (`.csv.gz`, `.json.gz`, `.xml.gz`). Files
are parsed as a stream, row by row, so the raw document is never held in memory;
only the merged transactions are.

### Tolerant parsing

Capture tools name and encode these fields differently, so ingestion maps what
it finds onto the schema and reports what it did.

- **Column names** are matched case-insensitively against an alias table:
  `tx_hash`, `hash`, `transaction_id` → `txid`; `source_ip`, `peer_ip`, `saddr`
  → `src_ip`; `inputs`, `vin`, `from_addresses` → `input_addresses`; and so on
  (`backend/ingestion/schema.py`). `ip:port` in one column is split.
- **Lists** may be JSON arrays, Python-style lists, or text separated by `;`,
  `|` or `,`. Inputs and outputs may also be lists of
  `{"address": ..., "amount": ...}` objects.
- **Timestamps** may be ISO-8601 (with `Z` or an offset), `YYYY-MM-DD HH:MM:SS`
  style text, or Unix epochs in seconds, milliseconds, microseconds or
  nanoseconds. Everything is stored as UTC.
- **Amounts** are BTC. A `_sat` / `_satoshi` suffix on the column name marks
  satoshis. Without it, a file whose amounts are all whole numbers reaching
  10,000 is recognised as satoshis and converted.

### What is rejected, and what is repaired

| Problem | Outcome |
|---|---|
| No transaction id, no usable timestamp, no addresses, non-numeric or negative amounts | Row rejected, counted by reason |
| Exact repeat of a sighting | Dropped as a duplicate |
| Same `txid` with different addresses or amounts | First version kept, conflict counted |
| Invalid IP or port | Row kept without that value |
| Amounts missing on one side | Reconstructed from the other side and the fee |
| Amount list not aligned with the address list | Total split evenly |
| Outputs exceed inputs | Kept, fee set to 0 |

The counts, example row numbers, the column mapping that was applied and the
unmapped columns are returned by `GET /ingestion` and shown in the dashboard's
data-quality panel.

### GeoIP

`geo_country` and `asn` describe the **source IP**. When an offline database is
present in `data/geoip/` (DB-IP Lite, downloaded once by
`scripts/download_geoip.py`, or MaxMind GeoLite2 placed there by hand), every IP
is resolved locally and the dataset's own columns are ignored. Without a
database the dataset's columns are used. The overview states which source was
used.

## 3. The synthetic capture

No real seized or intercepted data is used. `scripts/generate_dataset.py`
simulates a two-month Bitcoin economy in time order. Every address keeps a
balance, so each transaction spends funds its inputs actually hold and
`sum(inputs) = sum(outputs) + fee` exactly.

```bash
uv run python scripts/generate_dataset.py --records 10000 --seed 42 --output-dir data/synthetic
```

The same seed always produces the same files. `--records` scales the whole
economy (users, victims, mules, rounds), not just the row count.

### Who is in it (at 10,000 transactions)

| Actor | Behaviour |
|---|---|
| 1,500 users | Own a few addresses, pay merchants, each other and exchanges, send change to new addresses. 6% are privacy users on 3 to 6 IPs, partly in anonymising networks, with 25% non-standard ports |
| 4 exchanges | A deposit address per customer, swept together into a hot wallet; batched withdrawals |
| 14 merchants | Receive payments and consolidate them |
| CoinJoin coordinator | 28 equal-denomination mixing rounds among users |
| Darknet vendor | Broadcasts from 26 IPs in 26 different networks, 35% non-standard ports. Cashes out at exchanges and through 3 mules, and keeps moving funds to new addresses of its own |
| 2 ransomware groups | 40 victims each pay a collection address. The group consolidates, then launders through two peeling chains (12 to 24 hops), a fan-out/fan-in layering structure and CoinJoins. Mules forward within minutes to exchanges |

Two details keep the task honest:

- **Unwitting recipients.** A fifth of the peels pay ordinary users, who are
  licit. A system that flags everyone a criminal ever paid is penalised.
- **Unreported group.** Victims of group 1 "report" 35% of its collection
  addresses, which become the seed wallets. Group 2 is never reported, so it
  must be found without any seed.

### The network layer

- IPs come from 64 fixed public /24 networks, split into residential,
  datacenter and anonymising ranges, so a real GeoIP database resolves them.
- Each transaction is announced by its creator to one to three peers, which
  relay it a few seconds later. Each announcement is a row.
- The capture is imperfect on purpose: for 6% of transactions the creator's
  link was not monitored, so the first row comes from a relay; 3% of
  announcements are logged late; 8% of relays are one-off peers that never
  appear again.

### Files

| File (`data/synthetic/`) | Content |
|---|---|
| `transactions.csv` / `.json` / `.xml` | The same capture in three formats: 10,000 transactions in 20,341 rows, 20,342 addresses |
| `seed_wallets.txt` | 14 reported ransomware addresses, one per line |
| `ground_truth.json` | Used **only** for evaluation, never by the pipeline |

`ground_truth.json` holds:

| Key | Content |
|---|---|
| `wallets[address]` | `entity_id`, `entity_kind` (user, exchange, merchant, darknet_vendor, money_mule, ransomware_operator ...), `role`, `illicit`, and where it applies `group` (the operation) and `cashout_for` (the illicit customer of an exchange deposit address) |
| `patterns[]` | `type` and `txids` of every generated peeling chain, layering structure, pass-through and CoinJoin |
| `origin_ips[txid]` | The IP that really created each transaction |
| `seed_wallets[]` | The reported addresses |

`data/mock_uploads/bitcoin_transactions.csv` is a second sample (10,000
independently drawn transactions, one row each, no ground truth) for trying an
upload of a file that has one sighting per transaction.

### Using your own data

Upload the file in the dashboard, or:

```bash
uv run python scripts/run_analysis.py --input capture.json.gz --seeds known_bad.txt --output-dir out
```

Check the data-quality panel (or `POST /ingest`) first: it shows how the
columns were mapped and what was rejected.
