import csv
from pathlib import Path

import pytest

# The minimum dataset fields from the problem statement: no behavior label, geo, ASN, fee or script type.
PROBLEM_STATEMENT_FIELDS = (
    "timestamp", "src_ip", "dst_ip", "src_port", "dst_port", "txid",
    "input_addresses", "output_addresses", "input_amounts", "output_amounts",
)


@pytest.fixture
def minimal_csv_path(tmp_path) -> Path:
    """Write the first 300 synthetic records using only the problem-statement fields."""
    with Path("data/synthetic/transactions.csv").open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))[:300]
    path = tmp_path / "minimal.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=PROBLEM_STATEMENT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row[field] for field in PROBLEM_STATEMENT_FIELDS})
    return path
