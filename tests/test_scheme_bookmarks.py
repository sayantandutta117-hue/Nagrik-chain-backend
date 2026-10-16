"""
Tests for the two newest features:

* Government Scheme Eligibility (``POST /schemes/eligibility``)
* Saved Schemes / Bookmarks (``POST /users/bookmarks``,
  ``GET /users/bookmarks``, ``DELETE /users/bookmarks/{scheme_id}``)

The harness mirrors ``tests/test_application_tracker.py``: an in-memory SQLite
database, the real FastAPI app with the ``get_db`` dependency overridden, and
fake blockchain gateways so blockchain anchoring never blocks a request.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from nagarik_api import main
from nagarik_api.config import settings
from nagarik_api.db import Base, get_db
from nagarik_api.models import Citizen, OfficerProfile

API_HEADERS = {"x-api-key": settings.api_key}

ALICE = {"x-citizen-id": "cit_alice"}
BOB = {"x-citizen-id": "cit_bob"}
OFFICER = {"x-officer-id": "OFF-1001"}


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


@dataclass
class Harness:
    client: TestClient
    session_factory: sessionmaker


@pytest.fixture
def harness(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'schemes.db'}")
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


def seed_all(harness):
    seed_citizen(harness.session_factory, "cit_alice", "CHIN-ALICE001")
    seed_citizen(harness.session_factory, "cit_bob", "CHIN-BOB0002")
    seed_officer(harness.session_factory, "OFF-1001")


def eligibility_payload(**overrides):
    payload = {
        "citizen_id": "cit_alice",
        "age": 65,
        "state": "CA",
        "occupation": "retired",
        "annual_income": 20000,
        "extra": {"death_registered": False},
    }
    payload.update(overrides)
    return payload


# ── Scheme eligibility ────────────────────────────────────────────────────────


def test_eligibility_preliminary_matches_pension_for_senior(harness):
    response = harness.client.post("/schemes/eligibility", json=eligibility_payload(), headers=API_HEADERS)
    assert response.status_code == 200
    body = response.json()

    assert "preliminary" in body["disclaimer"].lower()
    assert body["schemes_evaluated"] == 3

    pension = next(m for m in body["preliminary_matches"] if m["scheme_id"] == "pension")
    assert pension["name"] == "Old Age Pension"
    assert any("age" in r.lower() for r in pension["matched_reasons"])
    assert pension["disclaimer"]


def test_eligibility_scholarship_matches_student_with_low_income(harness):
    response = harness.client.post(
        "/schemes/eligibility",
        json=eligibility_payload(age=20, occupation="student", annual_income=20000, extra={"student": True}),
        headers=API_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()

    scholarship = next(m for m in body["preliminary_matches"] if m["scheme_id"] == "scholarship")
    assert scholarship["matched_reasons"]


def test_eligibility_scholarship_does_not_match_non_student(harness):
    response = harness.client.post(
        "/schemes/eligibility",
        json=eligibility_payload(age=20, occupation="engineer", annual_income=20000, extra={"student": False}),
        headers=API_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()

    scholarship = next(nm for nm in body["non_matches"] if nm["scheme_id"] == "scholarship")
    assert scholarship["not_matched_reasons"]


def test_eligibility_reports_non_matches_with_reasons(harness):
    # 20-year-old, high income, not a student, no drought signal: nothing matches.
    response = harness.client.post(
        "/schemes/eligibility",
        json=eligibility_payload(age=20, occupation="engineer", annual_income=500000, extra={"student": False}),
        headers=API_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()

    assert body["preliminary_matches"] == []
    non_match_ids = {nm["scheme_id"] for nm in body["non_matches"]}
    assert non_match_ids == {"scholarship", "pension", "crop_insurance"}
    for nm in body["non_matches"]:
        assert nm["not_matched_reasons"]


def test_eligibility_boundary_age_sixty_matches_pension(harness):
    response = harness.client.post(
        "/schemes/eligibility",
        json=eligibility_payload(age=60, extra={"death_registered": False}),
        headers=API_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    pension = next(m for m in body["preliminary_matches"] if m["scheme_id"] == "pension")
    assert pension["matched_reasons"]


def test_eligibility_age_just_below_threshold_is_narrow_miss(harness):
    response = harness.client.post(
        "/schemes/eligibility",
        json=eligibility_payload(age=59, extra={"death_registered": False}),
        headers=API_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    pension = next(nm for nm in body["non_matches"] if nm["scheme_id"] == "pension")
    assert any("age" in r.lower() for r in pension["not_matched_reasons"])


@pytest.mark.parametrize(
    "payload",
    [
        {"age": -1, "state": "CA", "occupation": "retired", "annual_income": 20000},
        {"citizen_id": "cit_alice", "state": "CA", "occupation": "retired", "annual_income": -100},
        {"citizen_id": "", "age": 65, "state": "CA", "occupation": "retired", "annual_income": 20000},
        {"citizen_id": "cit_alice", "age": 65, "state": "", "occupation": "retired", "annual_income": 20000},
        {"citizen_id": "cit_alice", "age": 65, "state": "CA", "occupation": "", "annual_income": 20000},
    ],
)
def test_eligibility_rejects_invalid_values(harness, payload):
    response = harness.client.post("/schemes/eligibility", json=eligibility_payload(**payload), headers=API_HEADERS)
    assert response.status_code == 422


def test_eligibility_missing_scheme_data_returns_service_unavailable(harness):
    from nagarik_api import main as main_module
    original = main_module.list_schemes
    main_module.list_schemes = lambda: ()
    try:
        response = harness.client.post("/schemes/eligibility", json=eligibility_payload(), headers=API_HEADERS)
        assert response.status_code == 503
        assert "no scheme eligibility data is configured" in response.json()["detail"].lower()
    finally:
        main_module.list_schemes = original


# ── Saved schemes / bookmarks ─────────────────────────────────────────────────


def test_bookmark_create_and_retrieve(harness):
    seed_all(harness)
    response = harness.client.post(
        "/users/bookmarks", json={"scheme_id": "scholarship"}, headers={**API_HEADERS, **ALICE}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["id"].startswith("save_")
    assert body["citizen_id"] == "cit_alice"
    assert body["scheme_id"] == "scholarship"

    listed = harness.client.get("/users/bookmarks", headers={**API_HEADERS, **ALICE})
    assert listed.status_code == 200
    listed_ids = [b["id"] for b in listed.json()]
    assert body["id"] in listed_ids


def test_bookmark_duplicate_prevented(harness):
    seed_all(harness)
    harness.client.post("/users/bookmarks", json={"scheme_id": "scholarship"}, headers={**API_HEADERS, **ALICE})
    duplicate = harness.client.post(
        "/users/bookmarks", json={"scheme_id": "scholarship"}, headers={**API_HEADERS, **ALICE}
    )
    assert duplicate.status_code == 409


def test_bookmark_delete_then_repeat_delete_is_404(harness):
    seed_all(harness)
    harness.client.post("/users/bookmarks", json={"scheme_id": "pension"}, headers={**API_HEADERS, **ALICE})

    removed = harness.client.delete("/users/bookmarks/pension", headers={**API_HEADERS, **ALICE})
    assert removed.status_code == 204

    again = harness.client.delete("/users/bookmarks/pension", headers={**API_HEADERS, **ALICE})
    assert again.status_code == 404


def test_bookmark_nonexistent_scheme_is_404(harness):
    seed_all(harness)
    response = harness.client.post(
        "/users/bookmarks", json={"scheme_id": "no_such_scheme"}, headers={**API_HEADERS, **ALICE}
    )
    assert response.status_code == 404

    removed = harness.client.delete("/users/bookmarks/no_such_scheme", headers={**API_HEADERS, **ALICE})
    assert removed.status_code == 404


def test_bookmark_requires_api_key_and_citizen_header(harness):
    seed_all(harness)
    no_key = harness.client.post("/users/bookmarks", json={"scheme_id": "scholarship"}, headers=ALICE)
    assert no_key.status_code == 401

    no_citizen = harness.client.post("/users/bookmarks", json={"scheme_id": "scholarship"}, headers=API_HEADERS)
    assert no_citizen.status_code == 401


def test_bookmark_unknown_citizen_is_403(harness):
    seed_all(harness)
    response = harness.client.post(
        "/users/bookmarks", json={"scheme_id": "scholarship"}, headers={**API_HEADERS, "x-citizen-id": "cit_ghost"}
    )
    assert response.status_code == 403


def test_bookmark_cross_citizen_isolation(harness):
    seed_all(harness)
    harness.client.post("/users/bookmarks", json={"scheme_id": "scholarship"}, headers={**API_HEADERS, **ALICE})

    # Bob cannot delete Alice's bookmark (it is not found for Bob's citizen_id).
    bob_delete = harness.client.delete("/users/bookmarks/scholarship", headers={**API_HEADERS, **BOB})
    assert bob_delete.status_code == 404

    # Bob's list is empty.
    bob_list = harness.client.get("/users/bookmarks", headers={**API_HEADERS, **BOB})
    assert bob_list.status_code == 200
    assert bob_list.json() == []


def test_officer_cannot_use_citizen_bookmark_endpoints(harness):
    seed_all(harness)
    as_officer = harness.client.post(
        "/users/bookmarks", json={"scheme_id": "scholarship"}, headers={**API_HEADERS, **OFFICER}
    )
    assert as_officer.status_code == 401  # officers lack x-citizen-id
