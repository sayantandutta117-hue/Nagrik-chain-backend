"""
Government Application Tracker
===============================
Connects the existing citizen identity, document verification, eligibility,
fraud screening and blockchain audit infrastructure into one deterministic
application lifecycle:

    SUBMITTED → DOCUMENTS_REQUIRED → UNDER_REVIEW → APPROVED
                              ↘            ↘
                            WITHDRAWN    REJECTED

Design rules:
- Every eligibility, fraud and document decision reuses the existing
  ``EligibilityEngine``, ``FraudDetector`` and ``DocumentService``; no
  eligibility rule is duplicated or invented here.
- Guidance (``next_action``) is generated from the stored application state
  using deterministic rules only. No LLM and no invented policy.
- A fraud risk flag routes the application to an authorised human officer; it
  never declares fraud by itself.
- Audit payloads contain identifiers, hashes and status data only. Document
  contents and personal facts are never written to the audit log.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..ai.fraud import FraudDetector
from ..config import settings
from ..models import (
    ApplicationDocument,
    ApplicationStatusHistory,
    Citizen,
    Document,
    GovernmentApplication,
    OfficerProfile,
)
from ..schemas import (
    ApplicationCreate,
    ApplicationDocumentRef,
    ApplicationOut,
    AuditInfo,
    EligibilitySummary,
    FraudSummary,
    OFFICER_REVIEW_DECISIONS,
)
from .audit_service import AuditWriteResult, record_audit_event
from .document_service import DocumentService
from .eligibility_service import EligibilityEngine

logger = logging.getLogger(__name__)

STATUS_SUBMITTED = "SUBMITTED"
STATUS_DOCUMENTS_REQUIRED = "DOCUMENTS_REQUIRED"
STATUS_UNDER_REVIEW = "UNDER_REVIEW"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_WITHDRAWN = "WITHDRAWN"

APPLICATION_STATUSES: tuple[str, ...] = (
    STATUS_SUBMITTED,
    STATUS_DOCUMENTS_REQUIRED,
    STATUS_UNDER_REVIEW,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_WITHDRAWN,
)

TERMINAL_STATUSES: frozenset[str] = frozenset({STATUS_APPROVED, STATUS_REJECTED, STATUS_WITHDRAWN})

ALLOWED_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_SUBMITTED: frozenset(
        {
            STATUS_DOCUMENTS_REQUIRED,
            STATUS_UNDER_REVIEW,
            STATUS_APPROVED,
            STATUS_REJECTED,
            STATUS_WITHDRAWN,
        }
    ),
    STATUS_DOCUMENTS_REQUIRED: frozenset(
        {
            STATUS_DOCUMENTS_REQUIRED,
            STATUS_UNDER_REVIEW,
            STATUS_APPROVED,
            STATUS_REJECTED,
            STATUS_WITHDRAWN,
        }
    ),
    STATUS_UNDER_REVIEW: frozenset(
        {
            STATUS_DOCUMENTS_REQUIRED,
            STATUS_UNDER_REVIEW,
            STATUS_APPROVED,
            STATUS_REJECTED,
            STATUS_WITHDRAWN,
        }
    ),
    STATUS_APPROVED: frozenset(),
    STATUS_REJECTED: frozenset(),
    STATUS_WITHDRAWN: frozenset(),
}

# Statuses each actor type is allowed to set. Citizens may only withdraw their
# own application, officers only review it, and the system only applies the
# deterministic document/eligibility/fraud outcome of the current state.
ACTOR_ALLOWED_TARGETS: dict[str, frozenset[str]] = {
    "citizen": frozenset({STATUS_WITHDRAWN}),
    "officer": frozenset(OFFICER_REVIEW_DECISIONS),
    "system": frozenset({STATUS_DOCUMENTS_REQUIRED, STATUS_UNDER_REVIEW}),
}

NEXT_ACTION_SUBMIT_DOCUMENTS = "SUBMIT_DOCUMENTS"
NEXT_ACTION_PROVIDE_INFORMATION = "PROVIDE_MISSING_INFORMATION"
NEXT_ACTION_AWAIT_OFFICER_REVIEW = "AWAIT_OFFICER_REVIEW"
NEXT_ACTION_NONE = "NONE"

# Configurable defaults for the document checklist of each scheme. Deployments
# can override them per application through ApplicationCreate.required_documents.
SCHEME_REQUIRED_DOCUMENTS: dict[str, tuple[str, ...]] = {
    "scholarship": ("identity_document", "education_certificate", "income_certificate"),
    "pension": ("identity_document",),
    "crop_insurance": ("identity_document",),
}

# Fact keys each scheme's rules read, used only to explain what information is
# missing. The eligibility rules themselves live in EligibilityEngine.
SCHEME_FACT_FIELDS: dict[str, tuple[str, ...]] = {
    "scholarship": ("student", "annual_income"),
    "pension": ("age",),
    "crop_insurance": ("satellite_drought_index",),
}

MAX_REASON_LENGTH = 2000


class ApplicationError(Exception):
    """Base class for application lifecycle errors."""


class ApplicationNotFound(ApplicationError):
    """The requested application or document does not exist."""


class ApplicationAccessDenied(ApplicationError):
    """The actor is not allowed to see or change this application."""


class InvalidStatusTransition(ApplicationError):
    """The requested status change is not permitted from the current status."""


class InvalidApplicationInput(ApplicationError):
    """The request payload is not usable for the requested operation."""


@dataclass(frozen=True)
class ApplicationViewer:
    kind: str
    actor_id: str
    citizen_id: str | None = None

    @property
    def is_officer(self) -> bool:
        return self.kind == "officer"


@dataclass(frozen=True)
class Guidance:
    status: str
    next_action: str
    next_action_detail: str


def to_application_out(
    db: Session, application: GovernmentApplication, audit: AuditInfo | None = None
) -> ApplicationOut:
    links = (
        db.query(ApplicationDocument)
        .filter_by(application_id=application.id)
        .order_by(ApplicationDocument.attached_at.asc())
        .all()
    )
    references: list[ApplicationDocumentRef] = []
    for link in links:
        document = link.document
        references.append(
            ApplicationDocumentRef(
                document_id=link.document_id,
                document_type=link.document_type,
                sha256=document.sha256 if document else None,
                status=document.status if document else None,
                attached_by=link.attached_by,
                attached_at=link.attached_at,
            )
        )

    attached_types = [reference.document_type for reference in references if reference.document_type]
    missing_documents = [
        document_type
        for document_type in (application.required_documents or [])
        if document_type not in attached_types
    ]

    return ApplicationOut(
        id=application.id,
        citizen_id=application.citizen_id,
        scheme=application.scheme,
        status=application.status,
        facts=dict(application.facts or {}),
        required_documents=list(application.required_documents or []),
        attached_documents=references,
        missing_documents=missing_documents,
        eligibility=EligibilitySummary(
            decision=application.eligibility_decision or "unknown",
            confidence=application.eligibility_confidence,
            reasons=list(application.eligibility_reasons or []),
        ),
        fraud=FraudSummary(
            risk_score=application.fraud_risk_score,
            flagged=bool(application.risk_flagged),
            reasons=list(application.fraud_reasons or []),
        ),
        next_action=application.next_action,
        next_action_detail=application.next_action_detail,
        decision_reason=application.decision_reason or "",
        decided_by=application.decided_by,
        decided_at=application.decided_at,
        created_at=application.created_at,
        updated_at=application.updated_at,
        audit=audit,
    )


class ApplicationTracker:
    """Orchestrates the existing services across the application lifecycle."""

    def __init__(
        self,
        eligibility_engine: EligibilityEngine | None = None,
        fraud_detector: FraudDetector | None = None,
        document_service: DocumentService | None = None,
    ) -> None:
        self.eligibility = eligibility_engine or EligibilityEngine()
        self.fraud = fraud_detector or FraudDetector()
        self.documents = document_service or DocumentService()

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_application(
        self, db: Session, application_id: str, viewer: ApplicationViewer
    ) -> GovernmentApplication:
        application = self._find(db, application_id)
        if not viewer.is_officer and application.citizen_id != viewer.citizen_id:
            raise ApplicationAccessDenied("This application belongs to another citizen")
        return application

    def list_citizen_applications(
        self, db: Session, citizen_id: str
    ) -> list[GovernmentApplication]:
        return (
            db.query(GovernmentApplication)
            .filter_by(citizen_id=citizen_id)
            .order_by(GovernmentApplication.created_at.desc())
            .all()
        )

    def timeline(self, db: Session, application_id: str) -> list[ApplicationStatusHistory]:
        self._find(db, application_id)
        return (
            db.query(ApplicationStatusHistory)
            .filter_by(application_id=application_id)
            .order_by(ApplicationStatusHistory.created_at.asc())
            .all()
        )

    # ── Mutations ─────────────────────────────────────────────────────────────

    async def create_application(
        self, db: Session, citizen: Citizen, payload: ApplicationCreate
    ) -> tuple[GovernmentApplication, AuditInfo]:
        if payload.required_documents is None:
            required_documents = list(SCHEME_REQUIRED_DOCUMENTS.get(payload.scheme, ()))
        else:
            required_documents = list(payload.required_documents)
        application = GovernmentApplication(
            citizen_id=citizen.id,
            scheme=payload.scheme,
            status=STATUS_SUBMITTED,
            facts=dict(payload.facts),
            required_documents=required_documents,
        )
        db.add(application)
        db.commit()
        db.refresh(application)

        self._record_history(
            db,
            application,
            from_status=None,
            to_status=STATUS_SUBMITTED,
            actor_type="citizen",
            actor_id=citizen.id,
            reason="Application submitted by citizen",
        )
        audit = await self._audit(
            db,
            actor=f"citizen:{citizen.id}",
            action="APPLICATION_CREATED",
            subject_id=application.id,
            payload={
                "scheme": application.scheme,
                "required_documents": application.required_documents,
                "declared_fact_fields": sorted(application.facts),
            },
        )
        await self.refresh_guidance(
            db,
            application,
            actor_type="system",
            actor_id=citizen.id,
            reason="Automatic screening of the submitted application",
        )
        return application, audit

    async def attach_document_by_id(
        self,
        db: Session,
        application_id: str,
        document_id: str,
        document_type: str,
        citizen: Citizen,
    ) -> tuple[GovernmentApplication, AuditInfo]:
        application = self._find_owned(db, application_id, citizen)
        document = db.query(Document).filter_by(id=document_id.strip()).first()
        if not document:
            raise ApplicationNotFound(f"Document '{document_id}' was not found")
        if document.citizen_id != citizen.id:
            raise ApplicationAccessDenied("Document belongs to a different citizen")
        return await self._attach_document(
            db,
            application,
            document,
            document_type.strip() or document.document_type,
            citizen.id,
        )

    async def submit_document(
        self,
        db: Session,
        application_id: str,
        citizen: Citizen,
        filename: str,
        data: bytes,
        text: str,
        document_type: str,
    ) -> tuple[GovernmentApplication, AuditInfo]:
        application = self._find_owned(db, application_id, citizen)
        if not data:
            raise InvalidApplicationInput("Uploaded document file is empty")
        document = await self.documents.submit(db, citizen.id, filename, data, text)
        return await self._attach_document(
            db,
            application,
            document,
            document_type.strip() or document.document_type,
            citizen.id,
        )

    async def record_review(
        self,
        db: Session,
        application_id: str,
        officer: OfficerProfile,
        decision: str,
        reason: str,
    ) -> tuple[GovernmentApplication, AuditInfo]:
        application = self._find(db, application_id)
        if application.status in TERMINAL_STATUSES:
            raise InvalidStatusTransition(
                f"Application {application.id} is {application.status} and can no longer be reviewed"
            )
        audit = await self._audit(
            db,
            actor=f"officer:{officer.officer_id}",
            action="APPLICATION_REVIEWED",
            subject_id=application.id,
            payload={"decision": decision, "reason": reason[:MAX_REASON_LENGTH]},
        )
        await self._transition(
            db,
            application,
            target=decision,
            actor_type="officer",
            actor_id=officer.officer_id,
            reason=reason,
        )
        application.decided_by = officer.officer_id
        application.decided_at = datetime.utcnow()
        application.decision_reason = reason[:MAX_REASON_LENGTH]
        # An officer decision is authoritative: guidance is refreshed for the
        # new state but never overrides the officer's chosen status.
        guidance = await self._review_guidance(db, application, decision, officer.officer_id, reason)
        application.next_action = guidance.next_action
        application.next_action_detail = guidance.next_action_detail
        db.commit()
        db.refresh(application)
        return application, audit

    async def _review_guidance(
        self,
        db: Session,
        application: GovernmentApplication,
        decision: str,
        officer_id: str,
        reason: str,
    ) -> Guidance:
        if decision in (STATUS_APPROVED, STATUS_REJECTED):
            return Guidance(decision, NEXT_ACTION_NONE, self._terminal_detail(application))
        if decision == STATUS_DOCUMENTS_REQUIRED:
            attached_types = self._attached_document_types(db, application)
            missing = [
                document_type
                for document_type in (application.required_documents or [])
                if document_type not in attached_types
            ]
            if missing:
                detail = (
                    f"Officer {officer_id} requested documents. Missing required documents: "
                    f"{', '.join(missing)}."
                )
            else:
                detail = f"Officer {officer_id} requested documents: {reason}"
            return Guidance(STATUS_DOCUMENTS_REQUIRED, NEXT_ACTION_SUBMIT_DOCUMENTS, detail)
        return Guidance(
            STATUS_UNDER_REVIEW,
            NEXT_ACTION_AWAIT_OFFICER_REVIEW,
            f"Officer {officer_id} placed the application under review: {reason}",
        )

    def _attached_document_types(self, db: Session, application: GovernmentApplication) -> list[str]:
        return [
            link.document_type
            for link in db.query(ApplicationDocument).filter_by(application_id=application.id).all()
            if link.document_type
        ]

    async def withdraw_application(
        self, db: Session, application_id: str, citizen: Citizen, reason: str
    ) -> tuple[GovernmentApplication, AuditInfo]:
        application = self._find_owned(db, application_id, citizen)
        if application.status == STATUS_WITHDRAWN:
            raise InvalidStatusTransition(
                f"Application {application.id} is already withdrawn"
            )
        withdrawal_reason = reason or "Withdrawn by citizen"
        audit = await self._audit(
            db,
            actor=f"citizen:{citizen.id}",
            action="APPLICATION_WITHDRAWN",
            subject_id=application.id,
            payload={"reason": withdrawal_reason[:MAX_REASON_LENGTH]},
        )
        self._transition(
            db,
            application,
            target=STATUS_WITHDRAWN,
            actor_type="citizen",
            actor_id=citizen.id,
            reason=withdrawal_reason,
        )
        application.decided_by = citizen.id
        application.decided_at = datetime.utcnow()
        application.decision_reason = withdrawal_reason[:MAX_REASON_LENGTH]
        await self.refresh_guidance(
            db,
            application,
            actor_type="system",
            actor_id=citizen.id,
            reason="Application withdrawn by citizen",
        )
        return application, audit

    # ── Deterministic guidance ────────────────────────────────────────────────

    async def refresh_guidance(
        self,
        db: Session,
        application: GovernmentApplication,
        actor_type: str,
        actor_id: str,
        reason: str,
    ) -> GovernmentApplication:
        if application.status not in TERMINAL_STATUSES:
            self._rescreen(db, application)
        guidance = self.evaluate(application, self._attached_document_types(db, application))
        await self._transition(
            db,
            application,
            target=guidance.status,
            actor_type="system",
            actor_id=actor_id,
            reason=reason,
        )
        application.next_action = guidance.next_action
        application.next_action_detail = guidance.next_action_detail
        db.commit()
        db.refresh(application)
        return application

    def evaluate(self, application: GovernmentApplication, attached_document_types: list[str]) -> Guidance:
        if application.status in TERMINAL_STATUSES:
            return Guidance(
                status=application.status,
                next_action=NEXT_ACTION_NONE,
                next_action_detail=self._terminal_detail(application),
            )

        missing_documents = [
            document_type
            for document_type in (application.required_documents or [])
            if document_type not in attached_document_types
        ]
        if missing_documents:
            return Guidance(
                status=STATUS_DOCUMENTS_REQUIRED,
                next_action=NEXT_ACTION_SUBMIT_DOCUMENTS,
                next_action_detail=(
                    f"Missing required documents: {', '.join(missing_documents)}. "
                    f"Attach them with POST /applications/{application.id}/documents."
                ),
            )

        if application.risk_flagged:
            return Guidance(
                status=STATUS_UNDER_REVIEW,
                next_action=NEXT_ACTION_AWAIT_OFFICER_REVIEW,
                next_action_detail=(
                    "Automated risk screening flagged this application for authorised human review "
                    f"(risk score {application.fraud_risk_score:.2f} at or above the "
                    f"{settings.fraud_risk_flag_threshold:.2f} threshold). This is not a fraud "
                    "determination; only a reviewing officer may approve or reject it."
                ),
            )

        if application.eligibility_decision != "auto_approve":
            return Guidance(
                status=STATUS_UNDER_REVIEW,
                next_action=NEXT_ACTION_PROVIDE_INFORMATION,
                next_action_detail=self._eligibility_detail(application),
            )

        return Guidance(
            status=STATUS_UNDER_REVIEW,
            next_action=NEXT_ACTION_AWAIT_OFFICER_REVIEW,
            next_action_detail=(
                f"All required documents are attached and the '{application.scheme}' "
                "eligibility rules are satisfied. A reviewing officer must now approve or reject "
                "the application."
            ),
        )

    def _eligibility_detail(self, application: GovernmentApplication) -> str:
        reasons = "; ".join(application.eligibility_reasons or []) or "No explanation available"
        missing_facts = [
            field
            for field in SCHEME_FACT_FIELDS.get(application.scheme, ())
            if field not in (application.facts or {})
        ]
        detail = (
            f"The '{application.scheme}' eligibility rules are not satisfied: {reasons}. "
            "Provide corrected or additional information for a re-check."
        )
        if missing_facts:
            detail += f" Missing information: {', '.join(missing_facts)}."
        return detail

    def _terminal_detail(self, application: GovernmentApplication) -> str:
        if application.status == STATUS_APPROVED:
            return "Application approved by an authorised officer. No further action is required."
        if application.status == STATUS_REJECTED:
            return (
                "Application rejected by an authorised officer: "
                f"{application.decision_reason or 'no reason recorded'}"
            )
        return (
            "Application withdrawn by the citizen: "
            f"{application.decision_reason or 'no reason recorded'}"
        )

    # ── Internals ─────────────────────────────────────────────────────────────

    def _rescreen(self, db: Session, application: GovernmentApplication) -> None:
        result = self.eligibility.decide(application.scheme, application.facts)
        application.eligibility_decision = result["decision"]
        application.eligibility_confidence = float(result.get("confidence", 0.0))
        application.eligibility_reasons = list(result.get("reasons", []))

        risk_score, risk_reasons, risk_flagged = self._score_fraud(application)
        application.fraud_risk_score = risk_score
        application.fraud_reasons = risk_reasons
        application.risk_flagged = risk_flagged

    def _score_fraud(self, application: GovernmentApplication) -> tuple[float | None, list[str], bool]:
        try:
            result = self.fraud.score(application.facts)
        except Exception:
            logger.warning(
                "Fraud risk screening unavailable for application %s", application.id, exc_info=True
            )
            return (
                None,
                ["Automated risk screening is unavailable; a reviewing officer must assess risk"],
                False,
            )
        flagged = result.risk_score >= settings.fraud_risk_flag_threshold
        return result.risk_score, list(result.reasons), flagged

    async def _attach_document(
        self,
        db: Session,
        application: GovernmentApplication,
        document: Document,
        document_type: str,
        citizen_id: str,
    ) -> tuple[GovernmentApplication, AuditInfo]:
        if application.status in TERMINAL_STATUSES:
            raise InvalidStatusTransition(
                f"Application {application.id} is {application.status} and no longer accepts documents"
            )
        existing = (
            db.query(ApplicationDocument)
            .filter_by(application_id=application.id, document_id=document.id)
            .first()
        )
        if existing:
            raise InvalidStatusTransition(
                f"Document {document.id} is already attached to application {application.id}"
            )
        link = ApplicationDocument(
            application_id=application.id,
            document_id=document.id,
            document_type=document_type,
            attached_by=citizen_id,
        )
        db.add(link)
        db.commit()

        audit = await self._audit(
            db,
            actor=f"citizen:{citizen_id}",
            action="APPLICATION_DOCUMENT_ATTACHED",
            subject_id=application.id,
            payload={
                "document_id": document.id,
                "document_type": link.document_type,
                "document_sha256": document.sha256,
            },
        )
        await self.refresh_guidance(
            db,
            application,
            actor_type="system",
            actor_id=citizen_id,
            reason=f"Document {link.document_type} attached",
        )
        return application, audit

    async def _transition(
        self,
        db: Session,
        application: GovernmentApplication,
        target: str,
        actor_type: str,
        actor_id: str,
        reason: str,
    ) -> bool:
        if target not in APPLICATION_STATUSES:
            raise InvalidApplicationInput(f"Unknown application status '{target}'")
        current = application.status
        if target == current:
            return False
        if target not in ACTOR_ALLOWED_TARGETS.get(actor_type, frozenset()):
            raise ApplicationAccessDenied(
                f"Actor type '{actor_type}' is not allowed to set status '{target}'"
            )
        if target not in ALLOWED_STATUS_TRANSITIONS[current]:
            raise InvalidStatusTransition(
                f"Cannot change application {application.id} from {current} to {target}"
            )

        application.status = target
        self._record_history(
            db,
            application,
            from_status=current,
            to_status=target,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
        )
        await self._audit(
            db,
            actor=f"{actor_type}:{actor_id}",
            action="APPLICATION_STATUS_CHANGED",
            subject_id=application.id,
            payload={
                "from_status": current,
                "to_status": target,
                "reason": reason[:MAX_REASON_LENGTH],
            },
        )
        return True

    def _record_history(
        self,
        db: Session,
        application: GovernmentApplication,
        from_status: str | None,
        to_status: str,
        actor_type: str,
        actor_id: str | None,
        reason: str,
    ) -> None:
        db.add(
            ApplicationStatusHistory(
                application_id=application.id,
                from_status=from_status,
                to_status=to_status,
                actor_type=actor_type,
                actor_id=actor_id,
                reason=reason[:MAX_REASON_LENGTH],
            )
        )
        db.commit()

    def _find(self, db: Session, application_id: str) -> GovernmentApplication:
        application = db.query(GovernmentApplication).filter_by(id=application_id.strip()).first()
        if not application:
            raise ApplicationNotFound(f"Application '{application_id}' was not found")
        return application

    def _find_owned(
        self, db: Session, application_id: str, citizen: Citizen
    ) -> GovernmentApplication:
        application = self._find(db, application_id)
        if application.citizen_id != citizen.id:
            raise ApplicationAccessDenied("This application belongs to another citizen")
        return application

    async def _audit(
        self, db: Session, actor: str, action: str, subject_id: str, payload: dict[str, Any]
    ) -> AuditInfo:
        result: AuditWriteResult = await record_audit_event(db, actor, action, subject_id, payload)
        return AuditInfo(
            event_id=result.event.id if result.event else None,
            anchored_on_chain=result.anchored,
        )
