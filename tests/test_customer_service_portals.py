"""Authenticated customer and service workspace integration tests."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from medical_claims.auth import hash_password
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


def _case(storage: CaseStorage, event_id: str, owner_id: str, customer_id: str, service_id: str):
    case = storage.create_case(CreateCaseRequest(
        claim_event_id=event_id,
        data_source="user_upload",
        consent_status="authorized",
        deidentification_status="confirmed",
    ), owner_user_id=owner_id)
    storage.assign_case_participant(case.case_id, customer_id, "CUSTOMER")
    storage.assign_case_participant(case.case_id, service_id, "SERVICE_AGENT")
    return case


def _delivery(storage: CaseStorage, case_id: str) -> str:
    delivery_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "delivery_id": delivery_id,
        "case_id": case_id,
        "status": "DELIVERED_MOCK",
        "recommendation_included": True,
        "created_at": now,
        "content": {
            "care_guide": {
                "guide": {
                    "disease_topic": "康复关怀",
                    "opening": {
                        "title": "给您的话", "content": "请按医嘱稳步康复。",
                        "evidence_fields": ["diagnoses[0].name"],
                    },
                    "recovery_plan": [],
                    "follow_up_plan": [],
                    "red_flags": [],
                    "limitations": ["本指引不能替代医生诊疗。"],
                }
            },
            "recommendation": [{
                "report_id": "internal-report-id",
                "warnings": ["internal-warning"],
                "recommendations": [{
                    "item_type": "HOSPITAL",
                    "entity_id": "HOSP-SECRET",
                    "entity_name": "测试医院",
                    "city": "上海",
                    "department": "康复医学科",
                    "total_score": 88,
                    "scores": [{
                        "dimension_name": "专科匹配",
                        "score": 90,
                        "reason": "公开资料显示设有相关专科。",
                    }],
                    "evidence": [{
                        "reason": "具备相关服务能力",
                        "evidence_type": "internal-field",
                        "source_name": "医院官网",
                        "source_url": "https://example.test/hospital",
                        "evidence_summary": "官网列出了相关科室。",
                        "evidence_status": "VERIFIED",
                    }],
                    "profile_snapshot_id": "internal-snapshot",
                }],
            }],
            "recommendation_unavailable_reason": "当前暂无服务推荐。",
            "internal_marker": "must-not-leak",
        },
        "content_fingerprint": "0" * 64,
        "risk_gate_result": {"decision": "auto_deliver", "reasons": []},
    }
    with storage._connect() as db:
        db.execute(
            "INSERT INTO deliveries(delivery_id, case_id, content_fingerprint, record_json, created_at, "
            "channel, status, sent_at) VALUES (?, ?, ?, ?, ?, 'MOCK', 'DELIVERED_MOCK', ?)",
            (delivery_id, case_id, "0" * 64, json.dumps(record, ensure_ascii=False), now, now),
        )
    return delivery_id


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "portal.db"))
    _auth_sessions.clear()
    storage = CaseStorage()
    owner_id = _user(storage, "claim-owner", "CLAIM")
    customer_a = _user(storage, "customer-a", "CUSTOMER")
    customer_b = _user(storage, "customer-b", "CUSTOMER")
    service_a = _user(storage, "service-a", "SERVICE")
    service_b = _user(storage, "service-b", "SERVICE")
    case_a = _case(storage, "EVENT-A", owner_id, customer_a, service_a)
    case_b = _case(storage, "EVENT-B", owner_id, customer_b, service_b)
    delivery_a = _delivery(storage, case_a.case_id)
    delivery_b = _delivery(storage, case_b.case_id)
    return storage, case_a, case_b, delivery_a, delivery_b


def test_customer_workspace_is_case_scoped_and_exposes_only_delivered_content(tmp_path, monkeypatch):
    _, case_a, case_b, _, _ = _setup(tmp_path, monkeypatch)
    client = TestClient(app)
    _login(client, "customer-a")

    listing = client.get("/api/customer/cases")
    assert listing.status_code == 200
    assert [item["case_id"] for item in listing.json()] == [case_a.case_id]
    assert client.get(f"/api/customer/cases/{case_b.case_id}").status_code == 404

    detail = client.get(f"/api/customer/cases/{case_a.case_id}")
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["delivery"]["content"]["care_guide"]["guide"]["disease_topic"] == "康复关怀"
    recommendation = payload["delivery"]["content"]["recommendation"][0]["recommendations"][0]
    assert recommendation == {
        "item_type": "HOSPITAL",
        "entity_name": "测试医院",
        "city": "上海",
        "department": "康复医学科",
        "reasons": [{"title": "专科匹配", "explanation": "公开资料显示设有相关专科。"}],
        "limitations": [],
        "sources": [{
            "name": "医院官网",
            "url": "https://example.test/hospital",
            "summary": "官网列出了相关科室。",
        }],
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    for forbidden in (
        "ocr_result", "structured_result", "pain_result", "internal_marker", "risk_gate_result",
        "entity_id", "total_score", "scores", "evidence_fields", "evidence_type",
        "evidence_status", "profile_snapshot_id", "report_id", "warnings",
    ):
        assert forbidden not in serialized


def test_customer_feedback_persists_owner_and_creates_service_follow_up(tmp_path, monkeypatch):
    storage, case_a, _, delivery_a, delivery_b = _setup(tmp_path, monkeypatch)
    client = TestClient(app)
    _login(client, "customer-a")
    request = {
        "target_type": "overall_service",
        "rating": 2,
        "helpful": False,
        "category": "human_service_request",
        "comment": "希望客服说明下一步安排",
        "consent_to_contact": True,
    }
    response = client.post(f"/api/customer/deliveries/{delivery_a}/feedback", json=request)
    assert response.status_code == 201, response.text
    assert response.json()["follow_up"]["case_id"] == case_a.case_id
    assert client.post(f"/api/customer/deliveries/{delivery_a}/feedback", json=request).status_code == 409
    assert client.post(f"/api/customer/deliveries/{delivery_b}/feedback", json=request).status_code == 404

    with storage._connect() as db:
        feedback = db.execute(
            "SELECT customer_user_id, request_contact FROM feedback WHERE delivery_id=?", (delivery_a,),
        ).fetchone()
        task = db.execute(
            "SELECT source_type, status FROM follow_up_tasks WHERE case_id=?", (case_a.case_id,),
        ).fetchone()
    assert feedback[0] == storage.get_user_by_username("customer-a")["user_id"]
    assert feedback[1] == 1
    assert tuple(task) == ("DELIVERY_FEEDBACK", "OPEN")


def test_service_consultation_routing_and_follow_up_are_scoped(tmp_path, monkeypatch):
    _, case_a, case_b, delivery_a, _ = _setup(tmp_path, monkeypatch)
    customer = TestClient(app)
    _login(customer, "customer-a")
    assert customer.post(f"/api/customer/deliveries/{delivery_a}/feedback", json={
        "target_type": "overall_service", "rating": 2, "helpful": False,
        "category": "claim_progress", "comment": "请联系我", "consent_to_contact": True,
    }).status_code == 201
    customer.post("/api/auth/logout")

    client = TestClient(app)
    _login(client, "service-a")
    cases = client.get("/api/service/cases").json()
    assert [item["case_id"] for item in cases] == [case_a.case_id]
    request = {
        "case_id": case_a.case_id,
        "channel": "INBOUND_CALL",
        "deidentified_text": "客户咨询理赔进度，希望确认下一步时间。",
        "category": "claim_progress",
        "priority": "high",
        "consent_confirmed": True,
        "deidentification_confirmed": True,
    }
    created = client.post("/api/service/consultations", json=request)
    assert created.status_code == 201, created.text
    assert created.json()["review_status"] == "PENDING"
    assert client.post(
        "/api/service/consultations", json={**request, "consent_confirmed": False},
    ).status_code == 400
    assert client.post(
        "/api/service/consultations", json={**request, "deidentification_confirmed": False},
    ).status_code == 400
    assert client.post("/api/service/consultations", json={**request, "case_id": case_b.case_id}).status_code == 404
    routed = client.post(f"/api/service/consultations/{created.json()['analysis_id']}/route")
    assert routed.status_code == 200
    assert routed.json()["review_status"] == "ROUTED"

    tasks = client.get("/api/service/follow-ups").json()
    assert len(tasks) == 1
    task_id = tasks[0]["task_id"]
    claimed = client.post(f"/api/service/follow-ups/{task_id}/claim")
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["status"] == "PROCESSING"
    resolved = client.post(
        f"/api/service/follow-ups/{task_id}/resolve", json={"resolution": "已电话说明当前进度"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "RESOLVED"


def test_customer_and_service_pages_share_login_and_api_conventions(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "portal.db"))
    client = TestClient(app)
    assert "客户服务中心" in client.get("/customer").text
    assert "客服工作台" in client.get("/service").text
    assert client.get("/customer.js").status_code == 200
    assert client.get("/service.js").status_code == 200
    login_source = Path("web/dist/login.js").read_text(encoding="utf-8")
    assert "CUSTOMER:'/customer'" in login_source
    assert "MEDICAL:'/medical'" in login_source
    assert "SERVICE:'/service'" in login_source
    for source_name in ("customer.js", "service.js"):
        source = Path("web/dist", source_name).read_text(encoding="utf-8")
        assert "credentials: \"same-origin\"" in source
        assert "localStorage" not in source
    customer_html = Path("web/dist/customer.html").read_text(encoding="utf-8")
    customer_source = Path("web/dist/customer.js").read_text(encoding="utf-8")
    assert 'data-view="apply"' not in customer_html
    assert "发起理赔申请" not in customer_source
    assert ".entity_id" not in customer_source


def test_customer_self_service_routes_to_claim_acceptance_and_service_follow_up(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "self-service.db"))
    _auth_sessions.clear()
    storage = CaseStorage()
    claim_id = _user(storage, "claim-intake", "CLAIM")
    customer_id = _user(storage, "customer-self", "CUSTOMER")
    service_id = _user(storage, "service-route", "SERVICE")

    customer = TestClient(app)
    _login(customer, "customer-self")
    created = customer.post("/api/customer/cases", json={"claim_event_id": "SELF-001"})
    assert created.status_code == 201, created.text
    case_id = created.json()["case_id"]

    with storage._connect() as db:
        case = db.execute(
            "SELECT customer_user_id, claim_status FROM cases WHERE case_id=?", (case_id,),
        ).fetchone()
        participants = {
            tuple(row) for row in db.execute(
                "SELECT user_id, case_role FROM case_participants WHERE case_id=? AND status='ACTIVE'",
                (case_id,),
            )
        }
    assert tuple(case) == (customer_id, "PENDING_ACCEPTANCE")
    assert (customer_id, "CUSTOMER") in participants
    assert (claim_id, "CLAIM_OWNER") in participants

    claim = TestClient(app)
    _login(claim, "claim-intake")
    assert case_id in [item["case_id"] for item in claim.get("/api/claim/cases").json()]
    accepted = claim.post(f"/api/claim/cases/{case_id}/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["claim_status"] == "ACCEPTED"
    compliance = claim.patch(f"/api/claim/cases/{case_id}/compliance", json={
        "consent_status": "authorized", "deidentification_status": "confirmed",
    })
    assert compliance.status_code == 200, compliance.text
    assert compliance.json()["deidentification_status"] == "confirmed"
    assert customer.get(f"/api/customer/cases/{case_id}").json()["claim_status"] == "ACCEPTED"

    delivery_id = _delivery(storage, case_id)
    feedback = customer.post(f"/api/customer/deliveries/{delivery_id}/feedback", json={
        "target_type": "overall_service", "rating": 5, "helpful": True,
        "category": "clear", "comment": "请电话联系我", "consent_to_contact": True,
    })
    assert feedback.status_code == 201, feedback.text
    assert feedback.json()["service_route"]["user_id"] == service_id

    service = TestClient(app)
    _login(service, "service-route")
    assert case_id in [item["case_id"] for item in service.get("/api/service/cases").json()]
    assert len(service.get("/api/service/follow-ups").json()) == 1


def test_claim_agent_searches_customer_and_creates_bound_case(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "assisted-intake.db"))
    _auth_sessions.clear()
    storage = CaseStorage()
    _user(storage, "claim-assisted", "CLAIM")
    customer_id = _user(storage, "customer-existing", "CUSTOMER")

    claim = TestClient(app)
    _login(claim, "claim-assisted")
    search = claim.get("/api/claim/customers", params={"search": "customer-existing"})
    assert search.status_code == 200
    assert [item["username"] for item in search.json()] == ["customer-existing"]
    created = claim.post("/api/claim/cases", json={
        "claim_event_id": "PHONE-001",
        "customer_username": "customer-existing",
        "data_source": "phone_report",
        "consent_status": "pending",
        "deidentification_status": "pending",
    })
    assert created.status_code == 201, created.text
    detail = claim.get(f"/api/claim/cases/{created.json()['case_id']}").json()
    assert detail["claim_status"] == "ACCEPTED"
    assert detail["customer_username"] == "customer-existing"
    with storage._connect() as db:
        assert db.execute(
            "SELECT customer_user_id FROM cases WHERE case_id=?", (created.json()["case_id"],),
        ).fetchone()[0] == customer_id
