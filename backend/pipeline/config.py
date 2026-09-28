"""Configuration defaults for the orchestration pipeline."""

from __future__ import annotations

DEFAULT_OUTPUT_DIR = "data/processed"
DEFAULT_CONTAMINATION = 0.05
DEFAULT_RANDOM_STATE = 42
DEFAULT_EPS = 1.25
DEFAULT_MIN_SAMPLES = 5
SUPPORTED_FORMATS = {".csv", ".json", ".xml"}
