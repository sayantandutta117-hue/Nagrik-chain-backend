import math
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

SCHOLARSHIP_SCHEME = "scholarship"
SUPPORTED_SCHEMES: tuple[str, ...] = ("scholarship", "pension", "crop_insurance")
OFFICER_REVIEW_DECISIONS: frozenset[str] = frozenset(
    {"APPROVED", "REJECTED", "DOCUMENTS_REQUIRED", "UNDER_REVIEW"}
)


def validate_scholarship_facts(facts: dict) -> None:
    student = facts.get("student")
    if not isinstance(student, bool):
        raise ValueError("Scholarship application field 'student' must be a boolean")

    annual_income = facts.get("annual_income")
    if isinstance(annual_income, bool) or not isinstance(annual_income, (int, float)):
        raise ValueError("Scholarship application field 'annual_income' must be a number")
    if isinstance(annual_income, float) and not math.isfinite(annual_income):
        raise ValueError("Scholarship application field 'annual_income' must be a finite number")
    if annual_income < 0:
        raise ValueError("Scholarship application field 'annual_income' must be 0 or greater")


class CitizenCreate(BaseModel):
    full_name: str
    date_of_birth: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class CitizenOut(BaseModel):
    id: str
    chin: str
    full_name: str
    date_of_birth: str
    did: str

    model_config = {"from_attributes": True}


class ProfessionalCreate(BaseModel):
    citizen_id: str
    profession: str
    license_number: str
    issuer: str


class ProfessionalOut(BaseModel):
    id: str
    citizen_id: str
    profession: str
    license_number: str
    issuer: str
    did: str
    status: str

    model_config = {"from_attributes": True}


class DocumentOut(BaseModel):
    id: str
    citizen_id: str
    document_type: str
    sha256: str
    ipfs_cid: str
    status: str
    ai_score: float
    explanation: str

    model_config = {"from_attributes": True}


class EligibilityRequest(BaseModel):
    citizen_id: str = Field(min_length=1)
    scheme: str = Field(min_length=1)
    facts: dict

    @field_validator("citizen_id", "scheme")
    @classmethod
    def _reject_blank_identifier(cls, value: str, info: ValidationInfo) -> str:
        if not value.strip():
            raise ValueError(f"{info.field_name} must not be blank")
        return value

    @model_validator(mode="after")
    def _validate_scholarship_facts(self) -> "EligibilityRequest":
        if self.scheme != SCHOLARSHIP_SCHEME:
            return self
        validate_scholarship_facts(self.facts)
        return self


class EligibilityDecision(BaseModel):
    decision: str
    confidence: float
    reasons: list[str]
    audit_event_id: str | None = None


class FraudReportIn(BaseModel):
    subject_id: str
    report_type: str
    encrypted_message: str


class FraudScoreOut(BaseModel):
    subject_id: str
    risk_score: float
    reasons: list[str]


class ApplicationCreate(BaseModel):
    scheme: str = Field(min_length=1)
    facts: dict = Field(default_factory=dict)
    required_documents: list[str] | None = Field(default=None)

    @field_validator("scheme")
    @classmethod
    def _supported_scheme(cls, value: str) -> str:
        scheme = value.strip()
        if scheme not in SUPPORTED_SCHEMES:
            supported = ", ".join(SUPPORTED_SCHEMES)
            raise ValueError(f"Unsupported scheme '{scheme}'. Supported schemes: {supported}")
        return scheme

    @field_validator("required_documents")
    @classmethod
    def _clean_required_documents(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            document_type = str(item).strip()
            if not document_type:
                raise ValueError("Required document types must not be blank")
            if document_type not in cleaned:
                cleaned.append(document_type)
        return cleaned

    @model_validator(mode="after")
    def _validate_scheme_facts(self) -> "ApplicationCreate":
        if self.scheme == SCHOLARSHIP_SCHEME:
            validate_scholarship_facts(self.facts)
        return self


class ApplicationReviewRequest(BaseModel):
    decision: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("decision")
    @classmethod
    def _officer_decision(cls, value: str) -> str:
        decision = value.strip().upper()
        if decision not in OFFICER_REVIEW_DECISIONS:
            allowed = ", ".join(sorted(OFFICER_REVIEW_DECISIONS))
            raise ValueError(f"Unsupported review decision '{decision}'. Allowed decisions: {allowed}")
        return decision

    @field_validator("reason")
    @classmethod
    def _trim_reason(cls, value: str) -> str:
        return value.strip()


class ApplicationWithdrawRequest(BaseModel):
    reason: str = Field(default="", max_length=2000)

    @field_validator("reason")
    @classmethod
    def _trim_reason(cls, value: str) -> str:
        return value.strip()


class ApplicationDocumentRef(BaseModel):
    document_id: str
    document_type: str
    sha256: str | None = None
    status: str | None = None
    attached_by: str
    attached_at: datetime


class EligibilitySummary(BaseModel):
    decision: str
    confidence: float | None = None
    reasons: list[str] = []


class FraudSummary(BaseModel):
    risk_score: float | None = None
    flagged: bool = False
    reasons: list[str] = []


class AuditInfo(BaseModel):
    event_id: str | None = None
    anchored_on_chain: bool = False


class ApplicationOut(BaseModel):
    id: str
    citizen_id: str
    scheme: str
    status: str
    facts: dict[str, Any]
    required_documents: list[str]
    attached_documents: list[ApplicationDocumentRef] = []
    missing_documents: list[str] = []
    eligibility: EligibilitySummary
    fraud: FraudSummary
    next_action: str
    next_action_detail: str
    decision_reason: str = ""
    decided_by: str | None = None
    decided_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    audit: AuditInfo | None = None


class ApplicationTimelineEntry(BaseModel):
    from_status: str | None = None
    to_status: str
    actor_type: str
    actor_id: str | None = None
    reason: str = ""
    created_at: datetime


# ── Government Scheme Eligibility ──────────────────────────────────────────────

class SchemeMatch(BaseModel):
    scheme_id: str
    name: str
    matched_reasons: list[str] = []
    disclaimer: str = ""


class SchemeNonMatch(BaseModel):
    scheme_id: str
    name: str
    not_matched_reasons: list[str] = []


class SchemeEligibilityRequest(BaseModel):
    citizen_id: str = Field(min_length=1)
    age: int | None = Field(default=None, ge=0)
    state: str | None = Field(default=None, min_length=1)
    occupation: str | None = Field(default=None, min_length=1)
    annual_income: int | None = Field(default=None, ge=0)
    extra: dict[str, Any] | None = None

    @field_validator("citizen_id", "state", "occupation")
    @classmethod
    def _reject_blank_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.strip():
            raise ValueError("field must not be blank")
        return value


class SchemeEligibilityResponse(BaseModel):
    citizen_id: str
    preliminary_matches: list[SchemeMatch]
    non_matches: list[SchemeNonMatch]
    schemes_evaluated: int
    disclaimer: str


# ── Saved Schemes / Bookmarks ──────────────────────────────────────────────────

class SavedSchemeCreate(BaseModel):
    scheme_id: str = Field(min_length=1)

    @field_validator("scheme_id")
    @classmethod
    def _reject_blank_scheme_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("scheme_id must not be blank")
        return value


class SavedSchemeOut(BaseModel):
    id: str
    citizen_id: str
    scheme_id: str
    created_at: datetime

    model_config = {"from_attributes": True}
