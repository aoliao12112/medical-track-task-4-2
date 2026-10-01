from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from medical_claims.long_case import (
    chunk_documents,
    merge_claim_cases,
    validate_long_case_integrity,
)
from medical_claims.models import (
    ClaimCase,
    Diagnosis,
    DiagnosisType,
    EncounterType,
    Evidence,
    FieldStatus,
    FieldValue,
    FlagSeverity,
    QualityFlag,
)


def extracted(value, document_id: str, page: int, confidence: float = 0.95):
    return FieldValue(
        value=value,
        normalized_value=value,
        confidence=confidence,
        status=FieldStatus.EXTRACTED,
        evidence=[Evidence(document_id=document_id, page=page, text=str(value))],
    )


class LongCaseTest(unittest.TestCase):
    def test_chunk_documents_respects_page_and_character_limits(self) -> None:
        documents = [
            {"document_id": f"p{i}.jpg", "page": i, "full_text": "x" * 10, "blocks": []}
            for i in range(1, 11)
        ]
        chunks = chunk_documents(documents, max_pages=3, max_characters=25)
        self.assertEqual([len(chunk) for chunk in chunks], [2, 2, 2, 2, 2])
        self.assertEqual([item["page"] for chunk in chunks for item in chunk], list(range(1, 11)))

    def test_chunk_documents_uses_ocr_block_density(self) -> None:
        documents = [
            {
                "document_id": f"lab-{i}.jpg",
                "page": i,
                "full_text": "short",
                "blocks": [{} for _ in range(250)],
            }
            for i in range(1, 5)
        ]
        chunks = chunk_documents(
            documents,
            max_pages=6,
            max_characters=18_000,
            max_ocr_blocks=600,
        )
        self.assertEqual([len(chunk) for chunk in chunks], [2, 2])

    def test_merge_deduplicates_entities_and_reassigns_ids(self) -> None:
        first = ClaimCase(case_id="long-001", encounter_type=EncounterType.INPATIENT)
        first.hospitalization.admission_date = extracted(date(2025, 1, 1), "p1.jpg", 1)
        first.diagnoses = [
            Diagnosis(
                diagnosis_id="diag_1",
                name=extracted("肺炎", "p1.jpg", 1),
                diagnosis_type=DiagnosisType.DISCHARGE,
            )
        ]
        second = ClaimCase(case_id="long-001", encounter_type=EncounterType.INPATIENT)
        second.diagnoses = [
            Diagnosis(
                diagnosis_id="diag_1",
                name=extracted("肺炎", "p2.jpg", 2, 0.98),
                diagnosis_type=DiagnosisType.DISCHARGE,
            )
        ]
        second.quality_flags = [
            QualityFlag(
                code="MISSING_DIAGNOSIS",
                severity=FlagSeverity.WARNING,
                message="该分块没有诊断",
            )
        ]
        documents = [
            {"document_id": "p1.jpg", "page": 1, "full_text": "肺炎", "blocks": []},
            {"document_id": "p2.jpg", "page": 2, "full_text": "肺炎", "blocks": []},
        ]
        merged = merge_claim_cases("long-001", [first, second], documents, chunk_count=2)
        self.assertEqual(len(merged.diagnoses), 1)
        self.assertEqual(merged.diagnoses[0].diagnosis_id, "diag_1")
        self.assertEqual(len(merged.diagnoses[0].name.evidence), 2)
        self.assertIn("LONG_CASE_CHUNKED", {flag.code for flag in merged.quality_flags})
        self.assertNotIn("MISSING_DIAGNOSIS", {flag.code for flag in merged.quality_flags})

    def test_merge_marks_conflicting_case_level_values_for_review(self) -> None:
        first = ClaimCase(case_id="long-002", encounter_type=EncounterType.INPATIENT)
        second = ClaimCase(case_id="long-002", encounter_type=EncounterType.INPATIENT)
        first.costs.total_amount = extracted(Decimal("100.00"), "p1.jpg", 1, 0.90)
        second.costs.total_amount = extracted(Decimal("101.00"), "p2.jpg", 2, 0.99)
        documents = [
            {"document_id": "p1.jpg", "page": 1, "full_text": "100.00", "blocks": []},
            {"document_id": "p2.jpg", "page": 2, "full_text": "101.00", "blocks": []},
        ]
        merged = merge_claim_cases("long-002", [first, second], documents, chunk_count=2)
        self.assertEqual(merged.costs.total_amount.value, Decimal("101.00"))
        self.assertEqual(merged.costs.total_amount.status, FieldStatus.REVIEW_REQUIRED)
        self.assertIn("LONG_CASE_FIELD_CONFLICT", {flag.code for flag in merged.quality_flags})

    def test_integrity_check_detects_unknown_evidence_document(self) -> None:
        case = ClaimCase(case_id="long-003")
        case.diagnoses = [
            Diagnosis(
                diagnosis_id="diag_1",
                name=extracted("腰痛", "missing.jpg", 1),
                diagnosis_type=DiagnosisType.PRIMARY,
            )
        ]
        documents = [
            {"document_id": "p1.jpg", "page": 1, "full_text": "腰痛", "blocks": []}
        ]
        flags = validate_long_case_integrity(case, documents)
        self.assertIn("EVIDENCE_DOCUMENT_UNKNOWN", {flag.code for flag in flags})


if __name__ == "__main__":
    unittest.main()
