from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from medical_claims.long_case import (
    chunk_documents,
    enrich_discharge_summary_events,
    merge_claim_cases,
    validate_long_case_integrity,
)
from medical_claims.models import (
    ClaimCase,
    Diagnosis,
    DiagnosisType,
    EncounterType,
    Evidence,
    EventType,
    FieldStatus,
    FieldValue,
    FlagSeverity,
    MedicalEvent,
    Medication,
    Procedure,
    QualityFlag,
)
from medical_claims.quality_prompt import build_quality_case_context


def extracted(value, document_id: str, page: int, confidence: float = 0.95):
    return FieldValue(
        value=value,
        normalized_value=value,
        confidence=confidence,
        status=FieldStatus.EXTRACTED,
        evidence=[Evidence(document_id=document_id, page=page, text=str(value))],
    )


class LongCaseTest(unittest.TestCase):
    def test_discharge_summary_enrichment_preserves_high_value_events(self) -> None:
        case = ClaimCase(case_id="discharge-events", encounter_type=EncounterType.INPATIENT)
        case.hospitalization.admission_date = extracted(date(2025, 10, 27), "discharge-1.jpg", 1)
        case.hospitalization.discharge_date = extracted(date(2025, 10, 30), "discharge-2.jpg", 2)
        case.procedures = [
            Procedure(
                procedure_id="proc_1",
                name=extracted("单孔VATS左肺上叶部分肺切除术", "discharge-1.jpg", 1),
                performed_date=extracted(date(2025, 10, 28), "discharge-1.jpg", 1),
            )
        ]
        case.diagnoses = [
            Diagnosis(
                diagnosis_id="diag_1",
                name=extracted("肺部阴影", "discharge-1.jpg", 1),
                diagnosis_type=DiagnosisType.ADMISSION,
            ),
            Diagnosis(
                diagnosis_id="diag_2",
                name=extracted("肺良性病变", "discharge-2.jpg", 2),
                diagnosis_type=DiagnosisType.DISCHARGE,
            ),
            Diagnosis(
                diagnosis_id="diag_3",
                name=extracted("原位腺癌", "pathology.jpg", 4),
                diagnosis_type=DiagnosisType.PRIMARY,
            ),
        ]
        case.medications = [
            Medication(medication_id="med_1", name=extracted("注射用头孢唑林钠", "fees.jpg", 3))
        ]
        documents = [
            {
                "document_id": "discharge-1.jpg", "page": 1, "blocks": [],
                "full_text": (
                    "出院小结\n入院时主要症状及体征\n体检发现肺部阴影\n主要化验结果\n"
                    "病程与治疗结果 CT示左上叶亚实性结节。术中冰冻示腺上皮增生，有无癌变待石蜡。"
                    "入院后完善检查，给予控制感染，无明显手术禁忌症，"
                    "于2025/10/28全麻下行VATS左上叶部分肺切除术，"
                    "术后给予抗炎、止血、化痰及能量营养支持，术后恢复佳，嘱出院。"
                ),
            },
            {
                "document_id": "discharge-2.jpg", "page": 2, "blocks": [],
                "full_text": (
                    "出院小结\n治疗结果 好转\n出院诊断 肺良性病变，具体见石蜡报告。\n出院指导\n"
                    "1.术后约3周左右胸外科门诊复诊。复查时门诊查询正式病理报告，"
                    "根据正式病理报告制定后续用药治疗或者随访计划。\n"
                    "2.出院后每3天左右伤口换药，保持伤口干燥清洁，"
                    "术后3周左右伤口拆线（就近医院或者本院）。\n"
                    "3.止咳化痰等出院带药。4.加强营养，适当活动，循序渐进，避免感冒。\n"
                    "5.如有持续发热、剧烈胸痛等不适，及时急诊就诊。"
                ),
            },
            {
                "document_id": "fees.jpg", "page": 3, "blocks": [],
                "full_text": "住院费用清单 注射用头孢唑林钠 5.00瓶 7.35 36.75",
            },
            {
                "document_id": "pathology.jpg", "page": 4, "blocks": [],
                "full_text": "病理检查报告\n组织学类型：局灶癌变，原位腺癌。\n报告日期：2025-11-05",
            },
        ]

        enriched = enrich_discharge_summary_events(case, documents)
        descriptions = [item.description for item in enriched.events]
        for expected in (
            "无明显手术禁忌", "术后给予抗炎、止血、化痰及能量营养支持", "术后恢复佳",
            "术后约3周左右胸外科门诊复诊", "伤口换药", "伤口拆线",
            "持续发热、剧烈胸痛", "急诊就诊",
        ):
            self.assertTrue(any(expected in item for item in descriptions), expected)
        self.assertTrue(all(item.evidence for item in enriched.events))
        self.assertTrue(all(
            item.evidence[0].document_id.startswith("discharge-")
            or item.evidence[0].document_id == "pathology.jpg"
            for item in enriched.events
        ))
        linked = [
            item for item in enriched.events
            if "无明显手术禁忌" in item.description or "术后给予" in item.description
        ]
        self.assertTrue(linked)
        self.assertTrue(all("proc_1" in item.related_entity_ids for item in linked))
        pending_pathology = next(
            item for item in enriched.events
            if item.description == "出院诊断需结合正式石蜡病理报告确认"
        )
        final_pathology = next(
            item for item in enriched.events if "正式病理报告：局灶癌变，原位腺癌" in item.description
        )
        self.assertEqual(pending_pathology.event_time, date(2025, 10, 30))
        self.assertEqual(final_pathology.event_time, date(2025, 11, 5))
        self.assertIn("diag_3", final_pathology.related_entity_ids)

        medication = enriched.medications[0]
        for field in (medication.dose, medication.frequency, medication.route, medication.duration):
            self.assertIsNone(field.value)
            self.assertEqual(field.status, FieldStatus.NOT_PRESENT)

        quality_context = build_quality_case_context(enriched)
        quality_descriptions = [item["description"] for item in quality_context["medical_events"]]
        self.assertTrue(any("术后恢复佳" in item for item in quality_descriptions))
        self.assertTrue(any("伤口换药" in item for item in quality_descriptions))
        self.assertEqual(
            set(MedicalEvent.model_fields),
            {"event_id", "event_type", "event_time", "description", "confidence", "evidence", "related_entity_ids"},
        )

    def test_discharge_summary_enrichment_does_not_duplicate_existing_event(self) -> None:
        case = ClaimCase(case_id="discharge-dedupe")
        case.events = [
            MedicalEvent(
                event_id="event_1", event_type=EventType.OTHER,
                description="术后恢复佳", confidence=0.95,
                evidence=[Evidence(document_id="discharge.jpg", page=1, text="术后恢复佳")],
            )
        ]
        documents = [{
            "document_id": "discharge.jpg", "page": 1, "blocks": [],
            "full_text": "出院小结 病程与治疗结果 术后恢复佳，嘱出院。",
        }]
        enriched = enrich_discharge_summary_events(case, documents)
        self.assertEqual(
            sum(item.description == "术后恢复佳" for item in enriched.events), 1,
        )

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
