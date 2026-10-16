"""
Eligibility Engine
==================
Deterministic, rule-based eligibility decisions for the registered schemes.

The existing ``EligibilityEngine`` is preserved untouched: it is used by
``POST /eligibility`` and by the application tracker. The new
``SchemeEligibilityService`` is a separate, preliminary matcher that surfaces
candidate schemes for a citizen without claiming an official decision.
"""
from __future__ import annotations

from typing import Any

from ..ai.explainability import explain_decision
from ..schemas import SchemeEligibilityRequest, SchemeEligibilityResponse, SchemeMatch, SchemeNonMatch
from .scheme_registry import list_schemes


class EligibilityEngine:
    """Deterministic eligibility decision for a single scheme + facts."""

    def decide(self, scheme: str, facts: dict) -> dict:
        reasons: list[str] = []
        decision = "manual_review"
        confidence = 0.55

        if scheme == "scholarship":
            if facts.get("student") and facts.get("annual_income", 99999999) <= 250000:
                decision = "auto_approve"
                confidence = 0.91
                reasons.append("Student status verified and income is within scholarship threshold")
            else:
                decision = "reject"
                confidence = 0.78
                reasons.append("Scholarship facts did not satisfy mandatory rules")
        elif scheme == "pension":
            if facts.get("age", 0) >= 60 and not facts.get("death_registered", False):
                decision = "auto_approve"
                confidence = 0.93
                reasons.append("Age threshold met and no death certificate is registered")
            else:
                reasons.append("Pension requires age >= 60 and active life status")
        elif scheme == "crop_insurance":
            if facts.get("satellite_drought_index", 0) >= 0.7:
                decision = "auto_approve"
                confidence = 0.88
                reasons.append("Satellite drought index crossed payout threshold")
            else:
                decision = "assisted_review"
                confidence = 0.64
                reasons.append("Drought signal is below automatic payout threshold")
        else:
            reasons.append("Scheme is not configured for automatic decision")

        return {
            "decision": decision,
            "confidence": confidence,
            "reasons": reasons,
            "explainability": explain_decision(decision, facts, reasons),
        }


class SchemeEligibilityService:
    """Preliminary, rule-based matching of a citizen against registered schemes."""

    def evaluate(self, request: SchemeEligibilityRequest) -> SchemeEligibilityResponse:
        citizen = self._normalise_citizen(request)
        preliminary_matches: list[SchemeMatch] = []
        non_matches: list[SchemeNonMatch] = []
        evaluated = 0

        for scheme in list_schemes():
            evaluated += 1
            matched, matched_reasons, failed_reasons = scheme.evaluate(citizen)
            base = {"scheme_id": scheme.id, "name": scheme.name}
            if matched:
                preliminary_matches.append(
                    SchemeMatch(
                        **base,
                        matched_reasons=matched_reasons,
                        disclaimer=scheme.disclaimer,
                    )
                )
            else:
                non_matches.append(
                    SchemeNonMatch(**base, not_matched_reasons=failed_reasons)
                )

        return SchemeEligibilityResponse(
            citizen_id=request.citizen_id,
            preliminary_matches=preliminary_matches,
            non_matches=non_matches,
            schemes_evaluated=evaluated,
            disclaimer=(
                "These are preliminary matches based on the declared criteria only. "
                "They do not constitute an official eligibility decision or a "
                "guarantee of benefits."
            ),
        )

    @staticmethod
    def _normalise_citizen(request: SchemeEligibilityRequest) -> dict[str, Any]:
        citizen: dict[str, Any] = {
            "citizen_id": request.citizen_id,
            "age": request.age,
            "annual_income": request.annual_income,
            "state": request.state,
            "occupation": request.occupation,
        }
        if request.extra is not None:
            citizen.update(request.extra)
        return citizen