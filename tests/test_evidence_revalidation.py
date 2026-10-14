from copy import deepcopy

import pytest

from medical_claims.evidence_grounding import normalize_evidence_text, verify_case_evidence
from medical_claims.evidence_revalidation import revalidate_case
from medical_claims.models import (
    ClaimCase, Diagnosis, Evidence, FieldStatus, FieldValue, Medication, QualityFlag,
)


VERIFIER_WARNING = "Evidence verification failed；保留原值，请核对OCR原文与实体绑定。"


def review_case(*, text="诊断：肺炎", confidence=.98, status=FieldStatus.REVIEW_REQUIRED,
                extra_warnings=None, extra_flags=None):
    field = FieldValue(
        value="肺炎", normalized_value="肺炎", confidence=confidence, status=status,
        evidence=[Evidence(document_id="page", page=1, text=text)],
        warnings=[VERIFIER_WARNING, *(extra_warnings or [])],
    )
    case = ClaimCase(
        case_id="case-a",
        diagnoses=[Diagnosis(diagnosis_id="diag_1", name=field)],
        quality_flags=[
            QualityFlag(
                code="EVIDENCE_TEXT_NOT_FOUND", severity="warning",
                message="Evidence verification failed：原文证据无法定位",
                field_path="diagnoses[0].name", document_id="page",
            ),
            *(extra_flags or []),
        ],
    )
    documents = [{
        "document_id": "page", "page": 1, "full_text": "诊断：肺炎",
        "blocks": [{"text": "诊断：肺炎", "bbox": [0, 0, 100, 20]}],
    }]
    return case.model_dump(mode="json"), documents


def proposal(result):
    return next(item for item in result["proposals"] if item["field_path"] == "diagnoses[0].name")


def test_disappeared_evidence_failure_proposes_extracted_without_mutating_input():
    structured, documents = review_case()
    original = deepcopy(structured)
    item = proposal(revalidate_case(structured, documents))
    assert item["proposed_action"] == "PROPOSE_RESTORE_EXTRACTED"
    assert item["new_proposed_status"] == "extracted"
    assert item["warnings_removed"] == [VERIFIER_WARNING]
    assert structured == original


def test_persisting_evidence_failure_keeps_review_required():
    structured, documents = review_case(text="不存在的证据")
    item = proposal(revalidate_case(structured, documents))
    assert item["proposed_action"] == "KEEP_EVIDENCE_REVIEW"
    assert item["new_proposed_status"] == "review_required"
    assert "EVIDENCE_TEXT_NOT_FOUND" in item["new_evidence_codes"]


def test_new_failure_proposes_review_required_instead_of_silently_passing():
    structured, documents = review_case(text="不存在的证据", status=FieldStatus.EXTRACTED)
    structured["quality_flags"] = []
    structured["diagnoses"][0]["name"]["warnings"] = []
    item = proposal(revalidate_case(structured, documents))
    assert item["proposed_action"] == "KEEP_EVIDENCE_REVIEW"
    assert item["new_proposed_status"] == "review_required"


@pytest.mark.parametrize("decision", ["CONFIRMED", "CORRECTED", "UNRESOLVED"])
def test_manual_overlay_is_never_overwritten(decision):
    structured, documents = review_case()
    item = proposal(revalidate_case(
        structured, documents,
        {"diagnoses[0].name": {"decision": decision}},
    ))
    assert item["proposed_action"] == "PROTECT_MANUAL_OVERLAY"
    assert item["new_proposed_status"] == "review_required"
    assert item["manual_overlay"] == decision


def test_non_evidence_warning_is_retained_and_blocks_restoration():
    flag = QualityFlag(
        code="FIELD_CONFLICT", severity="warning", message="跨文档字段冲突",
        field_path="diagnoses[0].name",
    )
    structured, documents = review_case(
        extra_warnings=["模型仍提示语义不确定"], extra_flags=[flag],
    )
    item = proposal(revalidate_case(structured, documents))
    assert item["proposed_action"] == "KEEP_NON_EVIDENCE_REVIEW"
    assert "模型仍提示语义不确定" in item["warnings_retained"]


@pytest.mark.parametrize("confidence,status", [(.79, FieldStatus.REVIEW_REQUIRED), (.99, FieldStatus.CONFLICT)])
def test_low_confidence_and_conflict_are_not_released(confidence, status):
    structured, documents = review_case(confidence=confidence, status=status)
    item = proposal(revalidate_case(structured, documents))
    assert item["proposed_action"] == "KEEP_NON_EVIDENCE_REVIEW"
    assert item["new_proposed_status"] == status.value


def medication_case(*, document_id="医嘱单.jpg", evidence_text="阿莫西林 每次0.5g",
                    value="0.5g", duplicate=False):
    dose = FieldValue(
        value=value, normalized_value=value, confidence=.98,
        status=FieldStatus.REVIEW_REQUIRED,
        evidence=[Evidence(document_id=document_id, page=1, text=evidence_text)],
        warnings=[VERIFIER_WARNING],
    )
    medication = Medication(
        medication_id="med-1",
        name=FieldValue(value="阿莫西林", confidence=.99, status=FieldStatus.EXTRACTED,
                        evidence=[Evidence(document_id=document_id, page=1, text="阿莫西林")]),
        dose=dose,
    )
    medications = [medication]
    if duplicate:
        medications.append(Medication(
            medication_id="med-2",
            name=FieldValue(value="阿莫西林", confidence=.99, status=FieldStatus.EXTRACTED,
                            evidence=[Evidence(document_id=document_id, page=1, text="阿莫西林")]),
        ))
    case = ClaimCase(
        case_id="case-med", medications=medications,
        quality_flags=[QualityFlag(
            code="EVIDENCE_ENTITY_VALUE_UNBOUND", severity="warning",
            message="Evidence verification failed：旧绑定失败",
            field_path="medications[0].dose", document_id=document_id,
        )],
    )
    documents = [{
        "document_id": document_id, "page": 1,
        "full_text": evidence_text,
        "blocks": [{"text": evidence_text, "bbox": [0, 0, 200, 20]}],
    }]
    return case.model_dump(mode="json"), documents


def medication_proposal(structured, documents):
    result = revalidate_case(structured, documents)
    return next(item for item in result["proposals"]
                if item["field_path"] == "medications[0].dose")


def test_fee_list_specification_does_not_restore_medication_dose():
    structured, documents = medication_case(
        document_id="住院费用清单_1.jpg",
        evidence_text="阿莫西林 100ml:0.9g 单价12.00",
        value="100ml:0.9g",
    )
    item = medication_proposal(structured, documents)
    assert item["proposed_action"] == "KEEP_NON_EVIDENCE_REVIEW"
    assert item["new_proposed_status"] == "review_required"
    assert "商品规格" in item["reason"]


def test_medication_value_without_explicit_owner_does_not_restore():
    structured, documents = medication_case(
        evidence_text="阿莫西林\n每天三次", value="每天三次",
    )
    structured["medications"][0]["dose"]["evidence"][0]["text"] = "每天三次"
    item = medication_proposal(structured, documents)
    assert item["proposed_action"] == "KEEP_NON_EVIDENCE_REVIEW"
    assert item["new_proposed_status"] == "review_required"


def test_duplicate_medication_entity_does_not_restore_attribute():
    structured, documents = medication_case(duplicate=True)
    item = medication_proposal(structured, documents)
    assert item["proposed_action"] == "KEEP_NON_EVIDENCE_REVIEW"
    assert "重复同名" in item["reason"]


def test_meaningful_unit_and_ocr_decimal_space_are_normalized_safely():
    assert normalize_evidence_text("%") == "%"
    assert normalize_evidence_text("0. 1ml") == "0.1ml"
    assert normalize_evidence_text("1.2mg") != normalize_evidence_text("12mg")


def test_full_entity_name_in_evidence_avoids_false_competitor_mismatch():
    case = ClaimCase(
        case_id="case-entity",
        diagnoses=[
            Diagnosis(
                diagnosis_id="d1",
                name=FieldValue(
                    value="老年性白内障", confidence=.99, status=FieldStatus.EXTRACTED,
                    evidence=[Evidence(document_id="page", page=1,
                                       text="入院诊断：老年性白内障")],
                ),
            ),
            Diagnosis(
                diagnosis_id="d2",
                    name=FieldValue(value="高血压", confidence=.99,
                                    status=FieldStatus.EXTRACTED,
                                evidence=[Evidence(document_id="page", page=1,
                                                   text="高血压")]),
            ),
        ],
    )
    documents = [{
        "document_id": "page", "page": 1,
        "full_text": "入院诊断：老年性白内障；高血压",
        "blocks": [{"text": "入院诊断：老年性白内障；高血压",
                    "bbox": [0, 0, 300, 20]}],
    }]
    flags = verify_case_evidence(case, documents)
    assert not any(flag.code == "EVIDENCE_ENTITY_MISMATCH"
                   and flag.field_path == "diagnoses[0].name" for flag in flags)
