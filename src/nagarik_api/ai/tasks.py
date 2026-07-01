from __future__ import annotations

import asyncio
import os
from typing import Any

try:
    from celery import Celery
except ImportError:  # pragma: no cover - production dependency loaded when installed
    Celery = None  # type: ignore[assignment]

from .document_ai import DocumentAI


celery_app = (
    Celery(
        "nagarik_document_ai",
        broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
        backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1"),
    )
    if Celery
    else None
)


async def run_document_ai_async(data: bytes, filename: str, supplied_text: str = "") -> dict[str, Any]:
    result = await DocumentAI().process_document(data, filename, supplied_text)
    return {
        "document_type": result.document_type,
        "confidence": result.confidence,
        "language": result.language,
        "extracted_fields": result.extracted_fields,
        "reasons": result.reasons,
    }


if celery_app:

    @celery_app.task(name="nagarik.document_ai.process")
    def process_document_task(data: bytes, filename: str, supplied_text: str = "") -> dict[str, Any]:
        return asyncio.run(run_document_ai_async(data, filename, supplied_text))
