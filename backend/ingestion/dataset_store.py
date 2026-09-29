"""Small, disk-backed registry for locally uploaded investigation datasets."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


class DatasetStore:
    """Persist dataset metadata and analysis snapshots without a database."""

    def __init__(self, root: str | Path = "data/raw/uploads") -> None:
        self.root = Path(root)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _directory(self, dataset_id: str) -> Path:
        return self.root / dataset_id

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

    def register_file(self, source: str | Path, *, copy: bool = True) -> dict[str, Any]:
        source_path = Path(source)
        if not source_path.is_file():
            raise FileNotFoundError(f"Dataset not found: {source_path}")
        dataset_id = f"dataset_{uuid4().hex}"
        directory = self._directory(dataset_id)
        directory.mkdir(parents=True, exist_ok=False)
        destination = directory / f"source{source_path.suffix.lower()}"
        if copy:
            shutil.copy2(source_path, destination)
        else:
            destination = source_path
        metadata = {
            "dataset_id": dataset_id,
            "filename": source_path.name,
            "format": source_path.suffix.lower().lstrip("."),
            "size_bytes": source_path.stat().st_size,
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

    def register_upload(self, filename: str, content: bytes) -> dict[str, Any]:
        """Store an uploaded CSV, JSON, or XML payload under a generated ID."""
        safe_name = Path(filename).name
        suffix = Path(safe_name).suffix.lower()
        if suffix not in {".csv", ".json", ".xml"}:
            raise ValueError("Unsupported file type. Please upload CSV, JSON, or XML.")
        if not content:
            raise ValueError("Uploaded dataset is empty.")

        dataset_id = f"dataset_{uuid4().hex}"
        directory = self._directory(dataset_id)
        directory.mkdir(parents=True, exist_ok=False)
        destination = directory / f"source{suffix}"
        destination.write_bytes(content)
        metadata = {
            "dataset_id": dataset_id,
            "filename": safe_name,
            "format": suffix.lstrip("."),
            "size_bytes": len(content),
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

    def _write_metadata(self, metadata: dict[str, Any]) -> None:
        directory = self._directory(metadata["dataset_id"])
        directory.mkdir(parents=True, exist_ok=True)
        self._metadata_path(metadata["dataset_id"]).write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    def update(self, dataset_id: str, **changes: Any) -> dict[str, Any]:
        metadata = self.get(dataset_id)
        if metadata is None:
            raise KeyError(dataset_id)
        metadata.update(changes)
        metadata["updated_at"] = self._now()
        self._write_metadata(metadata)
        return metadata

    def snapshot_path(self, dataset_id: str) -> Path:
        return self._directory(dataset_id) / "analysis.json"

    def save_snapshot(self, dataset_id: str, snapshot: dict[str, Any]) -> None:
        # Written to a temporary file first so a reader never sees a half-written snapshot.
        path = self.snapshot_path(dataset_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(snapshot, separators=(",", ":"), default=str), encoding="utf-8")
        temporary.replace(path)

    def load_snapshot(self, dataset_id: str) -> dict[str, Any] | None:
        try:
            return json.loads(self.snapshot_path(dataset_id).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
