from __future__ import annotations

import unittest
from decimal import Decimal

from medical_claims.care_engine import (
    build_fallback_draft,
    finalize_care_guide,
    validate_care_guide,
)
from medical_claims.care_evaluation import evaluate_care_ratings
from medical_claims.care_knowledge import load_knowledge_cards, match_knowledge_card
from medical_claims.care_models import GenerationMethod, SafetyStatus
from medical_claims.care_prompt import build_case_fact_pack
from medical_claims.models import ClaimCase, Diagnosis, Evidence, FieldStatus, FieldValue


def extracted(value, text: str, document_id: str = "doc-1"):
    return FieldValue(
        value=value,
        normalized_value=value,
        confidence=0.95,
        status=FieldStatus.EXTRACTED,
        evidence=[Evidence(document_id=document_id, page=1, text=text)],
    )


class CareGuideTest(unittest.TestCase):
    def pneumonia_case(self) -> ClaimCase:
        case = ClaimCase(
            case_id="care-001",
            diagnoses=[
                Diagnosis(
                    diagnosis_id="dx-1",
                    name=extracted("肺炎", "出院诊断：肺炎"),
                )
            ],
        )
        case.costs.total_amount = extracted(Decimal("4500"), "医疗总费用4500元")
        case.costs.payment_channels.cash_payment = extracted(
            Decimal("600"), "个人现金支付600元"
        )
        return case

    def test_loads_and_matches_disease_cards(self) -> None:
        cards = load_knowledge_cards()
        self.assertGreaterEqual(len(cards), 4)
        card = match_knowledge_card(["社区获得性肺炎"], cards=cards)
        self.assertIsNotNone(card)
        self.assertEqual(card.disease, "肺炎")
        diabetes_card = match_knowledge_card(["2型糖尿病"], cards=cards)
        self.assertIsNotNone(diabetes_card)
        self.assertEqual(diabetes_card.disease, "糖尿病")
        fracture_card = match_knowledge_card(["桡骨骨折"], cards=cards)
        self.assertIsNotNone(fracture_card)
        self.assertEqual(fracture_card.disease, "骨折")
        nodule_card = match_knowledge_card(["双肺结节"], cards=cards)
        self.assertIsNotNone(nodule_card)
        self.assertEqual(nodule_card.disease, "肺结节")

    def test_fact_pack_does_not_treat_cash_as_self_pay(self) -> None:
        pack = build_case_fact_pack(self.pneumonia_case(), None)
        paths = {fact["path"] for fact in pack["facts"]}
        self.assertIn("costs.total_amount", paths)
        self.assertNotIn("costs.personal_burden.within_policy_self_pay", paths)
        self.assertNotIn("costs.personal_burden.outside_policy_self_expense", paths)

    def test_fallback_contains_all_required_sections(self) -> None:
        case = self.pneumonia_case()
        card = match_knowledge_card(["肺炎"])
        draft = build_fallback_draft(case, None, card, "肺炎")
        report = finalize_care_guide(
            case,
            draft,
            card,
            GenerationMethod.RULES_FALLBACK,
        )
        self.assertEqual(report.case_id, case.case_id)
        self.assertTrue(report.guide.recovery_plan)
        self.assertTrue(report.guide.follow_up_plan)
        self.assertTrue(report.guide.cost_explanation.content)
        self.assertTrue(report.review_required)  # 新生成的患者指引仍需逐病例事实复核

    def test_invalid_evidence_path_requires_review(self) -> None:
        case = self.pneumonia_case()
        card = match_knowledge_card(["肺炎"])
        draft = build_fallback_draft(case, None, card, "肺炎")
        draft.diagnosis_explanation.evidence_fields = ["diagnoses[99].name"]
        checks, _, _ = validate_care_guide(case, draft, card)
        evidence_check = next(item for item in checks if item.code == "EVIDENCE_PATHS")
        self.assertEqual(evidence_check.status, SafetyStatus.REVIEW)
        self.assertIn("diagnoses[99].name", evidence_check.field_paths)

    def test_blind_rating_requires_scores_and_zero_safety_errors(self) -> None:
        result = evaluate_care_ratings(
            [
                {
                    "case_id": "care-001",
                    "reviewer_id": "reviewer-01",
                    "warmth": 5,
                    "actionability": 4,
                    "personalization": 4,
                    "clarity": 5,
                    "factual_errors": 0,
                    "unsafe_suggestions": 0,
                },
                {
                    "case_id": "care-001",
                    "reviewer_id": "reviewer-02",
                    "warmth": 4,
                    "actionability": 4,
                    "personalization": 5,
                    "clarity": 4,
                    "factual_errors": 0,
                    "unsafe_suggestions": 0,
                },
            ]
        )
        self.assertTrue(result.target_passed)
        self.assertEqual(result.reviewers, 2)


if __name__ == "__main__":
    unittest.main()
