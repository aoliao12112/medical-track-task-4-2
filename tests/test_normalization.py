from __future__ import annotations

import unittest

from medical_claims.models import (
    ClaimCase,
    Evidence,
    Examination,
    ExaminationResult,
    FieldStatus,
    FieldValue,
    Medication,
    Procedure,
)
from medical_claims.normalization import normalize_case


def extracted(value: str, text: str) -> FieldValue[str]:
    return FieldValue[str](
        value=value,
        normalized_value=value,
        confidence=0.95,
        status=FieldStatus.EXTRACTED,
        evidence=[Evidence(document_id="doc-1", page=1, text=text)],
    )


class NormalizationTest(unittest.TestCase):
    def test_pure_imaging_is_not_kept_as_procedure(self) -> None:
        case = ClaimCase(
            case_id="normalize-imaging",
            procedures=[
                Procedure(
                    procedure_id="proc-1",
                    name=extracted("PET/CT躯干+局部显像", "PET/CT躯干+局部显像"),
                ),
                Procedure(
                    procedure_id="proc-2",
                    name=extracted("CT引导下经皮穿刺术前标记物植入术", "CT引导下经皮穿刺术前标记物植入术"),
                ),
            ],
        )

        normalized = normalize_case(case)

        self.assertEqual([item.name.value for item in normalized.procedures], ["CT引导下经皮穿刺术前标记物植入术"])
        self.assertEqual([item.name.value for item in normalized.examinations], ["PET/CT躯干+局部显像"])

    def test_frequency_result_unit_and_daily_limit_are_normalized(self) -> None:
        medication_text = "对乙酰氨基酚片0.5g，必要时4-6小时可重复用药，24小内不超过2g"
        case = ClaimCase(
            case_id="normalize-001",
            medications=[
                Medication(
                    medication_id="med-1",
                    name=extracted("对乙酰氨基酚片", medication_text),
                    frequency=extracted("bid", medication_text),
                    usage_condition=extracted("发热体温≥38.5℃时", medication_text),
                ),
                Medication(
                    medication_id="med-2",
                    name=extracted("0.9%氯化钠注射液", "0.9%氯化钠注射液250ml + 热毒宁注射液20ml"),
                    dose=extracted("250ml", "0.9%氯化钠注射液250ml + 热毒宁注射液20ml"),
                ),
            ],
            examinations=[
                Examination(
                    examination_id="exam-1",
                    name=extracted("血常规", "白细胞总数4.5×10*/L"),
                    results=[
                        ExaminationResult(
                            item_name=extracted("白细胞总数", "白细胞总数4.5×10*/L"),
                            value=extracted("4.5×10*/L", "白细胞总数4.5×10*/L"),
                            unit=extracted("×10*/L", "白细胞总数4.5×10*/L"),
                        )
                    ],
                )
            ],
        )

        normalized = normalize_case(case)

        self.assertEqual(normalized.medications[0].frequency.normalized_value, "每日2次")
        self.assertEqual(
            normalized.medications[0].additional_instructions.normalized_value,
            "必要时每4-6小时可重复；24小时内不超过2g",
        )
        self.assertEqual(normalized.medications[0].max_daily_dose.value, "2g")
        self.assertEqual(normalized.medications[0].max_daily_dose.normalized_value, "2g/24h")
        self.assertEqual(normalized.medications[0].max_daily_dose.status, FieldStatus.REVIEW_REQUIRED)
        self.assertEqual(normalized.medications[0].usage_condition.normalized_value, "体温≥38.5℃时使用")
        self.assertEqual(normalized.medications[1].usage_condition.normalized_value, "作为静脉输注溶媒")
        result = normalized.examinations[0].results[0]
        self.assertEqual(result.value.value, "4.5")
        self.assertEqual(result.unit.value, "×10*/L")
        self.assertEqual(result.unit.status, FieldStatus.REVIEW_REQUIRED)


if __name__ == "__main__":
    unittest.main()
