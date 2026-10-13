"""Frozen V1 contract and MEDICAL publication-gate tests."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from medical_claims.auth import hash_password
from medical_claims.care_engine import build_fallback_draft, finalize_care_guide
from medical_claims.care_models import GenerationMethod
from medical_claims.contracts import (
    CareRevisionRequestV1,
    CaseTaskV1,
    CustomerDeliveryV1,
    PainItemReviewRequestV1,
)
from medical_claims.models import ClaimCase
from medical_claims.pain_models import (
    DetectorInfo,
    DetectorMethod,
    PainDimension,
    PainPoint,
    PainPointReport,
    PainPointSummary,
    PainPointType,
    PainSeverity,
)
from medical_claims.review_models import FieldReviewDecision, FieldReviewRequest, ReviewDecision
from medical_claims.storage import CaseStorage
from medical_claims.workflow_models import CreateCaseRequest
from web.backend.app import _auth_sessions, app

from test_medical_orchestration import case_fixture, storage_fixture


def pain_report(case: ClaimCase) -> PainPointReport:
    point = PainPoint(
        pain_point_id="pain-1", dimension=PainDimension.CLINICAL,
        type=PainPointType.MEDICATION_INSTRUCTION_INCOMPLETE,
        title="用药信息需确认", severity=PainSeverity.MEDIUM,
        severity_score=55, confidence=.91,
        description="当前材料没有记录完整用药方式。",
        evidence=case.medications[0].name.evidence,
        related_fields=["medications[0].name"],
        recommended_action="复诊时请医生核对用法用量。",
        detector=DetectorInfo(method=DetectorMethod.RULE, version="test", rule_id="TEST-1"),
    )
    return PainPointReport(
        case_id=case.case_id, pain_points=[point],
        summary=PainPointSummary(total=1, clinical=1, cost=0, experience=0, high_priority=0),
    )


def test_contracts_are_versioned_strict_and_split_cost_routing():
    task = CaseTaskV1(
        task_id="task-1", case_id="case-1", source_type="SERVICE_CONSULTATION",
        source_id="consult-1", target_role="CLAIM", category="claim_cost",
        created_by="service-1", created_at="2026-10-12T00:00:00+00:00",
    )
    assert task.schema_version == "case-task-v1"
    with pytest.raises(ValidationError):
        CaseTaskV1(**{**task.model_dump(mode="json"), "category": "cost_question"})
    with pytest.raises(ValidationError):
        CaseTaskV1(**{
            **task.model_dump(mode="json"), "status": "RESOLVED",
            "resolved_at": None, "resolution": None,
        })

    delivery = CustomerDeliveryV1(
        delivery_id="delivery-1", case_id="case-1",
        created_at="2026-10-12T00:00:00+00:00", status="DELIVERED_MOCK",
        recommendation_included=False, content={
            "attention_items": [], "recommendation": [],
        },
    )
    assert delivery.schema_version == "customer-delivery-v1"
    with pytest.raises(ValidationError):
        CustomerDeliveryV1(**{
            **delivery.model_dump(mode="json"), "internal_score": 99,
        })


def test_medical_v1_accepts_system_pain_defaults_after_explicit_module_approval(tmp_path):
    storage, medical_id, raw = storage_fixture(tmp_path)
    state = storage.get_effective_case_state(raw.case_id)
    effective = ClaimCase.model_validate(state["effective"])
    pain = pain_report(effective)
    storage.save_medical_pain(
        raw.case_id, pain, raw, state["effective_fingerprint"], medical_id,
    )
    care = finalize_care_guide(
        effective, build_fallback_draft(effective, pain, None, "肺炎"), None,
        GenerationMethod.RULES_FALLBACK, [],
    )
    storage.save_medical_care(
        raw.case_id, care, raw, state["effective_fingerprint"], medical_id,
    )

    detail = storage.get_medical_case_detail(medical_id, raw.case_id)
    assert detail["medical_contract_enforced"] is True
    assert storage.medical_final_gate(detail).decision == "NOT_READY"
    storage.save_module_review(
        medical_id, raw.case_id, "pain_report", ReviewDecision.APPROVED, "",
        storage.module_review_content(raw.case_id, "pain"),
    )
    reviewed = storage.get_medical_case_detail(medical_id, raw.case_id)
    assert reviewed["pain_review_summary"]["pending"] == 0
    assert reviewed["pain_suggested_dispositions"][0]["decision"] == "SHOW"
    assert reviewed["pain_effective_result"]["pain_points"][0]["review_required"] is False
    low_confidence = pain.model_dump(mode="json")
    low_confidence["pain_points"][0]["confidence"] = 0.4
    assert storage._pain_system_suggestions(low_confidence)[0]["decision"] == "HIDE"

    storage.save_field_review(medical_id, raw.case_id, FieldReviewRequest(
        field_path="costs.total_amount", decision=FieldReviewDecision.CONFIRMED,
    ))
    storage.save_module_review(
        medical_id, raw.case_id, "care_guide", ReviewDecision.APPROVED, "",
        storage.module_review_content(raw.case_id, "care"),
    )
    ready = storage.medical_final_gate(
        storage.get_medical_case_detail(medical_id, raw.case_id),
    )
    assert ready.decision == "READY_FOR_FINAL_REVIEW", ready.reasons
    assert ready.approved_modules == ["pain", "care"]
    assert storage.customer_attention_items(raw.case_id)[0]["pain_point_id"] == "pain-1"

    before = storage.get_medical_case_detail(medical_id, raw.case_id)
    revised_draft = care.guide.model_copy(deep=True)
    revised_draft.psychological_support.content = "请先记录想与医生确认的问题，并在复诊时逐项沟通。"
    revised = finalize_care_guide(
        effective, revised_draft, None, GenerationMethod.RULES_FALLBACK, [],
    )
    revision = storage.save_care_revision(
        medical_id, raw.case_id,
        CareRevisionRequestV1(
            base_content_fingerprint=before["module_states"]["care"]["content_fingerprint"],
            guide=revised_draft, note="修订心理支持表达",
        ),
        revised,
    )
    assert revision.original_report.guide.psychological_support.content \
        != revision.revised_report.guide.psychological_support.content
    after_revision = storage.get_medical_case_detail(medical_id, raw.case_id)
    assert after_revision["module_states"]["care"]["review_status"] == "REVIEW_REQUIRED"
    assert storage.medical_final_gate(after_revision).decision == "NOT_READY"
    assert after_revision["care_revisions"][0]["note"] == "修订心理支持表达"


def test_medical_v1_http_endpoints_recalculate_revision_and_unlock_final_gate(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("COMPETITION_DB_PATH", str(tmp_path / "medical-v1-api.db"))
    _auth_sessions.clear()
    storage = CaseStorage()
    medical_id = storage.create_user(
        username="medical-v1-api", password_hash=hash_password("safe-password-123"),
        role_code="MEDICAL", display_name="V1 医疗复核员",
    )
    metadata = storage.create_case(CreateCaseRequest(
        claim_event_id="MEDICAL-V1-API", data_source="synthetic_test",
        consent_status="authorized", deidentification_status="confirmed",
    ))
    storage.assign_case_participant(metadata.case_id, medical_id, "MEDICAL_REVIEWER")
    raw = case_fixture(metadata.case_id)
    storage.save_structured(metadata.case_id, raw)
    with storage._connect() as db:
        db.execute("UPDATE cases SET care_status='CARE_TRIGGERED' WHERE case_id=?", (metadata.case_id,))
    state = storage.get_effective_case_state(metadata.case_id)
    effective = ClaimCase.model_validate(state["effective"])
    pain = pain_report(effective)
    storage.save_medical_pain(
        metadata.case_id, pain, raw, state["effective_fingerprint"], medical_id,
    )
    care = finalize_care_guide(
        effective, build_fallback_draft(effective, pain, None, "肺炎"), None,
        GenerationMethod.RULES_FALLBACK, [],
    )
    storage.save_medical_care(
        metadata.case_id, care, raw, state["effective_fingerprint"], medical_id,
    )

    client = TestClient(app)
    assert client.post("/api/auth/login", json={
        "username": "medical-v1-api", "password": "safe-password-123",
    }).status_code == 200
    assert client.post(
        f"/api/medical/cases/{metadata.case_id}/field-reviews",
        json={"field_path": "costs.total_amount", "decision": "CONFIRMED"},
    ).status_code == 201

    detail = client.get(f"/api/medical/cases/{metadata.case_id}").json()
    revised_guide = detail["care_result"]["guide"]
    revised_guide["opening"]["content"] = "您的材料已经完成核对，下面是本次就医关怀指引。"
    revision = client.put(
        f"/api/medical/cases/{metadata.case_id}/care/revision",
        json={
            "base_content_fingerprint": detail["module_states"]["care"]["content_fingerprint"],
            "guide": revised_guide, "note": "改善开场表达",
        },
    )
    assert revision.status_code == 200, revision.text
    assert revision.json()["original_report"]["guide"]["opening"]["content"] \
        != revision.json()["revised_report"]["guide"]["opening"]["content"]

    for module in ("pain", "care"):
        response = client.post(
            f"/api/medical/cases/{metadata.case_id}/module-reviews",
            json={"module": module, "decision": "APPROVED", "comment": ""},
        )
        assert response.status_code == 201, response.text
    final_detail = client.get(f"/api/medical/cases/{metadata.case_id}").json()
    assert final_detail["final_gate"]["decision"] == "READY_FOR_FINAL_REVIEW"
    assert final_detail["pain_review_summary"]["pending"] == 0
    assert final_detail["care_revisions"][0]["note"] == "改善开场表达"
