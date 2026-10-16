import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..blockchain.ethereum import EthereumGateway
from ..blockchain.fabric import FabricGateway
from ..config import settings
from ..models import AuditEvent
from ..security import canonical_hash

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditWriteResult:
    event: AuditEvent | None
    anchored: bool


class AuditService:
    def __init__(self) -> None:
        self.fabric = FabricGateway(settings.fabric_gateway_url)
        self.ethereum = EthereumGateway(settings.ethereum_rpc_url)

    async def record(self, db: Session, actor: str, action: str, subject_id: str, payload: dict) -> AuditEvent:
        payload_hash = canonical_hash(payload)
        event_payload = {
            "actor": actor,
            "action": action,
            "subject_id": subject_id,
            "payload_hash": payload_hash,
        }
        fabric_tx = await self.fabric.anchor_event(event_payload)
        eth_tx = await self.ethereum.emit_contract_event(action, event_payload)
        event = AuditEvent(
            actor=actor,
            action=action,
            subject_id=subject_id,
            payload_hash=payload_hash,
            fabric_tx_id=fabric_tx,
            ethereum_tx_hash=eth_tx,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return event


async def record_audit_event(
    db: Session, actor: str, action: str, subject_id: str, payload: dict
) -> AuditWriteResult:
    """
    Best-effort audit write used by the application tracker.

    The local audit event is always persisted when possible, while
    ``anchored`` is only True when both blockchain gateways accepted the
    event, so callers never claim on-chain anchoring that did not happen.
    """
    try:
        event: AuditEvent | None = await AuditService().record(db, actor, action, subject_id, payload)
    except Exception:
        logger.exception(
            "Blockchain audit anchoring failed for action=%s subject=%s", action, subject_id
        )
        db.rollback()
        event = _persist_audit_event(db, actor, action, subject_id, payload)
    anchored = bool(event and event.fabric_tx_id and event.ethereum_tx_hash)
    return AuditWriteResult(event=event, anchored=anchored)


def _persist_audit_event(
    db: Session, actor: str, action: str, subject_id: str, payload: dict
) -> AuditEvent | None:
    try:
        event = AuditEvent(
            actor=actor,
            action=action,
            subject_id=subject_id,
            payload_hash=canonical_hash(payload),
            fabric_tx_id=None,
            ethereum_tx_hash=None,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return event
    except Exception:
        db.rollback()
        logger.exception(
            "Local audit persistence failed for action=%s subject=%s", action, subject_id
        )
        return None
