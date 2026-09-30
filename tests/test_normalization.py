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
    def test_frequency_result_unit_and_daily_limit_are_normalized(self) -> None:
        medication_text = "对乙酰氨基酚片0.5g，必要时服用，24小内不超过2g"
        case = ClaimCase(
            case_id="normalize-001",
            medications=[
                Medication(
                    medication_id="med-1",
                    name=extracted("对乙酰氨基酚片", medication_text),
                    frequency=extracted("bid", medication_text),
                )
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
        self.assertEqual(normalized.medications[0].max_daily_dose.value, "2g")
        self.assertEqual(normalized.medications[0].max_daily_dose.normalized_value, "2g/24h")
        self.assertEqual(normalized.medications[0].max_daily_dose.status, FieldStatus.REVIEW_REQUIRED)
        result = normalized.examinations[0].results[0]
        self.assertEqual(result.value.value, "4.5")
        self.assertEqual(result.unit.value, "×10*/L")
        self.assertEqual(result.unit.status, FieldStatus.REVIEW_REQUIRED)


if __name__ == "__main__":
    unittest.main()
