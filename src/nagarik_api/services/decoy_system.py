"""
Decoy Application System
=========================
Officers cannot know whether an application is real or a system-generated
decoy. If an officer requests a bribe from a decoy application, the system
auto-records evidence and routes the case to the anti-corruption cell.

How it works:
1. The system periodically seeds ``DecoyApplication`` rows into the normal
   application queue alongside real ones.
2. Officers see them as identical to genuine applications.
3. If the officer rejects a decoy without a valid reason, or marks it
   as requiring an "out-of-band payment", the failure is recorded and
   fed into the integrity score engine.
4. Decoys are seeded using a deterministic but unpredictable schedule
   so officers cannot reverse-engineer the pattern.

This is the ``Decoy Applications`` feature from Slide 8 of the deck.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models import DecoyApplication





@dataclass
class DecoyResult:
    decoy_id: str
    officer_id: str
    passed: bool
    failure_reason: str
    action_taken: str


class DecoyApplicationSystem:
    """
    Generates and evaluates decoy applications for a given officer.
    """

    # HMAC key used to seed decoy IDs in an unpredictable but reproducible way
    _SEED_KEY = os.getenv("DECOY_SEED_KEY", secrets.token_hex(32)).encode()

    def seed_decoy(
        self,
        db: Session,
        officer_id: str,
        scheme: str,
        facts: dict[str, Any],
    ) -> DecoyApplication:
        """
        Generate a decoy application that looks real to the officer.
        The disguise_application_id is a deterministic HMAC so internal
        systems can verify it without storing a plaintext flag.
        """
        token = f"{officer_id}:{scheme}:{secrets.token_hex(8)}"
        disguise_id = hmac.new(self._SEED_KEY, token.encode(), hashlib.sha256).hexdigest()[:24]
        payload_hash = hashlib.sha256(
            str(sorted(facts.items())).encode()
        ).hexdigest()

        decoy = DecoyApplication(
            disguise_application_id=disguise_id,
            assigned_officer_id=officer_id,
            scheme=scheme,
            disguise_payload_hash=payload_hash,
        )
        db.add(decoy)
        db.commit()
        db.refresh(decoy)
        return decoy

    def is_decoy(self, db: Session, application_id: str) -> bool:
        return db.query(DecoyApplication).filter_by(
            disguise_application_id=application_id
        ).first() is not None

    def record_outcome(
        self,
        db: Session,
        application_id: str,
        officer_decision: str,
        officer_reason: str = "",
    ) -> DecoyResult | None:
        decoy = db.query(DecoyApplication).filter_by(
            disguise_application_id=application_id
        ).first()
        if not decoy:
            return None

        # A decoy application should always be auto-approved by an honest officer.
        # Any rejection or "pending further payment" outcome is a failure.
        is_failure = officer_decision.lower() not in {"auto_approve", "approved", "approve"}
        decoy.outcome = "failed" if is_failure else "passed"
        decoy.failure_reason = officer_reason if is_failure else ""
        decoy.resolved_at = datetime.now(timezone.utc)
        db.commit()

        action = (
            "ESCALATE_TO_ANTI_CORRUPTION_CELL"
            if is_failure
            else "CLEAR"
        )

        return DecoyResult(
            decoy_id=decoy.id,
            officer_id=decoy.assigned_officer_id,
            passed=not is_failure,
            failure_reason=decoy.failure_reason,
            action_taken=action,
        )

    def decoy_stats_for_officer(self, db: Session, officer_id: str) -> dict[str, Any]:
        entries = db.query(DecoyApplication).filter_by(assigned_officer_id=officer_id).all()
        total = len(entries)
        failures = sum(1 for e in entries if e.outcome == "failed")
        return {
            "officer_id": officer_id,
            "total_decoys": total,
            "failures": failures,
            "failure_rate": failures / total if total else 0.0,
        }
