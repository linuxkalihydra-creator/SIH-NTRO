# Synthetic Dataset Documentation

This project uses synthetic Bitcoin-like transaction and network records for prototyping only. The data is intentionally generated in a controlled way to support future ingestion, normalization, correlation, anomaly detection, and dashboard experimentation.

## Important disclaimer

The dataset is not derived from real blockchain traffic, seized wallet records, or any real criminal investigation data. All wallet identifiers, IP addresses, ports, ASNs, and transaction metadata are generated synthetically and only mimic the structure of a Bitcoin investigation workflow.

## Dataset purpose

The generated files are designed to support future offline stages in a pipeline such as:

- CSV/JSON/XML ingestion
- normalization and enrichment
- IP/wallet/TX correlation
- clustering and anomaly detection
- risk scoring and explainability
- dashboard and link analysis visualization

## Schema

Each generated record contains the following core fields:

- `timestamp`: UTC ISO-8601 timestamp for the observation
- `src_ip`: synthetic source IP address
- `dst_ip`: synthetic destination IP address
- `src_port`: source port used in the synthetic network observation
- `dst_port`: destination port used in the synthetic network observation
- `txid`: synthetic transaction identifier
- `input_addresses`: list of wallet-like input addresses
- `output_addresses`: list of wallet-like output addresses
- `input_amounts`: list of input BTC-like values
- `output_amounts`: list of output BTC-like values
- `fee`: synthetic transaction fee
- `script_type`: synthetic script type such as P2PKH or P2WPKH
- `geo_country`: synthetic country code
- `asn`: synthetic autonomous system number
- `behavior_type`: synthetic label used for generation and evaluation only

Additional fields may also appear, including:

- `block_height`
- `transaction_size`

## Behavioral profiles

The generator creates several behavioral profiles to support later anomaly and clustering work:

### NORMAL
A lower-volume profile with few counterparties and moderate transaction sizes. This acts as the baseline profile.

### EXCHANGE_LIKE
Higher frequency and more counterparties with stronger fan-in and fan-out characteristics.

### RAPID_TRANSFER
Transactions with very short time gaps between related wallet transfers, intended to resemble fast movement of funds.

### LAYERING_LIKE
Synthetic chains such as Wallet_A -> Wallet_B -> Wallet_C -> Wallet_D -> Wallet_E, representing layered transfer patterns.

### MIXING_LIKE
Multiple inputs and multiple outputs with relatively balanced output amounts and complex flows.

### HIGH_NETWORK_DIVERSITY
A wallet associated with several IPs, ASNs, and countries, producing unusually high network diversity.

## Data generation notes

- IP addresses are drawn from a fixed, deterministic pool of 12 public /24 networks. Documentation (RFC 5737), private, CGNAT, multicast and reserved blocks are excluded because they can never resolve in a real GeoIP database. The addresses are synthetic: they do not describe the real hosts at those addresses.
- Wallet addresses are Bitcoin-like strings but are synthetic, not real wallet identifiers.
- The transaction amounts are designed to be plausible and internally consistent.
- `fee` is approximated by sum(input amounts) - sum(output amounts), and is constrained to be non-negative.
- `behavior_type` is a synthetic ground-truth label for evaluation only and should not be used as a feature unless explicitly requested.

## Output files

The generator writes the same dataset to:

- `data/synthetic/transactions.csv`
- `data/synthetic/transactions.json`
- `data/synthetic/transactions.xml`

These files are intended for future ingestion and normalized processing in later phases.
