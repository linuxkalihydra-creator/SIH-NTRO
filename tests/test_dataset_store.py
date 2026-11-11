import io
from pathlib import Path

import pytest

from backend.ingestion.dataset_store import DatasetStore


def test_dataset_metadata_survives_a_new_store_instance(tmp_path: Path):
    source = tmp_path / "transactions.csv"
    source.write_text("txid\ntransaction-1\n", encoding="utf-8")
    store = DatasetStore(tmp_path / "uploads")
    dataset = store.register_file(source)
    store.update(dataset["dataset_id"], status="ready", analysis_status="completed", record_count=1)

    restarted_store = DatasetStore(tmp_path / "uploads")
    restored = restarted_store.get(dataset["dataset_id"])
    assert restored is not None
    assert restored["filename"] == "transactions.csv"
    assert restored["analysis_status"] == "completed"
    assert restarted_store.list()[0]["dataset_id"] == dataset["dataset_id"]


def test_uploaded_bytes_are_persisted_with_safe_metadata(tmp_path: Path):
    store = DatasetStore(tmp_path / "uploads")
    dataset = store.register_upload("../transactions.csv", b"txid\ntransaction-1\n")

    assert dataset["filename"] == "transactions.csv"
    assert (tmp_path / "uploads" / dataset["dataset_id"] / "source.csv").is_file()
    assert store.get(dataset["dataset_id"])["status"] == "uploaded"


def test_uploads_are_streamed_and_keep_their_format(tmp_path: Path):
    store = DatasetStore(tmp_path / "uploads")
    dataset = store.register_upload("capture.JSON.gz", io.BytesIO(b"\x1f\x8b" + b"0" * 5000))
    assert dataset["format"] == "json" and dataset["size_bytes"] == 5002
    assert Path(dataset["source_path"]).name == "source.json.gz"
    assert store.register_upload("rows.ndjson", b"{}")["format"] == "json" and store.register_upload("rows.tsv", b"a\tb\n")["format"] == "csv"


def test_unsupported_and_empty_uploads_leave_nothing_behind(tmp_path: Path):
    store = DatasetStore(tmp_path / "uploads")
    with pytest.raises(ValueError):
        store.register_upload("notes.txt", b"hello")
    with pytest.raises(ValueError):
        store.register_upload("empty.csv", io.BytesIO(b""))
    assert store.list() == [] and not any((tmp_path / "uploads").glob("*")) if (tmp_path / "uploads").exists() else True


def test_snapshots_are_compressed_and_replace_older_ones(tmp_path: Path):
    store = DatasetStore(tmp_path / "uploads")
    dataset_id = store.register_upload("t.csv", b"txid\n1\n")["dataset_id"]
    assert store.load_snapshot(dataset_id) is None
    legacy = store.directory(dataset_id) / "analysis.json"
    legacy.write_text('{"version": 1}', encoding="utf-8")
    assert store.snapshot_path(dataset_id) == legacy and store.load_snapshot(dataset_id) == {"version": 1}
    store.save_snapshot(dataset_id, {"version": 3, "payload": ["x"] * 1000})
    assert store.snapshot_path(dataset_id).name == "analysis.json.gz" and not legacy.exists()
    assert store.load_snapshot(dataset_id)["version"] == 3
    assert store.snapshot_path(dataset_id).stat().st_size < 1000   # compressed
    # Seeds chosen after an analysis are kept beside the snapshot until the next snapshot replaces them.
    assert store.load_seeds(dataset_id) is None
    store.save_seeds(dataset_id, ["bc1qseed", "1Seed"])
    assert store.load_seeds(dataset_id) == ["bc1qseed", "1Seed"]
    store.save_seeds(dataset_id, [])
    assert store.load_seeds(dataset_id) == []
    store.save_snapshot(dataset_id, {"version": 3})
    assert store.load_seeds(dataset_id) is None


def test_delete_removes_everything(tmp_path: Path):
    store = DatasetStore(tmp_path / "uploads")
    dataset_id = store.register_upload("t.csv", b"txid\n1\n")["dataset_id"]
    store.save_snapshot(dataset_id, {"version": 3})
    assert store.delete(dataset_id) is True and store.get(dataset_id) is None and not store.directory(dataset_id).exists()
    assert store.delete(dataset_id) is False
