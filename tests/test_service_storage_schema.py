import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from medical_claims.database_migrations import CURRENT_SCHEMA_VERSION
from medical_claims.storage import CaseStorage


class ServiceStorageSchemaTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "competition.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def connect(self):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def test_fresh_database_contains_authoritative_twenty_nine_table_schema(self):
        CaseStorage(self.db_path)
        with closing(self.connect()) as db:
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            self.assertEqual(
                tables,
                {
                    "schema_migrations", "users", "roles", "permissions",
                    "role_permissions", "case_participants", "cases", "case_results",
                    "case_profile_contexts", "profile_case_records", "quality_portfolios",
                    "provider_quality_observations", "provider_profile_snapshots",
                    "provider_metric_scores", "recommendation_reports",
                    "recommendation_items", "recommendation_scores",
                    "recommendation_evidence", "reviews", "deliveries", "feedback",
                    "follow_up_tasks", "service_sessions", "service_analysis_results",
                    "case_service_links", "system_logs", "batch_jobs", "batch_items",
                    "case_tasks",
                },
            )
            versions = [row[0] for row in db.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )]
            self.assertEqual(versions, [1, 2, 3, 4, 5, 6])
            self.assertEqual(CURRENT_SCHEMA_VERSION, 6)

    def test_fixed_roles_and_password_hash_column_match_runtime_auth(self):
        CaseStorage(self.db_path)
        with closing(self.connect()) as db:
            roles = {row[0] for row in db.execute("SELECT role_code FROM roles")}
            self.assertEqual(
                roles,
                {"CLAIM", "MEDICAL", "SERVICE", "CUSTOMER", "SYSTEM_ADMIN"},
            )
            user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
            self.assertIn("password_hash", user_columns)
            self.assertNotIn("password", user_columns)
            self.assertNotIn("salt", user_columns)

    def test_permission_matrix_keeps_medical_data_out_of_admin_and_service_roles(self):
        CaseStorage(self.db_path)
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT r.role_code, p.perm_code FROM roles r "
                "JOIN role_permissions rp ON rp.role_id=r.role_id "
                "JOIN permissions p ON p.perm_id=rp.perm_id"
            )
            permissions = {}
            for role_code, permission_code in rows:
                permissions.setdefault(role_code, set()).add(permission_code)
        self.assertIn("CARE_REVIEW", permissions["MEDICAL"])
        self.assertIn("FOLLOWUP_HANDLE", permissions["SERVICE"])
        self.assertNotIn("MEDICAL_CASE_VIEW", permissions["SERVICE"])
        self.assertNotIn("MEDICAL_CASE_VIEW", permissions["SYSTEM_ADMIN"])

    def test_service_session_schema_does_not_store_raw_identity_columns(self):
        CaseStorage(self.db_path)
        with closing(self.connect()) as db:
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(service_sessions)")
            }
            self.assertIn("session_id_hash", columns)
            self.assertIn("deidentified_text", columns)
            self.assertTrue({"oneid", "account_id", "wechat_id", "phone"}.isdisjoint(columns))

    def test_service_analysis_foreign_key_and_value_constraints(self):
        CaseStorage(self.db_path)
        now = datetime.now(timezone.utc).isoformat()
        with closing(self.connect()) as db:
            db.execute(
                "INSERT INTO service_sessions(session_id_hash, customer_user_id, channel, "
                "session_time, deidentified_text, text_fingerprint, data_source, consent_status, "
                "deidentification_status, created_at, updated_at) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("session-hash", "WECHAT", now, "已脱敏文本", "text-hash",
                 "authorized_deidentified_dataset", "authorized", "confirmed", now, now),
            )
            analysis = (
                "analysis-1", "session-hash", "理赔材料咨询", '["补件咨询"]', 1,
                '["信息不清"]', "medium", 55, 0.88, '[]', "建议补充材料说明",
                "service-intent-v1", "pending", None, now,
            )
            db.execute(
                "INSERT INTO service_analysis_results(analysis_id, session_id_hash, primary_intent, "
                "secondary_intents_json, has_friction, friction_types_json, severity, severity_score, "
                "confidence, evidence_json, recommended_action, engine_version, review_status, "
                "reviewer_user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                analysis,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO service_analysis_results(analysis_id, session_id_hash, primary_intent, "
                    "secondary_intents_json, has_friction, friction_types_json, severity, severity_score, "
                    "confidence, evidence_json, recommended_action, engine_version, review_status, "
                    "reviewer_user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("analysis-invalid", "missing-session", "其他", '[]', 0, '[]',
                     "low", 10, 0.5, '[]', None, "v1", "pending", None, now),
                )

    def test_legacy_profile_compatibility_tables_remain_present(self):
        CaseStorage(self.db_path)
        with closing(self.connect()) as db:
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )}
        self.assertTrue({
            "case_profile_contexts", "profile_case_records", "quality_portfolios"
        }.issubset(tables))


if __name__ == "__main__":
    unittest.main()
