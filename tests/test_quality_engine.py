from __future__ import annotations

from decimal import Decimal
import unittest

from medical_claims.models import ClaimCase, Evidence, FieldStatus, FieldValue
from medical_claims.quality_engine import build_quality_portfolio, recommend_profiles
from medical_claims.quality_models import (
    MetricStatus,
    ProfileCaseRecord,
    ProfileEntityType,
    QualityDimension,
    QualityPortfolioRequest,
)
from pydantic import ValidationError
from web.backend.app import app


def extracted(value):
    return FieldValue(
        value=value,
        confidence=0.95,
        status=FieldStatus.EXTRACTED,
        evidence=[Evidence(document_id="doc", page=1, text="evidence")],
    )


def record(case_id: str, hospital: str, cost: str, feedback: float) -> ProfileCaseRecord:
    case = ClaimCase(case_id=case_id)
    case.costs.total_amount = extracted(Decimal(cost))
    case.diagnoses = []
    return ProfileCaseRecord(
        case=case,
        institution_id=hospital,
        disease_group="pneumonia",
        patient_feedback_score=feedback,
    )


class QualityEngineTest(unittest.TestCase):
    def test_quality_routes_are_registered(self) -> None:
        paths = {route.path for route in app.routes}
        self.assertIn("/api/quality/audit-summary", paths)
        self.assertIn("/api/quality/profiles/build", paths)
        self.assertIn("/api/quality/recommendations", paths)

    def test_missing_readmission_is_explicitly_unavailable(self) -> None:
        portfolio = build_quality_portfolio(
            [record("c1", "HOSP-A", "1000", 4.5), record("c2", "HOSP-A", "1200", 4.0)],
            minimum_sample_size=2,
        )
        profile = next(
            item for item in portfolio.profiles if item.entity_type == ProfileEntityType.INSTITUTION
        )
        readmission = next(
            item for item in profile.metrics if item.dimension == QualityDimension.READMISSION_CONTROL
        )
        self.assertEqual(readmission.status, MetricStatus.DATA_UNAVAILABLE)
        self.assertIsNone(readmission.score)

    def test_small_sample_cannot_enter_recommendation(self) -> None:
        records = [
            record("c1", "HOSP-A", "1000", 4.8),
            record("c2", "HOSP-B", "1500", 4.0),
            record("c3", "HOSP-C", "1300", 4.2),
        ]
        portfolio = build_quality_portfolio(records, minimum_sample_size=2)
        report = recommend_profiles(portfolio, "pneumonia")
        self.assertFalse(report.generated)
        self.assertTrue(report.warnings)

    def test_duplicate_case_disease_rows_are_rejected(self) -> None:
        duplicate = record("same", "HOSP-A", "1000", 4.5)
        with self.assertRaises(ValidationError):
            QualityPortfolioRequest(records=[duplicate, duplicate], minimum_sample_size=2)

    def test_eligible_recommendation_has_two_reasons(self) -> None:
        records = [
            record("a1", "HOSP-A", "900", 4.8),
            record("a2", "HOSP-A", "1000", 4.6),
            record("b1", "HOSP-B", "1500", 4.1),
            record("b2", "HOSP-B", "1600", 4.0),
        ]
        portfolio = build_quality_portfolio(records, minimum_sample_size=2)
        report = recommend_profiles(portfolio, "pneumonia")
        self.assertTrue(report.generated)
        self.assertEqual(report.recommendations[0].entity_id, "HOSP-A")
        self.assertGreaterEqual(len(report.recommendations[0].reasons), 2)


if __name__ == "__main__":
    unittest.main()
