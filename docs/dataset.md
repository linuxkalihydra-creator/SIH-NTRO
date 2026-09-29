# Synthetic dataset

The data in `data/synthetic/` is produced by `scripts/generate_dataset.py`. It
models the fields of a Bitcoin P2P/transaction investigation dataset. No real
seized, intercepted or on-chain data is used, and no wallet, IP or activity
refers to a real person or crime.

```bash
uv run python scripts/generate_dataset.py --records 10000 --seed 42 --output-dir data/synthetic
```

The same seed always produces the same files; the committed dataset is
`--records 10000 --seed 42`.

## Schema

| Field | Meaning |
|---|---|
| `timestamp` | UTC ISO-8601 time the transaction was observed |
| `src_ip`, `src_port` | The node that relayed the transaction first (the spender's node or proxy) and its ephemeral port |
| `dst_ip`, `dst_port` | The listening peer it was relayed to. `8333` is standard; illicit actors often use others |
| `txid` | 64-hex transaction id |
| `input_addresses[]`, `input_amounts[]` | Spent addresses and the amount each contributed (aligned lists) |
| `output_addresses[]`, `output_amounts[]` | Receiving addresses and amounts (aligned lists) |
| `fee` | `sum(inputs) − sum(outputs)`, from a fee rate in sat/vB |
| `script_type` | Derived from the address type: P2PKH, P2SH_P2WPKH, P2WPKH, P2TR |
| `geo_country`, `asn` | Synthetic fallback values; replaced by the GeoIP database at analysis time |
| `block_height`, `transaction_size` | Block (one per 10 minutes) and estimated vbytes |
| `behavior_type` | Evaluation label only; never a model feature |

Only the first ten fields (up to the amount lists) are required by the pipeline.

## Simulation

The generator replays a two-month timeline with per-address balances, so every
transaction spends funds its inputs hold.

| Actor | Behaviour | Label |
|---|---|---|
| 1,500 users | Own 1–4 addresses; pay merchants, peers and exchanges; change usually goes to a fresh address; 6% are benign VPN users with several IPs | `NORMAL` |
| 4 exchanges | Batch withdrawals from a hot wallet; sweep customer deposit addresses into it | `EXCHANGE_LIKE` |
| 14 merchants | Fresh invoice addresses, periodic sweeps to an exchange | `NORMAL` |
| CoinJoin coordinator | Rounds of 5–11 participants with equal-denomination outputs | `MIXING_LIKE` |
| Darknet vendor | Payouts broadcast from 26 networks, a third via mules | `HIGH_NETWORK_DIVERSITY` |
| Ransomware group ×2 | 40 victims each pay a unique collection address → consolidation | `RANSOMWARE` |
| | Two peeling chains of 12–24 hops, peeling to exchanges, mules and services | `PEELING_CHAIN` |
| | Fan-out to 4–7 fresh intermediates, two hops each, reconverging on a sink, then cash-out | `LAYERING_LIKE` |
| | Part of the proceeds joins CoinJoin rounds | `MIXING_LIKE` |
| Money mules | Forward 98.5% of what they receive within 3–25 minutes, at high fee rates | `RAPID_TRANSFER` |

**Network layer.** IPs come from 64 fixed public /24 networks, split into
residential, datacenter and an anonymising (VPN/Tor-exit-like) pool used by
illicit actors. Documentation, private and reserved ranges are never used,
because GeoIP databases cannot resolve them.

**Seeds.** Only 35% of group 1's collection addresses are "reported" in
`seed_wallets.txt`. Group 2 has no seeds, so it has to be found by the models.

The dataset is cut to exactly `--records` transactions. Every illicit-flow and
CoinJoin transaction is kept, and licit traffic is sampled evenly to fill the
rest.

## Files

| File | Content |
|---|---|
| `transactions.csv` | Array fields as JSON strings |
| `transactions.json` | Native arrays |
| `transactions.xml` | Nested `<address>` / `<amount>` elements |
| `seed_wallets.txt` | Known-illicit seed addresses, one per line |
| `ground_truth.json` | For every address: `entity_id`, `entity_kind`, `role`, `illicit`; plus each pattern's transaction ids. Used by `scripts/evaluate_models.py` only |
