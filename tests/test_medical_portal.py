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
        claim_status="ACCEPTED",
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
    handoff = client.post(f"/api/claim/cases/{case_a.case_id}/care/trigger")
    assert handoff.status_code == 200, handoff.text
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


def test_noncompliant_case_cannot_enter_medical_workflow(tmp_path, monkeypatch):
    _, _, _, blocked = _setup(tmp_path, monkeypatch)
    client = TestClient(app)
    _login(client, "medical-a")
    # Merely assigning a reviewer does not replace the formal CLAIM handoff.
    assert client.get(f"/api/medical/cases/{blocked.case_id}").status_code == 404
    assert client.post(
        f"/api/medical/cases/{blocked.case_id}/pain/analyze", json={"use_llm": False},
    ).status_code == 404


def test_medical_q1_q2_q3_flow_and_downstream_invalidation(tmp_path, monkeypatch):
    storage, case_a, _, _ = _setup(tmp_path, monkeypatch)
    storage.assign_medical_reviewer(case_a.case_id, "medical-a")
    claim_client = TestClient(app)
    _login(claim_client, "claim-owner")
    handoff = claim_client.post(f"/api/claim/cases/{case_a.case_id}/care/trigger")
    assert handoff.status_code == 200, handoff.text
    claim_client.post("/api/auth/logout")
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

# --- new MEDICAL orchestration regression coverage ---
import asyncio
from io import BytesIO
import importlib
import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from medical_claims.auth import hash_password
from medical_claims.care_models import (
    CareAction, CareGuideDraft, CareGuideReport, CareSafetyCheck, CareSection,
    GenerationMethod, ReviewStatus, SafetyStatus,
)
from medical_claims.models import (
    Certainty, ClaimCase, Costs, Diagnosis, DiagnosisType, Evidence, FieldStatus, FieldValue,
    Medication, Procedure,
)
from medical_claims.pain_engine import analyze_pain_points
from medical_claims.pain_models import PainPointReport
from medical_claims.review_models import FieldReviewDecision, FieldReviewRequest
from medical_claims.storage import CaseStorage
from medical_claims.quality_phase1_models import (
    GeographicRecommendationStrategy, RecommendationIntent,
)
from medical_claims.workflow_models import CreateCaseRequest
from web.backend.app import _auth_sessions, app


PASSWORD = "safe-password-123"


def create_user(storage, username, role, *, phone=None):
    return storage.create_user(
        username=username, password_hash=hash_password(PASSWORD), role_code=role,
        display_name=f"{username}显示名", phone=phone,
    )


def login(client, username):
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response


def ready_case(storage, owner_user_id, customer_user_id=None):
    metadata = storage.create_case(CreateCaseRequest(
        claim_event_id="MEDICAL-CASE-001", data_source="synthetic_test",
        consent_status="authorized", deidentification_status="confirmed",
    ), owner_user_id=owner_user_id, claim_status="ACCEPTED")
    evidence = Evidence(document_id="fixture-page.jpg", page=1, text="合成测试诊断：肺炎")
    case = ClaimCase(case_id=metadata.case_id, diagnoses=[Diagnosis(
        diagnosis_id="dx-1", diagnosis_type=DiagnosisType.PRIMARY, certainty=Certainty.CONFIRMED,
        name=FieldValue(value="肺炎", status=FieldStatus.EXTRACTED, confidence=.95, evidence=[evidence]),
    )])
    pain = analyze_pain_points(case, [], [])
    section = CareSection(title="测试", content="仅用于工程回归测试。", evidence_fields=["diagnoses[0].name"])
    action = CareAction(action="按计划复诊", timing="遵医嘱", reason="验证流程", evidence_fields=["diagnoses[0].name"])
    care = CareGuideReport(
        case_id=metadata.case_id,
        guide=CareGuideDraft(
            case_id=metadata.case_id, disease_topic="肺炎", opening=section,
            diagnosis_explanation=section, treatment_review=section, cost_explanation=section,
            recovery_plan=[action], psychological_support=section, follow_up_plan=[action],
            limitations=["仅为测试夹具。"],
        ),
        generation_method=GenerationMethod.LLM,
        knowledge_review_status=ReviewStatus.REVIEWED,
        evidence_coverage=1,
        review_required=True,
        safety_checks=[CareSafetyCheck(code="FIXTURE_PASS", status=SafetyStatus.PASSED, message="测试安全项")],
    )
    storage.save_ocr(metadata.case_id, [{
        "document_id": "fixture-page.jpg", "page": 1, "full_text": "合成测试诊断：肺炎",
        "blocks": [{"text": "合成测试诊断：肺炎", "confidence": .99, "bbox": [1, 2, 3, 4]}],
    }])
    storage.save_structured(metadata.case_id, case)
    storage.save_pain(metadata.case_id, pain, case)
    storage.save_care(metadata.case_id, care, case, pain)
    if customer_user_id:
        storage.assign_case_participant(metadata.case_id, customer_user_id, "CUSTOMER")
    return metadata.case_id


def persist_quality_and_recommendation(storage, case_id):
    storage.save_provider_quality_observation(
        case_id=case_id, provider_type="HOSPITAL", provider_id="fixture-hospital",
        disease_group="pneumonia", metric_code="cost_efficiency", raw_value=82,
        unit="score", status="OBSERVED", evidence={"field": "costs.total_amount"},
        source_kind="synthetic_test",
    )
    now = "2026-10-10T00:00:00+00:00"
    report_id, item_id = str(uuid4()), str(uuid4())
    with storage._connect() as db:
        db.execute(
            "INSERT INTO recommendation_reports(report_id, case_id, report_version, disease_group, "
            "patient_city, status, planner_version, report_json, created_at, updated_at) "
            "VALUES (?, ?, 1, 'pneumonia', '上海', 'DRAFT', 'fixture', ?, ?, ?)",
            (report_id, case_id, json.dumps({"fixture": True}), now, now),
        )
        db.execute(
            "INSERT INTO recommendation_items(item_id, report_id, item_type, entity_id, entity_name, "
            "city, department, rank_no, total_score, confidence, evidence_status, created_at) "
            "VALUES (?, ?, 'HOSPITAL', 'fixture-hospital', '测试医院', '上海', '呼吸科', 1, 88, .8, 'VERIFIED', ?)",
            (item_id, report_id, now),
        )
        db.execute(
            "INSERT INTO recommendation_scores(score_id, item_id, dimension_code, dimension_name, score, "
            "weight, reason, created_at) VALUES (?, ?, 'disease_match', '病种匹配', 90, .35, '测试证据', ?)",
            (str(uuid4()), item_id, now),
        )
        db.execute(
            "INSERT INTO recommendation_evidence(evidence_id, item_id, reason, source_name, source_url, "
            "evidence_summary, evidence_status, evidence_confidence, created_at) "
            "VALUES (?, ?, '设有相关科室', '测试官网', 'https://example.test/hospital', '设有呼吸科', 'VERIFIED', .9, ?)",
            (str(uuid4()), item_id, now),
        )


def test_medical_api_requires_login_and_medical_role(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "competition.db"))
    _auth_sessions.clear()
    storage = CaseStorage()
    create_user(storage, "claim-med-denied", "CLAIM")
    client = TestClient(app)
    assert client.get("/api/medical/dashboard").status_code == 401
    login(client, "claim-med-denied")
    assert client.get("/api/medical/dashboard").status_code == 403


def test_medical_case_scope_and_real_results(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "competition.db"))
    _auth_sessions.clear()
    storage = CaseStorage()
    claim_id = create_user(storage, "claim-owner", "CLAIM")
    medical_id = create_user(storage, "medical-a", "MEDICAL")
    other_medical_id = create_user(storage, "medical-b", "MEDICAL")
    customer_id = create_user(storage, "customer-a", "CUSTOMER", phone="13812345678")
    case_id = ready_case(storage, claim_id, customer_id)
    storage.assign_case_participant(case_id, medical_id, "MEDICAL_REVIEWER")
    persist_quality_and_recommendation(storage, case_id)
    hidden_case_id = ready_case(storage, claim_id)
    storage.assign_case_participant(hidden_case_id, other_medical_id, "MEDICAL_REVIEWER")
    client = TestClient(app)
    login(client, "medical-a")
    items = client.get("/api/medical/cases").json()
    assert [item["case_id"] for item in items] == [case_id]
    detail = client.get(f"/api/medical/cases/{case_id}")
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["primary_diagnosis"] == "肺炎"
    assert payload["structured_result"]["diagnoses"]
    assert payload["pain_result"] is not None
    assert payload["care_result"]["guide"]["disease_topic"] == "肺炎"
    assert payload["quality"]["case_observations"][0]["metric_code"] == "cost_efficiency"
    assert payload["recommendation"]["items"][0]["entity_name"] == "测试医院"
    assert "phone" not in json.dumps(payload)
    assert "13812345678" not in json.dumps(payload)
    assert "bbox" not in json.dumps(payload)
    assert client.get(f"/api/medical/cases/{hidden_case_id}").status_code == 404


def test_only_care_workflow_cases_enter_medical_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "competition.db"))
    _auth_sessions.clear()
    storage = CaseStorage()
    claim_id = create_user(storage, "claim-flow", "CLAIM")
    medical_id = create_user(storage, "medical-flow", "MEDICAL")
    waiting = storage.create_case(CreateCaseRequest(
        claim_event_id="NOT-HANDED-OFF", data_source="synthetic_test",
        consent_status="authorized", deidentification_status="confirmed",
    ), owner_user_id=claim_id, claim_status="ACCEPTED")
    storage.assign_case_participant(waiting.case_id, medical_id, "MEDICAL_REVIEWER")
    evidence = Evidence(document_id="waiting.jpg", page=1, text="肺炎")
    storage.save_structured(waiting.case_id, ClaimCase(
        case_id=waiting.case_id,
        diagnoses=[Diagnosis(
            diagnosis_id="waiting-dx", diagnosis_type=DiagnosisType.PRIMARY,
            certainty=Certainty.CONFIRMED,
            name=FieldValue(
                value="肺炎", status=FieldStatus.EXTRACTED, confidence=.95,
                evidence=[evidence],
            ),
        )],
    ))
    ready_id = ready_case(storage, claim_id)
    storage.assign_case_participant(ready_id, medical_id, "MEDICAL_REVIEWER")
    client = TestClient(app)
    login(client, "medical-flow")
    assert [item["case_id"] for item in client.get("/api/medical/cases").json()] == [ready_id]
    blocked = client.post(
        f"/api/medical/cases/{waiting.case_id}/analysis/prepare",
        json={"modules": ["pain"], "use_llm": False},
    )
    assert blocked.status_code == 409
    assert "理赔人员触发医疗分析" in blocked.json()["detail"]
    assert storage.get_results(waiting.case_id)["pain_result"] is None


def test_claim_handoff_auto_assigns_available_medical_reviewer(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "competition.db"))
    _auth_sessions.clear()
    storage = CaseStorage()
    claim_id = create_user(storage, "claim-handoff", "CLAIM")
    medical_id = create_user(storage, "medical-handoff", "MEDICAL")
    case_id = ready_case(storage, claim_id)
    result = storage.trigger_care(claim_id, case_id)
    assert result["medical_reviewer_assigned"] is True
    assert storage.user_can_access_case(medical_id, case_id, "MEDICAL_CASE_VIEW")


def test_medical_review_is_validated_and_bound_to_logged_in_reviewer(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "competition.db"))
    _auth_sessions.clear()
    storage = CaseStorage()
    claim_id = create_user(storage, "claim-review", "CLAIM")
    medical_id = create_user(storage, "medical-review", "MEDICAL")
    customer_id = create_user(storage, "customer-review", "CUSTOMER")
    case_id = ready_case(storage, claim_id, customer_id)
    storage.assign_case_participant(case_id, medical_id, "MEDICAL_REVIEWER")
    medical = TestClient(app)
    login(medical, "medical-review")
    assert medical.post(f"/api/medical/cases/{case_id}/review", json={
        "decision": "RETURNED", "comment": "",
    }).status_code == 422
    pending = medical.get(f"/api/medical/cases/{case_id}").json()
    assert pending["review_status"] == "PENDING"
    assert pending["medical_review_stage"] == "PENDING"
    for module in ("pain", "care"):
        module_review = medical.post(f"/api/medical/cases/{case_id}/module-reviews", json={
            "module": module, "decision": "APPROVED", "comment": "",
        })
        assert module_review.status_code == 201, module_review.text
    ready = medical.get(f"/api/medical/cases/{case_id}").json()
    assert ready["review_status"] == "PENDING"
    assert ready["final_gate"]["decision"] == "READY_FOR_FINAL_REVIEW"
    assert ready["final_review_matrix"]["can_final_approve"] is True
    assert ready["medical_review_stage"] == "READY_FOR_FINAL_REVIEW"
    listed = next(item for item in medical.get("/api/medical/cases").json()
                  if item["case_id"] == case_id)
    assert listed["medical_review_stage"] == ready["medical_review_stage"]
    approved = medical.post(f"/api/medical/cases/{case_id}/review", json={
        "decision": "APPROVED", "comment": "结构化、痛点和指引已核对。",
    })
    assert approved.status_code == 201, approved.text
    assert approved.json()["review_status"] == "APPROVED"
    assert approved.json()["publication"]["status"] == "PUBLISHED"
    assert approved.json()["publication"]["channel"] == "customer_portal"
    published = medical.get(f"/api/medical/cases/{case_id}").json()
    assert published["review_status"] == "APPROVED"
    assert published["medical_review_stage"] == "PUBLISHED"
    published_listed = next(item for item in medical.get("/api/medical/cases").json()
                            if item["case_id"] == case_id)
    assert published_listed["medical_review_stage"] == "PUBLISHED"
    customer_portal = TestClient(app)
    login(customer_portal, "customer-review")
    customer_published = customer_portal.get(f"/api/customer/cases/{case_id}")
    assert customer_published.status_code == 200, customer_published.text
    assert customer_published.json()["delivery"]["status"] == "PUBLISHED"
    with storage._connect() as db:
        row = db.execute(
            "SELECT reviewer_user_id, comment FROM reviews "
            "WHERE case_id=? AND target_type='combined_delivery' "
            "ORDER BY rowid DESC LIMIT 1",
            (case_id,),
        ).fetchone()
    assert row["reviewer_user_id"] == medical_id
    assert row["comment"] == "结构化、痛点和指引已核对。"
    saved = storage.get_results(case_id)
    storage.save_care(
        case_id, CareGuideReport.model_validate(saved["care_result"]),
        ClaimCase.model_validate(saved["structured_result"]),
        PainPointReport.model_validate(saved["pain_result"]),
    )
    refreshed = medical.get(f"/api/medical/cases/{case_id}").json()
    assert refreshed["review_status"] == "PENDING"
    assert refreshed["medical_review_stage"] == "READY_FOR_FINAL_REVIEW"
    customer = TestClient(app)
    login(customer, "customer-review")
    assert customer.get(f"/api/medical/cases/{case_id}").status_code == 403


def test_medical_return_and_confirmation_decisions_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "competition.db"))
    _auth_sessions.clear()
    storage = CaseStorage()
    claim_id = create_user(storage, "claim-decisions", "CLAIM")
    medical_id = create_user(storage, "medical-decisions", "MEDICAL")
    case_id = ready_case(storage, claim_id)
    storage.assign_case_participant(case_id, medical_id, "MEDICAL_REVIEWER")
    client = TestClient(app)
    login(client, "medical-decisions")
    needs = client.post(f"/api/medical/cases/{case_id}/review", json={
        "decision": "NEEDS_CONFIRMATION", "comment": "请补充核对诊断证据。",
    })
    assert needs.status_code == 201, needs.text
    assert needs.json()["review_status"] == "NEEDS_CONFIRMATION"
    returned = client.post(f"/api/medical/cases/{case_id}/review", json={
        "decision": "RETURNED", "comment": "指引内容需要修改。",
    })
    assert returned.status_code == 201, returned.text
    assert returned.json()["review_status"] == "RETURNED"


def test_medical_frontend_and_login_routing_are_present(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "competition.db"))
    client = TestClient(app)
    assert "医疗复核工作区" in client.get("/medical").text
    source = Path("web/dist/medical.js").read_text(encoding="utf-8")
    for required in (
        "/api/medical/dashboard", "/api/medical/cases", "结构化病例", "痛点分析",
        "温度指引", "本次就医品质评价", "就医推荐", "医疗复核", "批准发布",
        "退回修改", "返回该模块", "/api/auth/change-password",
        "/field-reviews", "确认无误", "修改并确认", "无法确认", "原始材料页面",
    ):
        assert required in source
    customer_source = Path("web/dist/customer.js").read_text(encoding="utf-8")
    assert "total_score=" not in customer_source
    assert 'data-decision="NEEDS_CONFIRMATION"' not in source
    assert "localStorage" not in source
    assert "MEDICAL:'/medical'" in Path("web/dist/login.js").read_text(encoding="utf-8")


def case_with_pending_field(storage, owner_id, medical_id):
    case_id = ready_case(storage, owner_id)
    saved = storage.get_results(case_id)
    case = ClaimCase.model_validate(saved["structured_result"])
    case.diagnoses[0].name.status = FieldStatus.REVIEW_REQUIRED
    case.diagnoses[0].name.confidence = .62
    storage.save_structured(case_id, case)
    pain = analyze_pain_points(case, [], [])
    storage.save_pain(case_id, pain, case)
    previous_care = CareGuideReport.model_validate(saved["care_result"])
    storage.save_care(case_id, previous_care, case, pain)
    storage.assign_case_participant(case_id, medical_id, "MEDICAL_REVIEWER")
    return case_id


def test_field_reviews_persist_original_and_effective_values(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "competition.db"))
    _auth_sessions.clear()
    storage = CaseStorage()
    owner_id = create_user(storage, "claim-field-review", "CLAIM")
    medical_id = create_user(storage, "medical-field-review", "MEDICAL")
    case_id = case_with_pending_field(storage, owner_id, medical_id)
    client = TestClient(app)
    login(client, "medical-field-review")

    before = client.get(f"/api/medical/cases/{case_id}").json()
    assert before["field_review_summary"]["pending"] == 1
    assert before["pending_field_reviews"][0]["field_path"] == "diagnoses[0].name"
    assert all(item["field_path"] != "hospitalization.admission_date"
               for item in before["pending_field_reviews"])

    corrected = client.post(f"/api/medical/cases/{case_id}/field-reviews", json={
        "field_path": "diagnoses[0].name", "decision": "CORRECTED",
        "reviewed_value": "社区获得性肺炎", "reason": "根据出院小结修正",
    })
    assert corrected.status_code == 201, corrected.text
    after = client.get(f"/api/medical/cases/{case_id}").json()
    assert after["field_review_summary"] == {
        "pending": 0, "confirmed": 0, "corrected": 1, "unresolved": 0,
    }
    assert after["structured_result"]["diagnoses"][0]["name"]["value"] == "肺炎"
    assert after["structured_effective_result"]["diagnoses"][0]["name"]["value"] == "社区获得性肺炎"
    assert after["field_reviews"][0]["model_value"] == "肺炎"
    assert after["field_reviews"][0]["reviewer_user_id"] == medical_id


def test_confirm_unresolved_and_stale_field_review_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "competition.db"))
    _auth_sessions.clear()
    storage = CaseStorage()
    owner_id = create_user(storage, "claim-field-state", "CLAIM")
    medical_id = create_user(storage, "medical-field-state", "MEDICAL")
    case_id = case_with_pending_field(storage, owner_id, medical_id)
    client = TestClient(app)
    login(client, "medical-field-state")

    unresolved = client.post(f"/api/medical/cases/{case_id}/field-reviews", json={
        "field_path": "diagnoses[0].name", "decision": "UNRESOLVED",
        "reason": "原材料模糊",
    })
    assert unresolved.status_code == 201
    assert client.get(f"/api/medical/cases/{case_id}").json()["field_review_summary"]["unresolved"] == 1

    saved = storage.get_results(case_id)
    case = ClaimCase.model_validate(saved["structured_result"])
    case.diagnoses[0].name.value = "细菌性肺炎"
    storage.save_structured(case_id, case)
    pain = analyze_pain_points(case, [], [])
    storage.save_pain(case_id, pain, case)
    storage.save_care(case_id, CareGuideReport.model_validate(saved["care_result"]), case, pain)
    refreshed = client.get(f"/api/medical/cases/{case_id}").json()
    assert refreshed["field_reviews"] == []
    assert refreshed["field_review_summary"]["pending"] == 1

    confirmed = client.post(f"/api/medical/cases/{case_id}/field-reviews", json={
        "field_path": "diagnoses[0].name", "decision": "CONFIRMED",
    })
    assert confirmed.status_code == 201
    assert client.get(f"/api/medical/cases/{case_id}").json()["field_review_summary"]["confirmed"] == 1


def test_medical_document_preview_is_case_scoped_and_path_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "competition.db"))
    private_root = tmp_path / "private-documents"
    monkeypatch.setenv("PRIVATE_DOCUMENT_ROOT", str(private_root))
    _auth_sessions.clear()
    storage = CaseStorage()
    owner_id = create_user(storage, "claim-preview", "CLAIM")
    medical_id = create_user(storage, "medical-preview", "MEDICAL")
    create_user(storage, "medical-preview-other", "MEDICAL")
    case_id = ready_case(storage, owner_id)
    storage.assign_case_participant(case_id, medical_id, "MEDICAL_REVIEWER")
    resource_id = "a" * 32
    case_dir = private_root / case_id
    case_dir.mkdir(parents=True)
    (case_dir / f"{resource_id}.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    documents = storage.get_results(case_id)["ocr_result"]
    documents[0].update({
        "resource_id": resource_id, "stored_filename": f"{resource_id}.png",
        "media_type": "image/png",
    })
    storage.save_ocr(case_id, documents)

    medical = TestClient(app)
    login(medical, "medical-preview")
    preview = medical.get(f"/api/medical/cases/{case_id}/documents/{resource_id}")
    assert preview.status_code == 200
    assert preview.headers["cache-control"] == "private, no-store"
    assert "stored_filename" not in json.dumps(medical.get(f"/api/medical/cases/{case_id}").json())
    assert medical.get(f"/api/medical/cases/{case_id}/documents/not-a-resource").status_code == 404

    other = TestClient(app)
    login(other, "medical-preview-other")
    assert other.get(f"/api/medical/cases/{case_id}/documents/{resource_id}").status_code == 404
    claim = TestClient(app)
    login(claim, "claim-preview")
    assert claim.get(f"/api/medical/cases/{case_id}/documents/{resource_id}").status_code == 403


def test_claim_upload_retains_private_preview_resource(tmp_path, monkeypatch):
    private_root = tmp_path / "private-documents"
    monkeypatch.setenv("PRIVATE_DOCUMENT_ROOT", str(private_root))
    backend = importlib.import_module("web.backend.app")
    monkeypatch.setattr(backend, "recognize_image", lambda path, document_id, page: {
        "document_id": document_id, "page": page, "blocks": [], "full_text": "",
        "human_corrected": False,
    })
    upload = UploadFile(file=BytesIO(b"private-image-bytes"), filename="page.png")
    ocr_temp = tmp_path / "ocr-temp"
    ocr_temp.mkdir()
    documents = asyncio.run(backend.recognize_uploaded_documents(
        [upload], ocr_temp, case_id="case-preview",
    ))
    assert documents[0]["resource_id"]
    stored = private_root / "case-preview" / documents[0]["stored_filename"]
    assert stored.read_bytes() == b"private-image-bytes"
    assert str(private_root) not in json.dumps(documents)


def test_new_claim_handoff_can_prepare_and_review_independent_medical_modules(tmp_path, monkeypatch):
    """A CLAIM handoff can run the formal MEDICAL path without the legacy workbench."""
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "competition.db"))
    _auth_sessions.clear()
    storage = CaseStorage()
    claim_id = create_user(storage, "claim-formal-orchestration", "CLAIM")
    medical_id = create_user(storage, "medical-formal-orchestration", "MEDICAL")
    metadata = storage.create_case(CreateCaseRequest(
        claim_event_id="MEDICAL-FORMAL-001", data_source="synthetic_test",
        consent_status="authorized", deidentification_status="confirmed",
    ), owner_user_id=claim_id, claim_status="ACCEPTED")
    evidence = Evidence(document_id="fixture-page.jpg", page=1, text="肺炎 阿莫西林 1000元")
    pending_cost = FieldValue(
        value=1000, status=FieldStatus.REVIEW_REQUIRED, confidence=.65, evidence=[evidence],
    )
    case = ClaimCase(
        case_id=metadata.case_id,
        diagnoses=[Diagnosis(
            diagnosis_id="dx-formal", diagnosis_type=DiagnosisType.PRIMARY,
            certainty=Certainty.CONFIRMED,
            name=FieldValue(value="肺炎", status=FieldStatus.EXTRACTED,
                            confidence=.96, evidence=[evidence]),
        )],
        medications=[Medication(
            medication_id="med-formal",
            name=FieldValue(value="阿莫西林", status=FieldStatus.EXTRACTED,
                            confidence=.93, evidence=[evidence]),
        )],
        costs=Costs(total_amount=pending_cost),
    )
    storage.save_structured(metadata.case_id, case)
    handoff = storage.trigger_care(claim_id, metadata.case_id)
    assert handoff["medical_reviewer_assigned"] is True
    assert storage.user_can_access_case(medical_id, metadata.case_id, "MEDICAL_CASE_VIEW")

    client = TestClient(app)
    login(client, "medical-formal-orchestration")
    prepared = client.post(
        f"/api/medical/cases/{metadata.case_id}/analysis/prepare",
        json={
            "modules": ["pain", "care", "treatment_quality"],
            "use_llm": False,
        },
    )
    assert prepared.status_code == 200, prepared.text
    assert {name: value["status"] for name, value in prepared.json()["outcomes"].items()} == {
        "pain": "GENERATED", "care": "GENERATED", "treatment_quality": "GENERATED",
    }
    detail = client.get(f"/api/medical/cases/{metadata.case_id}").json()
    assert detail["field_review_summary"]["pending"] >= 1
    assert detail["pain_result"] is not None
    assert detail["care_result"] is not None
    assert len(detail["quality"]["case_observations"]) == 6
    assert all(
        detail["module_states"][module]["generation_status"] == "GENERATED"
        for module in ("pain", "care", "treatment_quality")
    )

    approved = client.post(
        f"/api/medical/cases/{metadata.case_id}/module-reviews",
        json={"module": "pain", "decision": "APPROVED", "comment": ""},
    )
    assert approved.status_code == 201, approved.text
    detail = client.get(f"/api/medical/cases/{metadata.case_id}").json()
    assert detail["module_states"]["pain"]["review_status"] == "APPROVED"
    assert detail["module_states"]["care"]["review_status"] == "REVIEW_REQUIRED"


def test_formal_medical_recommendation_is_persisted_in_existing_v2_tables(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "competition.db"))
    monkeypatch.setenv("QUALITY_DYNAMIC_DISCOVERY_ENABLED", "false")
    _auth_sessions.clear()
    storage = CaseStorage()
    claim_id = create_user(storage, "claim-formal-recommendation", "CLAIM")
    medical_id = create_user(storage, "medical-formal-recommendation", "MEDICAL")
    metadata = storage.create_case(CreateCaseRequest(
        claim_event_id="MEDICAL-RECOMMEND-001", data_source="synthetic_test",
        consent_status="authorized", deidentification_status="confirmed",
    ), owner_user_id=claim_id, claim_status="ACCEPTED")
    evidence = Evidence(
        document_id="fixture-cataract.jpg", page=1,
        text="老年性白内障，行白内障超声乳化联合人工晶体植入术",
    )
    case = ClaimCase(
        case_id=metadata.case_id,
        diagnoses=[Diagnosis(
            diagnosis_id="dx-cataract", diagnosis_type=DiagnosisType.PRIMARY,
            certainty=Certainty.CONFIRMED,
            name=FieldValue(value="老年性白内障", status=FieldStatus.EXTRACTED,
                            confidence=.97, evidence=[evidence]),
        )],
        procedures=[Procedure(
            procedure_id="proc-cataract",
            name=FieldValue(value="白内障超声乳化联合人工晶体植入术",
                            status=FieldStatus.EXTRACTED, confidence=.95, evidence=[evidence]),
        )],
    )
    storage.save_structured(metadata.case_id, case)
    assert storage.trigger_care(claim_id, metadata.case_id)["medical_reviewer_assigned"]

    backend = importlib.import_module("web.backend.app")
    intent = RecommendationIntent(
        primary_disease="老年性白内障",
        treatment_goal="改善白内障导致的视物模糊并完成术后连续照护",
        primary_specialty="眼科",
        secondary_specialties=[],
        required_capabilities=["白内障超声乳化手术", "人工晶体植入术"],
        comorbidities=[], case_complexity="low",
        geographic_strategy=GeographicRecommendationStrategy.LOCAL_FIRST,
        nearby_major_cities=["成都市", "西安市"],
        search_queries=["重庆市 白内障 眼科 医院官网"],
        reasoning="主诊断和本次手术共同指向白内障专科需求。",
        supporting_case_fields=["diagnoses[0].name", "procedures[0].name"],
    )
    monkeypatch.setattr(backend, "recommendation_intent_with_deepseek", lambda *args, **kwargs: intent)

    client = TestClient(app)
    login(client, "medical-formal-recommendation")
    response = client.post(
        f"/api/medical/cases/{metadata.case_id}/analysis/prepare",
        json={
            "modules": ["recommendation"], "use_llm": True,
            "city": "重庆市", "province": "重庆市",
            "nearby_major_cities": ["成都市", "西安市"],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["outcomes"]["recommendation"]["status"] == "GENERATED"
    saved = storage.load_recommendation_report(metadata.case_id)
    assert saved is not None
    assert saved["status"] == "DRAFT"
    assert saved["items"]
    with storage._connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM recommendation_reports WHERE case_id=?",
            (metadata.case_id,),
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM recommendation_items WHERE report_id=?",
            (saved["report_id"],),
        ).fetchone()[0] == len(saved["items"])

    approved = client.post(
        f"/api/medical/cases/{metadata.case_id}/module-reviews",
        json={"module": "recommendation", "decision": "APPROVED", "comment": "推荐依据已核对。"},
    )
    assert approved.status_code == 201, approved.text
    detail = client.get(f"/api/medical/cases/{metadata.case_id}").json()
    assert detail["module_states"]["recommendation"]["review_status"] == "APPROVED"
    old_state = detail["module_states"]["recommendation"]
    old_generation_id = old_state["current_generation_id"]
    old_content_fingerprint = old_state["content_fingerprint"]

    storage.save_field_review(medical_id, metadata.case_id, FieldReviewRequest(
        field_path="procedures[0].name",
        decision=FieldReviewDecision.CORRECTED,
        reviewed_value="白内障超声乳化联合人工晶体植入术（人工核对）",
        reason="依据手术记录核对",
    ))
    detail = client.get(f"/api/medical/cases/{metadata.case_id}").json()
    assert detail["module_states"]["recommendation"]["generation_status"] == "STALE"
    assert detail["module_states"]["recommendation"]["current_generation_id"] == old_generation_id

    regenerated = client.post(
        f"/api/medical/cases/{metadata.case_id}/analysis/prepare",
        json={
            "modules": ["recommendation"], "use_llm": True,
            "city": "重庆市", "province": "重庆市",
            "nearby_major_cities": ["成都市", "西安市"],
        },
    )
    assert regenerated.status_code == 200, regenerated.text
    outcome = regenerated.json()["outcomes"]["recommendation"]
    assert outcome["status"] == "GENERATED"
    assert outcome["generation_id"] != old_generation_id
    detail = client.get(f"/api/medical/cases/{metadata.case_id}").json()
    new_state = detail["module_states"]["recommendation"]
    assert new_state["generation_status"] == "GENERATED"
    assert new_state["result_available"] is True
    assert new_state["review_status"] == "REVIEW_REQUIRED"
    assert new_state["current_generation_id"] == outcome["generation_id"]
    assert new_state["content_fingerprint"] != old_content_fingerprint
    assert storage.load_recommendation_report(metadata.case_id)["report_version"] == 2

    reapproved = client.post(
        f"/api/medical/cases/{metadata.case_id}/module-reviews",
        json={"module": "recommendation", "decision": "APPROVED", "comment": "新版本推荐已核对。"},
    )
    assert reapproved.status_code == 201, reapproved.text
    detail = client.get(f"/api/medical/cases/{metadata.case_id}").json()
    recommendation_row = next(
        row for row in detail["final_review_matrix"]["modules"]
        if row["module"] == "recommendation"
    )
    assert recommendation_row["generation_status"] == "GENERATED"
    assert recommendation_row["review_status"] == "APPROVED"
