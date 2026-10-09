from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "web" / "dist"


class RolePagesTest(unittest.TestCase):
    def test_role_portal_links_every_business_view(self):
        portal = (DIST / "roles.html").read_text(encoding="utf-8")
        for page in (
            "patient.html",
            "claims-service.html",
            "medical-review.html",
            "operations.html",
            "system-admin.html",
        ):
            self.assertIn(f'href="./{page}"', portal)

    def test_wrappers_declare_the_expected_role(self):
        expected = {
            "claims-service.html": "claims_service",
            "medical-review.html": "medical_reviewer",
            "operations.html": "operations",
            "system-admin.html": "system_admin",
        }
        for page, role in expected.items():
            content = (DIST / page).read_text(encoding="utf-8")
            self.assertIn(f'data-role="{role}"', content)
            self.assertIn("./role-pages.js", content)
            self.assertIn("./role-pages.css", content)

    def test_claims_service_does_not_request_full_case_result(self):
        script = (DIST / "role-pages.js").read_text(encoding="utf-8")
        claims_start = script.index("async function loadClaims()")
        claims_end = script.index("function privacyClaims()")
        claims_source = script[claims_start:claims_end]
        self.assertNotIn("/result", claims_source)
        self.assertIn("/follow-ups", claims_source)

    def test_pages_state_that_frontend_hiding_is_not_authorization(self):
        portal = (DIST / "roles.html").read_text(encoding="utf-8")
        script = (DIST / "role-pages.js").read_text(encoding="utf-8")
        self.assertIn("真正权限仍由后端API", portal)
        self.assertIn("前端隐藏都不能替代后端权限判断", script)

    def test_restoring_case_uses_database_consent_and_deidentification_state(self):
        script = (DIST / "app.js").read_text(encoding="utf-8")
        restore_start = script.index("async function restoreWorkflowCase(caseId)")
        restore_end = script.index("function requireWorkflowCase()")
        restore_source = script[restore_start:restore_end]
        self.assertIn('metadata.consent_status === "authorized"', restore_source)
        self.assertIn('metadata.deidentification_status === "confirmed"', restore_source)
        self.assertIn("本地OCR完成，待脱敏复核", restore_source)
        self.assertIn("等待逐页脱敏复核", restore_source)

    def test_ocr_ready_has_a_user_facing_label(self):
        script = (DIST / "app.js").read_text(encoding="utf-8")
        self.assertIn("OCR_READY:'本地OCR已完成，待脱敏复核'", script)

    def test_manual_deidentification_override_is_explicit_and_uses_edited_payload(self):
        page = (DIST / "index.html").read_text(encoding="utf-8")
        script = (DIST / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="workflowSensitivePatternReviewed"', page)
        self.assertIn("function commitActiveOcrEditor()", script)
        self.assertIn("manual_override_confirmed", script)
        self.assertIn("/api/cases/save-reviewed-ocr", script)


if __name__ == "__main__":
    unittest.main()
