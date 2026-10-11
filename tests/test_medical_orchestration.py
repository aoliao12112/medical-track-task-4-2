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
from medical_claims.quality_phase1_service import run_treatment_evaluation
from medical_claims.quality_phase1_models import TreatmentEvaluationRequestV2
from medical_claims.review_models import FieldReviewDecision, FieldReviewRequest, ReviewDecision
from medical_claims.storage import CaseStorage
from medical_claims.workflow_models import CreateCaseRequest


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
