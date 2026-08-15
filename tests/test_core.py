import asyncio

import httpx

from nagarik_api.blockchain.ipfs import IPFSClient
from nagarik_api.ai.document_ai import DocumentAI
from nagarik_api.ai.biometrics import BiometricResult, BiometricVerifier, FaceEmbedding, SecureEmbeddingStore
from nagarik_api.ai.fraud import FraudDetector
from nagarik_api.ai.fraud_model import FraudModelBundle
from nagarik_api.ai.model_loader import DocumentAIModels
from nagarik_api.services.eligibility_service import EligibilityEngine


class FakeOCR:
    def extract_text(self, data, filename):
        return "Certificate issued to Ananya Sen by Kolkata University on 2020-01-01", 0.94, "fake-ocr"


class FakeLanguage:
    def detect(self, text):
        return "en", 0.99


class FakeRegistry:
    def load(self):
        return DocumentAIModels(
            ocr=FakeOCR(),
            language=FakeLanguage(),
            classifier=lambda text, candidate_labels: {
                "labels": ["education_certificate"],
                "scores": [0.91],
            },
            entity_extractor=lambda text: [
                {"word": "Ananya Sen", "entity_group": "PER", "score": 0.98, "start": 22, "end": 32},
                {"word": "Kolkata University", "entity_group": "ORG", "score": 0.96, "start": 36, "end": 54},
            ],
            document_labels=("education_certificate", "unknown"),
        )


def test_document_ai_runs_ocr_language_transformer_pipeline():
    result = asyncio.run(DocumentAI(FakeRegistry()).process_document(b"image-bytes", "certificate.png"))
    assert result.document_type == "education_certificate"
    assert result.language == "en"
    assert result.confidence >= 0.8
    assert result.extracted_fields["entities"][0]["label"] == "PER"


def test_eligibility_engine_approves_scholarship():
    result = EligibilityEngine().decide("scholarship", {"student": True, "annual_income": 200000})
    assert result["decision"] == "auto_approve"
def test_eligibility_engine_rejects_ineligible_scholarship():
    result = EligibilityEngine().decide(
        "scholarship",
        {"student": True, "annual_income": 300000},
    )
    assert result["decision"] == "reject"

class FakeFraudModel:
    def predict_proba(self, matrix):
        return [[0.18, 0.82]]


class FakeExplainer:
    def __call__(self, matrix):
        class Values:
            values = [[0.01, 0.42, -0.02, 0.18, 0.04, 0.03, 0.11, 0.06, 0.25, 0.09, 0.05, 0.08, 0.14, 0.07, -0.01, 0.12]]

        return Values()


class FakeFraudRegistry:
    def load(self):
        return FraudModelBundle(
            model=FakeFraudModel(),
            metadata={"version": "test-model"},
            explainer=FakeExplainer(),
        )


def test_fraud_detector_uses_model_and_shap_explanations():
    result = FraudDetector(model_registry=FakeFraudRegistry()).score(
        {
            "duplicate_hash_count": 2,
            "officer_rejection_rate_30d": 0.7,
            "officer_rejection_rate_baseline": 0.2,
            "bribe_report_count": 1,
        }
    )
    assert result.risk_score == 0.82
    assert "SHAP contribution" in result.reasons[0]


class FakeFaceBackend:
    def __init__(self):
        self.calls = 0

    def create_embedding(self, image_bytes):
        self.calls += 1
        if self.calls == 1:
            return FaceEmbedding((0.10, 0.20, 0.30, 0.40), "Facenet512", "fake")
        return FaceEmbedding((0.11, 0.19, 0.31, 0.39), "Facenet512", "fake")

    def detect_liveness(self, image_bytes):
        return BiometricResult(True, 0.97, ["DeepFace anti-spoofing check marked image as real"])


def test_face_biometrics_encrypts_embedding_and_scores_similarity():
    verifier = BiometricVerifier(
        backend=FakeFaceBackend(),
        embedding_store=SecureEmbeddingStore.with_generated_key(),
        similarity_threshold=0.95,
    )
    encrypted = verifier.enroll_face(b"enrollment-image", "citizen-1")
    assert "0.10" not in encrypted
    result = verifier.verify_face(b"probe-image", encrypted)
    assert result.passed
    assert result.score >= 0.99
    assert "Cosine similarity" in result.reasons[1]


class FakeAsyncClient:
    def __init__(self, timeout):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        if url.endswith("/api/v0/add"):
            return httpx.Response(200, json={"Hash": "bafyrealcid"}, request=request)
        if url.endswith("/api/v0/pin/add"):
            return httpx.Response(200, json={"Pins": ["bafyrealcid"]}, request=request)
        if url.endswith("/api/v0/block/stat"):
            return httpx.Response(200, json={"Key": "bafyrealcid", "Size": 128}, request=request)
        if url.endswith("/api/v0/cat"):
            return httpx.Response(200, content=b"stored-document", request=request)
        raise AssertionError(url)


def test_ipfs_client_adds_pins_verifies_and_retrieves(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    async def run():
        client = IPFSClient("http://kubo:5001")
        cid = await client.add_bytes(b"document", "document.pdf")
        retrieved = await client.retrieve(cid)
        return cid, retrieved

    cid, retrieved = asyncio.run(run())
    assert cid == "bafyrealcid"
    assert retrieved == b"stored-document"
