"""Authenticated medical-review workspace integration tests."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

from fastapi.testclient import TestClient

from medical_claims.auth import hash_password
from medical_claims.models import ClaimCase
from medical_claims.storage import CaseStorage
from medical_claims.workflow_models import CreateCaseRequest
from web.backend.app import _auth_sessions, app


PASSWORD = "safe-password-123"


def _user(storage: CaseStorage, username: str, role: str) -> str:
    return storage.create_user(
        username=username,
        password_hash=hash_password(PASSWORD),
        role_code=role,
        display_name=f"{username}-{role}",
    )


def _login(client: TestClient, username: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text


def _case(storage: CaseStorage, event_id: str, owner_id: str, *, compliant: bool = True):
    return storage.create_case(
        CreateCaseRequest(
            claim_event_id=event_id,
            data_source="synthetic_test",
            consent_status="authorized" if compliant else "pending",
            deidentification_status="confirmed" if compliant else "pending",
        ),
        owner_user_id=owner_id,
    )


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "medical-portal.db"))
    _auth_sessions.clear()
    storage = CaseStorage()
    owner_id = _user(storage, "claim-owner", "CLAIM")
    medical_a = _user(storage, "medical-a", "MEDICAL")
    medical_b = _user(storage, "medical-b", "MEDICAL")
    customer = _user(storage, "customer-a", "CUSTOMER")
    case_a = _case(storage, "MEDICAL-A", owner_id)
    case_b = _case(storage, "MEDICAL-B", owner_id)
    blocked = _case(storage, "MEDICAL-BLOCKED", owner_id, compliant=False)
    storage.assign_case_participant(case_a.case_id, customer, "CUSTOMER")
    storage.assign_case_participant(case_b.case_id, medical_b, "MEDICAL_REVIEWER")
    storage.assign_case_participant(blocked.case_id, medical_a, "MEDICAL_REVIEWER")
    for workflow_case in (case_a, case_b, blocked):
        storage.save_ocr(workflow_case.case_id, [{
            "document_id": "synthetic-page.png",
            "page": 1,
            "blocks": [],
            "full_text": "合成测试材料，不含真实人员或医疗数据。",
            "human_corrected": True,
        }])
        storage.save_structured(workflow_case.case_id, ClaimCase(case_id=workflow_case.case_id))
    return storage, case_a, case_b, blocked


def test_claim_assigns_medical_reviewer_and_case_scope_is_enforced(tmp_path, monkeypatch):
    _, case_a, case_b, _ = _setup(tmp_path, monkeypatch)
    client = TestClient(app)
    _login(client, "claim-owner")
    assigned = client.post(
        f"/api/claim/cases/{case_a.case_id}/medical-reviewer",
        json={"username": "medical-a"},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["reviewer"]["display_name"] == "medical-a-MEDICAL"
    assert client.post(
        f"/api/claim/cases/{case_a.case_id}/medical-reviewer",
        json={"username": "customer-a"},
    ).status_code == 404
    client.post("/api/auth/logout")

    _login(client, "medical-a")
    listing = client.get("/api/medical/cases")
    assert listing.status_code == 200
    listed_ids = {item["case_id"] for item in listing.json()}
    assert case_a.case_id in listed_ids
    assert case_b.case_id not in listed_ids
    assert client.get(f"/api/medical/cases/{case_b.case_id}").status_code == 404


def test_medical_detail_hides_content_until_compliance_is_ready(tmp_path, monkeypatch):
    _, _, _, blocked = _setup(tmp_path, monkeypatch)
    client = TestClient(app)
    _login(client, "medical-a")
    response = client.get(f"/api/medical/cases/{blocked.case_id}")
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["compliance_ready"] is False
    for field in ("ocr_result", "structured_result", "pain_result", "care_result"):
        assert detail[field] is None
    assert client.post(
        f"/api/medical/cases/{blocked.case_id}/pain/analyze", json={"use_llm": False},
    ).status_code == 409


def test_medical_q1_q2_q3_flow_and_downstream_invalidation(tmp_path, monkeypatch):
    storage, case_a, _, _ = _setup(tmp_path, monkeypatch)
    storage.assign_medical_reviewer(case_a.case_id, "medical-a")
    client = TestClient(app)
    _login(client, "medical-a")

    detail = client.get(f"/api/medical/cases/{case_a.case_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["compliance_ready"] is True
    assert detail.json()["ocr_result"][0]["human_corrected"] is True

    pain = client.post(
        f"/api/medical/cases/{case_a.case_id}/pain/analyze", json={"use_llm": False},
    )
    assert pain.status_code == 200, pain.text
    assert pain.json()["case_id"] == case_a.case_id

    care = client.post(
        f"/api/medical/cases/{case_a.case_id}/care/generate",
        json={"use_llm": False, "disease_topic": "康复关怀"},
    )
    assert care.status_code == 200, care.text
    assert care.json()["case_id"] == case_a.case_id
    assert storage.get_results(case_a.case_id)["pain_result"] is not None
    assert storage.get_results(case_a.case_id)["care_result"] is not None

    monkeypatch.setattr("web.backend.app._discover_mobile_preview_host", lambda: "192.168.1.20")
    preview = client.post(
        f"/api/medical/cases/{case_a.case_id}/mobile-preview",
        json={"expires_in_minutes": 30},
    )
    assert preview.status_code == 201, preview.text
    token = parse_qs(preview.json()["preview_url"].split("#", 1)[1])["preview_token"][0]
    publication = client.get("/api/mobile-preview", headers={"X-Preview-Token": token})
    assert publication.status_code == 200, publication.text
    serialized = publication.text
    assert case_a.case_id not in serialized
    assert publication.json()["delivery"]["content"]["care_guide"]["guide"]["disease_topic"] == "康复关怀"

    corrected = ClaimCase(case_id=case_a.case_id).model_dump(mode="json")
    saved = client.patch(f"/api/medical/cases/{case_a.case_id}/structured", json=corrected)
    assert saved.status_code == 200, saved.text
    results = storage.get_results(case_a.case_id)
    assert results["pain_result"] is None
    assert results["care_result"] is None


def test_medical_workspace_uses_shared_login_and_assets(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "medical-page.db"))
    client = TestClient(app)
    assert "医疗复核工作台" in client.get("/medical").text
    assert client.get("/medical.js").status_code == 200
    login_source = Path("web/dist/login.js").read_text(encoding="utf-8")
    medical_source = Path("web/dist/medical.js").read_text(encoding="utf-8")
    assert "MEDICAL:'/medical'" in login_source
    assert 'credentials: "same-origin"' in medical_source
    assert "localStorage" not in medical_source


def test_non_medical_role_cannot_use_medical_api(tmp_path, monkeypatch):
    _, case_a, _, _ = _setup(tmp_path, monkeypatch)
    client = TestClient(app)
    _login(client, "claim-owner")
    assert client.get("/api/medical/dashboard").status_code == 403
    assert client.get(f"/api/medical/cases/{case_a.case_id}").status_code == 403
