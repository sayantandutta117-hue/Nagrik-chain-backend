"""
Nagarik Chain — FastAPI Application
=====================================
Implements all REST endpoints for:
  - Citizen identity (CHIN + DID issuance)
  - Professional credential (P-DID) issuance
  - Document notarization (IPFS + AI pipeline)
  - Eligibility engine (scholarship, pension, crop insurance)
  - Fraud scoring (XGBoost/LightGBM + SHAP)
  - Bribe / fraud reporting
  - Officer integrity score
  - Whistleblower vault
  - Decoy application system
  - Smart contract workflow routing
  - Biometric enrollment + verification
  - ZK-SNARK proof generation + verification
  - Audit event retrieval
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.requests import Request

from .ai.biometrics import BiometricVerifier
from .ai.fraud import FraudDetector
from .ai.zk_proofs import ZKProver, ZKVerifier
from .config import settings
from .db import get_db, init_db
from .models import (
    ApplicationStatusHistory,
    AuditEvent,
    Citizen,
    DecoyApplication,
    GovernmentApplication,
    OfficerProfile,
    VaultEntry,
)
from .schemas import (
    ApplicationCreate,
    ApplicationOut,
    ApplicationReviewRequest,
    ApplicationTimelineEntry,
    ApplicationWithdrawRequest,
    CitizenCreate,
    CitizenOut,
    DocumentOut,
    EligibilityDecision,
    EligibilityRequest,
    FraudReportIn,
    FraudScoreOut,
    ProfessionalCreate,
    ProfessionalOut,
)
from .services.application_service import (
    ApplicationAccessDenied,
    ApplicationError,
    ApplicationNotFound,
    ApplicationTracker,
    ApplicationViewer,
    InvalidApplicationInput,
    InvalidStatusTransition,
    to_application_out,
)
from .services.audit_service import AuditService
from .services.decoy_system import DecoyApplicationSystem
from .services.document_service import DocumentService
from .services.eligibility_service import EligibilityEngine
from .services.identity_service import create_citizen, create_professional_credential
from .services.integrity_service import IntegrityScoreEngine
from .services.smart_contract_router import SmartContractEventRouter, WorkflowTrigger
from .services.whistleblower_vault import WhistleblowerVault

app = FastAPI(
    title="Nagarik Chain Backend",
    version="0.1.0",
    description=(
        "India's Blockchain-Based National Digital Identity & Governance System. "
        "Every endpoint is blockchain-audited, AI-verified, and permanently immutable."
    ),
)


# ── Application lifecycle error mapping ───────────────────────────────────────

@app.exception_handler(ApplicationNotFound)
async def application_not_found_handler(request: Request, exc: ApplicationError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ApplicationAccessDenied)
async def application_access_denied_handler(request: Request, exc: ApplicationError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(InvalidStatusTransition)
async def invalid_status_transition_handler(request: Request, exc: ApplicationError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(InvalidApplicationInput)
async def invalid_application_input_handler(request: Request, exc: ApplicationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


# ── Auth ─────────────────────────────────────────────────────────────────────

def require_api_key(x_api_key: str = Header(default="")) -> None:
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _find_citizen(db: Session, identifier: str) -> Citizen | None:
    identifier = identifier.strip()
    if not identifier:
        return None
    return (
        db.query(Citizen)
        .filter((Citizen.id == identifier) | (Citizen.chin == identifier))
        .first()
    )


def require_citizen(
    x_citizen_id: str = Header(default=""), db: Session = Depends(get_db)
) -> Citizen:
    if not x_citizen_id.strip():
        raise HTTPException(status_code=401, detail="Missing x-citizen-id header")
    citizen = _find_citizen(db, x_citizen_id)
    if not citizen:
        raise HTTPException(status_code=403, detail="Unknown citizen identity")
    return citizen


def require_officer(
    x_officer_id: str = Header(default=""), db: Session = Depends(get_db)
) -> OfficerProfile:
    officer_id = x_officer_id.strip()
    if not officer_id:
        raise HTTPException(status_code=401, detail="Missing x-officer-id header")
    officer = db.query(OfficerProfile).filter_by(officer_id=officer_id).first()
    if not officer:
        raise HTTPException(status_code=403, detail="Officer is not registered")
    return officer


def require_application_viewer(
    x_citizen_id: str = Header(default=""),
    x_officer_id: str = Header(default=""),
    db: Session = Depends(get_db),
) -> ApplicationViewer:
    officer_id = x_officer_id.strip()
    if officer_id:
        officer = db.query(OfficerProfile).filter_by(officer_id=officer_id).first()
        if not officer:
            raise HTTPException(status_code=403, detail="Officer is not registered")
        return ApplicationViewer(kind="officer", actor_id=officer.officer_id)
    if not x_citizen_id.strip():
        raise HTTPException(status_code=401, detail="Missing x-citizen-id or x-officer-id header")
    citizen = _find_citizen(db, x_citizen_id)
    if not citizen:
        raise HTTPException(status_code=403, detail="Unknown citizen identity")
    return ApplicationViewer(kind="citizen", actor_id=citizen.id, citizen_id=citizen.id)


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
def startup() -> None:
    init_db()


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "nagarik-chain", "version": "1.0.1"}


# ── Citizen Identity ──────────────────────────────────────────────────────────

@app.post("/citizens", response_model=CitizenOut, dependencies=[Depends(require_api_key)])
async def citizens(payload: CitizenCreate, db: Session = Depends(get_db)) -> CitizenOut:
    citizen = create_citizen(db, payload)
    await AuditService().record(db, "registry", "CITIZEN_CREATED", citizen.id, {"chin": citizen.chin})
    return citizen


# ── Professional Credentials (P-DID) ─────────────────────────────────────────

@app.post("/professionals", response_model=ProfessionalOut, dependencies=[Depends(require_api_key)])
async def professionals(payload: ProfessionalCreate, db: Session = Depends(get_db)) -> ProfessionalOut:
    credential = create_professional_credential(db, payload)
    await AuditService().record(
        db,
        "licensing-authority",
        "PDID_ISSUED",
        credential.id,
        {"license_number": credential.license_number, "profession": credential.profession},
    )
    return credential


# ── Document Notarization ─────────────────────────────────────────────────────

@app.post("/documents", response_model=DocumentOut, dependencies=[Depends(require_api_key)])
async def documents(
    citizen_id: str = Form(...),
    text: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> DocumentOut:
    data = await file.read()
    document = await DocumentService().submit(db, citizen_id, file.filename or "document.bin", data, text)
    await AuditService().record(
        db,
        "document-ai",
        "DOCUMENT_NOTARIZED",
        document.id,
        {"sha256": document.sha256, "ipfs_cid": document.ipfs_cid, "status": document.status},
    )
    return document


# ── Eligibility Engine ────────────────────────────────────────────────────────

@app.post("/eligibility", response_model=EligibilityDecision, dependencies=[Depends(require_api_key)])
async def eligibility(payload: EligibilityRequest, db: Session = Depends(get_db)) -> EligibilityDecision:
    result = EligibilityEngine().decide(payload.scheme, payload.facts)
    event = await AuditService().record(
        db,
        "eligibility-engine",
        "ELIGIBILITY_DECISION",
        payload.citizen_id,
        {"scheme": payload.scheme, **result},
    )
    return EligibilityDecision(
        decision=result["decision"],
        confidence=result["confidence"],
        reasons=result["reasons"],
        audit_event_id=event.id,
    )


# ── Government Application Tracker ────────────────────────────────────────────

@app.post("/applications", status_code=201, response_model=ApplicationOut, dependencies=[Depends(require_api_key)])
async def create_application(
    payload: ApplicationCreate, citizen: Citizen = Depends(require_citizen), db: Session = Depends(get_db)
) -> ApplicationOut:
    application, audit = await ApplicationTracker().create_application(db, citizen, payload)
    return to_application_out(db, application, audit)


@app.get("/applications/me", response_model=list[ApplicationOut], dependencies=[Depends(require_api_key)])
async def list_my_applications(
    citizen: Citizen = Depends(require_citizen), db: Session = Depends(get_db)
) -> list[ApplicationOut]:
    tracker = ApplicationTracker()
    applications = tracker.list_citizen_applications(db, citizen.id)
    return [to_application_out(db, application) for application in applications]


@app.get("/applications/{application_id}", response_model=ApplicationOut, dependencies=[Depends(require_api_key)])
async def get_application(
    application_id: str,
    viewer: ApplicationViewer = Depends(require_application_viewer),
    db: Session = Depends(get_db),
) -> ApplicationOut:
    application = ApplicationTracker().get_application(db, application_id, viewer)
    return to_application_out(db, application)


@app.post("/applications/{application_id}/documents", response_model=ApplicationOut, dependencies=[Depends(require_api_key)])
async def attach_application_document(
    application_id: str,
    document_id: str | None = Form(default=None),
    document_type: str = Form(default=""),
    text: str = Form(default=""),
    file: UploadFile | None = File(default=None),
    citizen: Citizen = Depends(require_citizen),
    db: Session = Depends(get_db),
) -> ApplicationOut:
    tracker = ApplicationTracker()
    if file is not None and file.filename:
        application, audit = await tracker.submit_document(
            db,
            application_id,
            citizen,
            file.filename,
            await file.read(),
            text,
            document_type,
        )
    elif document_id:
        application, audit = await tracker.attach_document_by_id(
            db, application_id, document_id, document_type, citizen
        )
    else:
        raise HTTPException(
            status_code=422, detail="Provide either an uploaded 'file' or an existing 'document_id'"
        )
    return to_application_out(db, application, audit)


@app.post("/applications/{application_id}/review", response_model=ApplicationOut, dependencies=[Depends(require_api_key)])
async def review_application(
    application_id: str,
    payload: ApplicationReviewRequest,
    officer: OfficerProfile = Depends(require_officer),
    db: Session = Depends(get_db),
) -> ApplicationOut:
    application, audit = await ApplicationTracker().record_review(
        db, application_id, officer, payload.decision, payload.reason
    )
    return to_application_out(db, application, audit)


@app.post("/applications/{application_id}/withdraw", response_model=ApplicationOut, dependencies=[Depends(require_api_key)])
async def withdraw_application(
    application_id: str,
    payload: ApplicationWithdrawRequest | None = None,
    citizen: Citizen = Depends(require_citizen),
    db: Session = Depends(get_db),
) -> ApplicationOut:
    reason = payload.reason if payload else ""
    application, audit = await ApplicationTracker().withdraw_application(
        db, application_id, citizen, reason
    )
    return to_application_out(db, application, audit)


@app.get("/applications/{application_id}/timeline", response_model=list[ApplicationTimelineEntry], dependencies=[Depends(require_api_key)])
async def application_timeline(
    application_id: str,
    viewer: ApplicationViewer = Depends(require_application_viewer),
    db: Session = Depends(get_db),
) -> list[ApplicationStatusHistory]:
    tracker = ApplicationTracker()
    tracker.get_application(db, application_id, viewer)
    return tracker.timeline(db, application_id)


# ── Fraud ─────────────────────────────────────────────────────────────────────

@app.post("/fraud/report", dependencies=[Depends(require_api_key)])
async def fraud_report(payload: FraudReportIn, db: Session = Depends(get_db)) -> dict:
    event = await AuditService().record(
        db, "anonymous", "BRIBE_REPORT", payload.subject_id, payload.model_dump()
    )
    return {"status": "sealed", "audit_event_id": event.id}


@app.post("/fraud/score", response_model=FraudScoreOut, dependencies=[Depends(require_api_key)])
def fraud_score(subject_id: str, facts: dict) -> FraudScoreOut:
    result = FraudDetector().score(facts)
    return FraudScoreOut(subject_id=subject_id, risk_score=result.risk_score, reasons=result.reasons)


# ── Biometrics ────────────────────────────────────────────────────────────────

class BiometricEnrollRequest(BaseModel):
    citizen_id: str
    image_base64: str


class BiometricVerifyRequest(BaseModel):
    citizen_id: str
    image_base64: str
    enrolled_embedding: str


class BiometricResponse(BaseModel):
    passed: bool
    score: float
    reasons: list[str]
    enrollment_token: str | None = None


@app.post("/biometrics/enroll", response_model=BiometricResponse, dependencies=[Depends(require_api_key)])
async def biometric_enroll(payload: BiometricEnrollRequest, db: Session = Depends(get_db)) -> BiometricResponse:
    verifier = BiometricVerifier()
    try:
        encrypted_embedding = verifier.enroll_face(
            payload.image_base64.encode(), payload.citizen_id
        )
        await AuditService().record(
            db, "biometric-service", "FACE_ENROLLED", payload.citizen_id, {"status": "enrolled"}
        )
        return BiometricResponse(
            passed=True,
            score=1.0,
            reasons=["Face enrolled with liveness check", "Embedding stored with Fernet encryption"],
            enrollment_token=encrypted_embedding,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/biometrics/verify", response_model=BiometricResponse, dependencies=[Depends(require_api_key)])
async def biometric_verify(payload: BiometricVerifyRequest, db: Session = Depends(get_db)) -> BiometricResponse:
    verifier = BiometricVerifier()
    result = verifier.verify_face(payload.image_base64.encode(), payload.enrolled_embedding)
    await AuditService().record(
        db,
        "biometric-service",
        "FACE_VERIFIED",
        payload.citizen_id,
        {"passed": result.passed, "score": result.score},
    )
    return BiometricResponse(passed=result.passed, score=result.score, reasons=result.reasons)


# ── ZK-SNARK Proofs ───────────────────────────────────────────────────────────

class ZKProveRequest(BaseModel):
    predicate: str = Field(description="age_gte | income_lte | has_valid_license")
    value: int = Field(description="The secret value to prove over")
    threshold: int = Field(default=0, description="Threshold for age/income predicates")
    license_hash: str = Field(default="", description="License hash for license predicate")


class ZKProveResponse(BaseModel):
    predicate: str
    public_input: str
    commitment_hex: str
    proof_type: str
    # blinding_factor is returned ONCE and must be stored by the citizen's wallet
    blinding_factor: str


class ZKVerifyRequest(BaseModel):
    predicate: str
    commitment_hex: str
    claimed_public_input: str


@app.post("/zk/prove", response_model=ZKProveResponse, dependencies=[Depends(require_api_key)])
def zk_prove(payload: ZKProveRequest) -> ZKProveResponse:
    prover = ZKProver()
    if payload.predicate == "age_gte":
        proof = prover.prove_age_gte(payload.value, payload.threshold)
    elif payload.predicate == "income_lte":
        proof = prover.prove_income_lte(payload.value, payload.threshold)
    elif payload.predicate == "has_valid_license":
        proof = prover.prove_has_valid_license(payload.license_hash)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown predicate '{payload.predicate}'")
    return ZKProveResponse(
        predicate=proof.predicate,
        public_input=proof.public_input,
        commitment_hex=proof.commitment_hex,
        proof_type=proof.proof_type,
        blinding_factor=proof.blinding_factor,
    )


@app.post("/zk/verify", dependencies=[Depends(require_api_key)])
def zk_verify(payload: ZKVerifyRequest) -> dict:
    result = ZKVerifier().verify_on_chain_commitment(
        payload.commitment_hex,
        payload.claimed_public_input,
        payload.predicate,
    )
    return {"valid": result.valid, "predicate": result.predicate, "reason": result.reason}


# ── Smart Contract Workflow Routing ───────────────────────────────────────────

class WorkflowTriggerRequest(BaseModel):
    workflow: str
    citizen_id: str
    trigger_data: dict[str, Any]
    verified_by: str = "nagarik-chain-node"


@app.post("/workflows/trigger", dependencies=[Depends(require_api_key)])
async def trigger_workflow(payload: WorkflowTriggerRequest, db: Session = Depends(get_db)) -> dict:
    trigger = WorkflowTrigger(
        workflow=payload.workflow,
        citizen_id=payload.citizen_id,
        trigger_data=payload.trigger_data,
        verified_by=payload.verified_by,
    )
    result = await SmartContractEventRouter().route(trigger)
    await AuditService().record(
        db,
        "smart-contract-router",
        f"WORKFLOW_{payload.workflow.upper()}_{result.status.upper()}",
        payload.citizen_id,
        {"workflow": result.workflow, "status": result.status, "tx": result.transaction_hash},
    )
    return {
        "workflow": result.workflow,
        "status": result.status,
        "case_id": result.case_id,
        "transaction_hash": result.transaction_hash,
        "reason": result.reason,
    }


# ── Officer Integrity Score ───────────────────────────────────────────────────

class IntegritySignalRequest(BaseModel):
    officer_id: str
    signals: dict[str, Any]


@app.post("/officers/integrity/update", dependencies=[Depends(require_api_key)])
def update_integrity(payload: IntegritySignalRequest, db: Session = Depends(get_db)) -> dict:
    report = IntegrityScoreEngine().update_and_score(db, payload.officer_id, payload.signals)
    return {
        "officer_id": report.officer_id,
        "integrity_score": report.integrity_score,
        "flags": report.flags,
        "recommended_action": report.recommended_action,
    }


@app.get("/officers/{officer_id}/integrity", dependencies=[Depends(require_api_key)])
def get_integrity(officer_id: str, db: Session = Depends(get_db)) -> dict:
    profile = db.query(OfficerProfile).filter_by(officer_id=officer_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Officer not found")
    report = IntegrityScoreEngine().compute(profile)
    return {
        "officer_id": report.officer_id,
        "integrity_score": report.integrity_score,
        "flags": report.flags,
        "recommended_action": report.recommended_action,
        "raw_signals": report.raw_signals,
    }


# ── Whistleblower Vault ───────────────────────────────────────────────────────

class WhistleblowerReportRequest(BaseModel):
    subject_officer_id: str
    report: dict[str, Any]
    dead_man_days: int = 30


@app.post("/vault/seal", dependencies=[Depends(require_api_key)])
def seal_report(payload: WhistleblowerReportRequest, db: Session = Depends(get_db)) -> dict:
    receipt = WhistleblowerVault().seal(
        db, payload.report, payload.subject_officer_id, payload.dead_man_days
    )
    return {
        "vault_id": receipt.vault_id,
        "content_hash": receipt.content_hash,
        "escalation_deadline": receipt.escalation_deadline,
        "message": receipt.message,
    }


@app.get("/vault/status/{content_hash}", dependencies=[Depends(require_api_key)])
def vault_status(content_hash: str, db: Session = Depends(get_db)) -> dict:
    return WhistleblowerVault().status(db, content_hash)


# ── Decoy Applications ────────────────────────────────────────────────────────

class DecoySeedRequest(BaseModel):
    officer_id: str
    scheme: str
    facts: dict[str, Any] = {}


@app.post("/decoy/seed", dependencies=[Depends(require_api_key)])
def seed_decoy(payload: DecoySeedRequest, db: Session = Depends(get_db)) -> dict:
    decoy = DecoyApplicationSystem().seed_decoy(db, payload.officer_id, payload.scheme, payload.facts)
    return {
        "disguise_application_id": decoy.disguise_application_id,
        "assigned_officer_id": decoy.assigned_officer_id,
        "scheme": decoy.scheme,
    }


@app.post("/decoy/outcome", dependencies=[Depends(require_api_key)])
def record_decoy_outcome(
    application_id: str,
    officer_decision: str,
    officer_reason: str = "",
    db: Session = Depends(get_db),
) -> dict:
    result = DecoyApplicationSystem().record_outcome(db, application_id, officer_decision, officer_reason)
    if not result:
        return {"is_decoy": False}
    return {
        "is_decoy": True,
        "passed": result.passed,
        "failure_reason": result.failure_reason,
        "action_taken": result.action_taken,
    }


# ── Audit Log ─────────────────────────────────────────────────────────────────

@app.get("/audit/{subject_id}", dependencies=[Depends(require_api_key)])
def audit_log(subject_id: str, db: Session = Depends(get_db)) -> list[dict]:
    events = db.query(AuditEvent).filter_by(subject_id=subject_id).order_by(AuditEvent.created_at.desc()).limit(50).all()
    return [
        {
            "id": e.id,
            "actor": e.actor,
            "action": e.action,
            "payload_hash": e.payload_hash,
            "fabric_tx_id": e.fabric_tx_id,
            "ethereum_tx_hash": e.ethereum_tx_hash,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]
