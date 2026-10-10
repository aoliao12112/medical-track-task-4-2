"""Authentication, CLAIM workspace and case-scope regression tests."""
from __future__ import annotations

import json
import importlib
from pathlib import Path

from fastapi.testclient import TestClient

from medical_claims.auth import hash_password, verify_password
from medical_claims.models import ClaimCase
from medical_claims.storage import CaseStorage
from medical_claims.workflow_models import CreateCaseRequest
from web.backend.app import app, _auth_sessions


def _create_claim_user(storage, username):
    return storage.create_user(
        username=username,
        password_hash=hash_password("safe-password-123"),
        role_code="CLAIM",
        display_name=f"{username}理赔员",
    )


def _login(client, username, password="safe-password-123"):
    response = client.post("/api/auth/login", json={
        "username": username,
        "password": password,
    })
    assert response.status_code == 200, response.text
    return response


def _case(storage, claim_event_id, owner_user_id):
    return storage.create_case(CreateCaseRequest(
        claim_event_id=claim_event_id,
        data_source="user_upload",
        consent_status="pending",
        deidentification_status="pending",
    ), owner_user_id=owner_user_id)


def test_password_hash_is_salted_and_verifiable():
    first = hash_password("safe-password-123")
    second = hash_password("safe-password-123")
    assert first != second
    assert verify_password("safe-password-123", first)
    assert not verify_password("wrong-password", first)
    assert "safe-password-123" not in first


def test_login_me_logout_cookie_session(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "competition.db"))
    _auth_sessions.clear()
    storage = CaseStorage()
    _create_claim_user(storage, "claim001")
    client = TestClient(app)
    assert client.get("/api/auth/me").status_code == 401
    response = _login(client, "claim001")
    assert response.json()["role_code"] == "CLAIM"
    assert "CASE_VIEW" in response.json()["permissions"]
    assert "medical_claims_session=" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    me = client.get("/api/auth/me").json()
    assert me["username"] == "claim001"
    assert "password_hash" not in me
    assert client.post("/api/auth/logout").json() == {"logged_out": True}
    assert client.get("/api/auth/me").status_code == 401


def test_own_profile_update_requires_login_and_writes_users_table(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "competition.db"))
    _auth_sessions.clear()
    storage = CaseStorage()
    user_id = _create_claim_user(storage, "claim-profile")
    client = TestClient(app)
    assert client.patch("/api/auth/me", json={"display_name": "新显示名称"}).status_code == 401
    _login(client, "claim-profile")
    response = client.patch("/api/auth/me", json={"display_name": "  新显示名称  "})
    assert response.status_code == 200, response.text
    assert response.json()["display_name"] == "新显示名称"
    phone = "+44 20 7946 0958"
    response = client.patch("/api/auth/me", json={"phone": phone})
    assert response.status_code == 200, response.text
    assert response.json()["phone"] == phone
    email = "claim.profile@example.com"
    response = client.patch("/api/auth/me", json={"email": email})
    assert response.status_code == 200, response.text
    assert response.json()["email"] == email
    assert "password_hash" not in response.json()
    me = client.get("/api/auth/me").json()
    assert me["display_name"] == "新显示名称"
    with storage._connect() as db:
        row = db.execute(
            "SELECT display_name, phone, email FROM users WHERE user_id=?", (user_id,),
        ).fetchone()
    assert tuple(row) == ("新显示名称", phone, email)


def test_own_profile_rejects_identity_and_authorization_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "competition.db"))
    _auth_sessions.clear()
    storage = CaseStorage()
    first_id = _create_claim_user(storage, "claim-first")
    second_id = _create_claim_user(storage, "claim-second")
    client = TestClient(app)
    _login(client, "claim-first")
    forbidden_payload = {
        "display_name": "越权名称",
        "phone": "12345",
        "email": "first@example.com",
        "user_id": second_id,
        "role_code": "SERVICE",
        "status": "DISABLED",
        "permissions": ["MEDICAL_REVIEW"],
    }
    response = client.patch("/api/auth/me", json=forbidden_payload)
    assert response.status_code == 422
    assert storage.get_user_identity(first_id)["display_name"] == "claim-first理赔员"
    assert storage.get_user_identity(second_id)["display_name"] == "claim-second理赔员"


def test_change_password_verifies_current_password_and_updates_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "competition.db"))
    _auth_sessions.clear()
    storage = CaseStorage()
    user_id = _create_claim_user(storage, "claim-password")
    client = TestClient(app)
    assert client.post("/api/auth/change-password", json={
        "current_password": "safe-password-123",
        "new_password": "new-safe-password-456",
        "confirm_new_password": "new-safe-password-456",
    }).status_code == 401
    _login(client, "claim-password")
    original_hash = storage.get_user_password_hash(user_id)
    wrong = client.post("/api/auth/change-password", json={
        "current_password": "wrong-password",
        "new_password": "new-safe-password-456",
        "confirm_new_password": "new-safe-password-456",
    })
    assert wrong.status_code == 400
    assert wrong.json()["detail"] == "当前密码不正确"
    assert storage.get_user_password_hash(user_id) == original_hash
    changed = client.post("/api/auth/change-password", json={
        "current_password": "safe-password-123",
        "new_password": "new-safe-password-456",
        "confirm_new_password": "new-safe-password-456",
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()["changed"] is True
    new_hash = storage.get_user_password_hash(user_id)
    assert new_hash != original_hash
    assert new_hash != "new-safe-password-456"
    assert verify_password("new-safe-password-456", new_hash)
    assert client.get("/api/auth/me").status_code == 200
    client.post("/api/auth/logout")
    old_login = client.post("/api/auth/login", json={
        "username": "claim-password", "password": "safe-password-123",
    })
    assert old_login.status_code == 401
    assert _login(client, "claim-password", "new-safe-password-456").status_code == 200


def test_claim_case_list_is_filtered_by_participant(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "competition.db"))
    _auth_sessions.clear()
    storage = CaseStorage()
    first_user = _create_claim_user(storage, "claim-a")
    second_user = _create_claim_user(storage, "claim-b")
    first_case = _case(storage, "CLAIM-A", first_user)
    second_case = _case(storage, "CLAIM-B", second_user)
    client = TestClient(app)
    _login(client, "claim-a")
    cases = client.get("/api/claim/cases").json()
    assert [item["case_id"] for item in cases] == [first_case.case_id]
    assert cases[0]["material_status"] == "not_submitted"
    assert "status" not in cases[0]
    assert "has_ocr_result" not in cases[0]
    assert "has_structured_result" not in cases[0]
    assert client.get(f"/api/claim/cases/{second_case.case_id}").status_code == 404
    detail = client.get(f"/api/claim/cases/{first_case.case_id}")
    assert detail.status_code == 200
    assert detail.json()["material_status"] == "not_submitted"
    assert "status" not in detail.json()
    assert "structured_summary" not in detail.json()
    assert "material_summary" not in detail.json()


def test_claim_create_assigns_owner_and_care_trigger_uses_real_state(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "competition.db"))
    _auth_sessions.clear()
    storage = CaseStorage()
    user_id = _create_claim_user(storage, "claim-create")
    client = TestClient(app)
    _login(client, "claim-create")
    created = client.post("/api/claim/cases", json={
        "claim_event_id": "CLAIM-PORTAL-001",
        "data_source": "user_upload",
        "consent_status": "authorized",
        "deidentification_status": "confirmed",
    })
    assert created.status_code == 201, created.text
    case_id = created.json()["case_id"]
    assert storage.user_can_access_case(user_id, case_id, "CASE_VIEW")
    dashboard = client.get("/api/claim/dashboard").json()
    assert dashboard["pending"] == 1
    assert dashboard["ready_for_care"] == 0
    blocked = client.post(f"/api/claim/cases/{case_id}/care/trigger")
    assert blocked.status_code == 409
    assert "结构化尚未完成" in blocked.json()["detail"]
    with storage._connect() as db:
        db.execute(
            "UPDATE case_results SET structured_result_json=? WHERE case_id=?",
            (json.dumps({"diagnoses": [], "procedures": [], "medications": []}), case_id),
        )
        db.execute("UPDATE cases SET care_status='STRUCTURED' WHERE case_id=?", (case_id,))
    dashboard = client.get("/api/claim/dashboard").json()
    assert dashboard["ready_for_care"] == 1
    triggered = client.post(f"/api/claim/cases/{case_id}/care/trigger")
    assert triggered.status_code == 200
    assert triggered.json()["care_status"] == "CARE_TRIGGERED"
    assert "已移交医护关怀" in triggered.json()["message"]
    dashboard = client.get("/api/claim/dashboard").json()
    assert dashboard["care_started"] == 1


def test_claim_upload_returns_business_status_without_medical_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "competition.db"))
    _auth_sessions.clear()
    storage = CaseStorage()
    _create_claim_user(storage, "claim-upload")
    client = TestClient(app)
    _login(client, "claim-upload")
    created = client.post("/api/claim/cases", json={
        "claim_event_id": "CLAIM-UPLOAD-001",
        "data_source": "user_upload",
        "consent_status": "authorized",
        "deidentification_status": "confirmed",
    }).json()
    backend_module = importlib.import_module("web.backend.app")

    async def fake_recognize(files, path):
        return []

    monkeypatch.setattr(backend_module, "recognize_uploaded_documents", fake_recognize)
    monkeypatch.setattr(
        backend_module,
        "structure_case_with_long_document_support",
        lambda case_id, documents: ClaimCase(case_id=case_id),
    )
    response = client.post(
        f"/api/claim/cases/{created['case_id']}/documents/process",
        files={"files": ("page.jpg", b"test-image", "image/jpeg")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["material_status"] == "completed"
    assert response.json()["analysis_ready"] is True
    assert "case" not in response.json()
    assert "documents" not in response.json()
    detail = client.get(f"/api/claim/cases/{created['case_id']}").json()
    assert detail["material_status"] == "completed"
    assert detail["analysis_ready"] is True


def test_claim_pages_are_served_without_replacing_legacy_workbench(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "competition.db"))
    client = TestClient(app)
    assert "账号登录" in client.get("/login").text
    assert "我的案件" in client.get("/claim").text
    assert "fileInput" in client.get("/legacy").text
    claim_source = Path("web/dist/claim.js").read_text(encoding="utf-8")
    for forbidden in ("诊断摘要", "治疗与手术", "用药摘要", "OCR 已完成", "structured_result"):
        assert forbidden not in claim_source
    assert "移交医护关怀" in claim_source
    for required in (
        "编辑资料", "修改密码", 'method: "PATCH"', "/api/auth/change-password",
        'document.getElementById("userName").textContent = currentUser.display_name',
        "profileError", "passwordError",
    ):
        assert required in claim_source
    assert "localStorage" not in claim_source
