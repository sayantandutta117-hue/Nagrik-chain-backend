from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


class FraudModelLoadError(RuntimeError):
    pass


@dataclass(frozen=True)
class FraudModelConfig:
    model_dir: Path = Path("models/fraud")
    version: str = "latest"

    @classmethod
    def from_env(cls) -> "FraudModelConfig":
        return cls(
            model_dir=Path(os.getenv("FRAUD_MODEL_DIR", str(cls.model_dir))),
            version=os.getenv("FRAUD_MODEL_VERSION", cls.version),
        )


class FraudModelBundle:
    def __init__(self, model: Any, metadata: dict[str, Any], explainer: Any | None = None) -> None:
        self.model = model
        self.metadata = metadata
        self.explainer = explainer

    def predict_risk(self, features: list[float]) -> float:
        matrix = [features]
        if hasattr(self.model, "predict_proba"):
            probability = self.model.predict_proba(matrix)[0][1]
        else:
            prediction = self.model.predict(matrix)
            probability = prediction[0] if isinstance(prediction, (list, tuple)) else prediction
        return max(0.0, min(1.0, float(probability)))

    def explain(self, features: list[float], feature_names: tuple[str, ...]) -> list[str]:
        contributions = self._shap_contributions(features)
        ranked = sorted(
            zip(feature_names, contributions),
            key=lambda item: abs(float(item[1])),
            reverse=True,
        )
        explanations = [
            f"{name} SHAP contribution {float(value):+.4f}"
            for name, value in ranked[:5]
            if abs(float(value)) > 0.0001
        ]
        if explanations:
            return explanations
        return [f"Model version {self.metadata.get('version', 'unknown')} produced low-magnitude SHAP drivers"]

    def _shap_contributions(self, features: list[float]) -> list[float]:
        if self.explainer is None:
            return [0.0 for _ in features]
        values = self.explainer([features])
        raw = getattr(values, "values", values)
        row = raw[0]
        if getattr(row, "ndim", 1) > 1:
            row = row[:, -1]
        return [float(value) for value in row]


class FraudModelRegistry:
    def __init__(self, config: FraudModelConfig) -> None:
        self.config = config

    @classmethod
    def from_env(cls) -> "FraudModelRegistry":
        return cls(FraudModelConfig.from_env())

    def load(self) -> FraudModelBundle:
        return _load_model(str(self.config.model_dir), self.config.version)


@lru_cache(maxsize=8)
def _load_model(model_dir: str, version: str) -> FraudModelBundle:
    base_dir = Path(model_dir)
    version_dir = _resolve_version_dir(base_dir, version)
    metadata_path = version_dir / "metadata.json"
    model_path = version_dir / "model.joblib"
    if not metadata_path.exists() or not model_path.exists():
        raise FraudModelLoadError(
            f"Missing fraud model artifact in {version_dir}. Run train_fraud_model.py first."
        )
    joblib = _optional_import("joblib", "Install joblib and train an XGBoost or LightGBM model.")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    model = joblib.load(model_path)
    explainer = _load_shap_explainer(model)
    return FraudModelBundle(model=model, metadata=metadata, explainer=explainer)


def _resolve_version_dir(model_dir: Path, version: str) -> Path:
    if version != "latest":
        return model_dir / version
    latest_path = model_dir / "latest"
    if latest_path.exists():
        marker = latest_path.read_text(encoding="utf-8").strip()
        if marker:
            return model_dir / marker
    candidates = sorted([path for path in model_dir.iterdir() if path.is_dir()]) if model_dir.exists() else []
    if not candidates:
        return model_dir / version
    return candidates[-1]


def _load_shap_explainer(model: Any) -> Any | None:
    try:
        shap = _optional_import("shap", "Install shap to enable model explanations.")
        return shap.Explainer(model)
    except Exception:
        return None


def _optional_import(module_name: str, install_hint: str) -> Any:
    import importlib

    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise FraudModelLoadError(f"Missing optional dependency '{module_name}'. {install_hint}") from exc
