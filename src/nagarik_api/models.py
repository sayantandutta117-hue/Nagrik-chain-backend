from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
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
