import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from medical_claims.access_control import Permission, RoleCode, has_permission
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

    def test_fresh_database_contains_complete_nineteen_table_schema(self):
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
                    "cases", "case_results", "case_profile_contexts",
                    "profile_case_records", "quality_portfolios", "reviews",
                    "deliveries", "feedback", "follow_up_tasks", "mobile_previews",
                    "schema_migrations", "service_sessions",
                    "service_analysis_results", "case_service_links",
                    "users", "roles", "user_roles", "case_access_grants", "audit_logs",
                },
            )
            versions = [row[0] for row in db.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )]
            self.assertEqual(versions, [1, 2, 3, 4, 5])

    def test_fixed_roles_are_seeded_without_password_storage(self):
        CaseStorage(self.db_path)
        with closing(self.connect()) as db:
            roles = {row[0] for row in db.execute("SELECT role_code FROM roles")}
            self.assertEqual(
                roles,
                {"patient", "claims_service", "medical_reviewer", "operations", "system_admin"},
            )
            user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
            self.assertTrue({"password", "password_hash", "salt"}.isdisjoint(user_columns))

    def test_permission_matrix_keeps_medical_data_out_of_admin_and_service_roles(self):
        self.assertTrue(has_permission(RoleCode.MEDICAL_REVIEWER, Permission.REVIEW_CARE_GUIDE))
        self.assertTrue(has_permission(RoleCode.CLAIMS_SERVICE, Permission.MANAGE_FOLLOW_UP_TASK))
        self.assertFalse(has_permission(RoleCode.CLAIMS_SERVICE, Permission.VIEW_DEIDENTIFIED_MEDICAL_RECORD))
        self.assertFalse(has_permission(RoleCode.SYSTEM_ADMIN, Permission.VIEW_DEIDENTIFIED_MEDICAL_RECORD))

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
                "INSERT INTO service_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("session-hash", "wechat", now, "已脱敏文本", "text-hash",
                 "authorized_deidentified_dataset", "authorized", "confirmed", now, now),
            )
            analysis = (
                "analysis-1", "session-hash", "理赔材料咨询", '["补件咨询"]', 1,
                '["信息不清"]', "medium", 55, 0.88, '[]', "建议补充材料说明",
                "service-intent-v1", "pending", None, '{}', now,
            )
            db.execute(
                "INSERT INTO service_analysis_results VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                analysis,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO service_analysis_results VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("analysis-invalid", "missing-session", "其他", '[]', 0, '[]',
                     "low", 10, 0.5, '[]', None, "v1", "pending", None, '{}', now),
                )

    def test_original_follow_up_table_is_migrated_without_losing_rows(self):
        storage = CaseStorage(self.db_path)
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "task_id": "task-1", "case_id": "case-1", "feedback_id": "feedback-1",
            "category": "cost_question", "status": "OPEN", "note": "需要解释费用",
            "resolution": None, "created_at": now, "resolved_at": None,
        }
        with closing(self.connect()) as db:
            db.execute(
                "INSERT INTO cases VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("case-1", "claim-1", "FOLLOWUP_PENDING", "user_upload",
                 "authorized", "confirmed", now, now),
            )
            db.execute("INSERT INTO case_results(case_id, updated_at) VALUES (?, ?)", ("case-1", now))
            db.execute(
                "INSERT INTO deliveries VALUES (?, ?, ?, ?, ?)",
                ("delivery-1", "case-1", "fingerprint", '{}', now),
            )
            db.execute(
                "INSERT INTO feedback VALUES (?, ?, ?, ?, ?)",
                ("feedback-1", "delivery-1", "case-1", '{}', now),
            )
            db.execute("DROP TABLE follow_up_tasks")
            db.execute(
                "CREATE TABLE follow_up_tasks ("
                "task_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(case_id), "
                "feedback_id TEXT NOT NULL UNIQUE REFERENCES feedback(feedback_id), "
                "status TEXT NOT NULL, record_json TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            db.execute(
                "INSERT INTO follow_up_tasks VALUES (?, ?, ?, ?, ?, ?)",
                ("task-1", "case-1", "feedback-1", "OPEN",
                 json.dumps(record, ensure_ascii=False), now),
            )
            db.commit()

        CaseStorage(self.db_path)
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM follow_up_tasks WHERE task_id='task-1'").fetchone()
            self.assertEqual(row["source_type"], "delivery_feedback")
            self.assertEqual(row["category"], "cost_question")
            self.assertEqual(row["priority"], "medium")
        self.assertEqual(storage.list_follow_ups("case-1")[0]["task_id"], "task-1")


if __name__ == "__main__":
    unittest.main()
