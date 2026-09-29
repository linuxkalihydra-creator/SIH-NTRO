"""Typed analysis result models for the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from backend.pipeline.investigation import Investigation


@dataclass
class AnalysisResult:
    """Structured data returned by the orchestration layer."""

    investigation: Investigation
    ingestion_statistics: dict[str, Any]
    validation_statistics: dict[str, Any]
    graph_statistics: dict[str, Any]
    processing_duration: float
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    graph_available: bool = False
    output_dir: str = "data/processed"

    @property
    def overview(self) -> dict[str, Any]:
        return self.investigation.overview

    @property
    def evaluation(self) -> dict[str, Any]:
        return self.investigation.overview.get("evaluation", {})

    @property
    def records(self) -> list[dict[str, Any]]:
        return self.investigation.records

    @property
    def wallet_features(self) -> pd.DataFrame:
        return self.investigation.wallet_features

    @property
    def risk_scores(self) -> pd.DataFrame:
        return self.investigation.risk_scores
