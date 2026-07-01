from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any, Protocol

from .model_loader import DocumentAIModels, ModelRegistry


@dataclass
class DocumentAIResult:
    document_type: str
    confidence: float
    language: str
    extracted_fields: dict
    reasons: list[str]


class OCRBackend(Protocol):
    def extract_text(self, data: bytes, filename: str) -> tuple[str, float, str]:
        ...


class LanguageDetector(Protocol):
    def detect(self, text: str) -> tuple[str, float]:
        ...


class DocumentAI:
    def __init__(self, registry: ModelRegistry | None = None) -> None:
        self.registry = registry or ModelRegistry.from_settings()

    async def process_document(
        self,
        data: bytes,
        filename: str,
        supplied_text: str = "",
    ) -> DocumentAIResult:
        return await asyncio.to_thread(self._process_sync, data, filename, supplied_text)

    def classify_and_extract(self, text: str) -> DocumentAIResult:
        return self._process_sync(text.encode("utf-8"), "inline-text.txt", text)

    def _process_sync(self, data: bytes, filename: str, supplied_text: str) -> DocumentAIResult:
        models = self.registry.load()
        ocr_text, ocr_confidence, ocr_engine = models.ocr.extract_text(data, filename)
        text = self._select_text(supplied_text, ocr_text)
        language, language_confidence = models.language.detect(text)
        classification = self._classify(models, text)
        entities = self._extract_entities(models, text)
        confidence = self._confidence(
            classification_score=classification["score"],
            ocr_confidence=ocr_confidence,
            language_confidence=language_confidence,
            entity_count=len(entities),
            text_length=len(text.strip()),
        )
        return DocumentAIResult(
            document_type=classification["label"],
            confidence=confidence,
            language=language,
            extracted_fields={
                "entities": entities,
                "ocr": {"engine": ocr_engine, "confidence": ocr_confidence},
                "classification": classification,
            },
            reasons=[
                f"OCR completed with {ocr_engine}",
                f"Language detected as {language}",
                "Document class predicted by transformer model",
                "Entities extracted by token-classification model",
            ],
        )

    def _classify(self, models: DocumentAIModels, text: str) -> dict:
        if not text.strip():
            return {"label": "unknown", "score": 0.0}
        result = models.classifier(text, candidate_labels=models.document_labels)
        labels = result.get("labels") or []
        scores = result.get("scores") or []
        if not labels or not scores:
            return {"label": "unknown", "score": 0.0}
        return {"label": str(labels[0]), "score": float(scores[0])}

    def _extract_entities(self, models: DocumentAIModels, text: str) -> list[dict[str, Any]]:
        if not text.strip():
            return []
        raw_entities = models.entity_extractor(text)
        normalized: list[dict[str, Any]] = []
        for entity in raw_entities:
            word = entity.get("word") or entity.get("entity_group") or ""
            normalized.append(
                {
                    "text": str(word).replace("##", ""),
                    "label": str(entity.get("entity_group") or entity.get("entity") or "MISC"),
                    "score": float(entity.get("score", 0.0)),
                    "start": entity.get("start"),
                    "end": entity.get("end"),
                }
            )
        return normalized

    def _select_text(self, supplied_text: str, ocr_text: str) -> str:
        if supplied_text.strip() and len(supplied_text.strip()) >= len(ocr_text.strip()):
            return supplied_text
        return ocr_text

    def _confidence(
        self,
        classification_score: float,
        ocr_confidence: float,
        language_confidence: float,
        entity_count: int,
        text_length: int,
    ) -> float:
        text_signal = min(1.0, math.log1p(max(text_length, 0)) / math.log1p(1500))
        entity_signal = min(1.0, entity_count / 8)
        score = (
            classification_score * 0.45
            + ocr_confidence * 0.25
            + language_confidence * 0.15
            + text_signal * 0.10
            + entity_signal * 0.05
        )
        return round(max(0.0, min(1.0, score)), 4)
