import unittest

from medical_claims.feedback_models import FeedbackRequest
from web.backend.app import app


class PatientFeedbackTest(unittest.TestCase):
    def test_delivery_feedback_route_is_registered(self) -> None:
        self.assertIn("/api/deliveries/{delivery_id}/feedback", {route.path for route in app.routes})

    def test_patient_experience_dimensions_are_structured(self) -> None:
        request = FeedbackRequest(
            target_type="overall_service",
            rating=5,
            helpful=True,
            category="clear",
            comment="内容清楚",
            clarity_score=5,
            reassurance_score=4,
            actionability_score=5,
            consent_to_contact=False,
        )
        self.assertEqual(request.clarity_score, 5)
        self.assertEqual(request.reassurance_score, 4)
        self.assertEqual(request.actionability_score, 5)


if __name__ == "__main__":
    unittest.main()
