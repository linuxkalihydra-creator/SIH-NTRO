"""Configuration defaults for the orchestration pipeline."""

from __future__ import annotations

from backend.ingestion.readers import SUPPORTED_SUFFIXES

DEFAULT_OUTPUT_DIR = "data/processed"
DEFAULT_CONTAMINATION = 0.05
DEFAULT_RANDOM_STATE = 42
# .csv .tsv .json .jsonl .ndjson .xml, each optionally followed by .gz
SUPPORTED_FORMATS = set(SUPPORTED_SUFFIXES)
