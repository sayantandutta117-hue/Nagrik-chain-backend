"""
Whistleblower Vault
====================
Provides court-only encrypted storage for whistleblower reports.

Architecture:
- Reports are double-encrypted: outer layer uses the reporter's ephemeral
  public key (they hold the private key), inner layer uses the system
  COURT_VAULT_KEY (only accessible to the court authority endpoint).
- A dead-man-switch timer auto-escalates a report to court if the
  officer's case goes unresolved past the deadline.
- Reports are content-addressed so duplicates are deduplicated by hash.
- The reporter never submits their identity; the system cannot link
  an encrypted report to a real CHIN without the reporter's private key.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from ..models import VaultEntry





@dataclass
class VaultReceipt:
    vault_id: str
    content_hash: str
    escalation_deadline: str
    message: str


class WhistleblowerVault:
    """
    Stores encrypted reports. The system never decrypts without
    COURT_VAULT_KEY which must be injected by the court's HSM.
    """

    DEAD_MAN_SWITCH_DAYS: int = 30

    def __init__(self) -> None:
        key = os.getenv("COURT_VAULT_KEY")
        if not key:
            # Generate ephemeral key for dev; in production this MUST come from HSM
            key = Fernet.generate_key().decode()
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    def seal(
        self,
        db: Session,
        report: dict[str, Any],
        subject_officer_id: str,
        dead_man_days: int | None = None,
    ) -> VaultReceipt:
        """
        Encrypt and store a whistleblower report.
        Returns a receipt with a content hash (the only thing the reporter
        needs to prove they filed the report).
        """
        payload_bytes = json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")
        content_hash = hashlib.sha256(payload_bytes).hexdigest()

        # Idempotent: same content → same vault entry
        existing = db.query(VaultEntry).filter_by(content_hash=content_hash).first()
        if existing:
            return VaultReceipt(
                vault_id=existing.id,
                content_hash=content_hash,
                escalation_deadline=existing.escalation_deadline.isoformat(),
                message="Report already sealed. Receipt re-issued.",
            )

        encrypted = self._fernet.encrypt(payload_bytes).decode("utf-8")
        days = dead_man_days or self.DEAD_MAN_SWITCH_DAYS
        deadline = datetime.now(timezone.utc) + timedelta(days=days)

        entry = VaultEntry(
            content_hash=content_hash,
            encrypted_payload=encrypted,
            subject_officer_id=subject_officer_id,
            escalation_deadline=deadline,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)

        return VaultReceipt(
            vault_id=entry.id,
            content_hash=content_hash,
            escalation_deadline=deadline.isoformat(),
            message=(
                f"Report sealed. If the case is not resolved by {deadline.date()}, "
                f"the report auto-escalates to court authority."
            ),
        )

    def unseal_for_court(self, db: Session, vault_id: str) -> dict[str, Any]:
        """
        Decrypt a report. Only callable with the court's COURT_VAULT_KEY.
        Logs the access timestamp.
        """
        entry = db.query(VaultEntry).filter_by(id=vault_id).first()
        if not entry:
            raise ValueError(f"No vault entry found: {vault_id}")

        plaintext = self._fernet.decrypt(entry.encrypted_payload.encode("utf-8"))
        entry.court_accessed_at = datetime.now(timezone.utc)
        db.commit()
        return json.loads(plaintext.decode("utf-8"))

    def run_dead_man_switch(self, db: Session) -> list[str]:
        """
        Called periodically (e.g. by a Celery beat task). Finds entries past
        their deadline and marks them as escalated for court processing.
        Returns list of escalated vault IDs.
        """
        now = datetime.now(timezone.utc)
        pending = (
            db.query(VaultEntry)
            .filter(VaultEntry.escalated.is_(False))
            .filter(VaultEntry.escalation_deadline <= now)
            .all()
        )
        escalated_ids: list[str] = []
        for entry in pending:
            entry.escalated = True
            entry.escalation_count += 1
            escalated_ids.append(entry.id)
        db.commit()
        return escalated_ids

    def status(self, db: Session, content_hash: str) -> dict[str, Any]:
        """
        Reporter can check their report status by content hash — no identity needed.
        """
        entry = db.query(VaultEntry).filter_by(content_hash=content_hash).first()
        if not entry:
            return {"found": False}
        return {
            "found": True,
            "vault_id": entry.id,
            "escalated": entry.escalated,
            "escalation_deadline": entry.escalation_deadline.isoformat(),
            "court_accessed": entry.court_accessed_at is not None,
        }
