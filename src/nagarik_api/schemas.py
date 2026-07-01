from pydantic import BaseModel, Field


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
    citizen_id: str
    scheme: str
    facts: dict


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
