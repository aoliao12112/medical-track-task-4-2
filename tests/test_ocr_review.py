from __future__ import annotations

import unittest

from web.backend.app import (
    OcrDocumentPayload,
    OcrRestructureRequest,
    app,
    document_ocr_confidence,
)


class OcrReviewWorkflowTest(unittest.TestCase):
    def test_ocr_confidence_and_human_correction_priority(self) -> None:
        document = {
            "blocks": [
                {"text": "诊断", "confidence": 0.8, "bbox": None},
                {"text": "肺结节", "confidence": 1.0, "bbox": None},
            ],
            "human_corrected": False,
        }
        self.assertAlmostEqual(document_ocr_confidence(document), 0.9)
        document["human_corrected"] = True
        self.assertEqual(document_ocr_confidence(document), 1.0)

    def test_correction_request_keeps_page_text(self) -> None:
        request = OcrRestructureRequest(
            case_id="OCR-REVIEW-001",
            documents=[
                OcrDocumentPayload(
                    document_id="page-1.jpg",
                    page=1,
                    full_text="出院诊断：肺结节",
                    human_corrected=True,
                )
            ],
        )
        self.assertEqual(request.documents[0].full_text, "出院诊断：肺结节")
        self.assertTrue(request.documents[0].human_corrected)

    def test_ocr_review_routes_are_registered(self) -> None:
        paths = {route.path for route in app.routes}
        self.assertIn("/api/cases/process-detailed", paths)
        self.assertIn("/api/cases/restructure-from-ocr", paths)


if __name__ == "__main__":
    unittest.main()
