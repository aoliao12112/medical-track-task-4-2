"""Local SQLite case storage, without report versions or event sourcing."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
from uuid import uuid4

from medical_claims.database_migrations import apply_migrations
from medical_claims.workflow_models import CreateCaseRequest, WorkflowCase, WorkflowStatus


DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "outputs" / "competition.db"


class CaseNotFound(KeyError):
    pass


class WorkflowConflict(ValueError):
    pass


class WorkflowItemNotFound(LookupError):
    pass


def encode(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class CaseStorage:
    # Mobile previews are deliberately process-local. They must never become a
    # persistent database table or survive a backend restart.
    _preview_lock = threading.Lock()
    _preview_cache: dict[str, dict] = {}

    def __init__(self, path=None):
        self.path = Path(path or os.getenv("COMPETITION_DB_PATH") or DEFAULT_DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            apply_migrations(db)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_case(self, request: CreateCaseRequest, owner_user_id: str | None = None) -> WorkflowCase:
        now = datetime.now(timezone.utc).isoformat()
        case = WorkflowCase(**request.model_dump(), case_id=str(uuid4()),
                            status=WorkflowStatus.RECEIVED, created_at=now, updated_at=now)
        with self._connect() as db:
            db.execute(
                "INSERT INTO cases(case_id, claim_event_id, status, care_status, claim_status, "
                "data_source, consent_status, deidentification_status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'COMPLETED', ?, ?, ?, ?, ?)",
                (case.case_id, case.claim_event_id, case.status.value, case.status.value,
                 case.data_source, case.consent_status, case.deidentification_status, now, now),
            )
            db.execute("INSERT INTO case_results(case_id, updated_at) VALUES (?, ?)",
                       (case.case_id, now))
            if owner_user_id is not None:
                owner = db.execute(
                    "SELECT 1 FROM users u JOIN roles r ON r.role_id=u.role_id "
                    "WHERE u.user_id=? AND u.status='ACTIVE' AND r.role_code='CLAIM'",
                    (owner_user_id,),
                ).fetchone()
                if owner is None:
                    raise WorkflowConflict("理赔负责人账号不存在或不可用")
                db.execute(
                    "INSERT INTO case_participants(case_id, user_id, case_role, status, assigned_at) "
                    "VALUES (?, ?, 'CLAIM_OWNER', 'ACTIVE', ?)",
                    (case.case_id, owner_user_id, now),
                )
        return case

    def _row(self, db, case_id):
        row = db.execute("SELECT * FROM cases JOIN case_results USING(case_id) WHERE case_id=?",
                         (case_id,)).fetchone()
        if row is None:
            raise CaseNotFound(case_id)
        return row

    def get_case(self, case_id):
        with self._connect() as db:
            row = self._row(db, case_id)
            result = {name: row[name] for name in WorkflowCase.model_fields}
            for kind in ("ocr", "structured", "pain", "care"):
                result[f"has_{kind}_result"] = row[f"{kind}_result_json"] is not None
            return result

    def get_results(self, case_id):
        with self._connect() as db:
            row = self._row(db, case_id)
            return {"case_id": case_id, **{
                f"{kind}_result": json.loads(row[f"{kind}_result_json"])
                if row[f"{kind}_result_json"] is not None else None
                for kind in ("ocr", "structured", "pain", "care")}}

    def schema_info(self):
        """Return non-sensitive schema metadata for health checks and diagnostics."""
        from .database_migrations import CURRENT_SCHEMA_VERSION

        with self._connect() as db:
            applied = [dict(row) for row in db.execute(
                "SELECT version, migration_name, applied_at, checksum "
                "FROM schema_migrations ORDER BY version"
            )]
            tables = [row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )]
            return {
                "current_version": CURRENT_SCHEMA_VERSION,
                "applied_migrations": applied,
                "tables": tables,
            }

    def create_user(self, username, password_hash, role_code, display_name,
                    phone=None, email=None, status="ACTIVE"):
        """Create a database identity; password hashing is owned by the auth layer."""
        now = datetime.now(timezone.utc).isoformat()
        user_id = str(uuid4())
        with self._connect() as db:
            role = db.execute("SELECT role_id FROM roles WHERE role_code=?", (role_code,)).fetchone()
            if role is None:
                raise WorkflowConflict("未知角色")
            db.execute(
                "INSERT INTO users(user_id, username, password_hash, role_id, display_name, phone, "
                "email, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, username, password_hash, role[0], display_name, phone, email, status, now, now),
            )
        return user_id

    def get_user_by_username(self, username):
        with self._connect() as db:
            row = db.execute(
                "SELECT u.*, r.role_code, r.role_name FROM users u "
                "JOIN roles r ON r.role_id=u.role_id WHERE u.username=?",
                (username,),
            ).fetchone()
            return dict(row) if row else None

    def get_user_identity(self, user_id):
        with self._connect() as db:
            row = db.execute(
                "SELECT u.user_id, u.username, u.display_name, u.phone, u.email, u.status, "
                "u.last_login_at, r.role_code, r.role_name FROM users u "
                "JOIN roles r ON r.role_id=u.role_id WHERE u.user_id=?",
                (user_id,),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["permissions"] = [item[0] for item in db.execute(
                "SELECT p.perm_code FROM permissions p "
                "JOIN role_permissions rp ON rp.perm_id=p.perm_id "
                "JOIN users u ON u.role_id=rp.role_id WHERE u.user_id=? ORDER BY p.perm_code",
                (user_id,),
            )]
            return result

    def mark_login(self, user_id):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("UPDATE users SET last_login_at=?, updated_at=? WHERE user_id=?", (now, now, user_id))

    def update_own_profile(self, user_id, *, display_name, phone, email):
        """Update the three self-service profile fields on the existing users row."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE users SET display_name=?, phone=?, email=?, updated_at=? "
                "WHERE user_id=? AND status='ACTIVE'",
                (display_name, phone, email, now, user_id),
            )
            if cursor.rowcount != 1:
                raise WorkflowItemNotFound("当前用户不存在或不可用")
        return self.get_user_identity(user_id)

    def get_user_password_hash(self, user_id):
        with self._connect() as db:
            row = db.execute(
                "SELECT password_hash FROM users WHERE user_id=? AND status='ACTIVE'",
                (user_id,),
            ).fetchone()
            return row[0] if row else None

    def update_own_password_hash(self, user_id, *, expected_hash, new_hash):
        """Use a compare-and-swap update so a concurrent password change is not overwritten."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE users SET password_hash=?, updated_at=? "
                "WHERE user_id=? AND status='ACTIVE' AND password_hash=?",
                (new_hash, now, user_id, expected_hash),
            )
            return cursor.rowcount == 1

    def list_claim_cases(self, user_id, search=None, claim_status=None,
                         consent_status=None, care_status=None, limit=100):
        clauses = [
            "EXISTS (SELECT 1 FROM case_participants mine WHERE mine.case_id=c.case_id "
            "AND mine.user_id=? AND mine.status='ACTIVE')"
        ]
        params: list[object] = [user_id]
        if search:
            clauses.append("(c.case_id LIKE ? OR c.claim_event_id LIKE ?)")
            term = f"%{search}%"
            params.extend((term, term))
        for column, value in (("claim_status", claim_status), ("consent_status", consent_status),
                              ("care_status", care_status)):
            if value:
                clauses.append(f"c.{column}=?")
                params.append(value)
        params.append(max(1, min(int(limit), 200)))
        sql = f"""
            SELECT c.case_id, c.claim_event_id, c.data_source, c.consent_status,
                   c.deidentification_status, c.claim_status, c.care_status, c.status,
                   c.created_at, c.updated_at,
                   (SELECT customer.display_name FROM users customer
                    WHERE customer.user_id=c.customer_user_id) AS customer_display_name,
                   (SELECT owner.display_name FROM case_participants owner_link
                    JOIN users owner ON owner.user_id=owner_link.user_id
                    WHERE owner_link.case_id=c.case_id AND owner_link.case_role='CLAIM_OWNER'
                    AND owner_link.status='ACTIVE' ORDER BY owner_link.assigned_at DESC LIMIT 1)
                    AS claim_owner_name,
                   CASE WHEN cr.ocr_result_json IS NOT NULL THEN 1 ELSE 0 END AS has_ocr_result,
                   CASE WHEN cr.structured_result_json IS NOT NULL THEN 1 ELSE 0 END AS has_structured_result
            FROM cases c
            JOIN case_results cr ON cr.case_id=c.case_id
            WHERE {' AND '.join(clauses)}
            ORDER BY c.updated_at DESC LIMIT ?
        """
        with self._connect() as db:
            results = [dict(row) for row in db.execute(sql, params)]
            for result in results:
                result["customer_display_name"] = self._mask_customer_label(result["customer_display_name"])
                result["material_status"] = self._claim_material_status(
                    result["status"], result["has_ocr_result"], result["has_structured_result"]
                )
                result["analysis_ready"] = bool(result.pop("has_structured_result"))
                result.pop("has_ocr_result", None)
                result.pop("status", None)
            return results

    def get_claim_case_detail(self, user_id, case_id):
        if not self.user_can_access_case(user_id, case_id, "CASE_VIEW"):
            raise CaseNotFound(case_id)
        with self._connect() as db:
            row = db.execute(
                "SELECT c.*, "
                "(SELECT customer.display_name FROM users customer WHERE customer.user_id=c.customer_user_id) "
                "AS customer_display_name, "
                "(SELECT owner.display_name FROM case_participants owner_link "
                "JOIN users owner ON owner.user_id=owner_link.user_id "
                "WHERE owner_link.case_id=c.case_id AND owner_link.case_role='CLAIM_OWNER' "
                "AND owner_link.status='ACTIVE' ORDER BY owner_link.assigned_at DESC LIMIT 1) "
                "AS claim_owner_name, cr.ocr_result_json, "
                "cr.structured_result_json, cr.pain_result_json, cr.care_result_json "
                "FROM cases c JOIN case_results cr ON cr.case_id=c.case_id "
                "WHERE c.case_id=?",
                (case_id,),
            ).fetchone()
            if row is None:
                raise CaseNotFound(case_id)
            result = {key: row[key] for key in (
                "case_id", "claim_event_id", "data_source", "consent_status",
                "deidentification_status", "claim_status", "care_status", "status",
                "created_at", "updated_at", "customer_display_name", "claim_owner_name",
            )}
            result["customer_display_name"] = self._mask_customer_label(result["customer_display_name"])
            for kind in ("ocr", "structured", "pain", "care"):
                result[f"has_{kind}_result"] = row[f"{kind}_result_json"] is not None
            result["material_status"] = self._claim_material_status(
                row["status"], row["ocr_result_json"], row["structured_result_json"]
            )
            result["analysis_ready"] = row["structured_result_json"] is not None
            for kind in ("ocr", "structured", "pain", "care"):
                result.pop(f"has_{kind}_result", None)
            result.pop("status", None)
            return result

    @staticmethod
    def _mask_customer_label(value):
        if not value:
            return None
        value = str(value)
        if len(value) == 1:
            return "*"
        if len(value) == 2:
            return value[0] + "*"
        return value[0] + "*" * (len(value) - 2) + value[-1]

    @staticmethod
    def _claim_material_status(workflow_status, ocr_payload, structured_payload):
        if workflow_status == "PROCESSING":
            return "processing"
        if structured_payload:
            return "completed"
        if ocr_payload:
            return "submitted"
        return "not_submitted"

    def claim_dashboard(self, user_id):
        cases = self.list_claim_cases(user_id, limit=200)
        awaiting_material = sum(not item["analysis_ready"] for item in cases)
        awaiting_authorization = sum(
            item["consent_status"] != "authorized" or item["deidentification_status"] != "confirmed"
            for item in cases
        )
        ready_for_care = sum(
            item["analysis_ready"] and item["consent_status"] == "authorized"
            and item["deidentification_status"] == "confirmed"
            and item["care_status"] in {"RECEIVED", "STRUCTURED"}
            for item in cases
        )
        return {
            "total": len(cases),
            "pending": sum(
                not item["analysis_ready"] or item["consent_status"] != "authorized"
                or item["deidentification_status"] != "confirmed"
                or item["care_status"] in {"RECEIVED", "STRUCTURED"}
                for item in cases
            ),
            "awaiting_material": awaiting_material,
            "awaiting_authorization": awaiting_authorization,
            "ready_for_care": ready_for_care,
            "care_started": sum(item["care_status"] not in {"RECEIVED", "STRUCTURED"} for item in cases),
            "recent_cases": cases[:8],
        }

    def claim_tasks(self, user_id):
        tasks = []
        for item in self.list_claim_cases(user_id, limit=200):
            reasons = []
            if item["consent_status"] != "authorized":
                reasons.append("授权待确认")
            if item["deidentification_status"] != "confirmed":
                reasons.append("脱敏状态待确认")
            if not item["analysis_ready"]:
                reasons.append("材料待补充或处理")
            if (item["analysis_ready"] and item["consent_status"] == "authorized"
                    and item["deidentification_status"] == "confirmed"
                    and item["care_status"] in {"RECEIVED", "STRUCTURED"}):
                reasons.append("可以移交医护关怀")
            if reasons:
                tasks.append({"case_id": item["case_id"], "claim_event_id": item["claim_event_id"],
                              "updated_at": item["updated_at"], "reasons": reasons})
        return tasks

    def trigger_care(self, user_id, case_id):
        if not self.user_can_access_case(user_id, case_id, "CARE_TRIGGER"):
            raise CaseNotFound(case_id)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            row = db.execute(
                "SELECT c.consent_status, c.deidentification_status, c.care_status, "
                "cr.structured_result_json FROM cases c JOIN case_results cr USING(case_id) "
                "WHERE c.case_id=?",
                (case_id,),
            ).fetchone()
            if row is None:
                raise CaseNotFound(case_id)
            blockers = []
            if row["consent_status"] != "authorized":
                blockers.append("材料使用授权尚未确认")
            if row["deidentification_status"] != "confirmed":
                blockers.append("材料脱敏状态尚未确认")
            if row["structured_result_json"] is None:
                blockers.append("病例结构化尚未完成")
            if blockers:
                raise WorkflowConflict("；".join(blockers))
            if row["care_status"] in {"RECEIVED", "STRUCTURED"}:
                db.execute("UPDATE cases SET care_status='CARE_TRIGGERED', updated_at=? WHERE case_id=?",
                           (now, case_id))
                care_status = "CARE_TRIGGERED"
            else:
                care_status = row["care_status"]
            return {"case_id": case_id, "care_status": care_status,
                    "message": "案件已移交医护关怀，等待医疗处理或复核"}

    def assign_case_participant(self, case_id, user_id, case_role):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            self._row(db, case_id)
            if db.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is None:
                raise WorkflowItemNotFound("用户不存在")
            db.execute(
                "INSERT INTO case_participants(case_id, user_id, case_role, status, assigned_at) "
                "VALUES (?, ?, ?, 'ACTIVE', ?) ON CONFLICT(case_id, user_id, case_role) "
                "DO UPDATE SET status='ACTIVE', assigned_at=excluded.assigned_at, unassigned_at=NULL",
                (case_id, user_id, case_role, now),
            )
            if case_role == "CUSTOMER":
                db.execute(
                    "UPDATE cases SET customer_user_id=?, updated_at=? WHERE case_id=?",
                    (user_id, now, case_id),
                )

    def assign_medical_reviewer(self, case_id, reviewer_username):
        with self._connect() as db:
            reviewer = db.execute(
                "SELECT u.user_id, u.display_name FROM users u "
                "JOIN roles r ON r.role_id=u.role_id "
                "WHERE u.username=? AND u.status='ACTIVE' AND r.role_code='MEDICAL'",
                (reviewer_username,),
            ).fetchone()
        if reviewer is None:
            raise WorkflowItemNotFound("医疗复核账号不存在或不可用")
        self.assign_case_participant(case_id, reviewer["user_id"], "MEDICAL_REVIEWER")
        return {"user_id": reviewer["user_id"], "display_name": reviewer["display_name"]}

    def user_can_access_case(self, user_id, case_id, permission_code):
        """Enforce role permission plus active case assignment."""
        with self._connect() as db:
            row = db.execute(
                "SELECT 1 FROM users u "
                "JOIN role_permissions rp ON rp.role_id=u.role_id "
                "JOIN permissions p ON p.perm_id=rp.perm_id "
                "JOIN case_participants cp ON cp.user_id=u.user_id "
                "WHERE u.user_id=? AND u.status='ACTIVE' AND cp.case_id=? "
                "AND cp.status='ACTIVE' AND p.perm_code=?",
                (user_id, case_id, permission_code),
            ).fetchone()
            return row is not None

    def _status(self, db, case_id, status):
        now = datetime.now(timezone.utc).isoformat()
        db.execute("UPDATE cases SET status=?, care_status=?, updated_at=? WHERE case_id=?",
                   (status.value, status.value, now, case_id))
        db.execute("UPDATE case_results SET updated_at=? WHERE case_id=?", (now, case_id))

    def begin_processing(self, case_id):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, case_id)
            if row["status"] == WorkflowStatus.PROCESSING.value:
                raise WorkflowConflict("案件正在处理，请等待当前请求完成")
            self._status(db, case_id, WorkflowStatus.PROCESSING)

    def finish_failed_processing(self, case_id):
        """No FAILED state: return to the last successfully saved stage."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, case_id)
            status = WorkflowStatus.RECEIVED
            for kind, stage in (("ocr", WorkflowStatus.OCR_READY),
                                ("structured", WorkflowStatus.STRUCTURED),
                                ("pain", WorkflowStatus.PAIN_READY), ("care", WorkflowStatus.CARE_READY)):
                if row[f"{kind}_result_json"] is not None:
                    status = stage
            self._status(db, case_id, status)

    def save_ocr(self, case_id, documents):
        with self._connect() as db:
            self._row(db, case_id)
            now = datetime.now(timezone.utc).isoformat()
            db.execute("UPDATE case_results SET ocr_result_json=?, updated_at=? WHERE case_id=?",
                       (encode(documents), now, case_id))
            db.execute("UPDATE cases SET updated_at=? WHERE case_id=?", (now, case_id))

    def save_ocr_review_draft(self, case_id, documents):
        """Save reviewed OCR locally and invalidate downstream derived data."""

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._row(db, case_id)
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "UPDATE case_results SET ocr_result_json=?, structured_result_json=NULL, "
                "pain_result_json=NULL, care_result_json=NULL, updated_at=? WHERE case_id=?",
                (encode(documents), now, case_id),
            )
            db.execute(
                "UPDATE cases SET status=?, care_status=?, "
                "deidentification_status='pending_review', updated_at=? WHERE case_id=?",
                (WorkflowStatus.OCR_READY.value, WorkflowStatus.OCR_READY.value, now, case_id),
            )
            self._invalidate_profile(db, case_id, clear_context=True)
            self._invalidate_recommendations(db, case_id)

    def confirm_deidentification(self, case_id, review_method="manual_confirmation", findings=None,
                                 document_fingerprint=None):
        """Confirm reviewed OCR and write the event to the canonical system log."""

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, case_id)
            if row["consent_status"] != "authorized":
                raise WorkflowConflict("外部模型处理前必须先确认材料使用授权")
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "UPDATE cases SET deidentification_status='confirmed', updated_at=? WHERE case_id=?",
                (now, case_id),
            )
            db.execute(
                "INSERT INTO system_logs(log_id, user_id, case_id, action, module, request_path, "
                "ip_address, request_params_json, result, error_message, created_at) "
                "VALUES (?, NULL, ?, 'confirm_deidentification', 'OCR_REVIEW', NULL, NULL, ?, "
                "'allowed', NULL, ?)",
                (
                    str(uuid4()), case_id,
                    encode({
                        "review_method": review_method,
                        "sensitive_pattern_findings": list(findings or []),
                        "document_fingerprint": document_fingerprint,
                    }),
                    now,
                ),
            )

    def save_structured(self, case_id, result):
        payload = json.loads(encode(result))
        if payload.get("case_id") != case_id:
            raise WorkflowConflict("结构化病例 case_id 不匹配")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._row(db, case_id)
            db.execute("UPDATE case_results SET structured_result_json=?, pain_result_json=NULL, "
                       "care_result_json=NULL WHERE case_id=?", (encode(payload), case_id))
            self._invalidate_profile(db, case_id, clear_context=True)
            self._invalidate_recommendations(db, case_id)
            self._status(db, case_id, WorkflowStatus.STRUCTURED)

    def require_current(self, case_id, case, pain=None):
        with self._connect() as db:
            return self._require_current(db, case_id, case, pain)

    def _require_current(self, db, case_id, case, pain=None):
        row = self._row(db, case_id)
        if row["status"] == WorkflowStatus.PROCESSING.value:
            raise WorkflowConflict("案件正在重新结构化")
        if row["structured_result_json"] != encode(case):
            raise WorkflowConflict("请求病例不是当前保存的结构化结果，请重新读取案件")
        if pain is not None and row["pain_result_json"] != encode(pain):
            raise WorkflowConflict("请求痛点不是当前保存的痛点结果，请重新读取案件")
        return row

    def save_pain(self, case_id, result, case):
        if result.case_id != case_id:
            raise WorkflowConflict("痛点报告 case_id 不匹配")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_current(db, case_id, case)
            db.execute("UPDATE case_results SET pain_result_json=?, care_result_json=NULL WHERE case_id=?",
                       (encode(result), case_id))
            self._invalidate_profile(db, case_id)
            self._invalidate_recommendations(db, case_id)
            self._status(db, case_id, WorkflowStatus.PAIN_READY)

    def save_care(self, case_id, result, case, pain):
        if result.case_id != case_id:
            raise WorkflowConflict("指引报告 case_id 不匹配")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._require_current(db, case_id, case, pain)
            if pain is None or row["pain_result_json"] is None:
                raise WorkflowConflict("请先保存痛点分析结果")
            db.execute("UPDATE case_results SET care_result_json=? WHERE case_id=?", (encode(result), case_id))
            self._status(db, case_id, WorkflowStatus.CARE_READY)

    def create_mobile_preview(
        self,
        case_id: str,
        ttl_minutes: int = 30,
        *,
        delivery_content: dict | None = None,
        content_fingerprint: str | None = None,
    ):
        """Create an expiring, non-persistent patient-facing snapshot."""

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=ttl_minutes)
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        public_case_id = f"PREVIEW-{secrets.token_hex(4).upper()}"
        publication_id = f"MP-{secrets.token_hex(8).upper()}"
        with self._connect() as db:
            row = self._row(db, case_id)
            if row["consent_status"] != "authorized" or row["deidentification_status"] != "confirmed":
                raise WorkflowConflict("手机预览仅允许用于已授权并确认脱敏的案件")
            if row["care_result_json"] is None:
                raise WorkflowConflict("请先生成温度指引")
            care = json.loads(row["care_result_json"])
        content = delivery_content or {
            "care_guide": care,
            "recommendation": [],
            "recommendation_unavailable_reason": None,
        }
        guide = (content.get("care_guide") or {}).get("guide")
        if not isinstance(guide, dict):
            raise WorkflowConflict("温度指引缺少可预览内容")
        safe_content = {
            "care_guide": content.get("care_guide"),
            "recommendation": content.get("recommendation") or [],
            "recommendation_unavailable_reason": content.get("recommendation_unavailable_reason"),
        }

        def replace_private_id(value):
            if isinstance(value, dict):
                return {key: replace_private_id(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace_private_id(item) for item in value]
            return public_case_id if value == case_id else value

        safe_content = replace_private_id(safe_content)
        publication = {
            "version": "patient-mobile-preview-v2-ephemeral",
            "publication_id": publication_id,
            "status": "preview",
            "case_id": public_case_id,
            "created_at": now.isoformat(),
            "published_at": None,
            "expires_at": expires_at.isoformat(),
            "content_fingerprint": content_fingerprint,
            "report": safe_content["care_guide"],
            "delivery": {"status": "preview", "content": safe_content},
        }
        with self._preview_lock:
            self._purge_previews(now)
            self._preview_cache[token_hash] = publication
        return {
            "token": token,
            "publication_id": publication_id,
            "expires_at": expires_at.isoformat(),
        }

    def get_mobile_preview(self, token: str):
        """Resolve a process-local preview by a raw token."""

        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        with self._preview_lock:
            self._purge_previews(now)
            publication = self._preview_cache.get(token_hash)
            if publication is None:
                raise WorkflowItemNotFound("手机预览链接不存在、已过期或服务已重启")
            return json.loads(json.dumps(publication, ensure_ascii=False))

    @classmethod
    def _purge_previews(cls, now: datetime):
        expired = [
            key for key, value in cls._preview_cache.items()
            if datetime.fromisoformat(value["expires_at"]) <= now
        ]
        for key in expired:
            cls._preview_cache.pop(key, None)

    def _invalidate_profile(self, db, case_id, clear_context=False):
        changed = db.execute("DELETE FROM profile_case_records WHERE case_id=? AND source_kind='workflow'",
                             (case_id,)).rowcount
        if clear_context:
            db.execute("DELETE FROM case_profile_contexts WHERE case_id=?", (case_id,))
        if changed:
            db.execute("DELETE FROM quality_portfolios")

    @staticmethod
    def _invalidate_recommendations(db, case_id):
        db.execute(
            "UPDATE recommendation_reports SET status='INVALIDATED', updated_at=? "
            "WHERE case_id=? AND status<>'INVALIDATED'",
            (datetime.now(timezone.utc).isoformat(), case_id),
        )

    def save_provider_quality_observation(self, *, case_id, provider_type, provider_id,
                                          disease_group, metric_code, raw_value=None,
                                          unit=None, status="OBSERVED", evidence=None,
                                          source_kind="workflow", observed_at=None):
        """Store one case-level quality fact without turning it into a conclusion."""
        now = datetime.now(timezone.utc).isoformat()
        observation_id = str(uuid4())
        with self._connect() as db:
            db.execute(
                "INSERT INTO provider_quality_observations(observation_id, case_id, provider_type, "
                "provider_id, disease_group, metric_code, raw_value, unit, status, evidence_json, "
                "source_kind, observed_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(case_id, provider_type, provider_id, disease_group, metric_code) "
                "DO UPDATE SET raw_value=excluded.raw_value, unit=excluded.unit, status=excluded.status, "
                "evidence_json=excluded.evidence_json, source_kind=excluded.source_kind, "
                "observed_at=excluded.observed_at, created_at=excluded.created_at",
                (observation_id, case_id, provider_type, provider_id, disease_group, metric_code,
                 raw_value, unit, status, encode(evidence or {}), source_kind, observed_at, now),
            )

    def save_provider_profile_snapshot(self, *, provider_type, provider_id, disease_group,
                                       metrics, sample_size, data_sufficiency_status,
                                       profile, composite_score=None):
        """Version an aggregated provider profile built from quality observations."""
        now = datetime.now(timezone.utc).isoformat()
        snapshot_id = str(uuid4())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT COALESCE(MAX(profile_version), 0) FROM provider_profile_snapshots "
                "WHERE provider_type=? AND provider_id=? AND disease_group=?",
                (provider_type, provider_id, disease_group),
            ).fetchone()[0]
            version = current + 1
            db.execute(
                "UPDATE provider_profile_snapshots SET status='SUPERSEDED', valid_to=? "
                "WHERE provider_type=? AND provider_id=? AND disease_group=? AND status='ACTIVE'",
                (now, provider_type, provider_id, disease_group),
            )
            db.execute(
                "INSERT INTO provider_profile_snapshots(snapshot_id, provider_type, provider_id, "
                "disease_group, profile_version, sample_size, data_sufficiency_status, composite_score, "
                "profile_json, status, valid_from, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "'ACTIVE', ?, ?)",
                (snapshot_id, provider_type, provider_id, disease_group, version, sample_size,
                 data_sufficiency_status, composite_score, encode(profile), now, now),
            )
            for metric in metrics:
                value = json.loads(encode(metric))
                db.execute(
                    "INSERT INTO provider_metric_scores(metric_score_id, snapshot_id, metric_code, "
                    "metric_name, score, status, sample_size, confidence, calculation, evidence_json, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid4()), snapshot_id, value["metric_code"], value["metric_name"],
                     value.get("score"), value["status"], value.get("sample_size", 0),
                     value.get("confidence", 0), value.get("calculation", ""),
                     encode(value.get("evidence") or {}), now),
                )
        return {"snapshot_id": snapshot_id, "profile_version": version, "status": "ACTIVE"}

    def load_provider_profile(self, provider_type, provider_id, disease_group, profile_version=None):
        with self._connect() as db:
            if profile_version is None:
                row = db.execute(
                    "SELECT * FROM provider_profile_snapshots WHERE provider_type=? AND provider_id=? "
                    "AND disease_group=? ORDER BY profile_version DESC LIMIT 1",
                    (provider_type, provider_id, disease_group),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT * FROM provider_profile_snapshots WHERE provider_type=? AND provider_id=? "
                    "AND disease_group=? AND profile_version=?",
                    (provider_type, provider_id, disease_group, profile_version),
                ).fetchone()
            if row is None:
                return None
            output = dict(row)
            output["profile"] = json.loads(row["profile_json"])
            output["metrics"] = [dict(item) for item in db.execute(
                "SELECT * FROM provider_metric_scores WHERE snapshot_id=? ORDER BY metric_code",
                (row["snapshot_id"],),
            )]
            return output

    def save_recommendation_report(self, case_id, report, clinician_recommendations=None,
                                   status="DRAFT"):
        """Persist a V2 recommendation as a versioned report and normalized children.

        The method does not calculate, filter or reorder recommendations.  It only
        stores the already calculated backend result and its evidence chain.
        """
        if status not in {"DRAFT", "REVIEWED", "PUBLISHED"}:
            raise ValueError("recommendation report status 无效")
        payload = json.loads(encode(report))
        context = payload.get("context") or {}
        if context.get("case_id") != case_id:
            raise WorkflowConflict("推荐报告 case_id 不匹配")
        clinician_rows = json.loads(encode(clinician_recommendations or []))
        now = datetime.now(timezone.utc).isoformat()
        report_id = str(uuid4())

        hospital_profiles = {
            item.get("hospital_id"): item
            for item in payload.get("candidate_profiles", [])
        }
        clinician_profiles = {
            item.get("clinician_id"): item
            for item in payload.get("candidate_clinician_profiles", [])
        }
        evidence_records = {}
        for profile in [*hospital_profiles.values(), *clinician_profiles.values()]:
            for evidence in profile.get("evidence_records", []):
                evidence_records[evidence.get("evidence_id")] = evidence

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._row(db, case_id)
            current = db.execute(
                "SELECT COALESCE(MAX(report_version), 0) FROM recommendation_reports WHERE case_id=?",
                (case_id,),
            ).fetchone()[0]
            version = current + 1
            self._invalidate_recommendations(db, case_id)
            intent = payload.get("recommendation_intent") or {}
            planner_version = intent.get("planner") or payload.get("engine_version") or "unknown"
            db.execute(
                "INSERT INTO recommendation_reports(report_id, case_id, report_version, disease_group, "
                "patient_city, status, planner_version, report_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (report_id, case_id, version, context.get("disease_group") or "unavailable",
                 context.get("city"), status, planner_version, encode(payload), now, now),
            )

            items = []
            for recommendation in payload.get("recommendations", []):
                profile = hospital_profiles.get(recommendation.get("hospital_id"), {})
                foundation = profile.get("institution_foundation") or {}
                specialty = profile.get("disease_specialty") or {}
                items.append(("HOSPITAL", recommendation, {
                    "entity_id": recommendation.get("hospital_id"),
                    "entity_name": recommendation.get("hospital_name") or foundation.get("hospital_name"),
                    "city": (recommendation.get("accessibility") or {}).get("hospital_city") or foundation.get("city"),
                    "department": "、".join(specialty.get("related_departments") or []),
                    "score": recommendation.get("recommendation_score"),
                    "confidence": recommendation.get("recommendation_confidence"),
                }))
            for recommendation in clinician_rows:
                profile = clinician_profiles.get(recommendation.get("clinician_id"), {})
                basic = profile.get("basic_qualification") or {}
                items.append(("DOCTOR", recommendation, {
                    "entity_id": recommendation.get("clinician_id"),
                    "entity_name": recommendation.get("clinician_name") or basic.get("clinician_name"),
                    "city": None,
                    "department": basic.get("department"),
                    "score": recommendation.get("recommendation_score"),
                    "confidence": recommendation.get("recommendation_confidence"),
                }))

            for item_type, recommendation, fields in items:
                item_id = str(uuid4())
                evidence_status = str(recommendation.get("evidence_status") or "pending_verification").upper()
                db.execute(
                    "INSERT INTO recommendation_items(item_id, report_id, item_type, entity_id, entity_name, "
                    "city, department, profile_snapshot_id, rank_no, total_score, confidence, evidence_status, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (item_id, report_id, item_type, fields["entity_id"], fields["entity_name"] or "unavailable",
                     fields["city"], fields["department"], recommendation.get("profile_snapshot_id"),
                     recommendation.get("rank", 1),
                     fields["score"], fields["confidence"], evidence_status, now),
                )
                for dimension in recommendation.get("dimension_scores", []):
                    db.execute(
                        "INSERT INTO recommendation_scores(score_id, item_id, dimension_code, dimension_name, "
                        "score, weight, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (str(uuid4()), item_id, dimension.get("dimension"), dimension.get("label"),
                         dimension.get("score"), dimension.get("weight"),
                         dimension.get("reasoning") or "", now),
                    )
                for reason in recommendation.get("reasons", []):
                    linked = [evidence_records[item] for item in reason.get("evidence_record_ids", [])
                              if item in evidence_records]
                    if not linked:
                        linked = [{
                            "evidence_field": reason.get("dimension"),
                            "source_name": reason.get("suggested_source_name"),
                            "source_url": reason.get("suggested_source_url"),
                            "supported_claim": reason.get("suggested_evidence_summary"),
                            "evidence_confidence": reason.get("evidence_confidence", 0.5),
                            "verified": False,
                        }]
                    for evidence in linked:
                        ev_status = reason.get("evidence_status") or (
                            "verified" if evidence.get("verified") else "pending_verification"
                        )
                        db.execute(
                            "INSERT INTO recommendation_evidence(evidence_id, item_id, reason, evidence_type, "
                            "source_name, source_url, evidence_summary, evidence_status, evidence_confidence, "
                            "verified_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (str(uuid4()), item_id, reason.get("explanation") or reason.get("title") or "",
                             evidence.get("evidence_field"), evidence.get("source_name"),
                             evidence.get("source_url"), evidence.get("supported_claim") or evidence.get("evidence_text"),
                             str(ev_status).upper(), evidence.get("evidence_confidence", 0.5),
                             now if str(ev_status).lower() == "verified" else None, now),
                        )
        return {"report_id": report_id, "report_version": version, "status": status}

    def load_recommendation_report(self, case_id, report_version=None):
        with self._connect() as db:
            self._row(db, case_id)
            if report_version is None:
                row = db.execute(
                    "SELECT * FROM recommendation_reports WHERE case_id=? "
                    "ORDER BY report_version DESC LIMIT 1",
                    (case_id,),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT * FROM recommendation_reports WHERE case_id=? AND report_version=?",
                    (case_id, report_version),
                ).fetchone()
            if row is None:
                return None
            items = []
            for item in db.execute(
                "SELECT * FROM recommendation_items WHERE report_id=? ORDER BY item_type, rank_no",
                (row["report_id"],),
            ):
                item_data = dict(item)
                item_data["scores"] = [dict(value) for value in db.execute(
                    "SELECT * FROM recommendation_scores WHERE item_id=? ORDER BY rowid", (item["item_id"],)
                )]
                item_data["evidence"] = [dict(value) for value in db.execute(
                    "SELECT * FROM recommendation_evidence WHERE item_id=? ORDER BY rowid", (item["item_id"],)
                )]
                items.append(item_data)
            output = dict(row)
            output["report"] = json.loads(row["report_json"])
            output["items"] = items
            return output

    def save_profile_context(self, case_id, settings, case, pain=None):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_current(db, case_id, case, pain)
            current = db.execute("SELECT context_json FROM case_profile_contexts WHERE case_id=?", (case_id,)).fetchone()
            payload = encode(settings)
            if current and current[0] == payload:
                return
            db.execute("INSERT INTO case_profile_contexts VALUES (?, ?, ?) ON CONFLICT(case_id) "
                       "DO UPDATE SET context_json=excluded.context_json, updated_at=excluded.updated_at",
                       (case_id, payload, datetime.now(timezone.utc).isoformat()))
            self._invalidate_profile(db, case_id)

    def get_profile_context(self, case_id):
        with self._connect() as db:
            self._row(db, case_id)
            row = db.execute("SELECT context_json FROM case_profile_contexts WHERE case_id=?", (case_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def profile_context_case_ids(self):
        with self._connect() as db:
            return [row[0] for row in db.execute("SELECT case_id FROM case_profile_contexts ORDER BY case_id")]

    def save_profile_record(self, payload, assembly, source_kind="imported"):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if source_kind == "workflow":
                self._require_current(db, payload.case.case_id, payload.case, payload.pain_report)
                metadata = self._row(db, payload.case.case_id)
                if metadata["consent_status"] != "authorized" or metadata["deidentification_status"] != "confirmed":
                    raise WorkflowConflict("画像数据入库需要已声明授权且已确认脱敏")
            elif db.execute("SELECT 1 FROM cases WHERE case_id=?", (payload.case.case_id,)).fetchone():
                raise WorkflowConflict("历史导入不能覆盖当前工作流案件，请使用案件上下文接口")
            record_json = encode(assembly.record) if assembly.record else None
            input_json = encode(payload)
            previous = db.execute("SELECT input_json, record_json FROM profile_case_records WHERE case_id=?",
                                  (payload.case.case_id,)).fetchone()
            if previous and previous[0] == input_json and previous[1] == record_json:
                return
            db.execute("INSERT INTO profile_case_records VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(case_id) "
                       "DO UPDATE SET input_json=excluded.input_json, record_json=excluded.record_json, "
                       "assembly_json=excluded.assembly_json, source_kind=excluded.source_kind, updated_at=excluded.updated_at",
                       (payload.case.case_id, input_json, record_json, encode(assembly), source_kind,
                        datetime.now(timezone.utc).isoformat()))
            db.execute("DELETE FROM quality_portfolios")

    def load_profile_records(self):
        from .quality_models import ProfileCaseRecord
        with self._connect() as db:
            return [ProfileCaseRecord.model_validate_json(row[0]) for row in db.execute(
                "SELECT record_json FROM profile_case_records WHERE record_json IS NOT NULL ORDER BY case_id")]

    def profile_dataset_summary(self):
        with self._connect() as db:
            rows = db.execute("SELECT input_json, record_json, assembly_json FROM profile_case_records ORDER BY case_id").fetchall()
            sources = {}
            accepted = 0
            for row in rows:
                source = json.loads(row[0])["data_source"]
                sources[source] = sources.get(source, 0) + 1
                accepted += row[1] is not None
            return {"submitted_cases": len(rows), "accepted_cases": accepted,
                    "excluded_cases": len(rows) - accepted, "data_sources": sources,
                    "contains_non_real_data": any(source not in {
                        "official_deidentified_dataset", "authorized_deidentified_dataset", "user_upload"}
                        for source in sources),
                    "source_verification": "caller_declaration_only"}

    def save_portfolio(self, portfolio, records):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            saved = [json.loads(row[0]) for row in db.execute(
                "SELECT record_json FROM profile_case_records WHERE record_json IS NOT NULL ORDER BY case_id")]
            expected = [record.model_dump(mode="json") for record in sorted(records, key=lambda item: item.case.case_id)]
            if encode(saved) != encode(expected):
                raise WorkflowConflict("画像计算期间历史数据已改变，请重新构建 portfolio")
            db.execute("INSERT INTO quality_portfolios VALUES ('current', ?, ?) ON CONFLICT(portfolio_key) "
                       "DO UPDATE SET portfolio_json=excluded.portfolio_json, updated_at=excluded.updated_at",
                       (encode(portfolio), datetime.now(timezone.utc).isoformat()))

    def load_portfolio(self):
        from .quality_models import QualityPortfolio
        with self._connect() as db:
            row = db.execute("SELECT portfolio_json FROM quality_portfolios WHERE portfolio_key='current'").fetchone()
            return QualityPortfolio.model_validate_json(row[0]) if row else None

    def _delivery_snapshot(self, db, case_id):
        row = self._row(db, case_id)
        portfolio = db.execute("SELECT portfolio_json FROM quality_portfolios WHERE portfolio_key='current'").fetchone()
        context = db.execute("SELECT context_json FROM case_profile_contexts WHERE case_id=?", (case_id,)).fetchone()
        return {"case_id": case_id, "consent_status": row["consent_status"],
                "deidentification_status": row["deidentification_status"], "data_source": row["data_source"],
                "portfolio": json.loads(portfolio[0]) if portfolio else None,
                "profile_context": json.loads(context[0]) if context else None,
                "results": {f"{kind}_result": json.loads(row[f"{kind}_result_json"])
                            if row[f"{kind}_result_json"] is not None else None
                            for kind in ("ocr", "structured", "pain", "care")}}

    def delivery_input_snapshot(self, case_id):
        with self._connect() as db:
            db.execute("BEGIN")
            return self._delivery_snapshot(db, case_id)

    def _verify_delivery_snapshot(self, db, prepared):
        if encode(self._delivery_snapshot(db, prepared["case_id"])) != encode(prepared["snapshot"]):
            raise WorkflowConflict("案件或画像内容已改变，旧审核/准备结果无效，请重新准备")

    def _current_reviews(self, db, prepared):
        return [json.loads(row[0]) for row in db.execute(
            "SELECT record_json FROM reviews WHERE case_id=? AND content_fingerprint=? ORDER BY rowid DESC",
            (prepared["case_id"], prepared["content_fingerprint"]))]

    def _delivery_authorization(self, db, prepared):
        from .delivery_gate import GateDecision
        gate = prepared["gate"]
        reviews = self._current_reviews(db, prepared)
        if gate.decision == GateDecision.BLOCKED:
            return {"can_send": False, "review_status": "BLOCKED", "reviews": reviews}
        if gate.decision == GateDecision.AUTO_DELIVER:
            return {"can_send": True, "review_status": "NOT_REQUIRED", "reviews": reviews}
        relevant_targets = {"care_guide"}
        if prepared["content"]["recommendation"]:
            relevant_targets.add("recommendation")
        decisions = {}
        for target in relevant_targets:
            latest = next((r for r in reviews if r["target_type"] in {target, "combined_delivery"}), None)
            decisions[target] = latest["decision"] if latest else None
        if any(value == "REJECTED" for value in decisions.values()):
            return {"can_send": False, "review_status": "REJECTED", "reviews": reviews}
        allowed = all(decisions.get(target.value) == "APPROVED" for target in gate.review_targets)
        return {"can_send": allowed, "review_status": "APPROVED" if allowed else "PENDING", "reviews": reviews}

    def _delivered_case_status(self, db, case_id):
        if db.execute("SELECT 1 FROM follow_up_tasks WHERE case_id=? AND status='OPEN'", (case_id,)).fetchone():
            return WorkflowStatus.FOLLOWUP_PENDING
        if db.execute("SELECT 1 FROM follow_up_tasks WHERE case_id=? AND status='RESOLVED'", (case_id,)).fetchone():
            return WorkflowStatus.RESOLVED
        if db.execute("SELECT 1 FROM feedback WHERE case_id=?", (case_id,)).fetchone():
            return WorkflowStatus.FEEDBACK_RECEIVED
        return WorkflowStatus.DELIVERED_MOCK

    def prepare_delivery(self, prepared):
        from .delivery_gate import GateDecision
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._verify_delivery_snapshot(db, prepared)
            authorization = self._delivery_authorization(db, prepared)
            row = self._row(db, prepared["case_id"])
            delivered = db.execute("SELECT 1 FROM deliveries WHERE case_id=? AND content_fingerprint=?",
                                   (prepared["case_id"], prepared["content_fingerprint"])).fetchone()
            post_states = {"DELIVERED_MOCK", "FEEDBACK_RECEIVED", "FOLLOWUP_PENDING", "RESOLVED"}
            if delivered and row["status"] not in post_states and row["status"] != "PROCESSING" and authorization["can_send"]:
                self._status(db, prepared["case_id"], self._delivered_case_status(db, prepared["case_id"]))
                row = self._row(db, prepared["case_id"])
            if row["status"] != "PROCESSING" and not (delivered and row["status"] in post_states):
                if prepared["gate"].decision == GateDecision.BLOCKED:
                    status = WorkflowStatus.BLOCKED
                elif authorization["review_status"] == "REJECTED":
                    status = WorkflowStatus.REJECTED
                elif authorization["can_send"]:
                    status = WorkflowStatus.DELIVERY_READY
                else:
                    status = WorkflowStatus.REVIEW_REQUIRED
                self._status(db, prepared["case_id"], status)
            return {"case_id": prepared["case_id"], **prepared["gate"].model_dump(mode="json"),
                    "content_fingerprint": prepared["content_fingerprint"],
                    "care_included": True, "recommendation_included": bool(prepared["content"]["recommendation"]),
                    "recommendation_unavailable_reason": prepared["content"]["recommendation_unavailable_reason"],
                    "already_delivered": bool(delivered),
                    **authorization}

    def save_review(self, prepared, request):
        from .delivery_gate import GateDecision
        from .review_models import ReviewRecord
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._verify_delivery_snapshot(db, prepared)
            if request.content_fingerprint != prepared["content_fingerprint"]:
                raise WorkflowConflict("审核页面内容已过时，请重新准备并核对当前内容")
            if prepared["gate"].decision != GateDecision.REVIEW_REQUIRED:
                raise WorkflowConflict("仅 REVIEW_REQUIRED 内容可提交审核；BLOCKED 不能由批准绕过，低风险无需批准")
            if self._row(db, prepared["case_id"])["status"] == "PROCESSING":
                raise WorkflowConflict("案件正在处理，不能审核旧内容")
            if db.execute("SELECT 1 FROM deliveries WHERE case_id=? AND content_fingerprint=?",
                          (prepared["case_id"], prepared["content_fingerprint"])).fetchone():
                raise WorkflowConflict("该内容已经触达，不能事后修改审核；请处理反馈或重新生成不同内容")
            if request.target_type.value == "recommendation" and not prepared["content"]["recommendation"]:
                raise WorkflowConflict("没有可用推荐，不能审核不存在的推荐内容")
            record = ReviewRecord(**request.model_dump(), review_id=str(uuid4()), case_id=prepared["case_id"],
                                  created_at=datetime.now(timezone.utc).isoformat())
            db.execute(
                "INSERT INTO reviews(review_id, case_id, content_fingerprint, target_type, decision, "
                "record_json, created_at, comment) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (record.review_id, record.case_id, record.content_fingerprint, record.target_type.value,
                 record.decision.value, encode(record), record.created_at, record.note),
            )
            authorization = self._delivery_authorization(db, prepared)
            status = WorkflowStatus.REJECTED if authorization["review_status"] == "REJECTED" else \
                     WorkflowStatus.APPROVED if authorization["can_send"] else WorkflowStatus.REVIEW_REQUIRED
            self._status(db, record.case_id, status)
            return record

    def list_reviews(self, case_id):
        with self._connect() as db:
            self._row(db, case_id)
            return [json.loads(row[0]) for row in db.execute("SELECT record_json FROM reviews WHERE case_id=? ORDER BY rowid DESC", (case_id,))]

    def send_delivery(self, prepared):
        from .delivery import DeliveryRecord, DeliveryMode
        from .delivery_gate import GateDecision
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._verify_delivery_snapshot(db, prepared)
            row = self._row(db, prepared["case_id"])
            if row["status"] == "PROCESSING":
                raise WorkflowConflict("案件正在处理，不能触达旧内容")
            authorization = self._delivery_authorization(db, prepared)
            if not authorization["can_send"]:
                raise WorkflowConflict("拒绝触达：" + authorization["review_status"])
            existing = db.execute("SELECT record_json FROM deliveries WHERE case_id=? AND content_fingerprint=?",
                                  (prepared["case_id"], prepared["content_fingerprint"])).fetchone()
            if existing:
                if row["status"] not in {"DELIVERED_MOCK", "FEEDBACK_RECEIVED", "FOLLOWUP_PENDING", "RESOLVED"}:
                    self._status(db, prepared["case_id"], self._delivered_case_status(db, prepared["case_id"]))
                return DeliveryRecord.model_validate_json(existing[0])
            mode = DeliveryMode.AUTO if prepared["gate"].decision == GateDecision.AUTO_DELIVER else DeliveryMode.HUMAN_APPROVED
            record = DeliveryRecord(delivery_id=str(uuid4()), case_id=prepared["case_id"], delivery_mode=mode,
                recommendation_included=bool(prepared["content"]["recommendation"]), risk_gate_result=prepared["gate"],
                content_fingerprint=prepared["content_fingerprint"], created_at=datetime.now(timezone.utc).isoformat(),
                content=prepared["content"])
            db.execute(
                "INSERT INTO deliveries(delivery_id, case_id, content_fingerprint, record_json, created_at, "
                "channel, status, sent_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (record.delivery_id, record.case_id, record.content_fingerprint, encode(record), record.created_at,
                 "MOCK", record.status, record.created_at),
            )
            has_open_tasks = db.execute("SELECT 1 FROM follow_up_tasks WHERE case_id=? AND status='OPEN'", (record.case_id,)).fetchone()
            self._status(db, record.case_id, WorkflowStatus.FOLLOWUP_PENDING if has_open_tasks else WorkflowStatus.DELIVERED_MOCK)
            return record

    def list_deliveries(self, case_id):
        with self._connect() as db:
            self._row(db, case_id)
            return [json.loads(row[0]) for row in db.execute("SELECT record_json FROM deliveries WHERE case_id=? ORDER BY rowid DESC", (case_id,))]

    def _delivery_row(self, db, delivery_id):
        row = db.execute("SELECT record_json FROM deliveries WHERE delivery_id=?", (delivery_id,)).fetchone()
        if row is None:
            raise WorkflowItemNotFound("delivery 不存在")
        return json.loads(row[0])

    def get_delivery(self, delivery_id):
        with self._connect() as db:
            return self._delivery_row(db, delivery_id)

    def save_feedback(self, delivery_id, request, customer_user_id=None):
        from .feedback_models import FeedbackRecord, FeedbackTarget, FeedbackCategory, FollowUpTask
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            delivery = self._delivery_row(db, delivery_id)
            if customer_user_id is not None and db.execute(
                "SELECT 1 FROM feedback WHERE delivery_id=? AND customer_user_id=?",
                (delivery_id, customer_user_id),
            ).fetchone():
                raise WorkflowConflict("本次关怀结果已提交过反馈")
            if request.target_type == FeedbackTarget.RECOMMENDATION and not delivery["recommendation_included"]:
                raise WorkflowConflict("本次触达未包含推荐，不能提交推荐内容评分")
            now = datetime.now(timezone.utc).isoformat()
            feedback = FeedbackRecord(**request.model_dump(), feedback_id=str(uuid4()), delivery_id=delivery_id,
                                      case_id=delivery["case_id"], created_at=now)
            db.execute(
                "INSERT INTO feedback(feedback_id, delivery_id, case_id, record_json, created_at, "
                "customer_user_id, target_type, rating, helpful, category, comment, request_contact) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (feedback.feedback_id, delivery_id, feedback.case_id, encode(feedback), now,
                 customer_user_id,
                 feedback.target_type.value, feedback.rating, int(feedback.helpful),
                 feedback.category.value, feedback.comment,
                 int(feedback.consent_to_contact)),
            )
            needs_followup = request.rating <= 3 or not request.helpful or request.category != FeedbackCategory.CLEAR
            task = None
            if needs_followup:
                task = FollowUpTask(task_id=str(uuid4()), case_id=feedback.case_id, feedback_id=feedback.feedback_id,
                                    category=feedback.category, note=f"rating={feedback.rating}, helpful={feedback.helpful}, category={feedback.category.value}", created_at=now)
                db.execute(
                    "INSERT INTO follow_up_tasks(task_id, case_id, feedback_id, status, record_json, "
                    "created_at, source_type, category, priority, note) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'DELIVERY_FEEDBACK', ?, 'MEDIUM', ?)",
                    (task.task_id, task.case_id, task.feedback_id, task.status.value, encode(task), now,
                     task.category.value, task.note),
                )
            if self._row(db, feedback.case_id)["status"] in {"DELIVERED_MOCK", "FEEDBACK_RECEIVED", "FOLLOWUP_PENDING", "RESOLVED"}:
                self._status(db, feedback.case_id, WorkflowStatus.FEEDBACK_RECEIVED)
                if db.execute("SELECT 1 FROM follow_up_tasks WHERE case_id=? AND status='OPEN'", (feedback.case_id,)).fetchone():
                    self._status(db, feedback.case_id, WorkflowStatus.FOLLOWUP_PENDING)
            return {"feedback": feedback.model_dump(mode="json"), "follow_up": task.model_dump(mode="json") if task else None}

    def list_feedback(self, case_id):
        with self._connect() as db:
            self._row(db, case_id)
            return [json.loads(row[0]) for row in db.execute("SELECT record_json FROM feedback WHERE case_id=? ORDER BY rowid DESC", (case_id,))]

    def list_follow_ups(self, case_id=None):
        with self._connect() as db:
            if case_id is not None:
                self._row(db, case_id)
                rows = db.execute("SELECT record_json FROM follow_up_tasks WHERE case_id=? ORDER BY rowid DESC", (case_id,))
            else:
                rows = db.execute("SELECT record_json FROM follow_up_tasks ORDER BY rowid DESC")
            return [json.loads(row[0]) for row in rows]

    def resolve_follow_up(self, task_id, resolution):
        from .feedback_models import FollowUpTask, FollowUpStatus
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record_json FROM follow_up_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise WorkflowItemNotFound("follow-up task 不存在")
            task = FollowUpTask.model_validate_json(row[0])
            if task.status == FollowUpStatus.RESOLVED:
                if task.resolution != resolution:
                    raise WorkflowConflict("该待办已解决，不能覆盖已保存的resolution")
                return task
            task.status = FollowUpStatus.RESOLVED
            task.resolution = resolution
            task.resolved_at = datetime.now(timezone.utc).isoformat()
            db.execute(
                "UPDATE follow_up_tasks SET status='RESOLVED', record_json=?, resolved_at=?, resolution=? "
                "WHERE task_id=?",
                (encode(task), task.resolved_at, task.resolution, task_id),
            )
            if self._row(db, task.case_id)["status"] in {"DELIVERED_MOCK", "FEEDBACK_RECEIVED", "FOLLOWUP_PENDING", "RESOLVED"}:
                open_task = db.execute(
                    "SELECT 1 FROM follow_up_tasks WHERE case_id=? AND status IN ('OPEN','PROCESSING')",
                    (task.case_id,),
                ).fetchone()
                self._status(db, task.case_id, WorkflowStatus.FOLLOWUP_PENDING if open_task else WorkflowStatus.RESOLVED)
            return task

    # Role workspaces intentionally expose narrow, permission-safe views.
    # Customer/service APIs stay delivery-only. Medical reviewers may receive these
    # fields only after case scope, consent and deidentification checks have passed.
    def list_medical_cases(self, user_id, limit=100):
        sql = """
            SELECT c.case_id, c.claim_event_id, c.claim_status, c.care_status, c.status,
                   c.consent_status, c.deidentification_status, c.created_at, c.updated_at,
                   customer.display_name AS customer_display_name,
                   CASE WHEN cr.ocr_result_json IS NOT NULL THEN 1 ELSE 0 END AS has_ocr_result,
                   CASE WHEN cr.structured_result_json IS NOT NULL THEN 1 ELSE 0 END AS has_structured_result,
                   CASE WHEN cr.pain_result_json IS NOT NULL THEN 1 ELSE 0 END AS has_pain_result,
                   CASE WHEN cr.care_result_json IS NOT NULL THEN 1 ELSE 0 END AS has_care_result,
                   (SELECT status FROM recommendation_reports rr WHERE rr.case_id=c.case_id
                    ORDER BY rr.report_version DESC LIMIT 1) AS recommendation_status,
                   (SELECT COUNT(*) FROM deliveries d WHERE d.case_id=c.case_id) AS delivery_count
            FROM cases c
            JOIN case_results cr ON cr.case_id=c.case_id
            JOIN case_participants cp ON cp.case_id=c.case_id
            LEFT JOIN users customer ON customer.user_id=c.customer_user_id
            WHERE cp.user_id=? AND cp.case_role='MEDICAL_REVIEWER' AND cp.status='ACTIVE'
            ORDER BY c.updated_at DESC LIMIT ?
        """
        with self._connect() as db:
            rows = [dict(row) for row in db.execute(
                sql, (user_id, max(1, min(int(limit), 200))),
            )]
            for row in rows:
                row["customer_display_name"] = self._mask_customer_label(row["customer_display_name"])
                for field in ("has_ocr_result", "has_structured_result", "has_pain_result", "has_care_result"):
                    row[field] = bool(row[field])
            return rows

    def medical_dashboard(self, user_id):
        cases = self.list_medical_cases(user_id, limit=200)
        return {
            "total": len(cases),
            "awaiting_structure": sum(not item["has_structured_result"] for item in cases),
            "awaiting_pain": sum(item["has_structured_result"] and not item["has_pain_result"] for item in cases),
            "awaiting_care": sum(item["has_pain_result"] and not item["has_care_result"] for item in cases),
            "awaiting_delivery_review": sum(
                item["has_care_result"] and item["status"] in {"CARE_READY", "REVIEW_REQUIRED", "BLOCKED"}
                for item in cases
            ),
            "recent_cases": cases[:8],
        }

    def get_medical_case_detail(self, user_id, case_id):
        if not self.user_can_access_case(user_id, case_id, "MEDICAL_CASE_VIEW"):
            raise CaseNotFound(case_id)
        with self._connect() as db:
            row = db.execute(
                "SELECT c.case_id, c.claim_event_id, c.claim_status, c.care_status, c.status, "
                "c.consent_status, c.deidentification_status, c.created_at, c.updated_at, "
                "customer.display_name AS customer_display_name, cr.ocr_result_json, "
                "cr.structured_result_json, cr.pain_result_json, cr.care_result_json "
                "FROM cases c JOIN case_results cr ON cr.case_id=c.case_id "
                "LEFT JOIN users customer ON customer.user_id=c.customer_user_id WHERE c.case_id=?",
                (case_id,),
            ).fetchone()
            if row is None:
                raise CaseNotFound(case_id)
            result = {key: row[key] for key in (
                "case_id", "claim_event_id", "claim_status", "care_status", "status",
                "consent_status", "deidentification_status", "created_at", "updated_at",
                "customer_display_name",
            )}
            result["customer_display_name"] = self._mask_customer_label(result["customer_display_name"])
            allowed = row["consent_status"] == "authorized" and row["deidentification_status"] == "confirmed"
            result["compliance_ready"] = allowed
            for name in ("ocr", "structured", "pain", "care"):
                value = row[f"{name}_result_json"]
                result[f"{name}_result"] = json.loads(value) if allowed and value else None
            result["reviews"] = [json.loads(review[0]) for review in db.execute(
                "SELECT record_json FROM reviews WHERE case_id=? ORDER BY rowid DESC", (case_id,),
            )]
            result["deliveries"] = [json.loads(delivery[0]) for delivery in db.execute(
                "SELECT record_json FROM deliveries WHERE case_id=? ORDER BY rowid DESC", (case_id,),
            )]
            return result

    def list_customer_cases(self, user_id, limit=100):
        sql = """
            SELECT c.case_id, c.claim_event_id, c.claim_status, c.care_status,
                   c.created_at, c.updated_at,
                   (SELECT d.delivery_id FROM deliveries d WHERE d.case_id=c.case_id
                    ORDER BY COALESCE(d.sent_at, d.created_at) DESC LIMIT 1) AS delivery_id,
                   (SELECT COALESCE(d.sent_at, d.created_at) FROM deliveries d WHERE d.case_id=c.case_id
                    ORDER BY COALESCE(d.sent_at, d.created_at) DESC LIMIT 1) AS delivered_at,
                   EXISTS(SELECT 1 FROM feedback f WHERE f.customer_user_id=?
                          AND f.delivery_id=(SELECT latest.delivery_id FROM deliveries latest
                          WHERE latest.case_id=c.case_id
                          ORDER BY COALESCE(latest.sent_at, latest.created_at) DESC LIMIT 1))
                          AS has_feedback
            FROM cases c
            JOIN case_participants cp ON cp.case_id=c.case_id
            WHERE cp.user_id=? AND cp.case_role='CUSTOMER' AND cp.status='ACTIVE'
            ORDER BY c.updated_at DESC LIMIT ?
        """
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                sql, (user_id, user_id, max(1, min(int(limit), 200))),
            )]

    def customer_dashboard(self, user_id):
        cases = self.list_customer_cases(user_id, limit=200)
        return {
            "total": len(cases),
            "results_available": sum(bool(item["delivery_id"]) for item in cases),
            "awaiting_result": sum(not item["delivery_id"] for item in cases),
            "feedback_submitted": sum(bool(item["has_feedback"]) for item in cases),
            "recent_cases": cases[:6],
        }

    @staticmethod
    def _customer_delivery_payload(delivery):
        content = delivery.get("content") or {}
        return {
            "delivery_id": delivery.get("delivery_id"),
            "created_at": delivery.get("created_at"),
            "status": delivery.get("status"),
            "recommendation_included": bool(delivery.get("recommendation_included")),
            "content": {
                "care_guide": content.get("care_guide"),
                "recommendation": content.get("recommendation") or [],
                "recommendation_unavailable_reason": content.get("recommendation_unavailable_reason"),
            },
        }

    def get_customer_case_detail(self, user_id, case_id):
        if not self.user_can_access_case(user_id, case_id, "OWN_CASE_VIEW"):
            raise CaseNotFound(case_id)
        with self._connect() as db:
            row = db.execute(
                "SELECT case_id, claim_event_id, claim_status, care_status, created_at, updated_at "
                "FROM cases WHERE case_id=?", (case_id,),
            ).fetchone()
            delivery_row = db.execute(
                "SELECT record_json FROM deliveries WHERE case_id=? "
                "ORDER BY COALESCE(sent_at, created_at) DESC LIMIT 1", (case_id,),
            ).fetchone()
            result = dict(row)
            result["delivery"] = (
                self._customer_delivery_payload(json.loads(delivery_row[0])) if delivery_row else None
            )
            if result["delivery"]:
                db.execute(
                    "UPDATE deliveries SET read_at=COALESCE(read_at, ?) WHERE delivery_id=?",
                    (datetime.now(timezone.utc).isoformat(), result["delivery"]["delivery_id"]),
                )
            result["feedback_submitted"] = bool(result["delivery"] and db.execute(
                "SELECT 1 FROM feedback WHERE delivery_id=? AND customer_user_id=?",
                (result["delivery"]["delivery_id"], user_id),
            ).fetchone())
            return result

    def list_customer_feedback(self, user_id):
        with self._connect() as db:
            rows = db.execute(
                "SELECT f.record_json FROM feedback f "
                "JOIN case_participants cp ON cp.case_id=f.case_id "
                "WHERE f.customer_user_id=? AND cp.user_id=? AND cp.case_role='CUSTOMER' "
                "AND cp.status='ACTIVE' ORDER BY f.created_at DESC",
                (user_id, user_id),
            )
            return [json.loads(row[0]) for row in rows]

    def save_customer_feedback(self, user_id, delivery_id, request):
        try:
            delivery = self.get_delivery(delivery_id)
        except WorkflowItemNotFound as exc:
            raise WorkflowItemNotFound("关怀结果不存在或当前账号无权访问") from exc
        if not self.user_can_access_case(user_id, delivery["case_id"], "FEEDBACK_SUBMIT"):
            raise WorkflowItemNotFound("关怀结果不存在或当前账号无权访问")
        return self.save_feedback(delivery_id, request, customer_user_id=user_id)

    def list_service_cases(self, user_id, limit=100):
        sql = """
            SELECT c.case_id, c.claim_event_id, c.claim_status, c.care_status,
                   c.created_at, c.updated_at, customer.display_name AS customer_display_name,
                   (SELECT COUNT(*) FROM feedback f WHERE f.case_id=c.case_id) AS feedback_count,
                   (SELECT COUNT(*) FROM follow_up_tasks t WHERE t.case_id=c.case_id
                    AND t.status IN ('OPEN','PROCESSING')) AS active_follow_up_count,
                   (SELECT COUNT(*) FROM case_service_links l WHERE l.case_id=c.case_id
                    AND l.reviewed=0) AS pending_consultation_count
            FROM cases c
            JOIN case_participants cp ON cp.case_id=c.case_id
            LEFT JOIN users customer ON customer.user_id=c.customer_user_id
            WHERE cp.user_id=? AND cp.case_role='SERVICE_AGENT' AND cp.status='ACTIVE'
            ORDER BY c.updated_at DESC LIMIT ?
        """
        with self._connect() as db:
            rows = [dict(row) for row in db.execute(
                sql, (user_id, max(1, min(int(limit), 200))),
            )]
            for row in rows:
                row["customer_display_name"] = self._mask_customer_label(row["customer_display_name"])
            return rows

    def service_dashboard(self, user_id):
        cases = self.list_service_cases(user_id, limit=200)
        follow_ups = self.list_service_follow_ups(user_id)
        consultations = self.list_service_consultations(user_id)
        return {
            "total_cases": len(cases),
            "pending_consultations": sum(item["review_status"] == "PENDING" for item in consultations),
            "active_follow_ups": sum(item["status"] in {"OPEN", "PROCESSING"} for item in follow_ups),
            "urgent_follow_ups": sum(
                item["status"] in {"OPEN", "PROCESSING"} and item["priority"] == "urgent"
                for item in follow_ups
            ),
            "recent_cases": cases[:6],
        }

    def create_service_consultation(self, user_id, *, case_id, channel, deidentified_text,
                                    category, priority):
        if not self.user_can_access_case(user_id, case_id, "SERVICE_SESSION_CREATE"):
            raise CaseNotFound(case_id)
        now = datetime.now(timezone.utc).isoformat()
        session_id_hash = hashlib.sha256(f"{uuid4()}:{now}".encode()).hexdigest()
        analysis_id = str(uuid4())
        link_id = str(uuid4())
        severity_score = {"low": 20, "medium": 45, "high": 75, "urgent": 95}[priority]
        action = {
            "claim_progress": "核查理赔进度并回访客户",
            "claim_material": "核对缺失材料并提供补交说明",
            "coverage_rule": "转交具备权限的理赔人员解释保障规则",
            "human_service_request": "安排人工客服尽快回访",
        }.get(category, "由客服核实诉求并持续跟进")
        with self._connect() as db:
            customer = db.execute(
                "SELECT customer_user_id FROM cases WHERE case_id=?", (case_id,),
            ).fetchone()
            db.execute(
                "INSERT INTO service_sessions(session_id_hash, customer_user_id, channel, session_time, "
                "deidentified_text, text_fingerprint, data_source, consent_status, "
                "deidentification_status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id_hash, customer[0] if customer else None, channel, now, deidentified_text,
                 hashlib.sha256(deidentified_text.encode()).hexdigest(), "service_portal",
                 "authorized", "confirmed", now, now),
            )
            db.execute(
                "INSERT INTO service_analysis_results(analysis_id, session_id_hash, primary_intent, "
                "secondary_intents_json, has_friction, friction_types_json, severity, severity_score, "
                "confidence, evidence_json, recommended_action, engine_version, review_status, created_at) "
                "VALUES (?, ?, ?, '[]', ?, ?, ?, ?, 1, '[]', ?, 'service-routing-v1', 'PENDING', ?)",
                (analysis_id, session_id_hash, category, int(category != "clear"),
                 encode([category] if category != "clear" else []), priority, severity_score, action, now),
            )
            db.execute(
                "INSERT INTO case_service_links(link_id, case_id, session_id_hash, link_method, confidence, "
                "reviewed, created_at) VALUES (?, ?, ?, 'service_manual', 1, 0, ?)",
                (link_id, case_id, session_id_hash, now),
            )
        return self.get_service_consultation(user_id, analysis_id)

    def get_service_consultation(self, user_id, analysis_id):
        items = self.list_service_consultations(user_id, analysis_id=analysis_id)
        if not items:
            raise WorkflowItemNotFound("咨询记录不存在或当前账号无权访问")
        return items[0]

    def list_service_consultations(self, user_id, analysis_id=None):
        clauses = ["cp.user_id=?", "cp.case_role='SERVICE_AGENT'", "cp.status='ACTIVE'"]
        params = [user_id]
        if analysis_id:
            clauses.append("a.analysis_id=?")
            params.append(analysis_id)
        sql = f"""
            SELECT a.analysis_id, l.case_id, c.claim_event_id, s.channel, s.session_time,
                   s.deidentified_text, a.primary_intent, a.severity, a.severity_score,
                   a.recommended_action, a.review_status, a.created_at,
                   customer.display_name AS customer_display_name
            FROM service_analysis_results a
            JOIN service_sessions s ON s.session_id_hash=a.session_id_hash
            JOIN case_service_links l ON l.session_id_hash=s.session_id_hash
            JOIN cases c ON c.case_id=l.case_id
            JOIN case_participants cp ON cp.case_id=c.case_id
            LEFT JOIN users customer ON customer.user_id=c.customer_user_id
            WHERE {' AND '.join(clauses)} ORDER BY a.created_at DESC
        """
        with self._connect() as db:
            rows = [dict(row) for row in db.execute(sql, params)]
            for row in rows:
                row["customer_display_name"] = self._mask_customer_label(row["customer_display_name"])
            return rows

    def route_service_consultation(self, user_id, analysis_id):
        item = self.get_service_consultation(user_id, analysis_id)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute(
                "UPDATE service_analysis_results SET review_status='ROUTED', reviewer_user_id=? "
                "WHERE analysis_id=?", (user_id, analysis_id),
            )
            db.execute(
                "UPDATE case_service_links SET reviewed=1, reviewer_user_id=? "
                "WHERE session_id_hash=(SELECT session_id_hash FROM service_analysis_results WHERE analysis_id=?)",
                (user_id, analysis_id),
            )
            db.execute("UPDATE cases SET updated_at=? WHERE case_id=?", (now, item["case_id"]))
        return self.get_service_consultation(user_id, analysis_id)

    def list_service_follow_ups(self, user_id, status=None):
        clauses = ["cp.user_id=?", "cp.case_role='SERVICE_AGENT'", "cp.status='ACTIVE'"]
        params = [user_id]
        if status:
            clauses.append("t.status=?")
            params.append(status)
        sql = f"""
            SELECT t.*, c.claim_event_id, assignee.display_name AS assignee_name,
                   f.comment AS feedback_comment, f.rating AS feedback_rating,
                   f.helpful AS feedback_helpful, f.request_contact
            FROM follow_up_tasks t
            JOIN cases c ON c.case_id=t.case_id
            JOIN case_participants cp ON cp.case_id=c.case_id
            LEFT JOIN users assignee ON assignee.user_id=t.assignee_user_id
            LEFT JOIN feedback f ON f.feedback_id=t.feedback_id
            WHERE {' AND '.join(clauses)} ORDER BY
              CASE lower(t.priority) WHEN 'urgent' THEN 0 WHEN 'high' THEN 1
                   WHEN 'medium' THEN 2 ELSE 3 END, t.created_at DESC
        """
        with self._connect() as db:
            results = []
            for row in db.execute(sql, params):
                item = json.loads(row["record_json"])
                item.update({
                    "status": row["status"], "priority": str(row["priority"]).lower(),
                    "assignee": row["assignee_name"], "claim_event_id": row["claim_event_id"],
                    "resolution": row["resolution"], "resolved_at": row["resolved_at"],
                    "feedback_comment": row["feedback_comment"],
                    "feedback_rating": row["feedback_rating"],
                    "feedback_helpful": bool(row["feedback_helpful"])
                    if row["feedback_helpful"] is not None else None,
                    "request_contact": bool(row["request_contact"]),
                })
                results.append(item)
            return results

    def claim_service_follow_up(self, user_id, task_id):
        from .feedback_models import FollowUpTask, FollowUpStatus
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT t.*, u.display_name FROM follow_up_tasks t "
                "JOIN case_participants cp ON cp.case_id=t.case_id "
                "JOIN users u ON u.user_id=? "
                "WHERE t.task_id=? AND cp.user_id=? AND cp.case_role='SERVICE_AGENT' "
                "AND cp.status='ACTIVE'", (user_id, task_id, user_id),
            ).fetchone()
            if row is None:
                raise WorkflowItemNotFound("跟进任务不存在或当前账号无权访问")
            if row["status"] in {"RESOLVED", "CLOSED"}:
                raise WorkflowConflict("已结束的跟进任务不能再次领取")
            if row["assignee_user_id"] not in (None, user_id):
                raise WorkflowConflict("该跟进任务已由其他客服领取")
            task = FollowUpTask.model_validate_json(row["record_json"])
            task.status = FollowUpStatus.PROCESSING
            task.assignee = row["display_name"]
            db.execute(
                "UPDATE follow_up_tasks SET status='PROCESSING', assignee_user_id=?, record_json=? "
                "WHERE task_id=?", (user_id, encode(task), task_id),
            )
        return next(
            item for item in self.list_service_follow_ups(user_id, status="PROCESSING")
            if item["task_id"] == task_id
        )

    def resolve_service_follow_up(self, user_id, task_id, resolution):
        with self._connect() as db:
            row = db.execute(
                "SELECT t.case_id, t.assignee_user_id FROM follow_up_tasks t "
                "JOIN case_participants cp ON cp.case_id=t.case_id "
                "WHERE t.task_id=? AND cp.user_id=? AND cp.case_role='SERVICE_AGENT' "
                "AND cp.status='ACTIVE'", (task_id, user_id),
            ).fetchone()
        if row is None:
            raise WorkflowItemNotFound("跟进任务不存在或当前账号无权访问")
        if row["assignee_user_id"] != user_id:
            raise WorkflowConflict("请先领取该跟进任务再处理")
        return self.resolve_follow_up(task_id, resolution)
