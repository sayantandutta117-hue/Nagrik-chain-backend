from sqlalchemy.orm import Session

from ..ai.document_ai import DocumentAI
from ..blockchain.ipfs import IPFSClient
from ..config import settings
from ..models import Document
from ..security import sha256_bytes


class DocumentService:
    def __init__(self) -> None:
        self.ipfs = IPFSClient(settings.ipfs_api_url)
        self.ai = DocumentAI()

    async def submit(self, db: Session, citizen_id: str, filename: str, data: bytes, text: str) -> Document:
        cid = await self.ipfs.add_bytes(data, filename)
        ai_result = await self.ai.process_document(data, filename, text)
        status = "auto_approved" if ai_result.confidence >= 0.80 else "assisted_review"
        document = Document(
            citizen_id=citizen_id,
            document_type=ai_result.document_type,
            sha256=sha256_bytes(data),
            ipfs_cid=cid,
            status=status,
            ai_score=ai_result.confidence,
            explanation=f"language={ai_result.language}; " + "; ".join(ai_result.reasons),
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        return document
