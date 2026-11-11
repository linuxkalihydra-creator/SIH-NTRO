"""Small, disk-backed registry for locally uploaded investigation datasets."""

from __future__ import annotations

import gzip
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any
from uuid import uuid4

from backend.ingestion.readers import SUFFIX_FORMATS, dataset_suffix

SNAPSHOT_NAME = "analysis.json.gz"
LEGACY_SNAPSHOT_NAME = "analysis.json"
SEEDS_NAME = "seeds.json"


class DatasetStore:
    """Persist dataset metadata and analysis snapshots without a database."""

    def __init__(self, root: str | Path = "data/raw/uploads") -> None:
        self.root = Path(root)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _directory(self, dataset_id: str) -> Path:
        return self.root / dataset_id

    def directory(self, dataset_id: str) -> Path:
        """Folder holding a dataset's source file, metadata, snapshot and outputs."""
        return self._directory(dataset_id)

    def _metadata_path(self, dataset_id: str) -> Path:
        return self._directory(dataset_id) / "metadata.json"

    def list(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        datasets = []
        for metadata_path in self.root.glob("*/metadata.json"):
            try:
                datasets.append(json.loads(metadata_path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(datasets, key=lambda item: item.get("created_at", ""), reverse=True)

    def get(self, dataset_id: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._metadata_path(dataset_id).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _source_name(filename: str) -> tuple[str, str]:
        """(``source.<format>[.gz]``, format name) for a supported dataset file name."""
        suffix, gzipped = dataset_suffix(filename)
        if suffix not in SUFFIX_FORMATS:
            raise ValueError("Unsupported file type. Upload a .csv, .tsv, .json, .jsonl, .ndjson or .xml file (each optionally .gz).")
        return f"source{suffix}{'.gz' if gzipped else ''}", SUFFIX_FORMATS[suffix]

    def _new(self, filename: str, format_name: str, size: int, destination: Path, dataset_id: str) -> dict[str, Any]:
        metadata = {
            "dataset_id": dataset_id,
            "filename": filename,
            "format": format_name,
            "size_bytes": size,
            "created_at": self._now(),
            "updated_at": self._now(),
            "status": "uploaded",
            "record_count": None,
            "analysis_status": "not_started",
            "error_message": None,
            "source_path": str(destination),
        }
        self._write_metadata(metadata)
        return metadata

    def register_file(self, source: str | Path, *, copy: bool = True) -> dict[str, Any]:
        source_path = Path(source)
        if not source_path.is_file():
            raise FileNotFoundError(f"Dataset not found: {source_path}")
        suffix, gzipped = dataset_suffix(source_path)
        name = f"source{suffix}{'.gz' if gzipped else ''}"
        dataset_id = f"dataset_{uuid4().hex}"
        directory = self._directory(dataset_id)
        directory.mkdir(parents=True, exist_ok=False)
        destination = directory / name
        if copy:
            shutil.copy2(source_path, destination)
        else:
            destination = source_path
        return self._new(source_path.name, SUFFIX_FORMATS.get(suffix, suffix.lstrip(".")), source_path.stat().st_size, destination, dataset_id)

    def register_upload(self, filename: str, content: bytes | IO[bytes]) -> dict[str, Any]:
        """Store an uploaded CSV, JSON or XML payload (bytes or a binary stream) under a generated ID."""
        safe_name = Path(filename).name
        source_name, format_name = self._source_name(safe_name)
        dataset_id = f"dataset_{uuid4().hex}"
        directory = self._directory(dataset_id)
        directory.mkdir(parents=True, exist_ok=False)
        destination = directory / source_name
        try:
            if isinstance(content, (bytes, bytearray)):
                destination.write_bytes(content)
            else:
                with destination.open("wb") as handle:  # streamed in chunks: large captures never sit in memory
                    shutil.copyfileobj(content, handle, length=1 << 20)
            size = destination.stat().st_size
            if not size:
                raise ValueError("Uploaded dataset is empty.")
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return self._new(safe_name, format_name, size, destination, dataset_id)

    def _write_metadata(self, metadata: dict[str, Any]) -> None:
        directory = self._directory(metadata["dataset_id"])
        directory.mkdir(parents=True, exist_ok=True)
        temporary = self._metadata_path(metadata["dataset_id"]).with_suffix(".tmp")
        temporary.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        temporary.replace(self._metadata_path(metadata["dataset_id"]))

    def update(self, dataset_id: str, **changes: Any) -> dict[str, Any]:
        metadata = self.get(dataset_id)
        if metadata is None:
            raise KeyError(dataset_id)
        metadata.update(changes)
        metadata["updated_at"] = self._now()
        self._write_metadata(metadata)
        return metadata

    def delete(self, dataset_id: str) -> bool:
        """Remove a dataset, its snapshot and its outputs."""
        directory = self._directory(dataset_id)
        if not self._metadata_path(dataset_id).exists():
            return False
        shutil.rmtree(directory, ignore_errors=True)
        return True

    def snapshot_path(self, dataset_id: str) -> Path:
        """The stored analysis: gzip-compressed JSON (an uncompressed snapshot from an older run is still found)."""
        directory = self._directory(dataset_id)
        legacy = directory / LEGACY_SNAPSHOT_NAME
        return legacy if legacy.exists() and not (directory / SNAPSHOT_NAME).exists() else directory / SNAPSHOT_NAME

    def save_snapshot(self, dataset_id: str, snapshot: dict[str, Any]) -> None:
        # Written to a temporary file first so a reader never sees a half-written snapshot.
        path = self._directory(dataset_id) / SNAPSHOT_NAME
        temporary = path.with_suffix(".tmp")
        with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=3) as handle:
            json.dump(snapshot, handle, separators=(",", ":"), default=str)
        temporary.replace(path)
        (self._directory(dataset_id) / LEGACY_SNAPSHOT_NAME).unlink(missing_ok=True)
        (self._directory(dataset_id) / SEEDS_NAME).unlink(missing_ok=True)  # the new snapshot carries its own seeds

    def save_seeds(self, dataset_id: str, seeds: list[str]) -> None:
        """Remember the seed wallets chosen after the last analysis.

        Scores are recomputed from the seeds whenever a stored analysis is loaded, so a seed change does not
        need the whole snapshot rewritten: this small file overrides the seeds inside it.
        """
        path = self._directory(dataset_id) / SEEDS_NAME
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps([str(seed) for seed in seeds]), encoding="utf-8")
        temporary.replace(path)

    def load_seeds(self, dataset_id: str) -> list[str] | None:
        """The seeds saved by :meth:`save_seeds`, or ``None`` when the snapshot's own seeds apply."""
        try:
            value = json.loads((self._directory(dataset_id) / SEEDS_NAME).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return [str(item) for item in value] if isinstance(value, list) else None

    def load_snapshot(self, dataset_id: str) -> dict[str, Any] | None:
        path = self.snapshot_path(dataset_id)
        try:
            if path.suffix == ".gz":
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    return json.load(handle)
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, EOFError):
            return None
