from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from medical_claims.models import (
    ClaimCase,
    EncounterType,
    Evidence,
    FieldStatus,
    FieldValue,
    Hospitalization,
)
from medical_claims.pipeline import ClaimPipeline
from medical_claims.providers import (
    DocumentInput,
    LlmProvider,
    MedicalEntity,
    MedicalNlpProvider,
    OcrBlock,
    OcrDocument,
    OcrProvider,
)


class FakeOcr(OcrProvider):
    def recognize(self, document: DocumentInput) -> OcrDocument:
        return OcrDocument(
            document_id=document.document_id,
            blocks=[OcrBlock(text="入院日期：2026-09-01", page=1, confidence=0.99)],
        )


class FakeNlp(MedicalNlpProvider):
    def extract_entities(self, document: OcrDocument) -> list[MedicalEntity]:
        return []


class FakeLlm(LlmProvider):
    def structure_case(self, case_id, documents, entities, output_schema) -> ClaimCase:
        evidence = Evidence(
            document_id=documents[0].document_id,
            page=1,
            text="入院日期：2026-09-01",
        )
        return ClaimCase(
            case_id=case_id,
            encounter_type=EncounterType.INPATIENT,
            hospitalization=Hospitalization(
                admission_date=FieldValue[date](
                    value=date(2026, 9, 1),
                    confidence=0.99,
                    status=FieldStatus.EXTRACTED,
                    evidence=[evidence],
                )
            ),
        )


class PipelineTest(unittest.TestCase):
    def test_pipeline_keeps_requested_case_id(self) -> None:
        pipeline = ClaimPipeline(FakeOcr(), FakeNlp(), FakeLlm())
        result = pipeline.process(
            "synthetic_001",
            [DocumentInput(document_id="doc_001", path=Path("sample.jpg"))],
        )
        self.assertEqual(result.case_id, "synthetic_001")
        self.assertEqual(result.hospitalization.admission_date.value, date(2026, 9, 1))

    def test_extracted_field_requires_evidence(self) -> None:
        with self.assertRaises(ValueError):
            FieldValue[str](
                value="甲型流感",
                confidence=0.9,
                status=FieldStatus.EXTRACTED,
            )


if __name__ == "__main__":
    unittest.main()

