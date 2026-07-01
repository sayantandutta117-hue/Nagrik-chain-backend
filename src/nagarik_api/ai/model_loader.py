from __future__ import annotations

import importlib
import io
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from PIL import Image


Pipeline = Callable[..., Any]


@dataclass(frozen=True)
class DocumentAIConfig:
    ocr_backend: str = "tesseract"
    tesseract_cmd: str | None = None
    paddleocr_lang: str = "en"
    language_backend: str = "langdetect"
    classifier_model: str = "joeddav/xlm-roberta-large-xnli"
    entity_model: str = "Davlan/bert-base-multilingual-cased-ner-hrl"
    document_labels: tuple[str, ...] = (
        "birth_certificate",
        "death_certificate",
        "income_certificate",
        "education_certificate",
        "identity_document",
        "property_document",
        "medical_certificate",
        "legal_affidavit",
        "unknown",
    )

    @classmethod
    def from_env(cls) -> "DocumentAIConfig":
        labels = os.getenv("DOCUMENT_AI_LABELS")
        return cls(
            ocr_backend=os.getenv("DOCUMENT_AI_OCR_BACKEND", cls.ocr_backend),
            tesseract_cmd=os.getenv("TESSERACT_CMD") or None,
            paddleocr_lang=os.getenv("PADDLEOCR_LANG", cls.paddleocr_lang),
            language_backend=os.getenv("DOCUMENT_AI_LANGUAGE_BACKEND", cls.language_backend),
            classifier_model=os.getenv("DOCUMENT_AI_CLASSIFIER_MODEL", cls.classifier_model),
            entity_model=os.getenv("DOCUMENT_AI_ENTITY_MODEL", cls.entity_model),
            document_labels=tuple(item.strip() for item in labels.split(",") if item.strip())
            if labels
            else cls.document_labels,
        )


@dataclass
class DocumentAIModels:
    ocr: Any
    language: Any
    classifier: Pipeline
    entity_extractor: Pipeline
    document_labels: tuple[str, ...]


class ModelLoadError(RuntimeError):
    pass


class ModelRegistry:
    def __init__(self, config: DocumentAIConfig) -> None:
        self.config = config

    @classmethod
    def from_settings(cls) -> "ModelRegistry":
        return cls(DocumentAIConfig.from_env())

    def load(self) -> DocumentAIModels:
        return _load_models(self.config)


@lru_cache(maxsize=4)
def _load_models(config: DocumentAIConfig) -> DocumentAIModels:
    return DocumentAIModels(
        ocr=_load_ocr(config),
        language=_load_language_detector(config),
        classifier=_load_zero_shot_classifier(config),
        entity_extractor=_load_entity_extractor(config),
        document_labels=config.document_labels,
    )


def _load_ocr(config: DocumentAIConfig) -> Any:
    if config.ocr_backend.lower() == "paddleocr":
        return PaddleOCREngine(config.paddleocr_lang)
    return TesseractOCREngine(config.tesseract_cmd)


def _load_language_detector(config: DocumentAIConfig) -> Any:
    if config.language_backend.lower() != "langdetect":
        raise ModelLoadError(f"Unsupported language detector: {config.language_backend}")
    return LangDetectLanguageDetector()


def _load_zero_shot_classifier(config: DocumentAIConfig) -> Pipeline:
    transformers = _optional_import("transformers", "pip install '.[ai]' to enable document classification")
    return transformers.pipeline("zero-shot-classification", model=config.classifier_model)


def _load_entity_extractor(config: DocumentAIConfig) -> Pipeline:
    transformers = _optional_import("transformers", "pip install '.[ai]' to enable entity extraction")
    return transformers.pipeline(
        "token-classification",
        model=config.entity_model,
        aggregation_strategy="simple",
    )


class TesseractOCREngine:
    def __init__(self, tesseract_cmd: str | None = None) -> None:
        self.pytesseract = _optional_import(
            "pytesseract",
            "Install Tesseract OCR and pytesseract, or set DOCUMENT_AI_OCR_BACKEND=paddleocr",
        )
        if tesseract_cmd:
            self.pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    def extract_text(self, data: bytes, filename: str) -> tuple[str, float, str]:
        image = Image.open(io.BytesIO(data))
        ocr = self.pytesseract.image_to_data(image, output_type=self.pytesseract.Output.DICT)
        tokens: list[str] = []
        confidences: list[float] = []
        for token, confidence in zip(ocr.get("text", []), ocr.get("conf", [])):
            token = str(token).strip()
            try:
                conf = float(confidence)
            except (TypeError, ValueError):
                conf = -1.0
            if token and conf >= 0:
                tokens.append(token)
                confidences.append(conf / 100.0)
        text = " ".join(tokens)
        score = sum(confidences) / len(confidences) if confidences else 0.0
        return text, round(score, 4), "tesseract"


class PaddleOCREngine:
    def __init__(self, lang: str) -> None:
        paddleocr = _optional_import("paddleocr", "pip install paddleocr to enable PaddleOCR")
        self.engine = paddleocr.PaddleOCR(use_angle_cls=True, lang=lang)

    def extract_text(self, data: bytes, filename: str) -> tuple[str, float, str]:
        image_path = _bytes_path(data, filename)
        try:
            result = self.engine.ocr(str(image_path), cls=True)
            tokens: list[str] = []
            scores: list[float] = []
            for page in result or []:
                for line in page or []:
                    if len(line) >= 2 and len(line[1]) >= 2:
                        tokens.append(str(line[1][0]))
                        scores.append(float(line[1][1]))
            return " ".join(tokens), round(sum(scores) / len(scores), 4) if scores else 0.0, "paddleocr"
        finally:
            image_path.unlink(missing_ok=True)


class LangDetectLanguageDetector:
    def __init__(self) -> None:
        self.langdetect = _optional_import("langdetect", "pip install langdetect to enable language detection")

    def detect(self, text: str) -> tuple[str, float]:
        if not text.strip():
            return "und", 0.0
        candidates = self.langdetect.detect_langs(text)
        if not candidates:
            return "und", 0.0
        best = candidates[0]
        return best.lang, round(float(best.prob), 4)


def _optional_import(module_name: str, install_hint: str) -> Any:
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise ModelLoadError(f"Missing optional dependency '{module_name}'. {install_hint}") from exc


def _bytes_path(data: bytes, filename: str) -> Path:
    import tempfile

    suffix = Path(filename).suffix or ".bin"
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    with handle:
        handle.write(data)
    return Path(handle.name)
