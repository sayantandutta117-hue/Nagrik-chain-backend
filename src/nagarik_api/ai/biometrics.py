from __future__ import annotations

import base64
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken


@dataclass
class BiometricResult:
    passed: bool
    score: float
    reasons: list[str]


@dataclass(frozen=True)
class FaceEmbedding:
    vector: tuple[float, ...]
    model: str
    detector_backend: str


class FaceRecognitionBackend(Protocol):
    def create_embedding(self, image_bytes: bytes) -> FaceEmbedding:
        ...

    def detect_liveness(self, image_bytes: bytes) -> BiometricResult:
        ...


class BiometricVerifier:
    def __init__(
        self,
        backend: FaceRecognitionBackend | None = None,
        embedding_store: "SecureEmbeddingStore | None" = None,
        similarity_threshold: float | None = None,
    ) -> None:
        self.backend = backend or DeepFaceRecognitionBackend.from_env()
        self.embedding_store = embedding_store or SecureEmbeddingStore.from_env()
        self.similarity_threshold = similarity_threshold or float(
            os.getenv("FACE_SIMILARITY_THRESHOLD", "0.72")
        )

    def verify_face_liveness(self, image_bytes: bytes) -> BiometricResult:
        return self.backend.detect_liveness(image_bytes)

    def enroll_face(self, image_bytes: bytes, subject_id: str) -> str:
        liveness = self.verify_face_liveness(image_bytes)
        if not liveness.passed:
            raise ValueError("Face enrollment rejected: " + "; ".join(liveness.reasons))
        embedding = self.backend.create_embedding(image_bytes)
        return self.embedding_store.serialize(subject_id, embedding)

    def verify_face(self, probe_image_bytes: bytes, encrypted_enrolled_embedding: str) -> BiometricResult:
        liveness = self.verify_face_liveness(probe_image_bytes)
        if not liveness.passed:
            return liveness
        enrolled = self.embedding_store.deserialize(encrypted_enrolled_embedding)
        probe = self.backend.create_embedding(probe_image_bytes)
        similarity = cosine_similarity(probe.vector, enrolled.vector)
        passed = similarity >= self.similarity_threshold
        return BiometricResult(
            passed=passed,
            score=round(similarity, 4),
            reasons=[
                f"Face embedding model={probe.model}",
                f"Cosine similarity compared against threshold {self.similarity_threshold:.2f}",
                "Probe image passed anti-spoofing/liveness before similarity scoring",
            ],
        )

    def verify_fingerprint_template(self, probe_hash: str, enrolled_hash: str) -> BiometricResult:
        if not probe_hash or not enrolled_hash:
            return BiometricResult(False, 0.0, ["Missing fingerprint template"])
        matches = sum(a == b for a, b in zip(probe_hash, enrolled_hash))
        score = matches / max(len(probe_hash), len(enrolled_hash))
        return BiometricResult(score >= 0.82, round(score, 4), ["SourceAFIS-compatible template score"])


class DeepFaceRecognitionBackend:
    def __init__(
        self,
        model_name: str = "Facenet512",
        detector_backend: str = "retinaface",
        enforce_detection: bool = True,
        align: bool = True,
    ) -> None:
        self.model_name = model_name
        self.detector_backend = detector_backend
        self.enforce_detection = enforce_detection
        self.align = align
        self.deepface = self._load_deepface()

    @classmethod
    def from_env(cls) -> "DeepFaceRecognitionBackend":
        return cls(
            model_name=os.getenv("FACE_EMBEDDING_MODEL", "Facenet512"),
            detector_backend=os.getenv("FACE_DETECTOR_BACKEND", "retinaface"),
            enforce_detection=os.getenv("FACE_ENFORCE_DETECTION", "true").lower() == "true",
            align=os.getenv("FACE_ALIGN", "true").lower() == "true",
        )

    def create_embedding(self, image_bytes: bytes) -> FaceEmbedding:
        image_path = _write_temp_image(image_bytes)
        try:
            representations = self.deepface.represent(
                img_path=str(image_path),
                model_name=self.model_name,
                detector_backend=self.detector_backend,
                enforce_detection=self.enforce_detection,
                align=self.align,
            )
        finally:
            image_path.unlink(missing_ok=True)
        if not representations:
            raise ValueError("No face embedding produced")
        vector = representations[0].get("embedding")
        if not vector:
            raise ValueError("DeepFace returned an empty embedding")
        return FaceEmbedding(
            vector=tuple(float(value) for value in vector),
            model=self.model_name,
            detector_backend=self.detector_backend,
        )

    def detect_liveness(self, image_bytes: bytes) -> BiometricResult:
        image_path = _write_temp_image(image_bytes)
        try:
            faces = self.deepface.extract_faces(
                img_path=str(image_path),
                detector_backend=self.detector_backend,
                enforce_detection=self.enforce_detection,
                align=self.align,
                anti_spoofing=True,
            )
        finally:
            image_path.unlink(missing_ok=True)
        if not faces:
            return BiometricResult(False, 0.0, ["No face detected by DeepFace anti-spoofing pipeline"])

        best = max(faces, key=lambda face: float(face.get("confidence", 0.0)))
        confidence = float(best.get("confidence", 0.0))
        is_real = bool(best.get("is_real", False))
        antispoof_score = _extract_antispoof_score(best)
        score = min(1.0, max(confidence, antispoof_score))
        reasons = [
            "DeepFace face detector located a face",
            "DeepFace anti-spoofing check marked image as real"
            if is_real
            else "DeepFace anti-spoofing check marked image as spoof risk",
        ]
        return BiometricResult(is_real and score >= 0.5, round(score, 4), reasons)

    def _load_deepface(self):
        try:
            from deepface import DeepFace
        except ImportError as exc:
            raise RuntimeError("Missing dependency 'deepface'. Install the ai extras to enable face recognition.") from exc
        return DeepFace


class SecureEmbeddingStore:
    def __init__(self, fernet: Fernet) -> None:
        self.fernet = fernet

    @classmethod
    def from_env(cls) -> "SecureEmbeddingStore":
        key = os.getenv("BIOMETRIC_EMBEDDING_KEY")
        if not key:
            raise RuntimeError("BIOMETRIC_EMBEDDING_KEY must be set for secure face embedding storage")
        return cls(Fernet(key.encode("utf-8")))

    @classmethod
    def with_generated_key(cls) -> "SecureEmbeddingStore":
        return cls(Fernet(Fernet.generate_key()))

    def serialize(self, subject_id: str, embedding: FaceEmbedding) -> str:
        payload = {
            "subject_id": subject_id,
            "model": embedding.model,
            "detector_backend": embedding.detector_backend,
            "embedding": list(embedding.vector),
        }
        encrypted = self.fernet.encrypt(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        return encrypted.decode("utf-8")

    def deserialize(self, encrypted_payload: str) -> FaceEmbedding:
        try:
            plaintext = self.fernet.decrypt(encrypted_payload.encode("utf-8"))
        except InvalidToken as exc:
            raise ValueError("Encrypted face embedding could not be decrypted") from exc
        payload = json.loads(plaintext.decode("utf-8"))
        return FaceEmbedding(
            vector=tuple(float(value) for value in payload["embedding"]),
            model=str(payload["model"]),
            detector_backend=str(payload["detector_backend"]),
        )


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("Face embeddings must have the same non-zero dimension")
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def _extract_antispoof_score(face: dict) -> float:
    for key in ("antispoof_score", "real_score", "confidence"):
        if key in face:
            try:
                return float(face[key])
            except (TypeError, ValueError):
                continue
    return 0.0


def _write_temp_image(image_bytes: bytes) -> Path:
    suffix = ".jpg"
    try:
        decoded = base64.b64decode(image_bytes, validate=True)
        if decoded:
            image_bytes = decoded
    except Exception:
        pass
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    with handle:
        handle.write(image_bytes)
    return Path(handle.name)
