"""Unsupervised anomaly-detection ensemble with model-faithful feature attributions.

Three detectors with different inductive biases score every row:

* **Isolation Forest** — isolation depth in random trees (global, axis-aligned outliers).
* **Autoencoder** — a small bottleneck neural network (scikit-learn MLP trained to
  reconstruct its input); rows it cannot reconstruct break the usual correlations
  between features.
* **ECOD** — empirical-CDF outlier detection (Li et al., 2022): the summed negative
  log tail probability of each feature, choosing the tail by the feature's skew.
  It is parameter-free and its per-feature terms are directly interpretable.

Each detector's raw score is robust-standardised (median / IQR on the training
rows). The ensemble score is the percentile of their mean, so a row that one
detector finds extremely unusual still ranks high, and ``agreement`` is the share
of detectors that place the row in their own top ``contamination`` fraction.

Attributions are computed by occlusion on the fitted models (robust-standardised
ensemble score): the average of the drop when a feature is reset to the training
median and the rise when only that feature is set on an otherwise median row. This
is the first/last-position approximation of a Shapley value and, unlike one-sided
occlusion, still credits features that are correlated with each other.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from scipy.stats import skew
from sklearn.ensemble import IsolationForest
from sklearn.exceptions import ConvergenceWarning
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler

from backend.ml.features import log_transform

DETECTORS = ("Isolation Forest", "Autoencoder", "ECOD")
DETECTOR_DESCRIPTIONS = {
    "Isolation Forest": "Random isolation trees; rows isolated in few splits are outliers.",
    "Autoencoder": "Bottleneck neural network trained to reconstruct normal behaviour; high reconstruction error is anomalous.",
    "ECOD": "Empirical-CDF tail probabilities per feature, skew-aware; parameter-free and per-feature interpretable.",
}


class AnomalyEnsemble:
    def __init__(self, contamination: float = 0.05, random_state: int = 42) -> None:
        self.contamination = contamination
        self.random_state = random_state

    # ── fitting ─────────────────────────────────────────────────────────────
    def fit(self, frame: pd.DataFrame) -> "AnomalyEnsemble":
        self.columns = list(frame.columns)
        self.population = {column: np.sort(frame[column].to_numpy(dtype=float)) for column in self.columns}
        transformed = log_transform(frame)
        self.scaler = StandardScaler().fit(transformed.to_numpy())
        z = self._scale(transformed.to_numpy())
        self.median_z = np.median(z, axis=0)
        n_features = z.shape[1]
        self.iforest = IsolationForest(n_estimators=300, contamination="auto", random_state=self.random_state).fit(z)
        hidden = max(8, (2 * n_features) // 3)
        bottleneck = max(3, n_features // 4)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConvergenceWarning)
            # Trained on at most 6000 rows: anomalies are rare, so a sample describes normal behaviour as well.
            sample = z if len(z) <= 6000 else z[np.random.default_rng(self.random_state).choice(len(z), 6000, replace=False)]
            self.autoencoder = MLPRegressor(hidden_layer_sizes=(hidden, bottleneck, hidden), activation="tanh", alpha=1e-3,
                                            learning_rate_init=3e-3, batch_size=256, max_iter=200, early_stopping=len(sample) >= 50,
                                            n_iter_no_change=8, random_state=self.random_state).fit(sample, sample)
        self.ecod_sorted = np.sort(z, axis=0)
        skews = np.nan_to_num(skew(z, axis=0), nan=0.0)
        self.ecod_right = skews >= 0
        raw = self._raw(z)
        self.train_sorted = {name: np.sort(values) for name, values in raw.items()}
        self.robust = {name: (float(np.median(values)), float(np.subtract(*np.percentile(values, [75, 25]))) or 1.0) for name, values in raw.items()}
        self.combined_sorted = np.sort(self._combined(raw))
        return self

    def _scale(self, values: np.ndarray) -> np.ndarray:
        return np.clip(np.nan_to_num(self.scaler.transform(values)), -8, 8)

    def _transform(self, frame: pd.DataFrame) -> np.ndarray:
        return self._scale(log_transform(frame[self.columns]).to_numpy())

    # ── scoring ─────────────────────────────────────────────────────────────
    def _ecod_terms(self, z: np.ndarray) -> np.ndarray:
        n = self.ecod_sorted.shape[0]
        terms = np.empty_like(z)
        for column in range(z.shape[1]):
            ordered = self.ecod_sorted[:, column]
            left = np.searchsorted(ordered, z[:, column], side="right") / n
            right = (n - np.searchsorted(ordered, z[:, column], side="left")) / n
            tail = right if self.ecod_right[column] else left
            terms[:, column] = -np.log(np.clip(tail, 1 / (n + 1), 1.0))
        return terms

    def _raw(self, z: np.ndarray) -> dict[str, np.ndarray]:
        reconstruction = self.autoencoder.predict(z)
        return {
            "Isolation Forest": -self.iforest.score_samples(z),
            "Autoencoder": np.mean((z - reconstruction) ** 2, axis=1),
            "ECOD": self._ecod_terms(z).sum(axis=1),
        }

    def _percentile(self, name: str, values: np.ndarray) -> np.ndarray:
        ordered = self.train_sorted[name]
        return np.searchsorted(ordered, values, side="right") / len(ordered)

    def _combined(self, raw: dict[str, np.ndarray]) -> np.ndarray:
        return np.mean([(raw[name] - self.robust[name][0]) / self.robust[name][1] for name in DETECTORS], axis=0)

    def score(self, frame: pd.DataFrame) -> pd.DataFrame:
        z = self._transform(frame)
        raw = self._raw(z)
        result = pd.DataFrame(index=frame.index)
        threshold = 1 - self.contamination
        flags = []
        for name in DETECTORS:
            percentile = self._percentile(name, raw[name])
            result[f"{name} percentile"] = percentile
            flags.append(percentile >= threshold)
        combined = self._combined(raw)
        # Rank on the mean of robust-standardised scores, so how extreme a row is still counts.
        result["ensemble_percentile"] = np.searchsorted(self.combined_sorted, combined, side="right") / len(self.combined_sorted)
        result["agreement"] = np.mean(flags, axis=0)
        result["combined_raw"] = combined
        return result

    # ── explanations ────────────────────────────────────────────────────────
    def feature_percentile(self, column: str, value: float) -> float:
        ordered = self.population[column]
        return float(np.searchsorted(ordered, value, side="right") / len(ordered))

    def attributions(self, frame: pd.DataFrame, top: int = 6) -> list[list[dict[str, float | str]]]:
        """Occlusion attributions for each row: features whose reset lowers the ensemble score most."""
        if frame.empty:
            return []
        z = self._transform(frame)
        base = self._combined(self._raw(z))
        rows, features = z.shape
        columns = np.tile(np.arange(features), rows)
        positions = np.arange(rows * features)
        # Removal: reset one feature to the median. Correlated features mask each other here.
        removed = np.repeat(z, features, axis=0)
        removed[positions, columns] = self.median_z[columns]
        removal = (base.repeat(features) - self._combined(self._raw(removed))).reshape(rows, features)
        # Insertion: start from the median row and restore only that feature.
        reference = float(self._combined(self._raw(self.median_z[None, :]))[0])
        inserted = np.tile(self.median_z, (rows * features, 1))
        inserted[positions, columns] = z.reshape(-1)
        insertion = (self._combined(self._raw(inserted)) - reference).reshape(rows, features)
        # Averaging both is the first/last-position approximation of a Shapley value.
        drops = 0.5 * (removal + insertion)
        output = []
        values = frame[self.columns].to_numpy(dtype=float)
        for row in range(rows):
            positive = np.clip(drops[row], 0, None)
            total = positive.sum()
            order = np.argsort(-positive)[:top]
            output.append([
                {
                    "feature": self.columns[index],
                    "value": float(values[row, index]),
                    "percentile": round(self.feature_percentile(self.columns[index], values[row, index]), 4),
                    "contribution": round(float(positive[index] / total), 4) if total > 0 else 0.0,
                }
                for index in order if positive[index] > 0
            ])
        return output


__all__ = ["AnomalyEnsemble", "DETECTORS", "DETECTOR_DESCRIPTIONS"]
