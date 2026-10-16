from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class Citizen(Base):
    __tablename__ = "citizens"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("cit"))
    chin: Mapped[str] = mapped_column(String, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String)
    date_of_birth: Mapped[str] = mapped_column(String)
    did: Mapped[str] = mapped_column(String, unique=True, index=True)
    public_key_pem: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    documents: Mapped[list["Document"]] = relationship(back_populates="citizen")


class ProfessionalCredential(Base):
    __tablename__ = "professional_credentials"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("pdid"))
    citizen_id: Mapped[str] = mapped_column(ForeignKey("citizens.id"))
    profession: Mapped[str] = mapped_column(String)
    license_number: Mapped[str] = mapped_column(String, unique=True)
    issuer: Mapped[str] = mapped_column(String)
    did: Mapped[str] = mapped_column(String, unique=True)
    status: Mapped[str] = mapped_column(String, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("doc"))
    citizen_id: Mapped[str] = mapped_column(ForeignKey("citizens.id"))
    document_type: Mapped[str] = mapped_column(String)
    sha256: Mapped[str] = mapped_column(String, index=True)
    ipfs_cid: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")
    ai_score: Mapped[float] = mapped_column(Float, default=0.0)
    explanation: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    citizen: Mapped[Citizen] = relationship(back_populates="documents")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("evt"))
    actor: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    subject_id: Mapped[str] = mapped_column(String, index=True)
    payload_hash: Mapped[str] = mapped_column(String)
    fabric_tx_id: Mapped[str | None] = mapped_column(String, nullable=True)
    ethereum_tx_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OfficerProfile(Base):
    __tablename__ = "officer_profiles"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("off"))
    officer_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    department: Mapped[str] = mapped_column(String, default="")
    integrity_score: Mapped[float] = mapped_column(Float, default=100.0)
    rejection_rate_30d: Mapped[float] = mapped_column(Float, default=0.0)
    rejection_rate_baseline: Mapped[float] = mapped_column(Float, default=0.0)
    average_delay_hours: Mapped[float] = mapped_column(Float, default=0.0)
    bribe_reports: Mapped[int] = mapped_column(String, default="0")  # stored as str for compat
    decoy_failures: Mapped[int] = mapped_column(String, default="0")
    decoy_total: Mapped[int] = mapped_column(String, default="0")
    manual_overturns: Mapped[int] = mapped_column(String, default="0")
    total_decisions: Mapped[int] = mapped_column(String, default="0")
    flagged_for_audit: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class VaultEntry(Base):
    __tablename__ = "vault_entries"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("vault"))
    content_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    encrypted_payload: Mapped[str] = mapped_column(Text)
    subject_officer_id: Mapped[str] = mapped_column(String, index=True)
    escalation_deadline: Mapped[datetime] = mapped_column(DateTime)
    escalated: Mapped[bool] = mapped_column(default=False)
    escalation_count: Mapped[int] = mapped_column(String, default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    court_accessed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DecoyApplication(Base):
    __tablename__ = "decoy_applications"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("decoy"))
    disguise_application_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    assigned_officer_id: Mapped[str] = mapped_column(String, index=True)
    scheme: Mapped[str] = mapped_column(String)
    disguise_payload_hash: Mapped[str] = mapped_column(String)
    outcome: Mapped[str] = mapped_column(String, default="pending")
    failure_reason: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class GovernmentApplication(Base):
    __tablename__ = "government_applications"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("app"))
    citizen_id: Mapped[str] = mapped_column(ForeignKey("citizens.id"), index=True)
    scheme: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default="SUBMITTED", index=True)
    facts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    required_documents: Mapped[list[str]] = mapped_column(JSON, default=list)
    eligibility_decision: Mapped[str | None] = mapped_column(String, nullable=True)
    eligibility_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    eligibility_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    fraud_risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    fraud_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    risk_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    next_action: Mapped[str] = mapped_column(String, default="AWAIT_OFFICER_REVIEW")
    next_action_detail: Mapped[str] = mapped_column(Text, default="")
    decision_reason: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str | None] = mapped_column(String, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    documents: Mapped[list["ApplicationDocument"]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )
    history: Mapped[list["ApplicationStatusHistory"]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )


class ApplicationDocument(Base):
    __tablename__ = "application_documents"
    __table_args__ = (UniqueConstraint("application_id", "document_id", name="uq_application_document"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("appdoc"))
    application_id: Mapped[str] = mapped_column(ForeignKey("government_applications.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    document_type: Mapped[str] = mapped_column(String, default="")
    attached_by: Mapped[str] = mapped_column(String, default="")
    attached_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    application: Mapped["GovernmentApplication"] = relationship(back_populates="documents")
    document: Mapped["Document"] = relationship()


class ApplicationStatusHistory(Base):
    __tablename__ = "application_status_history"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("hist"))
    application_id: Mapped[str] = mapped_column(ForeignKey("government_applications.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String, nullable=True)
    to_status: Mapped[str] = mapped_column(String)
    actor_type: Mapped[str] = mapped_column(String, default="system")
    actor_id: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    application: Mapped["GovernmentApplication"] = relationship(back_populates="history")


class SavedScheme(Base):
    """A scheme bookmarked by an authenticated citizen."""

    __tablename__ = "saved_schemes"
    __table_args__ = (
        UniqueConstraint("citizen_id", "scheme_id", name="uq_saved_scheme_citizen_scheme"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("save"))
    citizen_id: Mapped[str] = mapped_column(ForeignKey("citizens.id"), index=True)
    scheme_id: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SavedSchemeError(Exception):
    """Raised by the bookmark service with an HTTP status code."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code
