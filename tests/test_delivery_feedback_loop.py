"""Phase C synthetic safety signals, temporary SQLite, real workflow APIs."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from medical_claims.auth import hash_password
from medical_claims.models import ClaimCase, Diagnosis, DiagnosisType, Certainty, FieldValue, FieldStatus, Evidence, QualityFlag
from medical_claims.pain_engine import analyze_pain_points
from medical_claims.care_models import CareGuideReport, CareGuideDraft, CareSection, CareAction, CareSafetyCheck, SafetyStatus, GenerationMethod, ReviewStatus
from medical_claims.delivery_gate import evaluate_delivery_gate, GateDecision
from medical_claims.delivery import prepare_delivery_content
from medical_claims.quality_engine import build_quality_portfolio
from medical_claims.storage import CaseStorage, WorkflowConflict
from medical_claims.workflow_models import CreateCaseRequest
from web.backend.app import app


class DeliveryFeedbackLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "competition.db"
        self.env = patch.dict(os.environ, {"COMPETITION_DB_PATH": str(self.path)})
        self.env.start()
        self.storage = CaseStorage()
        self.client = TestClient(app)
        metadata = self.storage.create_case(CreateCaseRequest(claim_event_id="phase-c-synthetic", data_source="synthetic_test",
                    consent_status="authorized", deidentification_status="confirmed"), claim_status="ACCEPTED")
        self.case_id = metadata.case_id
        customer_id = self.storage.create_user(
            username="synthetic-customer",
            password_hash=hash_password("safe-password-123"),
            role_code="CUSTOMER",
            display_name="合成测试客户",
        )
        self.storage.assign_case_participant(self.case_id, customer_id, "CUSTOMER")
        self.case = ClaimCase(case_id=self.case_id, diagnoses=[Diagnosis(diagnosis_id="dx", diagnosis_type=DiagnosisType.PRIMARY,
            certainty=Certainty.CONFIRMED, name=FieldValue(value="肺炎", status=FieldStatus.EXTRACTED, confidence=.95,
            evidence=[Evidence(document_id="synthetic", page=1, text="合成测试诊断：肺炎")]))])
        self.pain = analyze_pain_points(self.case, [], [])
        section = CareSection(title="Synthetic fixture", content="仅用于闭环测试，不是实际医疗指引。", evidence_fields=["diagnoses[0].name"])
        action = CareAction(action="测试行动", timing="fixture", reason="synthetic test", evidence_fields=["diagnoses[0].name"])
        draft = CareGuideDraft(case_id=self.case_id, disease_topic="肺炎", opening=section, diagnosis_explanation=section,
            treatment_review=section, cost_explanation=section, recovery_plan=[action], psychological_support=section,
            follow_up_plan=[action], limitations=["仅为合成fixture，不代表医疗效果或安全验证。"])
        self.care = CareGuideReport(case_id=self.case_id, guide=draft, generation_method=GenerationMethod.LLM,
            knowledge_review_status=ReviewStatus.REVIEWED, evidence_coverage=1, review_required=False,
            safety_checks=[CareSafetyCheck(code="SYNTHETIC_PASS", status=SafetyStatus.PASSED, message="explicit fixture signal")])
        self.storage.save_structured(self.case_id, self.case)
        self.storage.save_pain(self.case_id, self.pain, self.case)
        self.save_care()

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.tmp.cleanup()

    def save_care(self):
        self.storage.save_care(self.case_id, self.care, self.case, self.pain)

    def prepare(self):
        response = self.client.post(f"/api/workflow/cases/{self.case_id}/delivery/prepare")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def send(self):
        return self.client.post(f"/api/workflow/cases/{self.case_id}/delivery/send")

    def review(self, decision="APPROVED", target="combined_delivery", fingerprint=None):
        fingerprint = fingerprint or self.prepare()["content_fingerprint"]
        return self.client.post(f"/api/workflow/cases/{self.case_id}/reviews", json={
            "reviewer": "synthetic team reviewer", "target_type": target, "decision": decision,
            "note": "fixture manual review", "content_fingerprint": fingerprint})

    def feedback(self, delivery_id, rating=2, helpful=False, category="cost_question", target="overall_service"):
        return self.client.post(f"/api/deliveries/{delivery_id}/feedback", json={
            "rating": rating, "helpful": helpful, "category": category, "target_type": target,
            "comment": "synthetic feedback: 费用解释需要补充"})

    def test_smoke_a_auto_delivery_without_approval(self):
        prepared = self.prepare()
        self.assertEqual(prepared["decision"], "AUTO_DELIVER")
        self.assertTrue(prepared["can_send"])
        self.assertEqual(self.storage.get_case(self.case_id)["status"], "DELIVERY_READY")
        response = self.send()
        self.assertEqual(response.status_code, 200, response.text)
        delivery = response.json()
        self.assertEqual(delivery["delivery_mode"], "AUTO")
        self.assertEqual(delivery["channel"], "mock_web_inbox")
        self.assertEqual(self.storage.list_reviews(self.case_id), [])
        self.assertEqual(self.storage.get_case(self.case_id)["status"], "DELIVERED_MOCK")

    def test_recommendation_unavailable_allows_care_only(self):
        prepared = self.prepare()
        self.assertFalse(prepared["recommendation_included"])
        delivery = self.send().json()
        self.assertTrue(delivery["care_included"])
        self.assertFalse(delivery["recommendation_included"])
        self.assertEqual(delivery["content"]["recommendation"], [])
        self.assertIn("暂不提供", delivery["content"]["recommendation_unavailable_reason"])

    def test_smoke_b_review_then_approved_delivery(self):
        self.care.review_required = True
        self.save_care()
        self.assertEqual(self.prepare()["decision"], "REVIEW_REQUIRED")
        self.assertEqual(self.send().status_code, 409)
        self.assertEqual(self.review().status_code, 201)
        self.assertEqual(self.storage.get_case(self.case_id)["status"], "APPROVED")
        self.assertEqual(self.prepare()["review_status"], "APPROVED")
        response = self.send()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["delivery_mode"], "HUMAN_APPROVED")

    def test_rejected_review_never_sends(self):
        self.care.review_required = True
        self.save_care()
        self.assertEqual(self.review("REJECTED").status_code, 201)
        prepared = self.prepare()
        self.assertEqual(prepared["review_status"], "REJECTED")
        self.assertFalse(prepared["can_send"])
        self.assertEqual(self.send().status_code, 409)
        self.assertEqual(self.storage.list_deliveries(self.case_id), [])

    def test_smoke_c_blocked_cannot_send_or_be_approved(self):
        self.care.safety_checks[0].status = SafetyStatus.BLOCKED
        self.save_care()
        self.assertEqual(self.prepare()["decision"], "BLOCKED")
        self.assertEqual(self.review().status_code, 409)
        self.assertEqual(self.send().status_code, 409)
        self.assertEqual(self.storage.list_deliveries(self.case_id), [])

    def test_existing_approval_does_not_override_new_blocked_signal(self):
        self.care.review_required = True
        self.save_care()
        self.review()
        self.care.safety_checks[0].status = SafetyStatus.BLOCKED
        self.save_care()
        self.assertEqual(self.send().status_code, 409)

    def test_smoke_d_feedback_followup_resolve_and_storage_restart(self):
        delivery = self.send().json()
        response = self.feedback(delivery["delivery_id"])
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["follow_up"]["status"], "OPEN")
        self.assertEqual(self.storage.get_case(self.case_id)["status"], "FOLLOWUP_PENDING")
        task_id = body["follow_up"]["task_id"]
        result = self.client.post(f"/api/follow-ups/{task_id}/resolve", json={"resolution": "已人工补充费用解释，无需重新生成报告"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["status"], "RESOLVED")
        self.assertEqual(self.storage.get_case(self.case_id)["status"], "RESOLVED")
        restarted = CaseStorage(self.path)
        self.assertEqual(restarted.get_delivery(delivery["delivery_id"]), delivery)
        self.assertEqual(restarted.list_feedback(self.case_id)[0], body["feedback"])
        self.assertEqual(restarted.list_follow_ups(self.case_id)[0]["status"], "RESOLVED")
        self.assertIsNotNone(restarted.list_follow_ups(self.case_id)[0]["resolved_at"])

    def test_good_clear_feedback_creates_no_task(self):
        delivery = self.send().json()
        response = self.feedback(delivery["delivery_id"], rating=5, helpful=True, category="clear")
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.json()["follow_up"])
        self.assertEqual(self.storage.list_follow_ups(self.case_id), [])
        self.assertEqual(self.storage.get_case(self.case_id)["status"], "FEEDBACK_RECEIVED")

    def test_question_with_high_rating_still_creates_followup(self):
        delivery = self.send().json()
        self.assertIsNotNone(self.feedback(delivery["delivery_id"], 5, True, "medication_question").json()["follow_up"])

    def test_not_helpful_with_clear_high_rating_creates_followup(self):
        delivery = self.send().json()
        self.assertIsNotNone(self.feedback(delivery["delivery_id"], 5, False, "clear").json()["follow_up"])

    def test_delivery_read_apis_and_idempotent_send(self):
        first = self.send().json()
        second = self.send().json()
        self.assertEqual(first["delivery_id"], second["delivery_id"])
        self.assertEqual(self.client.get(f"/api/deliveries/{first['delivery_id']}").json(), first)
        self.assertEqual(len(self.client.get(f"/api/workflow/cases/{self.case_id}/deliveries").json()), 1)

    def test_fallback_evidence_and_safety_review_require_manual_review(self):
        for kind in ("fallback", "evidence", "safety", "unchecked", "knowledge", "warning"):
            modified = self.care.model_copy(deep=True)
            if kind == "fallback": modified.generation_method = GenerationMethod.RULES_FALLBACK
            if kind == "evidence": modified.evidence_coverage = .79
            if kind == "safety": modified.safety_checks[0].status = SafetyStatus.REVIEW
            if kind == "unchecked": modified.safety_checks = []
            if kind == "knowledge": modified.knowledge_review_status = ReviewStatus.DRAFT_PENDING_MEDICAL_REVIEW
            if kind == "warning": modified.warnings = ["existing medical warning"]
            self.assertEqual(evaluate_delivery_gate(modified, self.case, self.pain, {"reports": []}).decision, GateDecision.REVIEW_REQUIRED, kind)

    def test_only_safety_relevant_structured_conflict_requires_review(self):
        case = self.case.model_copy(deep=True)
        case.quality_flags = [QualityFlag(code="COST_CONFLICT", severity="warning", message="existing conflict")]
        self.assertEqual(evaluate_delivery_gate(self.care, case, self.pain, {"reports": []}).decision, GateDecision.AUTO_DELIVER)
        case.quality_flags = []
        case.diagnoses[0].name.status = FieldStatus.CONFLICT
        self.assertEqual(evaluate_delivery_gate(self.care, case, self.pain, {"reports": []}).decision, GateDecision.REVIEW_REQUIRED)

    def test_low_recommendation_confidence_requires_review(self):
        query = {"reports": [{"status": "available", "report": {"warnings": [], "recommendations": [{
            "entity_id": "FIXTURE", "confidence": .4, "sample_size": 3,
            "reasons": [{"evidence_fields": ["costs.total_amount"]}, {"evidence_fields": ["diagnoses"]}]}]}}]}
        gate = evaluate_delivery_gate(self.care, self.case, self.pain, query)
        self.assertEqual(gate.decision, GateDecision.REVIEW_REQUIRED)
        self.assertIn("recommendation", [target.value for target in gate.review_targets])

    def test_stale_review_fingerprint_refused(self):
        self.care.review_required = True
        self.save_care()
        old = self.prepare()["content_fingerprint"]
        self.care.guide.opening.content = "changed synthetic content"
        self.save_care()
        self.assertEqual(self.review(fingerprint=old).status_code, 409)
        self.assertEqual(self.storage.list_reviews(self.case_id), [])

    def test_old_approval_does_not_apply_to_regenerated_guide(self):
        self.care.review_required = True
        self.save_care()
        self.review()
        self.care.guide.opening.content = "new synthetic content"
        self.save_care()
        self.assertEqual(self.send().status_code, 409)
        self.assertEqual(self.prepare()["review_status"], "PENDING")

    def test_portfolio_change_invalidates_approval(self):
        self.care.review_required = True
        self.save_care()
        self.review()
        self.storage.save_portfolio(build_quality_portfolio([]), [])
        self.assertEqual(self.send().status_code, 409)

    def test_transaction_rechecks_prepared_snapshot(self):
        old = prepare_delivery_content(self.storage, self.case_id)
        self.care.guide.cost_explanation.content = "new synthetic cost explanation"
        self.save_care()
        with self.assertRaises(WorkflowConflict):
            self.storage.send_delivery(old)

    def test_inbox_content_is_immutable_after_regeneration(self):
        sent = self.send().json()
        self.care.guide.cost_explanation.content = "new guide, not sent yet"
        self.save_care()
        self.assertEqual(self.storage.get_delivery(sent["delivery_id"]), sent)

    def test_multiple_tasks_keep_case_pending_until_all_resolved(self):
        sent = self.send().json()
        one = self.feedback(sent["delivery_id"]).json()["follow_up"]
        two = self.feedback(sent["delivery_id"]).json()["follow_up"]
        self.client.post(f"/api/follow-ups/{one['task_id']}/resolve", json={"resolution": "first handled"})
        self.assertEqual(self.storage.get_case(self.case_id)["status"], "FOLLOWUP_PENDING")
        self.client.post(f"/api/follow-ups/{two['task_id']}/resolve", json={"resolution": "second handled"})
        self.assertEqual(self.storage.get_case(self.case_id)["status"], "RESOLVED")

    def test_positive_feedback_does_not_clear_existing_open_task(self):
        sent = self.send().json()
        self.feedback(sent["delivery_id"])
        self.feedback(sent["delivery_id"], 5, True, "clear")
        self.assertEqual(self.storage.get_case(self.case_id)["status"], "FOLLOWUP_PENDING")

    def test_resolving_task_is_idempotent_and_nonblank(self):
        sent = self.send().json()
        task = self.feedback(sent["delivery_id"]).json()["follow_up"]
        path = f"/api/follow-ups/{task['task_id']}/resolve"
        self.assertEqual(self.client.post(path, json={"resolution": " "}).status_code, 422)
        self.assertEqual(self.client.post(path, json={"resolution": "handled"}).status_code, 200)
        self.assertEqual(self.client.post(path, json={"resolution": "handled"}).status_code, 200)
        self.assertEqual(self.client.post(path, json={"resolution": "overwrite"}).status_code, 409)

    def test_feedback_read_and_task_list_apis(self):
        delivery = self.send().json()
        feedback = self.feedback(delivery["delivery_id"]).json()
        self.assertEqual(self.client.get(f"/api/workflow/cases/{self.case_id}/feedback").json()[0], feedback["feedback"])
        self.assertEqual(self.client.get(f"/api/workflow/cases/{self.case_id}/follow-ups").json()[0], feedback["follow_up"])
        self.assertEqual(self.client.get("/api/follow-ups").json()[0], feedback["follow_up"])

    def test_missing_resources_return_404(self):
        for path in ("/api/workflow/cases/missing/deliveries", "/api/workflow/cases/missing/feedback",
                     "/api/workflow/cases/missing/follow-ups", "/api/workflow/cases/missing/reviews", "/api/deliveries/missing"):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        for path in ("/api/workflow/cases/missing/delivery/prepare", "/api/workflow/cases/missing/delivery/send"):
            self.assertEqual(self.client.post(path).status_code, 404, path)
        self.assertEqual(self.feedback("missing").status_code, 404)
        self.assertEqual(self.client.post("/api/follow-ups/missing/resolve", json={"resolution": "handled"}).status_code, 404)

    def test_feedback_validation_and_unavailable_recommendation_target(self):
        delivery = self.send().json()
        self.assertEqual(self.feedback(delivery["delivery_id"], rating=0).status_code, 422)
        self.assertEqual(self.feedback(delivery["delivery_id"], category="not_supported").status_code, 422)
        self.assertEqual(self.feedback(delivery["delivery_id"], target="recommendation").status_code, 409)

    def test_low_risk_does_not_accept_unnecessary_human_approval(self):
        self.assertEqual(self.review().status_code, 409)
        self.assertEqual(self.send().json()["delivery_mode"], "AUTO")

    def test_care_only_review_target_can_approve_care_only_delivery(self):
        self.care.review_required = True
        self.save_care()
        self.assertEqual(self.review(target="care_guide").status_code, 201)
        self.assertEqual(self.send().json()["delivery_mode"], "HUMAN_APPROVED")

    def test_old_feedback_does_not_overwrite_new_processing_stage(self):
        delivery = self.send().json()
        self.storage.save_structured(self.case_id, self.case)
        feedback = self.feedback(delivery["delivery_id"]).json()
        self.assertEqual(self.storage.get_case(self.case_id)["status"], "STRUCTURED")
        self.client.post(f"/api/follow-ups/{feedback['follow_up']['task_id']}/resolve", json={"resolution": "old delivery handled"})
        self.assertEqual(self.storage.get_case(self.case_id)["status"], "STRUCTURED")

    def test_refresh_readiness_preserves_resolved_state(self):
        self.test_smoke_d_feedback_followup_resolve_and_storage_restart()
        self.prepare()
        self.send()
        self.assertEqual(self.storage.get_case(self.case_id)["status"], "RESOLVED")

    def test_preparation_while_processing_does_not_replace_processing_state(self):
        self.storage.begin_processing(self.case_id)
        self.assertEqual(self.prepare()["decision"], "BLOCKED")
        self.assertEqual(self.storage.get_case(self.case_id)["status"], "PROCESSING")
        self.assertEqual(self.send().status_code, 409)

    def test_cannot_reject_already_delivered_content_retroactively(self):
        self.care.review_required = True
        self.save_care()
        self.assertEqual(self.review().status_code, 201)
        sent = self.send().json()
        self.assertTrue(self.prepare()["already_delivered"])
        self.assertEqual(self.review("REJECTED").status_code, 409)
        self.assertEqual(self.send().json(), sent)
        self.assertEqual(self.storage.get_case(self.case_id)["status"], "DELIVERED_MOCK")

    def test_identical_regeneration_restores_delivered_state_without_duplicate(self):
        sent = self.send().json()
        self.save_care()
        self.assertEqual(self.storage.get_case(self.case_id)["status"], "CARE_READY")
        self.assertTrue(self.prepare()["already_delivered"])
        self.assertEqual(self.storage.get_case(self.case_id)["status"], "DELIVERED_MOCK")
        self.assertEqual(self.send().json(), sent)
        self.assertEqual(len(self.storage.list_deliveries(self.case_id)), 1)

    def test_new_delivery_preserves_open_followup_from_prior_delivery(self):
        sent = self.send().json()
        task = self.feedback(sent["delivery_id"]).json()["follow_up"]
        self.care.guide.opening.content = "new synthetic guide"
        self.save_care()
        new = self.send().json()
        self.assertNotEqual(new["delivery_id"], sent["delivery_id"])
        self.assertEqual(self.storage.get_case(self.case_id)["status"], "FOLLOWUP_PENDING")
        self.client.post(f"/api/follow-ups/{task['task_id']}/resolve", json={"resolution": "prior feedback handled"})
        self.assertEqual(self.storage.get_case(self.case_id)["status"], "RESOLVED")
