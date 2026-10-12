import math

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

SCHOLARSHIP_SCHEME = "scholarship"


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

        student = self.facts.get("student")
        if not isinstance(student, bool):
            raise ValueError("Scholarship application field 'student' must be a boolean")

        annual_income = self.facts.get("annual_income")
        if isinstance(annual_income, bool) or not isinstance(annual_income, (int, float)):
            raise ValueError("Scholarship application field 'annual_income' must be a number")
        if isinstance(annual_income, float) and not math.isfinite(annual_income):
            raise ValueError("Scholarship application field 'annual_income' must be a finite number")
        if annual_income < 0:
            raise ValueError("Scholarship application field 'annual_income' must be 0 or greater")

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
