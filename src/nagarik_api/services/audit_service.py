from sqlalchemy.orm import Session

from ..blockchain.ethereum import EthereumGateway
from ..blockchain.fabric import FabricGateway
from ..config import settings
from ..models import AuditEvent
from ..security import canonical_hash


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
