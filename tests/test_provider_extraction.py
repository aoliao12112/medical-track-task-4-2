from __future__ import annotations

import unittest

from medical_claims.provider_extraction import (
    build_safe_context,
    detect_disease_groups,
    extract_clinician_candidates,
    extract_institution_candidates,
    pseudonymous_id,
)
from medical_claims.provider_models import ClinicianRole
from medical_claims.providers import OcrBlock, OcrDocument


def document(*lines: str) -> OcrDocument:
    return OcrDocument(
        document_id="test-page.jpg",
        blocks=[OcrBlock(text=line, page=1, confidence=0.95) for line in lines],
    )


class ProviderExtractionTest(unittest.TestCase):
    def test_extracts_institution_and_role_labeled_clinicians(self) -> None:
        doc = document(
            "医疗机构名称：东湖市第一人民医院",
            "主治医师：张三",
            "报告医师：李四",
        )
        institutions = extract_institution_candidates([doc])
        clinicians = extract_clinician_candidates([doc])
        self.assertEqual(institutions[0].value, "东湖市第一人民医院")
        self.assertEqual(
            {(item.value, item.role) for item in clinicians},
            {
                ("张三", ClinicianRole.ATTENDING_PHYSICIAN),
                ("李四", ClinicianRole.REPORTING_PHYSICIAN),
            },
        )

    def test_negative_diagnosis_is_not_counted(self) -> None:
        groups = detect_disease_groups(
            [document("CT提示：未见明显骨折", "出院诊断：2型糖尿病、社区获得性肺炎")]
        )
        self.assertEqual(groups, ["diabetes", "pneumonia"])

    def test_normalizes_common_institution_prefix_noise(self) -> None:
        doc = document(
            "hospitalName上海市浦东新区公利医院",
            "收款单位（章）吉林医院",
        )
        values = [item.value for item in extract_institution_candidates([doc])]
        self.assertEqual(values, ["上海市浦东新区公利医院", "吉林医院"])

    def test_safe_context_never_contains_raw_names(self) -> None:
        context, private_mapping = build_safe_context(
            "CASE-001",
            [document("东湖市第一人民医院", "经治医师：王小明", "诊断：骨折")],
            b"unit-test-secret",
        )
        public_text = context.model_dump_json()
        self.assertNotIn("东湖市第一人民医院", public_text)
        self.assertNotIn("王小明", public_text)
        self.assertIn("东湖市第一人民医院", private_mapping["institutions"].values())
        self.assertIn("王小明", private_mapping["clinicians"].values())
        self.assertTrue(context.clinicians[0].ranking_eligible)

    def test_pseudonym_is_deterministic_and_secret_dependent(self) -> None:
        first = pseudonymous_id("HOSP", "某医院", b"secret-a")
        self.assertEqual(first, pseudonymous_id("HOSP", "某医院", b"secret-a"))
        self.assertNotEqual(first, pseudonymous_id("HOSP", "某医院", b"secret-b"))


if __name__ == "__main__":
    unittest.main()
