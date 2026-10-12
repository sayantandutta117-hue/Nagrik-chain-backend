import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from nagarik_api import main
from nagarik_api.config import settings
from nagarik_api.db import Base, get_db
from nagarik_api.schemas import EligibilityRequest

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


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'audit.db'}")
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
    return TestClient(main.app)


def scholarship(**facts):
    return {"citizen_id": "CIT-1", "scheme": "scholarship", "facts": facts}


def post(client, payload):
    return client.post("/eligibility", json=payload, headers=API_HEADERS)


def test_valid_scholarship_application_still_returns_auto_approve(client):
    response = post(client, scholarship(student=True, annual_income=200000))
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "auto_approve"
    assert body["audit_event_id"]


def test_income_at_zero_boundary_is_accepted(client):
    response = post(client, scholarship(student=True, annual_income=0))
    assert response.status_code == 200
    assert response.json()["decision"] == "auto_approve"


def test_income_at_threshold_is_accepted_and_above_threshold_is_rejected(client):
    at_threshold = post(client, scholarship(student=True, annual_income=250000))
    assert at_threshold.status_code == 200
    assert at_threshold.json()["decision"] == "auto_approve"

    above_threshold = post(client, scholarship(student=True, annual_income=250001))
    assert above_threshold.status_code == 200
    assert above_threshold.json()["decision"] == "reject"


def test_student_false_is_accepted_and_results_in_rejection(client):
    response = post(client, scholarship(student=False, annual_income=200000))
    assert response.status_code == 200
    assert response.json()["decision"] == "reject"


def test_out_of_range_float_annual_income_is_rejected():
    with pytest.raises(ValidationError):
        EligibilityRequest.model_validate(scholarship(student=True, annual_income=float("inf")))


@pytest.mark.parametrize("income", [-1, -50000, -200000])
def test_negative_annual_income_is_rejected(client, income):
    response = post(client, scholarship(student=True, annual_income=income))
    assert response.status_code == 422
    assert "annual_income" in response.text


def test_non_numeric_annual_income_is_rejected(client):
    response = post(client, scholarship(student=True, annual_income="two lakh"))
    assert response.status_code == 422
    assert "annual_income" in response.text


def test_non_numeric_annual_income_does_not_cause_server_error(client):
    response = post(client, scholarship(student=True, annual_income=None))
    assert response.status_code == 422
    assert "internal" not in response.text.lower()


def test_boolean_annual_income_is_rejected(client):
    response = post(client, scholarship(student=True, annual_income=True))
    assert response.status_code == 422


@pytest.mark.parametrize("student", ["yes", 1, 0, None, [], {}])
def test_non_boolean_student_flag_is_rejected(client, student):
    response = post(client, scholarship(student=student, annual_income=100000))
    assert response.status_code == 422
    assert "student" in response.text


def test_missing_student_flag_is_rejected(client):
    response = post(client, scholarship(annual_income=200000))
    assert response.status_code == 422


def test_missing_annual_income_is_rejected(client):
    response = post(client, scholarship(student=True))
    assert response.status_code == 422


def test_empty_facts_for_scholarship_are_rejected(client):
    response = post(client, scholarship())
    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"scheme": "scholarship", "facts": {"student": True, "annual_income": 200000}},
        {"citizen_id": "CIT-1", "facts": {"student": True, "annual_income": 200000}},
        {"citizen_id": "CIT-1", "scheme": "scholarship"},
    ],
)
def test_missing_required_fields_are_rejected(client, payload):
    response = post(client, payload)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"citizen_id": "", "scheme": "scholarship", "facts": {"student": True, "annual_income": 100000}},
        {"citizen_id": "   ", "scheme": "scholarship", "facts": {"student": True, "annual_income": 100000}},
        {"citizen_id": "CIT-1", "scheme": "", "facts": {"student": True, "annual_income": 100000}},
        {"citizen_id": "CIT-1", "scheme": "   ", "facts": {"student": True, "annual_income": 100000}},
    ],
)
def test_blank_required_strings_are_rejected(client, payload):
    response = post(client, payload)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"citizen_id": "CIT-1", "scheme": "scholarship", "facts": ["student", True]},
        {"citizen_id": "CIT-1", "scheme": "scholarship", "facts": "student"},
        {"citizen_id": 42, "scheme": "scholarship", "facts": {"student": True, "annual_income": 100000}},
    ],
)
def test_malformed_payload_types_are_rejected(client, payload):
    response = post(client, payload)
    assert response.status_code == 422


def test_validation_error_response_is_structured_and_leaks_no_internals(client):
    response = post(client, scholarship(student=True, annual_income=-100))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert {"loc", "msg", "type"} <= set(detail[0])
    text = response.text
    for forbidden in ("Traceback", "File \"", "sqlalchemy", "nagarik_api", "EthereumError"):
        assert forbidden not in text


def test_non_scholarship_scheme_facts_are_not_blocked_by_scholarship_validation(client):
    pension = post(client, {"citizen_id": "CIT-1", "scheme": "pension", "facts": {"age": 65}})
    assert pension.status_code == 200
    assert pension.json()["decision"] == "auto_approve"

    crop = post(client, {"citizen_id": "CIT-1", "scheme": "crop_insurance", "facts": {"satellite_drought_index": 0.9}})
    assert crop.status_code == 200
    assert crop.json()["decision"] == "auto_approve"


def test_eligibility_request_accepts_valid_scholarship_payload():
    payload = EligibilityRequest.model_validate(scholarship(student=True, annual_income=150000))
    assert payload.citizen_id == "CIT-1"
    assert payload.scheme == "scholarship"
    assert payload.facts == {"student": True, "annual_income": 150000}


@pytest.mark.parametrize(
    "facts",
    [
        {"student": True, "annual_income": -1},
        {"student": True, "annual_income": "150000"},
        {"student": True, "annual_income": True},
        {"student": "true", "annual_income": 150000},
        {"annual_income": 150000},
        {"student": True},
    ],
)
def test_eligibility_request_model_rejects_invalid_scholarship_facts(facts):
    with pytest.raises(ValidationError):
        EligibilityRequest.model_validate(scholarship(**facts))


def test_eligibility_request_model_allows_missing_facts_for_other_schemes():
    payload = EligibilityRequest.model_validate({"citizen_id": "CIT-1", "scheme": "pension", "facts": {"age": 65}})
    assert payload.facts == {"age": 65}
