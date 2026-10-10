"""Phase A persistence and API integration, without external medical services."""
import os
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient

from medical_claims.models import ClaimCase, EncounterType
from medical_claims.pain_engine import analyze_pain_points
from medical_claims.care_engine import build_fallback_draft, finalize_care_guide
from medical_claims.care_models import GenerationMethod
from medical_claims.storage import CaseStorage, WorkflowConflict
from medical_claims.workflow_models import CreateCaseRequest
from web.backend.app import app


class CompetitionWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "competition.db"
        self.env = patch.dict(os.environ, {"COMPETITION_DB_PATH": str(self.path)})
        self.env.start()
        self.storage = CaseStorage()
        self.client = TestClient(app)
        self.request = CreateCaseRequest(claim_event_id="claim-demo-001", data_source="synthetic_test",
                                         consent_status="authorized", deidentification_status="confirmed")
        self.metadata = self.storage.create_case(self.request)
        self.case = ClaimCase(case_id=self.metadata.case_id)
        self.pain = analyze_pain_points(self.case, [], [])
        draft = build_fallback_draft(self.case, self.pain, None, "unknown")
        self.care = finalize_care_guide(self.case, draft, None, GenerationMethod.RULES_FALLBACK, [])

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.tmp.cleanup()

    def test_create_received(self):
        response = self.client.post("/api/workflow/cases", json=self.request.model_dump())
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "RECEIVED")
        UUID(response.json()["case_id"])

    def test_save_structured(self):
        self.storage.save_structured(self.case.case_id, self.case)
        self.assertEqual(self.storage.get_case(self.case.case_id)["status"], "STRUCTURED")

    def test_save_pain(self):
        self.storage.save_structured(self.case.case_id, self.case)
        self.storage.save_pain(self.case.case_id, self.pain, self.case)
        self.assertEqual(self.storage.get_case(self.case.case_id)["status"], "PAIN_READY")

    def test_save_care(self):
        self.storage.save_structured(self.case.case_id, self.case)
        self.storage.save_pain(self.case.case_id, self.pain, self.case)
        self.storage.save_care(self.case.case_id, self.care, self.case, self.pain)
        self.assertEqual(self.storage.get_case(self.case.case_id)["status"], "CARE_READY")

    def test_storage_restart(self):
        self.test_save_care()
        reopened = CaseStorage(self.path)
        self.assertEqual(reopened.get_case(self.case.case_id)["status"], "CARE_READY")
        self.assertEqual(reopened.get_results(self.case.case_id)["care_result"], self.care.model_dump(mode="json"))

    def test_restructuring_clears_downstream(self):
        self.test_save_care()
        self.storage.save_structured(self.case.case_id, self.case)
        saved = self.storage.get_results(self.case.case_id)
        self.assertIsNone(saved["pain_result"])
        self.assertIsNone(saved["care_result"])
        self.assertEqual(self.storage.get_case(self.case.case_id)["status"], "STRUCTURED")

    def test_missing_case_404(self):
        for suffix in ("", "/result"):
            self.assertEqual(self.client.get("/api/workflow/cases/missing" + suffix).status_code, 404)
        response = self.client.post("/api/cases/restructure-from-ocr", json={
            "case_id": "missing", "documents": [{"document_id": "doc", "page": 1, "full_text": "test"}]})
        self.assertEqual(response.status_code, 404)

    def test_api_chain_and_reload_with_stubbed_ocr_and_structuring(self):
        documents = [{"document_id": "sample.png", "page": 1, "full_text": "synthetic test", "blocks": []}]
        with patch("web.backend.app.recognize_uploaded_documents", return_value=documents), \
             patch("web.backend.app.structure_case_with_long_document_support", return_value=self.case):
            response = self.client.post("/api/cases/process-detailed", data={"case_id": self.case.case_id},
                                        files={"files": ("sample.png", b"stubbed-image", "image/png")})
        self.assertEqual(response.status_code, 200, response.text)
        structured = response.json()["case"]
        pain = self.client.post("/api/cases/analyze-pain-points", json={"case": structured, "use_llm": False})
        self.assertEqual(pain.status_code, 200, pain.text)
        care = self.client.post("/api/cases/generate-care-guide", json={
            "case": structured, "pain_report": pain.json(), "use_llm": False})
        self.assertEqual(care.status_code, 200, care.text)
        saved = self.client.get(f"/api/workflow/cases/{self.case.case_id}/result").json()
        self.assertEqual(saved["care_result"], care.json())
        self.assertEqual(saved["ocr_result"], documents)
        self.assertEqual(self.client.get(f"/api/workflow/cases/{self.case.case_id}").json()["status"], "CARE_READY")

    def test_processing_failure_preserves_ocr_and_previous_stage(self):
        documents = [{"document_id": "doc", "page": 1, "full_text": "test", "blocks": []}]
        with patch("web.backend.app.recognize_uploaded_documents", return_value=documents), \
             patch("web.backend.app.structure_case_with_long_document_support", side_effect=RuntimeError("unavailable")):
            response = self.client.post("/api/cases/process-detailed", data={"case_id": self.case.case_id},
                                        files={"files": ("sample.png", b"stub", "image/png")})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.storage.get_case(self.case.case_id)["status"], "OCR_READY")
        self.assertEqual(self.storage.get_results(self.case.case_id)["ocr_result"], documents)

    def test_ocr_only_never_calls_structuring_service(self):
        pending = self.storage.create_case(CreateCaseRequest(
            claim_event_id="claim-pending-ocr",
            data_source="official_dataset",
            consent_status="authorized",
            deidentification_status="pending_review",
        ))
        documents = [{"document_id": "sample.png", "page": 1, "full_text": "仅本地OCR", "blocks": []}]
        with patch("web.backend.app.recognize_uploaded_documents", return_value=documents), \
             patch("web.backend.app.structure_case_with_long_document_support") as structure:
            response = self.client.post(
                "/api/cases/ocr-only",
                data={"case_id": pending.case_id},
                files={"files": ("sample.png", b"stubbed-image", "image/png")},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["external_model_called"])
        structure.assert_not_called()
        self.assertEqual(self.storage.get_case(pending.case_id)["status"], "OCR_READY")

    def test_external_structuring_requires_deidentification_confirmation(self):
        pending = self.storage.create_case(CreateCaseRequest(
            claim_event_id="claim-pending-structure",
            data_source="official_dataset",
            consent_status="authorized",
            deidentification_status="pending_review",
        ))
        payload = {
            "case_id": pending.case_id,
            "documents": [{"document_id": "reviewed", "page": 1, "full_text": "脱敏医疗文本"}],
        }
        denied = self.client.post("/api/cases/restructure-from-ocr", json=payload)
        self.assertEqual(denied.status_code, 409, denied.text)
        payload["deidentification_confirmed"] = True
        linked_case = self.case.model_copy(update={"case_id": pending.case_id})
        with patch("web.backend.app.structure_case_with_long_document_support", return_value=linked_case):
            allowed = self.client.post("/api/cases/restructure-from-ocr", json=payload)
        self.assertEqual(allowed.status_code, 200, allowed.text)
        metadata = self.storage.get_case(pending.case_id)
        self.assertEqual(metadata["deidentification_status"], "confirmed")
        self.assertEqual(metadata["status"], "STRUCTURED")

    def test_flagged_placeholder_requires_explicit_manual_override_and_is_audited(self):
        pending = self.storage.create_case(CreateCaseRequest(
            claim_event_id="claim-manual-deidentification",
            data_source="official_dataset",
            consent_status="authorized",
            deidentification_status="pending_review",
        ))
        payload = {
            "case_id": pending.case_id,
            "deidentification_confirmed": True,
            "documents": [{
                "document_id": "reviewed",
                "page": 1,
                "full_text": "身份证号：000000000000000000（虚构占位符）",
            }],
        }
        denied = self.client.post("/api/cases/restructure-from-ocr", json=payload)
        self.assertEqual(denied.status_code, 409, denied.text)

        payload["manual_override_confirmed"] = True
        linked_case = self.case.model_copy(update={"case_id": pending.case_id})
        with patch("web.backend.app.structure_case_with_long_document_support", return_value=linked_case) as structure:
            allowed = self.client.post("/api/cases/restructure-from-ocr", json=payload)
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertEqual(structure.call_args.args[1][0]["full_text"], payload["documents"][0]["full_text"])
        with self.storage._connect() as db:
            audit = db.execute(
                "SELECT request_params_json FROM system_logs "
                "WHERE case_id=? AND action='confirm_deidentification'",
                (pending.case_id,),
            ).fetchone()
        self.assertIsNotNone(audit)
        self.assertEqual(json.loads(audit[0])["review_method"], "manual_override")

    def test_save_reviewed_ocr_persists_modified_text_without_calling_llm(self):
        self.storage.save_structured(self.case.case_id, self.case)
        documents = [{
            "document_id": "reviewed-page",
            "page": 1,
            "full_text": "患者姓名：[已脱敏]，诊断：肺炎",
            "human_corrected": True,
        }]
        with patch("web.backend.app.structure_case_with_long_document_support") as structure:
            response = self.client.post("/api/cases/save-reviewed-ocr", json={
                "case_id": self.case.case_id,
                "documents": documents,
            })
        self.assertEqual(response.status_code, 200, response.text)
        structure.assert_not_called()
        metadata = self.storage.get_case(self.case.case_id)
        saved = self.storage.get_results(self.case.case_id)
        self.assertEqual(metadata["status"], "OCR_READY")
        self.assertEqual(metadata["deidentification_status"], "pending_review")
        self.assertEqual(saved["ocr_result"][0]["full_text"], documents[0]["full_text"])
        self.assertIsNone(saved["structured_result"])

    def test_stale_or_unlinked_results_rejected(self):
        self.storage.save_structured(self.case.case_id, self.case)
        changed = self.case.model_copy(deep=True)
        changed.encounter_type = EncounterType.INPATIENT
        with self.assertRaises(WorkflowConflict):
            self.storage.save_pain(self.case.case_id, self.pain, changed)
        with self.assertRaises(WorkflowConflict):
            self.storage.save_care(self.case.case_id, self.care, self.case, None)
        self.storage.begin_processing(self.case.case_id)
        with self.assertRaises(WorkflowConflict):
            self.storage.save_pain(self.case.case_id, self.pain, self.case)

    def test_rerun_pain_clears_care(self):
        self.test_save_care()
        self.storage.save_pain(self.case.case_id, self.pain, self.case)
        self.assertIsNone(self.storage.get_results(self.case.case_id)["care_result"])
        self.assertEqual(self.storage.get_case(self.case.case_id)["status"], "PAIN_READY")

    def test_ocr_restructure_api_invalidates_saved_reports(self):
        self.test_save_care()
        with patch("web.backend.app.structure_case_with_long_document_support", return_value=self.case):
            response = self.client.post("/api/cases/restructure-from-ocr", json={
                "case_id": self.case.case_id, "documents": [
                    {"document_id": "corrected", "page": 1, "full_text": "corrected test", "human_corrected": True}]})
        self.assertEqual(response.status_code, 200, response.text)
        saved = self.client.get(f"/api/workflow/cases/{self.case.case_id}/result").json()
        self.assertIsNone(saved["pain_result"])
        self.assertIsNone(saved["care_result"])
        self.assertTrue(saved["ocr_result"][0]["human_corrected"])
        metadata = self.client.get(f"/api/workflow/cases/{self.case.case_id}").json()
        self.assertEqual(metadata["status"], "STRUCTURED")
        self.assertTrue(metadata["has_structured_result"])
        self.assertFalse(metadata["has_pain_result"])

    def test_stale_api_request_returns_conflict(self):
        self.storage.save_structured(self.case.case_id, self.case)
        stale = self.case.model_copy(deep=True)
        stale.encounter_type = EncounterType.INPATIENT
        response = self.client.post("/api/cases/analyze-pain-points", json={"case": stale.model_dump(mode="json"), "use_llm": False})
        self.assertEqual(response.status_code, 409)

    def test_processing_and_unknown_upload_case(self):
        self.storage.begin_processing(self.case.case_id)
        self.assertEqual(self.storage.get_case(self.case.case_id)["status"], "PROCESSING")
        response = self.client.post("/api/cases/process-detailed", data={"case_id": "missing"},
                                    files={"files": ("sample.png", b"stub", "image/png")})
        self.assertEqual(response.status_code, 404)
