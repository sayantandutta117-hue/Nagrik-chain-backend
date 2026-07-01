from __future__ import annotations

from dataclasses import dataclass

from .fraud_features import FraudFeatureEngineer
from .fraud_model import FraudModelRegistry


@dataclass
class FraudResult:
    risk_score: float
    reasons: list[str]


class FraudDetector:
    def __init__(
        self,
        feature_engineer: FraudFeatureEngineer | None = None,
        model_registry: FraudModelRegistry | None = None,
    ) -> None:
        self.feature_engineer = feature_engineer or FraudFeatureEngineer()
        self.model_registry = model_registry or FraudModelRegistry.from_env()

    def score(self, facts: dict) -> FraudResult:
        feature_vector = self.feature_engineer.transform_one(facts)
        model_bundle = self.model_registry.load()
        risk_score = model_bundle.predict_risk(feature_vector.values)
        explanations = model_bundle.explain(feature_vector.values, self.feature_engineer.feature_names)
        return FraudResult(risk_score=round(risk_score, 4), reasons=explanations)
