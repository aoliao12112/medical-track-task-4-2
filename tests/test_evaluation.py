from __future__ import annotations

import unittest

from medical_claims.evaluation import evaluate


def field(value: str) -> dict[str, object]:
    return {"value": value, "normalized_value": value, "status": "extracted"}


class EvaluationTest(unittest.TestCase):
    def test_duplicate_fields_are_counted_in_micro_f1(self) -> None:
        gold = {
            "case_id": "1",
            "medications": [
                {"name": field("药品A"), "frequency": field("bid")},
                {"name": field("药品B"), "frequency": field("bid")},
            ],
        }
        prediction = {
            "case_id": "1",
            "medications": [
                {"name": field("药品A"), "frequency": field("每日2次")},
            ],
        }

        result = evaluate(prediction, gold)

        self.assertEqual(result.predicted_fields, 2)
        self.assertEqual(result.gold_fields, 4)
        self.assertEqual(result.correct_fields, 2)
        self.assertEqual(result.precision, 1.0)
        self.assertEqual(result.recall, 0.5)
        self.assertAlmostEqual(result.f1, 2 / 3)


if __name__ == "__main__":
    unittest.main()
