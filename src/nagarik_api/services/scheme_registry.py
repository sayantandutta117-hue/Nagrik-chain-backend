"""
Government Scheme Registry
==========================
Declarative, rule-based registry of supported government schemes.

This module is intentionally data-only: it defines scheme identifiers, the
criteria each scheme uses, and the *preliminary* eligibility rules derived
from those criteria. It does not claim that any citizen is officially
eligible or guaranteed to receive a benefit -- it only surfaces candidate
schemes whose declared criteria appear to be met, and explains the gaps for
the ones that narrowly fail.

The registry is modular so additional schemes and criteria can be added
later without touching the eligibility engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SchemeCriterion:
    """A single, transparent rule used to evaluate a scheme."""

    key: str
    label: str
    operator: str  # "gte", "lte", "in", "eq", "present"
    value: Any
    description: str

    def evaluate(self, citizen: dict[str, Any]) -> tuple[bool, str]:
        actual = citizen.get(self.key)
        if actual is None:
            return False, f"{self.label} is missing (expected {self.value})"
        try:
            if self.operator == "gte":
                satisfied = float(actual) >= float(self.value)
            elif self.operator == "lte":
                satisfied = float(actual) <= float(self.value)
            elif self.operator == "in":
                satisfied = str(actual) in {str(v) for v in self.value}
            elif self.operator == "eq":
                satisfied = str(actual) == str(self.value)
            elif self.operator == "present":
                satisfied = bool(actual)
            else:
                return False, f"Unknown criterion operator '{self.operator}'"
        except (TypeError, ValueError):
            return False, f"{self.label} '{actual}' is not a valid value"
        if satisfied:
            return True, f"{self.label} ({self.value}) satisfied"
        return False, f"{self.label} ({self.value}) not satisfied"


@dataclass(frozen=True)
class Scheme:
    """A registered government scheme with its eligibility criteria."""

    id: str
    name: str
    description: str
    criteria: tuple[SchemeCriterion, ...] = ()
    # Optional, conservative default document checklist used by the
    # application tracker when a citizen does not declare one.
    required_documents: tuple[str, ...] = ()
    # A short, human-readable note that eligibility is preliminary.
    disclaimer: str = (
        "Preliminary match only. Final approval is decided by the responsible "
        "authority after document verification."
    )

    def evaluate(self, citizen: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
        matched: list[str] = []
        failed: list[str] = []
        for criterion in self.criteria:
            satisfied, message = criterion.evaluate(citizen)
            if satisfied:
                matched.append(message)
            else:
                failed.append(message)
        return len(self.criteria) > 0 and not failed, matched, failed


SCHEMES: tuple[Scheme, ...] = (
    Scheme(
        id="scholarship",
        name="Student Scholarship",
        description=(
            "Income-tested scholarship for students whose annual family income "
            "is at or below the threshold."
        ),
        criteria=(
            SchemeCriterion("student", "Student status", "eq", True, "Citizen must be a student"),
            SchemeCriterion(
                "annual_income",
                "Annual family income",
                "lte",
                250000,
                "Annual family income must be at or below 250000 INR",
            ),
        ),
        required_documents=("identity_document", "education_certificate", "income_certificate"),
    ),
    Scheme(
        id="pension",
        name="Old Age Pension",
        description=(
            "Monthly pension for citizens who have reached the pensionable age "
            "and are not already covered by another pension scheme."
        ),
        criteria=(
            SchemeCriterion("age", "Age", "gte", 60, "Citizen must be at least 60 years old"),
            SchemeCriterion(
                "death_registered",
                "Death registration",
                "eq",
                False,
                "Citizen must not have a registered death certificate",
            ),
        ),
        required_documents=("identity_document",),
    ),
    Scheme(
        id="crop_insurance",
        name="Crop Insurance",
        description=(
            "Automatic payout for farmers whose satellite-detected drought index "
            "crosses the payout threshold."
        ),
        criteria=(
            SchemeCriterion(
                "satellite_drought_index",
                "Drought index",
                "gte",
                0.7,
                "Satellite drought index must be at or above 0.7",
            ),
        ),
        required_documents=("identity_document",),
    ),
)


def get_scheme(scheme_id: str) -> Scheme | None:
    for scheme in SCHEMES:
        if scheme.id == scheme_id:
            return scheme
    return None


def list_schemes() -> tuple[Scheme, ...]:
    return SCHEMES


def is_supported_scheme(scheme_id: str) -> bool:
    return get_scheme(scheme_id) is not None