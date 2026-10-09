import inspect

import pytest

from medical_claims.external_model_policy import (
    ExternalModelAuthorizationError,
    ExternalModelPurpose,
    require_external_model_authorization,
)
from medical_claims.models import ClaimCase, Diagnosis, Evidence, FieldStatus, FieldValue
from medical_claims.quality_phase1_models import PatientRecommendationContext
from medical_claims.recommendation_intent import build_recommendation_fact_pack
from web.backend.app import generate_case_care_guide, get_phase1_hospital_recommendations


def cataract_case():
    field = FieldValue(
        value="老年性白内障", normalized_value="老年性白内障", confidence=0.95,
        status=FieldStatus.EXTRACTED,
        evidence=[Evidence(document_id="deidentified.jpg", page=1, text="老年性白内障")],
    )
    return ClaimCase(case_id="PRIVATE-CASE-ID", diagnoses=[Diagnosis(diagnosis_id="D1", name=field)])


@pytest.mark.parametrize("purpose", list(ExternalModelPurpose))
def test_care_and_recommendation_intent_share_authorized_deidentified_policy(monkeypatch, purpose):
    monkeypatch.setenv("EXTERNAL_MODEL_PROCESSING_ENABLED", "true")
    require_external_model_authorization({
        "consent_status": "authorized",
        "deidentification_status": "confirmed",
    }, purpose)


@pytest.mark.parametrize("purpose", list(ExternalModelPurpose))
def test_both_external_model_purposes_are_denied_by_the_same_policy(monkeypatch, purpose):
    monkeypatch.setenv("EXTERNAL_MODEL_PROCESSING_ENABLED", "true")
    with pytest.raises(ExternalModelAuthorizationError):
        require_external_model_authorization({
            "consent_status": "unknown",
            "deidentification_status": "confirmed",
        }, purpose)


def test_third_and_fourth_question_endpoints_call_the_same_policy_helper():
    assert "require_external_model_authorization" in inspect.getsource(generate_case_care_guide)
    assert "require_external_model_authorization" in inspect.getsource(get_phase1_hospital_recommendations)


def test_recommendation_intent_fact_pack_excludes_identifiers_and_ocr_text():
    case = cataract_case()
    facts = build_recommendation_fact_pack(case, PatientRecommendationContext(
        case_id=case.case_id, disease_group="cataract", city="重庆市", province="重庆市",
    ))
    serialized = str(facts)
    assert "case_id" not in facts
    assert "ocr" not in serialized.lower()
    assert "document_id" not in serialized
    assert set(facts) == {
        "upstream_clinical_context",
        "recommendation_region", "encounter_type", "hospitalization_department",
        "diagnoses", "procedures", "medical_events",
    }
