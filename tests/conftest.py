import csv
from pathlib import Path

import pytest

# The minimum dataset fields from the problem statement: no behavior label, geo, ASN, fee or script type.
PROBLEM_STATEMENT_FIELDS = (
    "timestamp", "src_ip", "dst_ip", "src_port", "dst_port", "txid",
    "input_addresses", "output_addresses", "input_amounts", "output_amounts",
)


@pytest.fixture(scope="session", autouse=True)
def no_downloaded_geoip(tmp_path_factory):
    """Tests use only the GeoIP databases they build themselves, never the downloaded ones.

    Session scope, so fixtures of every scope (including module-scoped pipeline runs) see it.
    """
    missing = tmp_path_factory.mktemp("geoip")
    patch = pytest.MonkeyPatch()
    patch.setenv("GEOIP_COUNTRY_DB", str(missing / "no-country.mmdb"))
    patch.setenv("GEOIP_ASN_DB", str(missing / "no-asn.mmdb"))
    yield
    patch.undo()


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path):
    """Keep tests away from the real upload store and from each other's cached analyses."""
    from backend.api import main
    from backend.ingestion.dataset_store import DatasetStore

    main.app.state.dataset_store = DatasetStore(tmp_path / "uploads")
    main._cache.clear()
    yield
    main._cache.clear()


@pytest.fixture(scope="session")
def small_world():
    """A 1500-transaction synthetic economy (records, label counts, ground truth)."""
    from scripts.generate_dataset import generate_world

    return generate_world(records=1500, seed=7)


@pytest.fixture(scope="session")
def small_paths(small_world, tmp_path_factory):
    """The small dataset written as CSV, JSON and XML plus its seed list."""
    from scripts.generate_dataset import write_ground_truth, write_outputs

    directory = tmp_path_factory.mktemp("small")
    csv_path, json_path, xml_path = write_outputs(small_world[0], directory)
    _, seeds_path = write_ground_truth(small_world[2], directory)
    return {"csv": csv_path, "json": json_path, "xml": xml_path, "seeds": seeds_path}


@pytest.fixture
def minimal_csv_path(tmp_path, small_paths) -> Path:
    """The small dataset reduced to only the problem-statement fields."""
    with small_paths["csv"].open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    path = tmp_path / "minimal.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=PROBLEM_STATEMENT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row[field] for field in PROBLEM_STATEMENT_FIELDS})
    return path
