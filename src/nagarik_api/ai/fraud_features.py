from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FraudFeatureVector:
    values: list[float]
    raw: dict[str, Any]


class FraudFeatureEngineer:
    feature_names: tuple[str, ...] = (
        "duplicate_hash_count_log",
        "officer_rejection_rate_30d",
        "officer_rejection_rate_delta",
        "average_processing_delay_hours_log",
        "same_device_application_count_log",
        "same_bank_account_beneficiary_count_log",
        "cross_node_mismatch_count_log",
        "document_graph_degree_log",
        "bribe_report_count_log",
        "decoy_failure_rate",
        "geo_velocity_km_per_hour_log",
        "identity_reuse_count_log",
        "ip_reputation_score",
        "prior_manual_overturn_rate",
        "professional_credential_age_days_log",
        "benefit_amount_percentile",
    )

    dataset_columns: tuple[str, ...] = feature_names + ("label",)

    def transform_one(self, facts: dict[str, Any]) -> FraudFeatureVector:
        raw = self._normalize(facts)
        return FraudFeatureVector(
            values=[
                self._log1p(raw["duplicate_hash_count"]),
                raw["officer_rejection_rate_30d"],
                raw["officer_rejection_rate_30d"] - raw["officer_rejection_rate_baseline"],
                self._log1p(raw["average_processing_delay_hours"]),
                self._log1p(raw["same_device_application_count"]),
                self._log1p(raw["same_bank_account_beneficiary_count"]),
                self._log1p(raw["cross_node_mismatch_count"]),
                self._log1p(raw["document_graph_degree"]),
                self._log1p(raw["bribe_report_count"]),
                raw["decoy_failure_rate"],
                self._log1p(raw["geo_velocity_km_per_hour"]),
                self._log1p(raw["identity_reuse_count"]),
                raw["ip_reputation_score"],
                raw["prior_manual_overturn_rate"],
                self._log1p(raw["professional_credential_age_days"]),
                raw["benefit_amount_percentile"],
            ],
            raw=raw,
        )

    def transform_many(self, rows: list[dict[str, Any]]) -> list[list[float]]:
        return [self.transform_one(row).values for row in rows]

    def _normalize(self, facts: dict[str, Any]) -> dict[str, float]:
        return {
            "duplicate_hash_count": self._number(facts, "duplicate_hash_count"),
            "officer_rejection_rate_30d": self._bounded(facts, "officer_rejection_rate_30d"),
            "officer_rejection_rate_baseline": self._bounded(facts, "officer_rejection_rate_baseline"),
            "average_processing_delay_hours": self._number(facts, "average_processing_delay_hours"),
            "same_device_application_count": self._number(facts, "same_device_application_count"),
            "same_bank_account_beneficiary_count": self._number(
                facts,
                "same_bank_account_beneficiary_count",
            ),
            "cross_node_mismatch_count": self._number(facts, "cross_node_mismatch_count"),
            "document_graph_degree": self._number(facts, "document_graph_degree"),
            "bribe_report_count": self._number(facts, "bribe_report_count"),
            "decoy_failure_rate": self._bounded(facts, "decoy_failure_rate"),
            "geo_velocity_km_per_hour": self._number(facts, "geo_velocity_km_per_hour"),
            "identity_reuse_count": self._number(facts, "identity_reuse_count"),
            "ip_reputation_score": self._bounded(facts, "ip_reputation_score"),
            "prior_manual_overturn_rate": self._bounded(facts, "prior_manual_overturn_rate"),
            "professional_credential_age_days": self._number(
                facts,
                "professional_credential_age_days",
            ),
            "benefit_amount_percentile": self._bounded(facts, "benefit_amount_percentile"),
        }

    def _number(self, facts: dict[str, Any], key: str) -> float:
        value = facts.get(key, 0.0)
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.0

    def _bounded(self, facts: dict[str, Any], key: str) -> float:
        return max(0.0, min(1.0, self._number(facts, key)))

    def _log1p(self, value: float) -> float:
        return math.log1p(max(0.0, value))
