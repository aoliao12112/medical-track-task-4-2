import unittest

from medical_claims.json_utils import normalize_json_output


class NormalizeJsonOutputTest(unittest.TestCase):
    def test_keeps_plain_json(self) -> None:
        self.assertEqual(normalize_json_output('{"case_id":"A"}'), '{"case_id":"A"}')

    def test_removes_markdown_json_fence(self) -> None:
        value = "```json\n{\n  \"case_id\": \"A\"\n}\n```"
        self.assertEqual(normalize_json_output(value), '{\n  "case_id": "A"\n}')

    def test_accepts_single_backtick_wrapper_and_explanation(self) -> None:
        value = '`json\n{"case_id":"A"}\n`\n以上为结构化结果。'
        self.assertEqual(normalize_json_output(value), '{"case_id":"A"}')

    def test_rejects_text_without_json_object(self) -> None:
        with self.assertRaisesRegex(ValueError, "没有找到 JSON 对象"):
            normalize_json_output("无法生成结构化结果")


if __name__ == "__main__":
    unittest.main()
