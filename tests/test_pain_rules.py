from __future__ import annotations

import unittest
from decimal import Decimal

from medical_claims.models import (
    AbnormalFlag,
    ClaimCase,
    Diagnosis,
    Evidence,
    Examination,
    ExaminationResult,
    FieldStatus,
    FieldValue,
    Medication,
    ReconciliationStatus,
)
from medical_claims.pain_engine import analyze_pain_points
from medical_claims.pain_evaluation import evaluate_pain_points
from medical_claims.pain_models import (
    DetectorInfo,
    DetectorMethod,
    PainDimension,
    PainPoint,
    PainPointType,
    PainSeverity,
)


def extracted(value, text: str, confidence: float = 0.95):
    return FieldValue(
        value=value,
        confidence=confidence,
        status=FieldStatus.EXTRACTED,
        evidence=[Evidence(document_id="doc-1", page=1, text=text)],
    )


class PainRulesTest(unittest.TestCase):
    def test_detects_three_dimensions_with_traceable_evidence(self) -> None:
        case = ClaimCase(case_id="pain-001")
        case.costs.total_amount = extracted(Decimal("1000"), "医疗总费用1000元")
        case.costs.personal_burden.within_policy_self_pay = extracted(
            Decimal("300"), "个人自付300元"
        )
        case.costs.personal_burden.outside_policy_self_expense = extracted(
            Decimal("250"), "个人自费250元"
        )
        case.costs.reconciliation.status = ReconciliationStatus.WARNING
        case.costs.reconciliation.difference = Decimal("20")
        case.medications = [
            Medication(
                medication_id="med-1",
                name=extracted("某药片", "某药片"),
            )
        ]
        case.diagnoses = [
            Diagnosis(
                diagnosis_id="dx-1",
                name=extracted("冠状动脉粥样硬化性心脏病", "冠状动脉粥样硬化性心脏病"),
            )
        ]
        case.examinations = [
            Examination(
                examination_id="exam-1",
                name=extracted("病原检测", "病原检测"),
                results=[
                    ExaminationResult(
                        item_name=extracted("甲型流感抗原", "甲型流感抗原"),
                        value=extracted("阳性", "结果：阳性"),
                        abnormal_flag=AbnormalFlag.POSITIVE,
                    )
                ],
            )
        ]

        report = analyze_pain_points(case)
        types = {point.type for point in report.pain_points}
        self.assertIn(PainPointType.HIGH_OUTSIDE_POLICY_RATIO, types)
        self.assertIn(PainPointType.HIGH_PERSONAL_BURDEN_RATIO, types)
        self.assertIn(PainPointType.COST_RECONCILIATION_ANOMALY, types)
        self.assertIn(PainPointType.MEDICATION_INSTRUCTION_INCOMPLETE, types)
        self.assertIn(PainPointType.ABNORMAL_EXAMINATION_FOLLOWUP, types)
        self.assertIn(PainPointType.DIAGNOSIS_TERM_HARD_TO_UNDERSTAND, types)
        self.assertEqual(report.summary.total, len(report.pain_points))
        self.assertEqual(
            {point.dimension for point in report.pain_points},
            {PainDimension.CLINICAL, PainDimension.COST, PainDimension.EXPERIENCE},
        )
        self.assertTrue(all(point.evidence for point in report.pain_points))

    def test_payment_channel_does_not_create_personal_burden_pain(self) -> None:
        case = ClaimCase(case_id="pain-002")
        case.costs.total_amount = extracted(Decimal("1000"), "总费用1000元")
        case.costs.payment_channels.cash_payment = extracted(
            Decimal("900"), "现金支付900元"
        )
        report = analyze_pain_points(case)
        self.assertNotIn(
            PainPointType.HIGH_PERSONAL_BURDEN_RATIO,
            {point.type for point in report.pain_points},
        )
        self.assertNotIn(
            PainPointType.HIGH_OUTSIDE_POLICY_RATIO,
            {point.type for point in report.pain_points},
        )

    def test_prn_medication_with_safety_limit_does_not_require_fixed_duration(self) -> None:
        case = ClaimCase(
            case_id="pain-prn",
            medications=[
                Medication(
                    medication_id="med-prn",
                    name=extracted("对乙酰氨基酚片", "对乙酰氨基酚片0.5g"),
                    dose=extracted("0.5g", "对乙酰氨基酚片0.5g"),
                    frequency=extracted("必要时", "必要时4-6小时可重复用药"),
                    route=extracted("口服", "口服"),
                    usage_condition=extracted("体温≥38.5℃", "体温≥38.5℃时口服"),
                    max_daily_dose=extracted("2g/24h", "24小时内不超过2g"),
                )
            ],
        )
        report = analyze_pain_points(case)
        self.assertNotIn(
            PainPointType.MEDICATION_INSTRUCTION_INCOMPLETE,
            {point.type for point in report.pain_points},
        )

    def test_llm_point_without_source_evidence_is_rejected(self) -> None:
        case = ClaimCase(
            case_id="pain-003",
            diagnoses=[
                Diagnosis(
                    diagnosis_id="dx-1",
                    name=extracted("肺炎", "诊断：肺炎"),
                )
            ],
        )
        hallucinated = PainPoint(
            pain_point_id="PP-LLM-1",
            dimension=PainDimension.CLINICAL,
            type=PainPointType.MEDICATION_DIAGNOSIS_REVIEW,
            title="需要复核",
            severity=PainSeverity.MEDIUM,
            severity_score=60,
            confidence=0.7,
            description="模型候选",
            evidence=[Evidence(document_id="doc-1", page=1, text="原文不存在")],
            related_fields=["diagnoses[0]"],
            recommended_action="人工复核",
            detector=DetectorInfo(method=DetectorMethod.LLM, version="test"),
        )
        report = analyze_pain_points(case, [hallucinated])
        self.assertFalse(report.pain_points)
        self.assertTrue(any("缺少可溯源证据" in warning for warning in report.analysis_warnings))

    def test_pain_evaluation_reports_expert_agreement(self) -> None:
        case = ClaimCase(
            case_id="pain-004",
            medications=[
                Medication(
                    medication_id="med-1",
                    name=extracted("某药", "某药"),
                )
            ],
        )
        report = analyze_pain_points(case)
        result = evaluate_pain_points(report.model_dump(mode="json"), report.model_dump(mode="json"))
        self.assertEqual(result.expert_agreement, 1.0)
        self.assertEqual(result.f1, 1.0)
        self.assertTrue(result.target_passed)


if __name__ == "__main__":
    unittest.main()
