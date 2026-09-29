import unittest

from medical_claims.models import ClaimCase


class ExaminationSchemaTest(unittest.TestCase):
    def test_laboratory_results_are_structured_and_traceable(self) -> None:
        case = ClaimCase.model_validate(
            {
                "case_id": "TEST-FLU-001",
                "encounter_type": "outpatient",
                "examinations": [
                    {
                        "examination_id": "EXAM-001",
                        "category": "laboratory",
                        "name": {
                            "value": "甲型流感病毒抗原检测",
                            "normalized_value": "甲型流感病毒抗原检测",
                            "confidence": 0.99,
                            "status": "extracted",
                            "evidence": [
                                {
                                    "document_id": "test_flu.jpg",
                                    "page": 1,
                                    "text": "甲型流感病毒抗原检测：阳性",
                                }
                            ],
                        },
                        "results": [
                            {
                                "item_name": {
                                    "value": "甲型流感病毒抗原",
                                    "confidence": 0.99,
                                    "status": "extracted",
                                    "evidence": [
                                        {
                                            "document_id": "test_flu.jpg",
                                            "page": 1,
                                            "text": "甲型流感病毒抗原检测：阳性",
                                        }
                                    ],
                                },
                                "value": {
                                    "value": "阳性",
                                    "normalized_value": "positive",
                                    "confidence": 0.99,
                                    "status": "extracted",
                                    "evidence": [
                                        {
                                            "document_id": "test_flu.jpg",
                                            "page": 1,
                                            "text": "甲型流感病毒抗原检测：阳性",
                                        }
                                    ],
                                },
                                "abnormal_flag": "positive",
                            }
                        ],
                    }
                ],
                "events": [
                    {
                        "event_id": "E1",
                        "event_type": "visit",
                        "description": "发热门诊就诊",
                        "confidence": 0.98,
                    }
                ],
            }
        )

        self.assertEqual(case.examinations[0].results[0].value.value, "阳性")
        self.assertEqual(case.events[0].event_type.value, "visit")


if __name__ == "__main__":
    unittest.main()
