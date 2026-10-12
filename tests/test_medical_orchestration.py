"""Module-level MEDICAL generation, review and invalidation regression tests."""
from pathlib import Path

from medical_claims.auth import hash_password
from medical_claims.care_engine import build_fallback_draft, finalize_care_guide
from medical_claims.care_models import GenerationMethod
from medical_claims.medical_orchestration import (
    GenerationStatus, MedicalModule, eligibility, modules_affected_by,
)
from medical_claims.models import (
    Certainty, ClaimCase, Costs, Diagnosis, DiagnosisType, Evidence,
    FieldStatus, FieldValue, Medication,
)
from medical_claims.pain_engine import analyze_pain_points
from medical_claims.pain_models import (
    DetectorInfo, DetectorMethod, PainDimension, PainPoint, PainPointReport,
    PainPointSummary, PainPointType, PainSeverity,
)
from medical_claims.quality_phase1_service import run_treatment_evaluation
from medical_claims.quality_phase1_models import TreatmentEvaluationRequestV2
from medical_claims.review_models import FieldReviewDecision, FieldReviewRequest, ReviewDecision
from medical_claims.storage import CaseStorage
from medical_claims.workflow_models import CreateCaseRequest
from fastapi import HTTPException
from web.backend.app import MedicalModuleReviewRequest, _care_review_payload, _medical_review_matrix


def field(value, *, status=FieldStatus.EXTRACTED, confidence=.95):
    return FieldValue(
        value=value, status=status, confidence=confidence,
        evidence=[Evidence(document_id="fixture.jpg", page=1, text=str(value))],
    )


def case_fixture(case_id):
    return ClaimCase(
        case_id=case_id,
        diagnoses=[Diagnosis(
            diagnosis_id="D1", diagnosis_type=DiagnosisType.PRIMARY,
            certainty=Certainty.CONFIRMED, name=field("肺炎"),
        )],
        medications=[Medication(medication_id="M1", name=field("阿莫西林"))],
        costs=Costs(total_amount=field(
            1000, status=FieldStatus.REVIEW_REQUIRED, confidence=.7,
        )),
    )


def pain_fixture(case_id):
    return PainPointReport(
        case_id=case_id,
        summary=PainPointSummary(total=1, clinical=0, cost=0, experience=1, high_priority=0),
        pain_points=[PainPoint(
            pain_point_id="P1", dimension=PainDimension.EXPERIENCE,
            type=PainPointType.CARE_GUIDANCE_INCOMPLETE, title="复诊安排需要确认",
            severity=PainSeverity.MEDIUM, severity_score=50, confidence=.8,
            description="材料中未见明确复诊时间。",
            evidence=[Evidence(document_id="fixture.jpg", page=1, text="出院记录")],
            related_fields=["diagnoses[0].name"], recommended_action="复诊时向医生确认",
            review_required=True,
            detector=DetectorInfo(method=DetectorMethod.RULE, version="test", rule_id="TEST"),
        )],
    )


def storage_fixture(tmp_path: Path):
    storage = CaseStorage(tmp_path / "competition.db")
    medical_id = storage.create_user(
        username="medical-orchestration", password_hash=hash_password("password-123"),
        role_code="MEDICAL", display_name="测试复核人员",
    )
    metadata = storage.create_case(CreateCaseRequest(
        claim_event_id="ORCH-1", data_source="synthetic_test",
        consent_status="authorized", deidentification_status="confirmed",
    ))
    storage.assign_case_participant(metadata.case_id, medical_id, "MEDICAL_REVIEWER")
    case = case_fixture(metadata.case_id)
    storage.save_structured(metadata.case_id, case)
    # Formal MEDICAL details are visible only after CLAIM has handed the case
    # into the care workflow. This fixture models that business transition.
    with storage._connect() as db:
        db.execute(
            "UPDATE cases SET care_status='CARE_TRIGGERED' WHERE case_id=?",
            (metadata.case_id,),
        )
    return storage, medical_id, case


def test_pending_noncritical_field_does_not_block_modules():
    case = case_fixture("CASE-ELIGIBLE")
    assert eligibility(MedicalModule.PAIN, case, pain_available=False)[0]
    assert eligibility(MedicalModule.CARE, case, pain_available=True)[0]
    assert eligibility(MedicalModule.TREATMENT_QUALITY, case, pain_available=True)[0]
    assert eligibility(MedicalModule.RECOMMENDATION, case, pain_available=True, city="上海")[0]


def test_primary_diagnosis_conflict_only_blocks_dependent_recommendation():
    case = case_fixture("CASE-CONFLICT")
    case.diagnoses[0].name = field("肺炎", status=FieldStatus.CONFLICT)
    assert eligibility(MedicalModule.PAIN, case, pain_available=False)[0]
    allowed, blockers, _ = eligibility(
        MedicalModule.RECOMMENDATION, case, pain_available=True, city="上海",
    )
    assert not allowed
    assert "主病种无法可靠确定" in blockers


def test_pending_diagnosis_can_prepare_care_but_not_recommendation():
    case = case_fixture("CASE-PENDING-DIAGNOSIS")
    case.diagnoses[0].name = field("肺炎", status=FieldStatus.REVIEW_REQUIRED)
    assert eligibility(MedicalModule.CARE, case, pain_available=True)[0]
    assert not eligibility(
        MedicalModule.RECOMMENDATION, case, pain_available=True, city="上海",
    )[0]


def test_dependency_map_keeps_cost_away_from_recommendation():
    affected = modules_affected_by("costs.total_amount")
    assert MedicalModule.PAIN in affected
    assert MedicalModule.CARE in affected
    assert MedicalModule.TREATMENT_QUALITY in affected
    assert MedicalModule.RECOMMENDATION not in affected
    assert modules_affected_by("diagnoses[0].name") == set(MedicalModule)


def test_effective_case_drives_generation_and_selective_stale_state(tmp_path):
    storage, medical_id, raw = storage_fixture(tmp_path)
    state = storage.get_effective_case_state(raw.case_id)
    effective = ClaimCase.model_validate(state["effective"])
    pain = analyze_pain_points(effective, [], [])
    storage.save_medical_pain(raw.case_id, pain, raw, state["effective_fingerprint"], medical_id)
    care = finalize_care_guide(
        effective, build_fallback_draft(effective, pain, None, "肺炎"), None,
        GenerationMethod.RULES_FALLBACK, [],
    )
    storage.save_medical_care(raw.case_id, care, raw, state["effective_fingerprint"], medical_id)
    storage.record_medical_module_generation(
        raw.case_id, "recommendation", state["effective_fingerprint"], medical_id,
    )
    storage.save_field_review(medical_id, raw.case_id, FieldReviewRequest(
        field_path="costs.total_amount", decision=FieldReviewDecision.CORRECTED,
        reviewed_value=1200, reason="依据费用清单修正",
    ))
    updated = storage.get_effective_case_state(raw.case_id)
    assert updated["effective"]["costs"]["total_amount"]["value"] == 1200
    with storage._connect() as db:
        assert storage._latest_module_fingerprint(db, raw.case_id, "pain") != updated["effective_fingerprint"]
        assert storage._latest_module_fingerprint(db, raw.case_id, "care") != updated["effective_fingerprint"]
        assert storage._latest_module_fingerprint(db, raw.case_id, "recommendation") == updated["effective_fingerprint"]


def test_quality_persists_six_independent_observations(tmp_path):
    storage, medical_id, raw = storage_fixture(tmp_path)
    state = storage.get_effective_case_state(raw.case_id)
    effective = ClaimCase.model_validate(state["effective"])
    pain = analyze_pain_points(effective, [], [])
    evaluation = run_treatment_evaluation(TreatmentEvaluationRequestV2(
        case=effective, disease_group="pneumonia", pain_report=pain,
    ))
    storage.save_treatment_evaluation(
        raw.case_id, evaluation, "pneumonia", state["effective_fingerprint"], medical_id,
    )
    with storage._connect() as db:
        rows = db.execute(
            "SELECT metric_code, status FROM provider_quality_observations "
            "WHERE case_id=? AND provider_type='TREATMENT'", (raw.case_id,),
        ).fetchall()
    assert len(rows) == 6
    assert {row["status"] for row in rows} <= {"OBSERVED", "REVIEW_REQUIRED", "DATA_UNAVAILABLE"}


def test_module_review_is_bound_to_current_result(tmp_path):
    storage, medical_id, raw = storage_fixture(tmp_path)
    state = storage.get_effective_case_state(raw.case_id)
    effective = ClaimCase.model_validate(state["effective"])
    pain = analyze_pain_points(effective, [], [])
    storage.save_medical_pain(raw.case_id, pain, raw, state["effective_fingerprint"], medical_id)
    content = storage.module_review_content(raw.case_id, "pain")
    storage.save_module_review(
        medical_id, raw.case_id, "pain_report", ReviewDecision.APPROVED, "", content,
    )
    detail = storage.get_medical_case_detail(medical_id, raw.case_id)
    assert detail["module_states"]["pain"]["review_status"] == "APPROVED"
    changed = pain.model_copy(update={"analysis_warnings": ["new version"]})
    storage.save_medical_pain(raw.case_id, changed, raw, state["effective_fingerprint"], medical_id)
    detail = storage.get_medical_case_detail(medical_id, raw.case_id)
    assert detail["module_states"]["pain"]["review_status"] == "REVIEW_REQUIRED"


def test_identical_regeneration_does_not_reuse_old_module_review(tmp_path):
    storage, medical_id, raw = storage_fixture(tmp_path)
    state = storage.get_effective_case_state(raw.case_id)
    effective = ClaimCase.model_validate(state["effective"])
    pain = analyze_pain_points(effective, [], [])
    storage.save_medical_pain(raw.case_id, pain, raw, state["effective_fingerprint"], medical_id)
    storage.save_module_review(
        medical_id, raw.case_id, "pain_report", ReviewDecision.APPROVED, "",
        storage.module_review_content(raw.case_id, "pain"),
    )
    assert storage.get_medical_case_detail(
        medical_id, raw.case_id,
    )["module_states"]["pain"]["review_status"] == "APPROVED"
    storage.save_medical_pain(raw.case_id, pain, raw, state["effective_fingerprint"], medical_id)
    assert storage.get_medical_case_detail(
        medical_id, raw.case_id,
    )["module_states"]["pain"]["review_status"] == "REVIEW_REQUIRED"


def test_field_pending_count_excludes_pain_and_care_review_flags(tmp_path):
    storage, medical_id, raw = storage_fixture(tmp_path)
    state = storage.get_effective_case_state(raw.case_id)
    effective = ClaimCase.model_validate(state["effective"])
    pain = analyze_pain_points(effective, [], [])
    storage.save_medical_pain(raw.case_id, pain, raw, state["effective_fingerprint"], medical_id)
    care = finalize_care_guide(
        effective, build_fallback_draft(effective, pain, None, "肺炎"), None,
        GenerationMethod.RULES_FALLBACK, [],
    )
    storage.save_medical_care(raw.case_id, care, raw, state["effective_fingerprint"], medical_id)
    storage.save_field_review(medical_id, raw.case_id, FieldReviewRequest(
        field_path="costs.total_amount", decision=FieldReviewDecision.CONFIRMED,
    ))
    detail = storage.get_medical_case_detail(medical_id, raw.case_id)
    assert detail["field_review_summary"]["pending"] == 0
    assert detail["field_review_summary"]["unresolved"] == 0
    assert detail["pending_confirmation_count"] == 0
    assert detail["medical_attention_count"] > 0


def test_pain_review_overlay_edits_and_hides_without_overwriting_model_result(tmp_path):
    storage, medical_id, raw = storage_fixture(tmp_path)
    state = storage.get_effective_case_state(raw.case_id)
    effective = ClaimCase.model_validate(state["effective"])
    pain = pain_fixture(raw.case_id)
    storage.save_medical_pain(raw.case_id, pain, raw, state["effective_fingerprint"], medical_id)
    point = pain.pain_points[0]
    storage.save_module_review(
        medical_id, raw.case_id, "pain_report", ReviewDecision.APPROVED, "已逐条复核",
        storage.module_review_content(raw.case_id, "pain"),
        {"item_decisions": [{
            "pain_point_id": point.pain_point_id, "decision": "EDIT",
            "title": "人工修订标题", "description": "人工修订说明",
            "recommended_action": "复诊时向医生确认",
        }]},
    )
    detail = storage.get_medical_case_detail(medical_id, raw.case_id)
    assert detail["pain_result"]["pain_points"][0]["title"] == point.title
    assert detail["pain_effective_result"]["pain_points"][0]["title"] == "人工修订标题"
    assert detail["pain_effective_result"]["pain_points"][0]["review_required"] is False
    storage.save_module_review(
        medical_id, raw.case_id, "pain_report", ReviewDecision.APPROVED, "隐藏该项",
        storage.module_review_content(raw.case_id, "pain"),
        {"item_decisions": [{"pain_point_id": point.pain_point_id, "decision": "HIDE"}]},
    )
    detail = storage.get_medical_case_detail(medical_id, raw.case_id)
    assert all(item["pain_point_id"] != point.pain_point_id
               for item in detail["pain_effective_result"]["pain_points"])


def test_pain_regeneration_invalidates_item_overlay(tmp_path):
    storage, medical_id, raw = storage_fixture(tmp_path)
    state = storage.get_effective_case_state(raw.case_id)
    effective = ClaimCase.model_validate(state["effective"])
    pain = pain_fixture(raw.case_id)
    storage.save_medical_pain(raw.case_id, pain, raw, state["effective_fingerprint"], medical_id)
    point = pain.pain_points[0]
    storage.save_module_review(
        medical_id, raw.case_id, "pain_report", ReviewDecision.APPROVED, "隐藏该项",
        storage.module_review_content(raw.case_id, "pain"),
        {"item_decisions": [{"pain_point_id": point.pain_point_id, "decision": "HIDE"}]},
    )
    storage.save_medical_pain(raw.case_id, pain, raw, state["effective_fingerprint"], medical_id)
    detail = storage.get_medical_case_detail(medical_id, raw.case_id)
    assert detail["pain_item_decisions"] == []
    assert detail["module_states"]["pain"]["review_status"] == "REVIEW_REQUIRED"


def test_care_revision_allows_emotional_text_without_evidence_but_rejects_invalid_fact_reference():
    case = case_fixture("CASE-CARE-REVISION")
    pain = pain_fixture(case.case_id)
    report = finalize_care_guide(
        case, build_fallback_draft(case, pain, None, "肺炎"), None,
        GenerationMethod.RULES_FALLBACK, [],
    )
    guide = report.guide.model_dump(mode="json")
    guide["opening"]["evidence_fields"] = []
    guide["psychological_support"]["evidence_fields"] = []
    valid = MedicalModuleReviewRequest(
        module=MedicalModule.CARE, decision="APPROVED",
        care_revision={"guide": guide},
    )
    payload = _care_review_payload(
        case.case_id, report.model_dump(mode="json"), case.model_dump(mode="json"), valid,
    )
    assert payload["validation"]["invalid_evidence"] == []
    guide["diagnosis_explanation"]["evidence_fields"] = ["diagnoses[9].name"]
    invalid = MedicalModuleReviewRequest(
        module=MedicalModule.CARE, decision="APPROVED",
        care_revision={"guide": guide},
    )
    try:
        _care_review_payload(
            case.case_id, report.model_dump(mode="json"), case.model_dump(mode="json"), invalid,
        )
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "引用不存在" in str(exc.detail)
    else:
        raise AssertionError("无效事实引用不应允许通过")


def test_care_revision_overlay_preserves_original_and_drives_delivery_snapshot(tmp_path):
    storage, medical_id, raw = storage_fixture(tmp_path)
    state = storage.get_effective_case_state(raw.case_id)
    effective = ClaimCase.model_validate(state["effective"])
    pain = pain_fixture(raw.case_id)
    storage.save_medical_pain(raw.case_id, pain, raw, state["effective_fingerprint"], medical_id)
    care = finalize_care_guide(
        effective, build_fallback_draft(effective, pain, None, "肺炎"), None,
        GenerationMethod.RULES_FALLBACK, [],
    )
    storage.save_medical_care(raw.case_id, care, raw, state["effective_fingerprint"], medical_id)
    guide = care.guide.model_dump(mode="json")
    guide["opening"]["content"] = "这是医疗人员修订后的开场关怀。"
    storage.save_module_review(
        medical_id, raw.case_id, "care_guide", ReviewDecision.APPROVED, "已修订",
        storage.module_review_content(raw.case_id, "care"),
        {"care_revision": {"guide": guide}},
    )
    detail = storage.get_medical_case_detail(medical_id, raw.case_id)
    assert detail["care_result"]["guide"]["opening"]["content"] != guide["opening"]["content"]
    assert detail["care_effective_result"]["guide"]["opening"]["content"] == guide["opening"]["content"]
    snapshot = storage.delivery_input_snapshot(raw.case_id)
    assert snapshot["results"]["care_result"]["guide"]["opening"]["content"] == guide["opening"]["content"]


def test_final_review_matrix_blocks_stale_and_unapproved_customer_visible_modules():
    detail = {
        "structured_result": {"case_id": "C1"},
        "field_review_summary": {"pending": 0, "unresolved": 0},
        "module_states": {
            "pain": {"result_available": True, "generation_status": "GENERATED", "review_status": "APPROVED"},
            "care": {"result_available": True, "generation_status": "STALE", "review_status": "APPROVED"},
            "treatment_quality": {"result_available": True, "generation_status": "GENERATED", "review_status": "REVIEW_REQUIRED"},
            "recommendation": {"result_available": False, "generation_status": "READY", "review_status": "NOT_REVIEWED"},
        },
    }
    matrix = _medical_review_matrix(detail)
    assert matrix["can_final_approve"] is False
    assert "温度指引结果已过期" in matrix["blockers"]
    assert "本次就医品质评价尚未通过医疗复核" in matrix["blockers"]
    assert all("推荐" not in item for item in matrix["blockers"])


def test_noncritical_cost_conflict_does_not_force_case_delivery_review():
    from medical_claims.care_models import CareSafetyCheck, ReviewStatus, SafetyStatus
    from medical_claims.delivery_gate import GateDecision, evaluate_delivery_gate
    case = case_fixture("CASE-GATE")
    pain = analyze_pain_points(case, [], [])
    care = finalize_care_guide(
        case, build_fallback_draft(case, pain, None, "肺炎"), None,
        GenerationMethod.LLM, [],
    ).model_copy(update={
        "review_required": False, "knowledge_review_status": ReviewStatus.REVIEWED,
        "evidence_coverage": 1,
        "safety_checks": [CareSafetyCheck(code="SAFE", status=SafetyStatus.PASSED, message="通过")],
    })
    assert evaluate_delivery_gate(care, case, pain, {"reports": []}).decision == GateDecision.AUTO_DELIVER


def test_contradicted_recommendation_is_strongly_blocked():
    from medical_claims.care_models import CareSafetyCheck, ReviewStatus, SafetyStatus
    from medical_claims.delivery_gate import GateDecision, evaluate_delivery_gate
    case = case_fixture("CASE-CONTRADICTED")
    pain = analyze_pain_points(case, [], [])
    care = finalize_care_guide(
        case, build_fallback_draft(case, pain, None, "肺炎"), None,
        GenerationMethod.LLM, [],
    ).model_copy(update={
        "review_required": False, "knowledge_review_status": ReviewStatus.REVIEWED,
        "evidence_coverage": 1,
        "safety_checks": [CareSafetyCheck(code="SAFE", status=SafetyStatus.PASSED, message="通过")],
    })
    recommendation = {"reports": [{"status": "available", "report": {
        "warnings": [], "recommendations": [{
            "entity_id": "HOSPITAL-X", "confidence": .8,
            "evidence_status": "CONTRADICTED",
            "reasons": [{"evidence_fields": ["diagnoses"]}, {"evidence_fields": ["city"]}],
        }],
    }}]}
    assert evaluate_delivery_gate(care, case, pain, recommendation).decision == GateDecision.BLOCKED
