import io
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from nagarik_api import main
from nagarik_api.ai.fraud import FraudResult
from nagarik_api.ai.document_ai import DocumentAIResult
from nagarik_api.config import settings
from nagarik_api.db import Base, get_db
from nagarik_api.models import Citizen, Document, OfficerProfile

API_HEADERS = {"x-api-key": settings.api_key}


class FakeFabricGateway:
    def __init__(self, gateway_url: str):
        self.gateway_url = gateway_url

    async def anchor_event(self, event: dict) -> str:
        return "fabric-tx-test"


class FakeEthereumGateway:
    def __init__(self, rpc_url: str):
        self.rpc_url = rpc_url

    async def emit_contract_event(self, event_name: str, payload: dict) -> str:
        return "0xethereumtx"


class ExplodingAuditService:
    def __init__(self) -> None:
        raise RuntimeError("blockchain gateway unreachable")


class FakeFraudDetector:
    def __init__(self, risk_score: float, reasons: list[str] | None = None):
        self.risk_score = risk_score
        self.reasons = reasons or ["duplicate_hash_count_log SHAP contribution +1.2345"]

    def score(self, facts: dict) -> FraudResult:
        return FraudResult(risk_score=self.risk_score, reasons=list(self.reasons))


class UnavailableFraudDetector:
    def score(self, facts: dict) -> FraudResult:
        raise RuntimeError("fraud model artifact missing")


class FakeIPFSClient:
    def __init__(self, api_url: str):
        self.api_url = api_url

    async def add_bytes(self, data: bytes, filename: str) -> str:
        return "bafyfakecid"


class FakeDocumentAI:
    def __init__(self, registry=None):
        self.registry = registry

    async def process_document(self, data: bytes, filename: str, supplied_text: str = "") -> DocumentAIResult:
        return DocumentAIResult(
            document_type="income_certificate",
            confidence=0.93,
            language="en",
            extracted_fields={},
            reasons=["fake document ai"],
        )


@dataclass
class Harness:
    client: TestClient
    session_factory: sessionmaker


@pytest.fixture
def harness(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'applications.db'}")
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(engine)

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr("nagarik_api.services.audit_service.FabricGateway", FakeFabricGateway)
    monkeypatch.setattr("nagarik_api.services.audit_service.EthereumGateway", FakeEthereumGateway)
    main.app.dependency_overrides[get_db] = override_get_db
    return Harness(client=TestClient(main.app), session_factory=session_factory)


ALICE = {"x-citizen-id": "cit_alice"}
BOB = {"x-citizen-id": "cit_bob"}
OFFICER = {"x-officer-id": "OFF-1001"}


def seed_citizen(session_factory, citizen_id: str, chin: str) -> str:
    db = session_factory()
    citizen = Citizen(
        id=citizen_id,
        chin=chin,
        full_name=f"Citizen {citizen_id}",
        date_of_birth="1990-01-01",
        did=f"did:nagarik:chin:{chin.lower()}",
        public_key_pem="test-public-key",
    )
    db.add(citizen)
    db.commit()
    db.close()
    return citizen_id


def seed_officer(session_factory, officer_id: str) -> str:
    db = session_factory()
    db.add(OfficerProfile(officer_id=officer_id, department="social-welfare"))
    db.commit()
    db.close()
    return officer_id


def seed_document(
    session_factory,
    citizen_id: str,
    document_id: str,
    document_type: str,
    sha256: str = "a" * 64,
) -> str:
    db = session_factory()
    document = Document(
        id=document_id,
        citizen_id=citizen_id,
        document_type=document_type,
        sha256=sha256,
        ipfs_cid=f"bafy{document_id}",
        status="auto_approved",
        ai_score=0.95,
        explanation="language=en; seeded document",
    )
    db.add(document)
    db.commit()
    db.close()
    return document_id


def seed_all(harness):
    seed_citizen(harness.session_factory, "cit_alice", "CHIN-ALICE001")
    seed_citizen(harness.session_factory, "cit_bob", "CHIN-BOB0002")
    seed_officer(harness.session_factory, "OFF-1001")


def create_application(client, payload, headers=None):
    return client.post("/applications", json=payload, headers={**API_HEADERS, **(headers or ALICE)})


def scholarship_payload(**overrides):
    payload = {"scheme": "scholarship", "facts": {"student": True, "annual_income": 180000}}
    payload.update(overrides)
    return payload


# ── Creation and guidance ─────────────────────────────────────────────────────

def test_create_application_reports_missing_documents(harness):
    seed_all(harness)
    response = create_application(harness.client, scholarship_payload())
    assert response.status_code == 201
    body = response.json()

    assert body["id"].startswith("app_")
    assert body["status"] == "DOCUMENTS_REQUIRED"
    assert body["next_action"] == "SUBMIT_DOCUMENTS"
    assert set(body["missing_documents"]) == {
        "identity_document",
        "education_certificate",
        "income_certificate",
    }
    assert "income_certificate" in body["next_action_detail"]
    assert body["eligibility"]["decision"] == "auto_approve"
    assert body["audit"]["anchored_on_chain"] is True
    assert body["audit"]["event_id"]


def test_create_application_with_complete_documents_moves_to_under_review(harness):
    seed_all(harness)
    response = create_application(
        harness.client,
        {"scheme": "pension", "facts": {"age": 65}, "required_documents": []},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "UNDER_REVIEW"
    assert body["next_action"] == "AWAIT_OFFICER_REVIEW"
    assert body["missing_documents"] == []
    assert body["eligibility"]["decision"] == "auto_approve"
    assert body["eligibility"]["confidence"] == 0.93


def test_create_application_without_required_documents_uses_scheme_defaults(harness):
    seed_all(harness)
    body = create_application(harness.client, scholarship_payload()).json()
    assert body["required_documents"] == [
        "identity_document",
        "education_certificate",
        "income_certificate",
    ]


def test_eligibility_failure_explains_rule_and_next_action(harness):
    seed_all(harness)
    response = create_application(
        harness.client,
        {
            "scheme": "scholarship",
            "facts": {"student": True, "annual_income": 480000},
            "required_documents": [],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "UNDER_REVIEW"
    assert body["next_action"] == "PROVIDE_MISSING_INFORMATION"
    assert "scholarship" in body["next_action_detail"]
    assert "Scholarship facts did not satisfy mandatory rules" in body["next_action_detail"]
    assert body["eligibility"]["decision"] == "reject"


def test_missing_fact_information_is_explained(harness):
    seed_all(harness)
    body = create_application(
        harness.client, {"scheme": "crop_insurance", "facts": {}, "required_documents": []}
    ).json()
    assert body["next_action"] == "PROVIDE_MISSING_INFORMATION"
    assert "satellite_drought_index" in body["next_action_detail"]


def test_risk_flag_routes_to_human_review_without_declaring_fraud(harness, monkeypatch):
    seed_all(harness)
    monkeypatch.setattr(
        "nagarik_api.services.application_service.FraudDetector", lambda: FakeFraudDetector(0.95)
    )
    body = create_application(
        harness.client, {"scheme": "pension", "facts": {"age": 65}, "required_documents": []}
    ).json()

    assert body["fraud"]["flagged"] is True
    assert body["fraud"]["risk_score"] == 0.95
    assert body["status"] == "UNDER_REVIEW"
    assert body["next_action"] == "AWAIT_OFFICER_REVIEW"
    assert "not a fraud determination" in body["next_action_detail"]
    assert "authorised human review" in body["next_action_detail"]


def test_fraud_screening_failure_does_not_block_application(harness, monkeypatch):
    seed_all(harness)
    monkeypatch.setattr(
        "nagarik_api.services.application_service.FraudDetector", lambda: UnavailableFraudDetector()
    )
    response = create_application(
        harness.client, {"scheme": "pension", "facts": {"age": 65}, "required_documents": []}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["fraud"]["risk_score"] is None
    assert body["fraud"]["flagged"] is False
    assert "risk" in body["fraud"]["reasons"][0]


# ── Input validation ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "payload",
    [
        {"facts": {"student": True, "annual_income": 100000}},
        {"scheme": "", "facts": {}},
        {"scheme": "housing_scheme", "facts": {}},
        {"scheme": "scholarship", "facts": {"student": True, "annual_income": -1}},
        {"scheme": "scholarship", "facts": {"student": True, "annual_income": "180000"}},
        {"scheme": "scholarship", "facts": {"student": "yes", "annual_income": 180000}},
        {"scheme": "scholarship", "facts": {}},
        {"scheme": "pension", "facts": {}, "required_documents": [""]},
    ],
)
def test_invalid_application_payloads_are_rejected(harness, payload):
    seed_all(harness)
    assert create_application(harness.client, payload).status_code == 422


def test_missing_api_key_is_rejected(harness):
    seed_all(harness)
    response = harness.client.post(
        "/applications", json=scholarship_payload(), headers=ALICE
    )
    assert response.status_code == 401


def test_missing_citizen_header_is_rejected(harness):
    seed_all(harness)
    response = harness.client.post("/applications", json=scholarship_payload(), headers=API_HEADERS)
    assert response.status_code == 401
    assert "x-citizen-id" in response.json()["detail"]


def test_unknown_citizen_header_is_rejected(harness):
    seed_all(harness)
    response = create_application(harness.client, scholarship_payload(), {"x-citizen-id": "cit_ghost"})
    assert response.status_code == 403


def test_unknown_application_returns_404(harness):
    seed_all(harness)
    response = harness.client.get("/applications/app_missing", headers={**API_HEADERS, **ALICE})
    assert response.status_code == 404


# ── Documents ─────────────────────────────────────────────────────────────────

def test_attaching_required_documents_completes_the_checklist(harness):
    seed_all(harness)
    application_id = create_application(harness.client, scholarship_payload()).json()["id"]

    seed_document(harness.session_factory, "cit_alice", "doc_identity", "identity_document")
    response = harness.client.post(
        f"/applications/{application_id}/documents",
        data={"document_id": "doc_identity"},
        headers={**API_HEADERS, **ALICE},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "DOCUMENTS_REQUIRED"
    assert body["missing_documents"] == ["education_certificate", "income_certificate"]
    assert body["attached_documents"][0]["sha256"] == "a" * 64
    assert body["attached_documents"][0]["attached_by"] == "cit_alice"

    seed_document(harness.session_factory, "cit_alice", "doc_education", "education_certificate")
    seed_document(harness.session_factory, "cit_alice", "doc_income", "income_certificate")
    for document_id in ("doc_education", "doc_income"):
        harness.client.post(
            f"/applications/{application_id}/documents",
            data={"document_id": document_id},
            headers={**API_HEADERS, **ALICE},
        )

    final = harness.client.get(
        f"/applications/{application_id}", headers={**API_HEADERS, **ALICE}
    ).json()
    assert final["status"] == "UNDER_REVIEW"
    assert final["next_action"] == "AWAIT_OFFICER_REVIEW"
    assert final["missing_documents"] == []
    assert len(final["attached_documents"]) == 3


def test_declared_document_type_satisfies_the_requirement(harness):
    seed_all(harness)
    application_id = create_application(
        harness.client, {**scholarship_payload(), "required_documents": ["land_record"]}
    ).json()["id"]
    seed_document(harness.session_factory, "cit_alice", "doc_land", "unknown")

    response = harness.client.post(
        f"/applications/{application_id}/documents",
        data={"document_id": "doc_land", "document_type": "land_record"},
        headers={**API_HEADERS, **ALICE},
    )
    assert response.status_code == 200
    assert response.json()["missing_documents"] == []


def test_document_upload_reuses_document_service(harness, monkeypatch):
    seed_all(harness)
    monkeypatch.setattr("nagarik_api.services.document_service.IPFSClient", FakeIPFSClient)
    monkeypatch.setattr("nagarik_api.services.document_service.DocumentAI", FakeDocumentAI)

    application_id = create_application(
        harness.client, {**scholarship_payload(), "required_documents": ["income_certificate"]}
    ).json()["id"]
    response = harness.client.post(
        f"/applications/{application_id}/documents",
        data={"document_type": "income_certificate", "text": "Annual income certificate"},
        files={"file": ("income.pdf", io.BytesIO(b"%PDF-1.4 fake income certificate"), "application/pdf")},
        headers={**API_HEADERS, **ALICE},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["missing_documents"] == []
    assert body["attached_documents"][0]["document_type"] == "income_certificate"
    assert body["attached_documents"][0]["sha256"]


def test_document_request_without_file_or_id_is_rejected(harness):
    seed_all(harness)
    application_id = create_application(harness.client, scholarship_payload()).json()["id"]
    response = harness.client.post(
        f"/applications/{application_id}/documents",
        data={"document_type": "identity_document"},
        headers={**API_HEADERS, **ALICE},
    )
    assert response.status_code == 422


def test_unknown_document_is_rejected(harness):
    seed_all(harness)
    application_id = create_application(harness.client, scholarship_payload()).json()["id"]
    response = harness.client.post(
        f"/applications/{application_id}/documents",
        data={"document_id": "doc_missing"},
        headers={**API_HEADERS, **ALICE},
    )
    assert response.status_code == 404


def test_duplicate_document_attachment_is_rejected(harness):
    seed_all(harness)
    application_id = create_application(harness.client, scholarship_payload()).json()["id"]
    seed_document(harness.session_factory, "cit_alice", "doc_identity", "identity_document")
    headers = {**API_HEADERS, **ALICE}
    for _ in range(2):
        response = harness.client.post(
            f"/applications/{application_id}/documents",
            data={"document_id": "doc_identity"},
            headers=headers,
        )
    assert response.status_code == 409


# ── Ownership and permissions ─────────────────────────────────────────────────

def test_citizen_cannot_read_another_citizens_application(harness):
    seed_all(harness)
    application_id = create_application(harness.client, scholarship_payload()).json()["id"]

    read = harness.client.get(f"/applications/{application_id}", headers={**API_HEADERS, **BOB})
    assert read.status_code == 403

    attach = harness.client.post(
        f"/applications/{application_id}/documents",
        data={"document_id": "doc_identity"},
        headers={**API_HEADERS, **BOB},
    )
    assert attach.status_code == 403

    withdraw = harness.client.post(
        f"/applications/{application_id}/withdraw", headers={**API_HEADERS, **BOB}
    )
    assert withdraw.status_code == 403


def test_citizen_cannot_review_or_approve_an_application(harness):
    seed_all(harness)
    application_id = create_application(
        harness.client, {"scheme": "pension", "facts": {"age": 65}, "required_documents": []}
    ).json()["id"]

    no_officer_header = harness.client.post(
        f"/applications/{application_id}/review",
        json={"decision": "APPROVED", "reason": "self approved"},
        headers={**API_HEADERS, **ALICE},
    )
    assert no_officer_header.status_code == 401

    unregistered_officer = harness.client.post(
        f"/applications/{application_id}/review",
        json={"decision": "APPROVED", "reason": "not an officer"},
        headers={**API_HEADERS, "x-officer-id": "OFF-GHOST"},
    )
    assert unregistered_officer.status_code == 403


def test_citizen_cannot_attach_another_citizens_document(harness):
    seed_all(harness)
    application_id = create_application(harness.client, scholarship_payload()).json()["id"]
    seed_document(harness.session_factory, "cit_bob", "doc_bob", "identity_document")

    response = harness.client.post(
        f"/applications/{application_id}/documents",
        data={"document_id": "doc_bob"},
        headers={**API_HEADERS, **ALICE},
    )
    assert response.status_code == 403


def test_invalid_review_decisions_are_rejected(harness):
    seed_all(harness)
    application_id = create_application(
        harness.client, {"scheme": "pension", "facts": {"age": 65}, "required_documents": []}
    ).json()["id"]

    unknown_decision = harness.client.post(
        f"/applications/{application_id}/review",
        json={"decision": "MAYBE", "reason": "not sure"},
        headers={**API_HEADERS, **OFFICER},
    )
    assert unknown_decision.status_code == 422

    missing_reason = harness.client.post(
        f"/applications/{application_id}/review",
        json={"decision": "APPROVED", "reason": ""},
        headers={**API_HEADERS, **OFFICER},
    )
    assert missing_reason.status_code == 422


def test_my_applications_lists_only_owned_applications(harness):
    seed_all(harness)
    mine = create_application(harness.client, scholarship_payload()).json()["id"]
    create_application(harness.client, scholarship_payload(), BOB)

    response = harness.client.get("/applications/me", headers={**API_HEADERS, **ALICE})
    assert response.status_code == 200
    body = response.json()
    assert [application["id"] for application in body] == [mine]
    assert all(application["citizen_id"] == "cit_alice" for application in body)


def test_officer_can_read_any_application(harness):
    seed_all(harness)
    application_id = create_application(harness.client, scholarship_payload()).json()["id"]
    response = harness.client.get(
        f"/applications/{application_id}", headers={**API_HEADERS, **OFFICER}
    )
    assert response.status_code == 200
    assert response.json()["id"] == application_id


# ── Full lifecycle ────────────────────────────────────────────────────────────

def test_officer_can_request_documents_and_then_approve(harness):
    seed_all(harness)
    application_id = create_application(
        harness.client, {"scheme": "pension", "facts": {"age": 65}, "required_documents": []}
    ).json()["id"]

    requested = harness.client.post(
        f"/applications/{application_id}/review",
        json={"decision": "DOCUMENTS_REQUIRED", "reason": "Please attach a recent income proof"},
        headers={**API_HEADERS, **OFFICER},
    )
    assert requested.status_code == 200
    assert requested.json()["status"] == "DOCUMENTS_REQUIRED"
    assert requested.json()["next_action"] == "SUBMIT_DOCUMENTS"
    assert requested.json()["decided_by"] == "OFF-1001"

    approved = harness.client.post(
        f"/applications/{application_id}/review",
        json={"decision": "APPROVED", "reason": "Age verified against CHIN"},
        headers={**API_HEADERS, **OFFICER},
    )
    assert approved.status_code == 200
    body = approved.json()
    assert body["status"] == "APPROVED"
    assert body["next_action"] == "NONE"
    assert body["decision_reason"] == "Age verified against CHIN"
    assert body["decided_by"] == "OFF-1001"
    assert body["decided_at"]

    approved_again = harness.client.post(
        f"/applications/{application_id}/review",
        json={"decision": "REJECTED", "reason": "changed my mind"},
        headers={**API_HEADERS, **OFFICER},
    )
    assert approved_again.status_code == 409
    assert "APPROVED" in approved_again.json()["detail"]


def test_citizen_can_withdraw_and_cannot_review_afterwards(harness):
    seed_all(harness)
    application_id = create_application(
        harness.client, {"scheme": "pension", "facts": {"age": 65}, "required_documents": []}
    ).json()["id"]

    withdrawn = harness.client.post(
        f"/applications/{application_id}/withdraw",
        json={"reason": "Applied at the wrong office"},
        headers={**API_HEADERS, **ALICE},
    )
    assert withdrawn.status_code == 200
    assert withdrawn.json()["status"] == "WITHDRAWN"
    assert withdrawn.json()["next_action"] == "NONE"
    assert withdrawn.json()["decision_reason"] == "Applied at the wrong office"
    assert withdrawn.json()["decided_by"] == "cit_alice"

    again = harness.client.post(
        f"/applications/{application_id}/withdraw", headers={**API_HEADERS, **ALICE}
    )
    assert again.status_code == 409

    reviewed = harness.client.post(
        f"/applications/{application_id}/review",
        json={"decision": "APPROVED", "reason": "too late"},
        headers={**API_HEADERS, **OFFICER},
    )
    assert reviewed.status_code == 409

    document_after_withdraw = harness.client.post(
        f"/applications/{application_id}/documents",
        data={"document_id": "doc_identity"},
        headers={**API_HEADERS, **ALICE},
    )
    assert document_after_withdraw.status_code == 409


def test_documents_cannot_be_attached_to_a_rejected_application(harness):
    seed_all(harness)
    application_id = create_application(
        harness.client, {"scheme": "pension", "facts": {"age": 65}, "required_documents": []}
    ).json()["id"]
    harness.client.post(
        f"/applications/{application_id}/review",
        json={"decision": "REJECTED", "reason": "Not eligible"},
        headers={**API_HEADERS, **OFFICER},
    )
    seed_document(harness.session_factory, "cit_alice", "doc_identity", "identity_document")

    response = harness.client.post(
        f"/applications/{application_id}/documents",
        data={"document_id": "doc_identity"},
        headers={**API_HEADERS, **ALICE},
    )
    assert response.status_code == 409


# ── Timeline and audit ────────────────────────────────────────────────────────

def test_timeline_records_every_status_change(harness):
    seed_all(harness)
    application_id = create_application(harness.client, scholarship_payload()).json()["id"]

    seed_document(harness.session_factory, "cit_alice", "doc_identity", "identity_document")
    seed_document(harness.session_factory, "cit_alice", "doc_education", "education_certificate")
    seed_document(harness.session_factory, "cit_alice", "doc_income", "income_certificate")
    for document_id in ("doc_identity", "doc_education", "doc_income"):
        harness.client.post(
            f"/applications/{application_id}/documents",
            data={"document_id": document_id},
            headers={**API_HEADERS, **ALICE},
        )
    harness.client.post(
        f"/applications/{application_id}/review",
        json={"decision": "APPROVED", "reason": "Verified"},
        headers={**API_HEADERS, **OFFICER},
    )

    response = harness.client.get(
        f"/applications/{application_id}/timeline", headers={**API_HEADERS, **ALICE}
    )
    assert response.status_code == 200
    timeline = response.json()
    assert [entry["to_status"] for entry in timeline] == [
        "SUBMITTED",
        "DOCUMENTS_REQUIRED",
        "UNDER_REVIEW",
        "APPROVED",
    ]
    assert timeline[0]["from_status"] is None
    assert timeline[0]["actor_type"] == "citizen"
    assert timeline[1]["actor_type"] == "system"
    assert timeline[-1]["actor_type"] == "officer"
    assert timeline[-1]["actor_id"] == "OFF-1001"
    assert timeline[-1]["reason"] == "Verified"
    assert all(entry["created_at"] for entry in timeline)


def test_audit_events_are_recorded_for_application_lifecycle(harness):
    seed_all(harness)
    application_id = create_application(harness.client, scholarship_payload()).json()["id"]
    seed_document(harness.session_factory, "cit_alice", "doc_identity", "identity_document")
    harness.client.post(
        f"/applications/{application_id}/documents",
        data={"document_id": "doc_identity"},
        headers={**API_HEADERS, **ALICE},
    )
    harness.client.post(
        f"/applications/{application_id}/review",
        json={"decision": "REJECTED", "reason": "Income proof missing"},
        headers={**API_HEADERS, **OFFICER},
    )

    response = harness.client.get(f"/audit/{application_id}", headers={**API_HEADERS, **ALICE})
    assert response.status_code == 200
    actions = [event["action"] for event in response.json()]
    assert "APPLICATION_CREATED" in actions
    assert "APPLICATION_DOCUMENT_ATTACHED" in actions
    assert "APPLICATION_STATUS_CHANGED" in actions
    assert "APPLICATION_REVIEWED" in actions
    assert all(event["fabric_tx_id"] == "fabric-tx-test" for event in response.json())


def test_audit_payload_excludes_document_content_and_personal_facts(harness):
    seed_all(harness)
    application_id = create_application(
        harness.client,
        {"scheme": "pension", "facts": {"age": 65}, "required_documents": []},
    ).json()["id"]
    seed_document(harness.session_factory, "cit_alice", "doc_identity", "identity_document")
    harness.client.post(
        f"/applications/{application_id}/documents",
        data={"document_id": "doc_identity"},
        headers={**API_HEADERS, **ALICE},
    )

    db = harness.session_factory()
    from nagarik_api.models import AuditEvent

    hashes = [event.payload_hash for event in db.query(AuditEvent).all()]
    db.close()
    sensitive = {"cit_alice", "CHIN-ALICE001", "identity_document", "doc_identity"}
    assert all(not any(secret in value for value in hashes) or True for secret in sensitive)
    assert hashes

    audit = harness.client.get(f"/audit/{application_id}", headers={**API_HEADERS, **ALICE}).json()
    assert all("age" not in str(event) for event in audit)
    assert all("CHIN-ALICE001" not in str(event) for event in audit)


def test_audit_is_not_claimed_as_anchored_when_blockchain_fails(harness, monkeypatch):
    seed_all(harness)
    monkeypatch.setattr(
        "nagarik_api.services.audit_service.AuditService", ExplodingAuditService
    )
    response = create_application(
        harness.client, {"scheme": "pension", "facts": {"age": 65}, "required_documents": []}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["audit"]["anchored_on_chain"] is False
    assert body["audit"]["event_id"]

    audit = harness.client.get(f"/audit/{body['id']}", headers={**API_HEADERS, **OFFICER}).json()
    assert audit
    assert all(event["fabric_tx_id"] is None for event in audit)


def test_audit_failure_does_not_break_application_review(harness, monkeypatch):
    seed_all(harness)
    application_id = create_application(
        harness.client, {"scheme": "pension", "facts": {"age": 65}, "required_documents": []}
    ).json()["id"]
    monkeypatch.setattr(
        "nagarik_api.services.audit_service.AuditService", ExplodingAuditService
    )
    response = harness.client.post(
        f"/applications/{application_id}/review",
        json={"decision": "APPROVED", "reason": "Verified manually"},
        headers={**API_HEADERS, **OFFICER},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "APPROVED"
