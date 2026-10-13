"""Local SQLite case storage, without report versions or event sourcing."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import re
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
    # Mobile previews remain process-local and expire automatically.
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

    def create_case(
        self,
        request: CreateCaseRequest,
        owner_user_id: str | None = None,
        customer_user_id: str | None = None,
        *,
        claim_status: str = "PENDING_ACCEPTANCE",
        route_to_claim: bool = False,
    ) -> WorkflowCase:
        if claim_status not in {"PENDING_ACCEPTANCE", "ACCEPTED", "COMPLETED"}:
            raise WorkflowConflict("不支持的理赔状态")
        now = datetime.now(timezone.utc).isoformat()
        case = WorkflowCase(**request.model_dump(), case_id=str(uuid4()),
                            status=WorkflowStatus.RECEIVED, created_at=now, updated_at=now)
        with self._connect() as db:
            if customer_user_id is not None:
                customer = db.execute(
                    "SELECT 1 FROM users u JOIN roles r ON r.role_id=u.role_id "
                    "WHERE u.user_id=? AND u.status='ACTIVE' AND r.role_code='CUSTOMER'",
                    (customer_user_id,),
                ).fetchone()
                if customer is None:
                    raise WorkflowConflict("客户账号不存在或不可用")
            if route_to_claim and owner_user_id is None:
                routed_owner = db.execute(
                    "SELECT u.user_id FROM users u JOIN roles r ON r.role_id=u.role_id "
                    "LEFT JOIN case_participants cp ON cp.user_id=u.user_id "
                    "AND cp.case_role='CLAIM_OWNER' AND cp.status='ACTIVE' "
                    "WHERE u.status='ACTIVE' AND r.role_code='CLAIM' "
                    "GROUP BY u.user_id ORDER BY COUNT(cp.case_id), u.user_id LIMIT 1"
                ).fetchone()
                if routed_owner is None:
                    raise WorkflowConflict("当前没有可受理案件的理赔人员")
                owner_user_id = routed_owner[0]
            if owner_user_id is not None:
                owner = db.execute(
                    "SELECT 1 FROM users u JOIN roles r ON r.role_id=u.role_id "
                    "WHERE u.user_id=? AND u.status='ACTIVE' AND r.role_code='CLAIM'",
                    (owner_user_id,),
                ).fetchone()
                if owner is None:
                    raise WorkflowConflict("理赔负责人账号不存在或不可用")
            db.execute(
                "INSERT INTO cases(case_id, claim_event_id, status, care_status, claim_status, "
                "data_source, consent_status, deidentification_status, customer_user_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (case.case_id, case.claim_event_id, case.status.value, case.status.value, claim_status,
                  case.data_source, case.consent_status, case.deidentification_status,
                  customer_user_id, now, now),
            )
            db.execute("INSERT INTO case_results(case_id, updated_at) VALUES (?, ?)",
                       (case.case_id, now))
            if owner_user_id is not None:
                db.execute(
                    "INSERT INTO case_participants(case_id, user_id, case_role, status, assigned_at) "
                    "VALUES (?, ?, 'CLAIM_OWNER', 'ACTIVE', ?)",
                    (case.case_id, owner_user_id, now),
                )
            if customer_user_id is not None:
                db.execute(
                    "INSERT INTO case_participants(case_id, user_id, case_role, status, assigned_at) "
                    "VALUES (?, ?, 'CUSTOMER', 'ACTIVE', ?)",
                    (case.case_id, customer_user_id, now),
                )
        return case

    def create_customer_case(self, request: CreateCaseRequest, customer_user_id: str) -> WorkflowCase:
        """Create the default self-service claim and route it to a claim intake owner."""
        return self.create_case(
            request,
            customer_user_id=customer_user_id,
            claim_status="PENDING_ACCEPTANCE",
            route_to_claim=True,
        )

    def create_claim_case_for_customer(
        self, request: CreateCaseRequest, owner_user_id: str, customer_username: str,
    ) -> WorkflowCase:
        customer = self.get_user_by_username(customer_username)
        if customer is None or customer["status"] != "ACTIVE" or customer["role_code"] != "CUSTOMER":
            raise WorkflowItemNotFound("客户账号不存在或不可用")
        return self.create_case(
            request,
            owner_user_id=owner_user_id,
            customer_user_id=customer["user_id"],
            claim_status="ACCEPTED",
        )

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

    def search_active_users_by_role(self, role_code, search, limit=20):
        term = f"%{str(search).strip()}%"
        with self._connect() as db:
            rows = db.execute(
                "SELECT u.user_id, u.username, u.display_name, u.phone FROM users u "
                "JOIN roles r ON r.role_id=u.role_id "
                "WHERE r.role_code=? AND u.status='ACTIVE' "
                "AND (u.username LIKE ? OR u.display_name LIKE ? OR u.phone LIKE ?) "
                "ORDER BY u.username LIMIT ?",
                (role_code, term, term, term, max(1, min(int(limit), 50))),
            )
            results = []
            for row in rows:
                phone = str(row["phone"] or "")
                results.append({
                    "user_id": row["user_id"],
                    "username": row["username"],
                    "display_name": self._mask_customer_label(row["display_name"]),
                    "phone_last4": phone[-4:] if phone else None,
                })
            return results

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
                "(SELECT customer.username FROM users customer WHERE customer.user_id=c.customer_user_id) "
                "AS customer_username, "
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
                "created_at", "updated_at", "customer_display_name", "customer_username",
                "claim_owner_name",
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
    def _mask_phone_last4(value):
        digits = re.sub(r"\D", "", str(value or ""))
        return f"****{digits[-4:]}" if len(digits) >= 4 else None

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
            if item["claim_status"] == "PENDING_ACCEPTANCE":
                reasons.append("客户申请待受理")
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
        for task in self.list_case_tasks(user_id, "CLAIM"):
            tasks.append({
                "task_id": task["task_id"],
                "case_id": task["case_id"],
                "claim_event_id": task["claim_event_id"],
                "updated_at": task["created_at"],
                "reasons": [f"客服转交：{task['note'] or task['category']}"],
                "source_type": task["source_type"],
                "category": task["category"],
                "priority": task["priority"],
            })
        return tasks

    def list_case_tasks(self, user_id, target_role):
        if target_role not in {"CLAIM", "MEDICAL"}:
            raise WorkflowConflict("当前角色没有跨角色待办列表")
        participant_role = "CLAIM_OWNER" if target_role == "CLAIM" else "MEDICAL_REVIEWER"
        sql = """
            SELECT t.*, c.claim_event_id, s.deidentified_text AS source_summary
            FROM case_tasks t
            JOIN cases c ON c.case_id=t.case_id
            JOIN case_participants cp ON cp.case_id=t.case_id
                AND cp.user_id=t.assignee_user_id AND cp.case_role=? AND cp.status='ACTIVE'
            JOIN users u ON u.user_id=t.assignee_user_id AND u.status='ACTIVE'
            JOIN roles r ON r.role_id=u.role_id AND r.role_code=?
            LEFT JOIN service_analysis_results a
                ON t.source_type='SERVICE_CONSULTATION' AND a.analysis_id=t.source_id
            LEFT JOIN service_sessions s ON s.session_id_hash=a.session_id_hash
            WHERE t.assignee_user_id=? AND t.target_role=?
            ORDER BY CASE t.priority WHEN 'URGENT' THEN 0 WHEN 'HIGH' THEN 1
                          WHEN 'MEDIUM' THEN 2 ELSE 3 END,
                     t.created_at DESC
        """
        with self._connect() as db:
            items = [dict(row) for row in db.execute(
                sql, (participant_role, target_role, user_id, target_role),
            )]
        for item in items:
            item["schema_version"] = "case-task-v1"
        return items

    def trigger_care(self, user_id, case_id):
        if not self.user_can_access_case(user_id, case_id, "CARE_TRIGGER"):
            raise CaseNotFound(case_id)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            row = db.execute(
                "SELECT c.claim_status, c.consent_status, c.deidentification_status, c.care_status, "
                "cr.structured_result_json FROM cases c JOIN case_results cr USING(case_id) "
                "WHERE c.case_id=?",
                (case_id,),
            ).fetchone()
            if row is None:
                raise CaseNotFound(case_id)
            blockers = []
            if row["claim_status"] == "PENDING_ACCEPTANCE":
                blockers.append("理赔案件尚未受理")
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
            reviewer = None
            assigned = db.execute(
                "SELECT 1 FROM case_participants cp JOIN users u ON u.user_id=cp.user_id "
                "JOIN roles r ON r.role_id=u.role_id WHERE cp.case_id=? AND cp.case_role='MEDICAL_REVIEWER' "
                "AND cp.status='ACTIVE' AND u.status='ACTIVE' AND r.role_code='MEDICAL'",
                (case_id,),
            ).fetchone()
            if assigned is None:
                reviewer = db.execute(
                    "SELECT u.user_id FROM users u JOIN roles r ON r.role_id=u.role_id "
                    "LEFT JOIN case_participants cp ON cp.user_id=u.user_id "
                    "AND cp.case_role='MEDICAL_REVIEWER' AND cp.status='ACTIVE' "
                    "WHERE u.status='ACTIVE' AND r.role_code='MEDICAL' "
                    "GROUP BY u.user_id ORDER BY COUNT(cp.case_id), u.user_id LIMIT 1"
                ).fetchone()
                if reviewer is not None:
                    db.execute(
                        "INSERT INTO case_participants(case_id, user_id, case_role, status, assigned_at) "
                        "VALUES (?, ?, 'MEDICAL_REVIEWER', 'ACTIVE', ?)",
                        (case_id, reviewer[0], now),
                    )
            return {"case_id": case_id, "care_status": care_status,
                    "medical_reviewer_assigned": assigned is not None or reviewer is not None,
                    "message": "案件已移交医护关怀，等待医疗处理或复核"}

    @staticmethod
    def _medical_review_status(decision, care_status=None):
        if care_status in {"APPROVED", "PUBLISHED", "DELIVERED_MOCK", "FEEDBACK_RECEIVED", "FOLLOWUP_PENDING", "RESOLVED"}:
            return "APPROVED"
        if care_status == "REJECTED":
            return "RETURNED"
        if care_status == "REVIEW_REQUIRED" and decision == "NEEDS_CONFIRMATION":
            return "NEEDS_CONFIRMATION"
        return "PENDING"

    @staticmethod
    def _primary_diagnosis(structured):
        diagnoses = (structured or {}).get("diagnoses") or []
        chosen = next((item for item in diagnoses if item.get("diagnosis_type") == "primary"), None)
        chosen = chosen or (diagnoses[0] if diagnoses else None)
        if not chosen:
            return None
        name = chosen.get("name") or {}
        return name.get("normalized_value") or name.get("value")

    @staticmethod
    def _structured_fingerprint(structured):
        if not structured:
            return None
        return hashlib.sha256(encode(structured).encode("utf-8")).hexdigest()

    @staticmethod
    def _field_at_path(structured, field_path):
        current = structured
        for name, index in re.findall(r"([^.\[\]]+)(?:\[(\d+)\])?", field_path):
            if not isinstance(current, dict) or name not in current:
                raise WorkflowItemNotFound("结构化字段不存在或已变化")
            current = current[name]
            if index:
                if not isinstance(current, list) or int(index) >= len(current):
                    raise WorkflowItemNotFound("结构化字段不存在或已变化")
                current = current[int(index)]
        if not isinstance(current, dict) or "status" not in current or "value" not in current:
            raise WorkflowConflict("该路径不是可复核的结构化字段")
        return current

    @staticmethod
    def _field_label(field_path):
        names = {
            "admission_date": "入院日期", "discharge_date": "出院日期",
            "length_of_stay_days": "住院天数", "department": "住院科室",
            "name": "名称", "code": "编码", "performed_date": "操作日期",
            "body_site": "部位", "dose": "剂量", "frequency": "频次",
            "route": "给药途径", "duration": "疗程", "conclusion": "检查结论",
            "total_amount": "总费用", "policy_scope_amount": "政策范围内费用",
            "outside_policy_amount": "政策范围外费用", "amount": "金额",
            "category": "类别", "value": "结果值", "unit": "单位",
            "reference_range": "参考范围",
        }
        groups = {"diagnoses": "诊断", "procedures": "手术/操作", "medications": "用药",
                  "examinations": "检查", "hospitalization": "住院信息", "costs": "费用"}
        first = field_path.split(".", 1)[0].split("[", 1)[0]
        last = field_path.rsplit(".", 1)[-1].split("[", 1)[0]
        index = re.search(r"\[(\d+)\]", field_path)
        prefix = groups.get(first, first)
        return f"{prefix}{int(index.group(1)) + 1 if index else ''} · {names.get(last, last)}"

    @classmethod
    def _field_review_candidates(cls, structured):
        if not structured:
            return []
        flags = structured.get("quality_flags") or []

        def flag_matches(path, flag_path):
            if not flag_path:
                return False
            pattern = re.escape(flag_path).replace(r"\[\]", r"\[\d+\]")
            return re.fullmatch(pattern, path) is not None

        candidates = []

        def walk(value, path=""):
            if isinstance(value, dict):
                if "status" in value and "value" in value:
                    if value.get("value") is None or value.get("status") in {
                        "not_present_in_document", "not_applicable"
                    }:
                        return
                    relevant = [flag for flag in flags if flag_matches(path, flag.get("field_path"))]
                    confidence = value.get("confidence")
                    reasons = []
                    if value.get("status") in {"review_required", "conflict"}:
                        reasons.append("字段状态需要人工确认")
                    if confidence is not None and float(confidence) < .8:
                        reasons.append("字段可信度低于 80%")
                    reasons.extend(flag.get("message") for flag in relevant if flag.get("message"))
                    if reasons:
                        candidates.append({
                            "field_path": path, "field_label": cls._field_label(path),
                            "model_value": value.get("normalized_value")
                            if value.get("normalized_value") is not None else value.get("value"),
                            "status": value.get("status"), "confidence": confidence,
                            "reason": "；".join(dict.fromkeys(reasons)),
                            "source_references": deepcopy(value.get("evidence") or []),
                        })
                    return
                for key, item in value.items():
                    if key not in {"quality_flags", "events"}:
                        walk(item, f"{path}.{key}" if path else key)
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    walk(item, f"{path}[{index}]")

        walk(structured)
        return candidates

    @staticmethod
    def _latest_field_reviews(db, case_id, fingerprint):
        rows = db.execute(
            "SELECT record_json FROM reviews WHERE case_id=? AND content_fingerprint=? "
            "AND target_type LIKE 'structured_field:%' ORDER BY rowid DESC",
            (case_id, fingerprint),
        ).fetchall()
        latest = {}
        for row in rows:
            record = json.loads(row[0])
            latest.setdefault(record["field_path"], record)
        return latest

    @classmethod
    def _field_review_state(cls, db, case_id, structured):
        fingerprint = cls._structured_fingerprint(structured)
        if not fingerprint:
            return fingerprint, [], [], deepcopy(structured)
        reviews_by_path = cls._latest_field_reviews(db, case_id, fingerprint)
        candidates = cls._field_review_candidates(structured)
        pending = [item for item in candidates if item["field_path"] not in reviews_by_path]
        effective = deepcopy(structured)
        for path, review in reviews_by_path.items():
            if review.get("decision") not in {"CONFIRMED", "CORRECTED"}:
                continue
            try:
                field = cls._field_at_path(effective, path)
            except (WorkflowConflict, WorkflowItemNotFound):
                continue
            field["value"] = review.get("final_value")
            if "normalized_value" in field:
                field["normalized_value"] = review.get("final_value")
            # Review metadata stays in ``field_reviews``.  The effective case
            # must remain a valid ClaimCase so every existing downstream
            # algorithm can consume it without a parallel JSON schema.
        return fingerprint, pending, list(reviews_by_path.values()), effective

    @classmethod
    def _medical_pending_count(cls, structured, pain, care, handled_paths=None,
                               pain_item_reviews=None):
        handled_paths = set(handled_paths or [])
        count = sum(item["field_path"] not in handled_paths
                    for item in cls._field_review_candidates(structured))
        count += sum(
            flag.get("severity") in {"warning", "error"} and not flag.get("field_path")
            for flag in (structured or {}).get("quality_flags", [])
        )
        if pain_item_reviews is None:
            count += sum(bool(item.get("review_required"))
                         for item in (pain or {}).get("pain_points", []))
        else:
            count += cls._pain_review_summary(pain, pain_item_reviews)["pending"]
        if care and (care.get("review_required") or care.get("warnings")):
            count += 1
        return count

    @staticmethod
    def _content_fingerprint(value):
        return hashlib.sha256(encode(value).encode("utf-8")).hexdigest() if value is not None else None

    @staticmethod
    def _module_target(module):
        return {
            "pain": "pain_report",
            "care": "care_guide",
            "treatment_quality": "treatment_quality",
            "recommendation": "recommendation",
        }[module]

    @staticmethod
    def _record_module_state(db, case_id, module, effective_fingerprint, *, user_id=None,
                             action="MEDICAL_MODULE_GENERATED"):
        generation_id = str(uuid4())
        db.execute(
            "INSERT INTO system_logs(log_id, user_id, case_id, action, module, request_path, "
            "ip_address, request_params_json, result, error_message, created_at) "
            "VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, 'SUCCESS', NULL, ?)",
            (generation_id, user_id, case_id, action, module,
             encode({"effective_fingerprint": effective_fingerprint}),
             datetime.now(timezone.utc).isoformat()),
        )
        return generation_id

    @staticmethod
    def _latest_module_fingerprint(db, case_id, module):
        row = db.execute(
            "SELECT request_params_json FROM system_logs WHERE case_id=? AND module=? "
            "AND action IN ('MEDICAL_MODULE_GENERATED','MEDICAL_MODULE_REBASED') "
            "AND result='SUCCESS' ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (case_id, module),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0]).get("effective_fingerprint")

    @staticmethod
    def _latest_module_generation_id(db, case_id, module):
        row = db.execute(
            "SELECT log_id FROM system_logs WHERE case_id=? AND module=? "
            "AND action='MEDICAL_MODULE_GENERATED' AND result='SUCCESS' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (case_id, module),
        ).fetchone()
        return row[0] if row else None

    @classmethod
    def _module_content_fingerprint(cls, db, case_id, module, content):
        if content is None:
            return None
        content = cls._stable_module_review_content(module, content)
        generation_id = cls._latest_module_generation_id(db, case_id, module)
        if generation_id is None:
            # Legacy results predate module generation logs. Keep them
            # reviewable without rewriting existing business data.
            return cls._content_fingerprint(content)
        return cls._content_fingerprint({
            "module": module, "generation_id": generation_id, "content": content,
        })

    @staticmethod
    def _stable_module_review_content(module, content):
        """Remove lifecycle metadata that must not invalidate a content review."""
        stable = deepcopy(content)
        if module == "recommendation" and isinstance(stable, dict):
            # DRAFT -> REVIEWED/PUBLISHED is a workflow transition on the same
            # immutable report version.  Those fields cannot be part of the
            # medical-review fingerprint or approval invalidates itself.
            stable.pop("status", None)
            stable.pop("updated_at", None)
        return stable

    @classmethod
    def _module_review_fingerprints(cls, db, case_id, module, content):
        """Current fingerprint plus the pre-fix recommendation fingerprint."""
        current = cls._module_content_fingerprint(db, case_id, module, content)
        candidates = [current] if current else []
        if module == "recommendation" and isinstance(content, dict):
            generation_id = cls._latest_module_generation_id(db, case_id, module)
            if generation_id:
                legacy = deepcopy(content)
                legacy["status"] = "DRAFT"
                if legacy.get("created_at"):
                    legacy["updated_at"] = legacy["created_at"]
                legacy_fingerprint = cls._content_fingerprint({
                    "module": module, "generation_id": generation_id,
                    "content": legacy,
                })
                if legacy_fingerprint not in candidates:
                    candidates.append(legacy_fingerprint)
        return candidates

    @classmethod
    def _latest_pain_item_reviews(cls, db, case_id, pain):
        """Return one current decision per pain point, ignoring stale reports."""
        if not pain:
            return []
        fingerprint = cls._module_content_fingerprint(db, case_id, "pain", pain)
        rows = db.execute(
            "SELECT record_json FROM reviews WHERE case_id=? AND content_fingerprint=? "
            "AND target_type LIKE 'pain_item:%' ORDER BY rowid DESC",
            (case_id, fingerprint),
        ).fetchall()
        latest = {}
        for row in rows:
            record = json.loads(row[0])
            latest.setdefault(record["pain_point_id"], record)
        point_order = [item.get("pain_point_id") for item in pain.get("pain_points", [])]
        return [latest[point_id] for point_id in point_order if point_id in latest]

    @staticmethod
    def _pain_review_summary(pain, reviews):
        point_ids = [item.get("pain_point_id") for item in (pain or {}).get("pain_points", [])]
        decisions = {item.get("pain_point_id"): item.get("decision") for item in reviews}
        return {
            "total": len(point_ids),
            "pending": sum(point_id not in decisions for point_id in point_ids),
            "show": sum(value in {"SHOW", "CONFIRM"} for value in decisions.values()),
            "edit": sum(value == "EDIT" for value in decisions.values()),
            "hide": sum(value == "HIDE" for value in decisions.values()),
        }

    @staticmethod
    def _pain_item_decisions(item_reviews, pain):
        """Convert current item reviews to the module overlay representation."""
        points = {
            item.get("pain_point_id"): item
            for item in (pain or {}).get("pain_points", [])
        }
        decisions = []
        for raw in item_reviews:
            point_id = raw.get("pain_point_id")
            if point_id not in points:
                continue
            decision = raw.get("decision")
            item = {
                "pain_point_id": point_id,
                "decision": "CONFIRM" if decision == "SHOW" else decision,
            }
            if decision == "EDIT":
                item.update({
                    "title": raw.get("title") or points[point_id].get("title"),
                    "description": raw.get("summary") or points[point_id].get("description"),
                    "recommended_action": raw.get("suggestion")
                    or points[point_id].get("recommended_action"),
                })
            decisions.append(item)
        return decisions

    @classmethod
    def _pain_review_with_current_items(cls, pain, review, item_reviews):
        """Repair the effective overlay for reviews saved before item snapshots."""
        if (review or {}).get("decision") != "APPROVED":
            return review
        payload = (review.get("payload") or {})
        if payload.get("item_decisions"):
            return review
        fallback = cls._pain_item_decisions(item_reviews, pain)
        if not fallback:
            return review
        resolved = deepcopy(review)
        resolved["payload"] = {**payload, "item_decisions": fallback}
        return resolved

    @staticmethod
    def _pain_system_suggestions(pain):
        """Return review defaults without treating them as medical approval."""
        suggestions = []
        for point in (pain or {}).get("pain_points", []):
            evidence = [
                item for item in point.get("evidence", [])
                if item.get("document_id") and str(item.get("text") or "").strip()
            ]
            required = all([
                point.get("pain_point_id"),
                point.get("dimension") in {"clinical", "cost", "experience"},
                point.get("type"), str(point.get("title") or "").strip(),
                str(point.get("description") or "").strip(),
                str(point.get("recommended_action") or "").strip(), evidence,
            ])
            confidence = float(point.get("confidence") or 0)
            if not required:
                decision = "HIDE"
                reason = "关键信息或可溯源病例证据不完整，需人工处理后才能纳入关怀。"
                blocker = "痛点缺少正式输出所需字段或可溯源证据"
            elif confidence < 0.65:
                decision = "HIDE"
                reason = "当前可信度较低，系统建议先作为补充观察。"
                blocker = None
            else:
                decision = "SHOW"
                reason = "维度、类型、归类原因、行动建议和病例证据完整。"
                blocker = None
            suggestions.append({
                "pain_point_id": point.get("pain_point_id"),
                "decision": decision, "reason": reason,
                "approval_blocker": blocker,
            })
        return suggestions

    @classmethod
    def _pain_approval_decisions(cls, pain, item_reviews, inline_decisions):
        """Resolve system defaults plus explicit reviewer overrides for approval."""
        points = {
            item.get("pain_point_id"): item
            for item in (pain or {}).get("pain_points", [])
        }
        suggestions = {
            item["pain_point_id"]: item for item in cls._pain_system_suggestions(pain)
        }
        resolved = {
            point_id: {
                "pain_point_id": point_id,
                "decision": "CONFIRM" if suggestion["decision"] == "SHOW" else "HIDE",
            }
            for point_id, suggestion in suggestions.items()
        }
        for item in cls._pain_item_decisions(item_reviews, pain):
            resolved[item["pain_point_id"]] = item
        for raw in inline_decisions:
            point_id = raw.get("pain_point_id")
            if point_id in points:
                item = deepcopy(raw)
                if item.get("decision") == "SHOW":
                    item["decision"] = "CONFIRM"
                resolved[point_id] = item
        blockers = [
            suggestion["approval_blocker"] for suggestion in suggestions.values()
            if suggestion.get("approval_blocker")
        ]
        return [resolved[point_id] for point_id in points if point_id in resolved], blockers

    def get_effective_case_state(self, case_id):
        """Return the raw extraction plus the field-review overlay used downstream."""
        with self._connect() as db:
            row = self._row(db, case_id)
            raw = json.loads(row["structured_result_json"]) if row["structured_result_json"] else None
            fingerprint, pending, reviews, effective = self._field_review_state(db, case_id, raw)
            effective_fingerprint = self._structured_fingerprint(effective)
            return {
                "raw": raw, "effective": effective,
                "raw_fingerprint": fingerprint,
                "effective_fingerprint": effective_fingerprint,
                "pending": pending, "field_reviews": reviews,
            }

    def save_medical_pain(self, case_id, result, raw_case, effective_fingerprint, user_id=None):
        """Save pain computed from the effective case without deleting viewable old Care."""
        if result.case_id != case_id:
            raise WorkflowConflict("痛点报告 case_id 不匹配")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_current(db, case_id, raw_case)
            db.execute("UPDATE case_results SET pain_result_json=?, updated_at=? WHERE case_id=?",
                       (encode(result), datetime.now(timezone.utc).isoformat(), case_id))
            self._record_module_state(db, case_id, "pain", effective_fingerprint, user_id=user_id)

    def save_medical_care(self, case_id, result, raw_case, effective_fingerprint, user_id=None):
        """Save Care computed from the effective case and current saved pain."""
        if result.case_id != case_id:
            raise WorkflowConflict("指引报告 case_id 不匹配")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._require_current(db, case_id, raw_case)
            if row["pain_result_json"] is None:
                raise WorkflowConflict("请先保存痛点分析结果")
            db.execute("UPDATE case_results SET care_result_json=?, updated_at=? WHERE case_id=?",
                       (encode(result), datetime.now(timezone.utc).isoformat(), case_id))
            self._record_module_state(db, case_id, "care", effective_fingerprint, user_id=user_id)

    def record_medical_module_generation(self, case_id, module, effective_fingerprint, user_id=None):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._row(db, case_id)
            self._record_module_state(db, case_id, module, effective_fingerprint, user_id=user_id)

    def save_treatment_evaluation(self, case_id, evaluation, disease_group,
                                  effective_fingerprint, user_id=None):
        """Persist one-case treatment observations in the existing quality tables."""
        payload = json.loads(encode(evaluation))
        dimensions = {
            "treatment_diagnosis_match": payload["treatment_diagnosis_match"],
            "medication_rationality": payload["medication_rationality"],
            "procedure_rationality": payload["procedure_rationality"],
            "treatment_completeness": payload["treatment_completeness"],
            "discharge_continuity": payload["discharge_continuity"],
            "risk_management": payload["risk_management"],
        }
        status_map = {
            "evaluated": "OBSERVED", "needs_review": "REVIEW_REQUIRED",
            "insufficient_evidence": "DATA_UNAVAILABLE", "not_applicable": "DATA_UNAVAILABLE",
        }
        quality_summary = {
            "overall_score": payload.get("overall_score"),
            "overall_level": payload.get("overall_level"),
            "scored_weight_coverage": payload.get("scored_weight_coverage", 0),
            "evaluation_method": payload.get("evaluation_method", "RULES_FALLBACK"),
            "warnings": payload.get("warnings") or [],
        }
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._row(db, case_id)
            provider_id = f"case:{case_id}"
            db.execute(
                "DELETE FROM provider_quality_observations WHERE case_id=? AND provider_type='TREATMENT'",
                (case_id,),
            )
            for metric_code, dimension in dimensions.items():
                db.execute(
                    "INSERT INTO provider_quality_observations(observation_id, case_id, provider_type, "
                    "provider_id, disease_group, metric_code, raw_value, unit, status, evidence_json, "
                    "source_kind, observed_at, created_at) VALUES (?, ?, 'TREATMENT', ?, ?, ?, ?, 'score', "
                    "?, ?, 'workflow', ?, ?) ON CONFLICT(case_id, provider_type, provider_id, "
                    "disease_group, metric_code) DO UPDATE SET raw_value=excluded.raw_value, "
                    "status=excluded.status, evidence_json=excluded.evidence_json, "
                    "observed_at=excluded.observed_at, created_at=excluded.created_at",
                    (str(uuid4()), case_id, provider_id, disease_group, metric_code,
                     dimension.get("score"), status_map[dimension["status"]],
                     encode({
                         "reasoning": dimension.get("reasoning"),
                         "assessment_status": dimension.get("status"),
                         "level": dimension.get("level"),
                         "evidence": dimension.get("evidence") or [],
                         "evidence_fields": dimension.get("evidence_fields") or [],
                         "limitations": dimension.get("limitations") or [],
                         "confidence": dimension.get("confidence", 0),
                         "effective_fingerprint": effective_fingerprint,
                         "quality_summary": quality_summary,
                     }), now, now),
                )
            self._record_module_state(
                db, case_id, "treatment_quality", effective_fingerprint, user_id=user_id,
            )

    def module_review_content(self, case_id, module):
        with self._connect() as db:
            row = self._row(db, case_id)
            if module == "pain":
                content = json.loads(row["pain_result_json"]) if row["pain_result_json"] else None
                generation_id = self._latest_module_generation_id(db, case_id, module)
                return ({"module": module, "generation_id": generation_id, "content": content}
                        if content is not None and generation_id else content)
            if module == "care":
                content = json.loads(row["care_result_json"]) if row["care_result_json"] else None
                generation_id = self._latest_module_generation_id(db, case_id, module)
                return ({"module": module, "generation_id": generation_id, "content": content}
                        if content is not None and generation_id else content)
            if module == "treatment_quality":
                values = []
                for item in db.execute(
                    "SELECT metric_code, raw_value, status, evidence_json FROM provider_quality_observations "
                    "WHERE case_id=? AND provider_type='TREATMENT' ORDER BY metric_code", (case_id,),
                ):
                    value = dict(item)
                    value["evidence"] = json.loads(value.pop("evidence_json"))
                    values.append(value)
                content = values or None
                generation_id = self._latest_module_generation_id(db, case_id, module)
                return ({"module": module, "generation_id": generation_id, "content": content}
                        if content is not None and generation_id else content)
            if module == "recommendation":
                content = self.load_recommendation_report(case_id)
                content = self._stable_module_review_content(module, content)
                generation_id = self._latest_module_generation_id(db, case_id, module)
                return ({"module": module, "generation_id": generation_id, "content": content}
                        if content is not None and generation_id else content)
            raise WorkflowConflict("未知复核模块")

    def save_pain_item_review(self, user_id, case_id, pain_point_id, request):
        """Save an internal care-disposition override bound to the current pain report."""
        from .contracts import PainItemReviewRecordV1
        if not self.user_can_access_case(user_id, case_id, "MEDICAL_CASE_VIEW"):
            raise CaseNotFound(case_id)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, case_id)
            pain = json.loads(row["pain_result_json"]) if row["pain_result_json"] else None
            if pain is None:
                raise WorkflowConflict("痛点分析尚未生成")
            structured = json.loads(row["structured_result_json"]) if row["structured_result_json"] else None
            _raw_fp, _pending, _field_reviews, effective = self._field_review_state(
                db, case_id, structured,
            )
            generated_from = self._latest_module_fingerprint(db, case_id, "pain")
            effective_fingerprint = self._structured_fingerprint(effective)
            if generated_from and generated_from != effective_fingerprint:
                raise WorkflowConflict("痛点分析已过期，请先重新生成")
            point = next(
                (item for item in pain.get("pain_points", [])
                 if item.get("pain_point_id") == pain_point_id),
                None,
            )
            if point is None:
                raise WorkflowItemNotFound("痛点条目不存在或已变化")
            reviewer = db.execute(
                "SELECT display_name FROM users WHERE user_id=?", (user_id,),
            ).fetchone()
            if reviewer is None:
                raise WorkflowConflict("当前复核用户不存在")
            now = datetime.now(timezone.utc).isoformat()
            content_fingerprint = self._module_content_fingerprint(db, case_id, "pain", pain)
            record = PainItemReviewRecordV1(
                **request.model_dump(), review_id=str(uuid4()), case_id=case_id,
                pain_point_id=pain_point_id, content_fingerprint=content_fingerprint,
                original_title=point.get("title") or "需要关注",
                original_summary=point.get("description") or "",
                original_suggestion=point.get("recommended_action") or "",
                severity=point.get("severity") or "low",
                reviewer_user_id=user_id, reviewer_name=reviewer["display_name"],
                reviewed_at=now,
            )
            db.execute(
                "INSERT INTO reviews(review_id, case_id, content_fingerprint, target_type, decision, "
                "record_json, created_at, comment, reviewer_user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record.review_id, case_id, content_fingerprint,
                 f"pain_item:{pain_point_id}", record.decision.value, encode(record), now,
                 record.note, user_id),
            )
            return record

    def save_care_revision(self, user_id, case_id, request, revised_report):
        """Persist an auditable before/after revision and make it the current Care result."""
        from .contracts import CareRevisionRecordV1
        from .care_models import CareGuideReport
        if not self.user_can_access_case(user_id, case_id, "MEDICAL_CASE_VIEW"):
            raise CaseNotFound(case_id)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, case_id)
            current = json.loads(row["care_result_json"]) if row["care_result_json"] else None
            if current is None:
                raise WorkflowConflict("温度指引尚未生成")
            current_fingerprint = self._module_content_fingerprint(db, case_id, "care", current)
            if request.base_content_fingerprint != current_fingerprint:
                raise WorkflowConflict("温度指引已更新，请重新打开后再修订")
            if revised_report.case_id != case_id:
                raise WorkflowConflict("修订稿 case_id 与当前案件不一致")
            structured = json.loads(row["structured_result_json"]) if row["structured_result_json"] else None
            _raw_fp, _pending, _field_reviews, effective = self._field_review_state(
                db, case_id, structured,
            )
            effective_fingerprint = self._structured_fingerprint(effective)
            reviewer = db.execute(
                "SELECT display_name FROM users WHERE user_id=?", (user_id,),
            ).fetchone()
            if reviewer is None:
                raise WorkflowConflict("当前复核用户不存在")
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "UPDATE case_results SET care_result_json=?, updated_at=? WHERE case_id=?",
                (encode(revised_report), now, case_id),
            )
            self._record_module_state(
                db, case_id, "care", effective_fingerprint, user_id=user_id,
            )
            revised_payload = CareGuideReport.model_validate(revised_report).model_dump(mode="json")
            revised_fingerprint = self._module_content_fingerprint(
                db, case_id, "care", revised_payload,
            )
            record = CareRevisionRecordV1(
                revision_id=str(uuid4()), case_id=case_id,
                reviewer=reviewer["display_name"], reviewer_user_id=user_id,
                note=request.note.strip(),
                base_content_fingerprint=request.base_content_fingerprint,
                content_fingerprint=revised_fingerprint,
                original_report=CareGuideReport.model_validate(current),
                revised_report=CareGuideReport.model_validate(revised_payload),
                created_at=now,
            )
            db.execute(
                "INSERT INTO reviews(review_id, case_id, content_fingerprint, target_type, decision, "
                "record_json, created_at, comment, reviewer_user_id) VALUES (?, ?, ?, 'care_revision', "
                "'CORRECTED', ?, ?, ?, ?)",
                (record.revision_id, case_id, revised_fingerprint, encode(record), now,
                 record.note, user_id),
            )
            return record

    def save_module_review(self, user_id, case_id, target_type, decision, note, content, payload=None):
        """Persist a review for one immutable module result, independent of delivery review."""
        from .review_models import ReviewRecord, ReviewRequest, ReviewTarget
        if not self.user_can_access_case(user_id, case_id, "MEDICAL_CASE_VIEW"):
            raise CaseNotFound(case_id)
        target = ReviewTarget(target_type)
        if target == ReviewTarget.COMBINED_DELIVERY:
            raise WorkflowConflict("案件级发布复核必须使用发布门禁接口")
        fingerprint = self._content_fingerprint(content)
        if not fingerprint:
            raise WorkflowConflict("该模块尚无可复核结果")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, case_id)
            structured = json.loads(row["structured_result_json"]) if row["structured_result_json"] else None
            _raw_fp, _pending, _field_reviews, effective = self._field_review_state(
                db, case_id, structured,
            )
            module = {
                "pain_report": "pain", "care_guide": "care",
                "treatment_quality": "treatment_quality", "recommendation": "recommendation",
            }[target.value]
            generated_from = self._latest_module_fingerprint(db, case_id, module)
            if generated_from and generated_from != self._structured_fingerprint(effective):
                raise WorkflowConflict("该模块已过期，请重新生成后再复核")
            if decision.value == "APPROVED" and module == "pain":
                pain = json.loads(row["pain_result_json"]) if row["pain_result_json"] else None
                item_reviews = self._latest_pain_item_reviews(db, case_id, pain)
                inline_decisions = (payload or {}).get("item_decisions") or []
                final_decisions, blockers = self._pain_approval_decisions(
                    pain, item_reviews, inline_decisions,
                )
                if blockers:
                    raise WorkflowConflict("；".join(dict.fromkeys(blockers)))
                if self._pain_review_summary(pain, final_decisions)["pending"]:
                    raise WorkflowConflict("仍有痛点缺少明确的纳入、编辑后纳入或忽略结论")
                payload = dict(payload or {})
                payload["item_decisions"] = final_decisions
            if decision.value == "APPROVED" and module == "care":
                care = json.loads(row["care_result_json"]) if row["care_result_json"] else None
                blocked = [item for item in (care or {}).get("safety_checks", [])
                           if item.get("status") == "blocked"]
                if blocked:
                    raise WorkflowConflict("温度指引存在阻断级安全检查，不能通过")
            reviewer = db.execute("SELECT display_name FROM users WHERE user_id=?", (user_id,)).fetchone()
            if reviewer is None:
                raise WorkflowConflict("当前复核用户不存在")
            request = ReviewRequest(target_type=target, reviewer=reviewer[0], decision=decision,
                                    note=note, content_fingerprint=fingerprint, payload=payload or {})
            record = ReviewRecord(**request.model_dump(), review_id=str(uuid4()), case_id=case_id,
                                  created_at=datetime.now(timezone.utc).isoformat())
            db.execute(
                "INSERT INTO reviews(review_id, case_id, content_fingerprint, target_type, decision, "
                "record_json, created_at, comment, reviewer_user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record.review_id, case_id, fingerprint, target.value, decision.value,
                 encode(record), record.created_at, note, user_id),
            )
            return record

    @classmethod
    def _latest_module_review(cls, db, case_id, module, content):
        fingerprints = cls._module_review_fingerprints(db, case_id, module, content)
        if not fingerprints:
            return None
        placeholders = ",".join("?" for _ in fingerprints)
        row = db.execute(
            "SELECT record_json FROM reviews WHERE case_id=? AND target_type=? "
            f"AND content_fingerprint IN ({placeholders}) ORDER BY rowid DESC LIMIT 1",
            (case_id, cls._module_target(module), *fingerprints),
        ).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _effective_pain_result(pain, review):
        if not pain:
            return pain
        effective = deepcopy(pain)
        if (review or {}).get("decision") != "APPROVED":
            return effective
        payload = (review or {}).get("payload") or {}
        decisions = {item.get("pain_point_id"): item for item in payload.get("item_decisions", [])}
        visible = []
        for point in effective.get("pain_points", []):
            item = decisions.get(point.get("pain_point_id")) or {}
            action = item.get("decision")
            if action == "HIDE":
                continue
            if action == "EDIT":
                for key in ("title", "description", "recommended_action"):
                    if item.get(key):
                        point[key] = item[key]
            if action in {"CONFIRM", "EDIT"}:
                point["review_required"] = False
            visible.append(point)
        effective["pain_points"] = visible
        effective["summary"] = {
            "total": len(visible),
            "clinical": sum(item.get("dimension") == "clinical" for item in visible),
            "cost": sum(item.get("dimension") == "cost" for item in visible),
            "experience": sum(item.get("dimension") == "experience" for item in visible),
            "high_priority": sum(item.get("severity") in {"high", "critical"} for item in visible),
        }
        return effective

    @staticmethod
    def _effective_care_result(care, review):
        if not care:
            return care
        guide = ((review or {}).get("payload") or {}).get("care_revision", {}).get("guide")
        if not guide:
            return deepcopy(care)
        effective = deepcopy(care)
        effective["guide"] = deepcopy(guide)
        return effective

    @classmethod
    def _care_evidence_options(cls, structured):
        options = []
        def walk(value, path=""):
            if isinstance(value, dict):
                if "status" in value and "value" in value:
                    item = value.get("normalized_value") if value.get("normalized_value") is not None else value.get("value")
                    if item is not None and value.get("status") not in {"not_present_in_document", "not_applicable"}:
                        options.append({"path": path, "label": cls._field_label(path), "value": item})
                    return
                for key, child in value.items():
                    if key not in {"quality_flags", "events"}:
                        walk(child, f"{path}.{key}" if path else key)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    walk(child, f"{path}[{index}]")
        walk(structured or {})
        return options

    def _module_states(self, db, case_id, effective, pain, care, observations, recommendation):
        from .medical_orchestration import (
            GenerationStatus, MedicalModule, ModuleReviewStatus, ModuleState, eligibility,
        )
        effective_fingerprint = self._structured_fingerprint(effective)
        if not effective:
            return {module.value: ModuleState(
                module=module, generation_status=GenerationStatus.INPUT_INSUFFICIENT,
                blockers=["结构化病例尚未生成"],
            ).model_dump(mode="json") for module in MedicalModule}
        from .models import ClaimCase
        case = ClaimCase.model_validate(effective)
        treatment_content = [{
            "metric_code": item.get("metric_code"), "raw_value": item.get("raw_value"),
            "status": item.get("status"), "evidence": item.get("evidence") or {},
        } for item in observations if item.get("provider_type") == "TREATMENT"]
        content = {
            "pain": pain,
            "care": care,
            "treatment_quality": treatment_content or None,
            "recommendation": recommendation,
        }
        states = {}
        for module in MedicalModule:
            available = content[module.value] is not None
            city = ((recommendation or {}).get("patient_city") if recommendation else None)
            allowed, blockers, warnings = eligibility(
                module, case, pain_available=pain is not None, city=city,
            )
            generated_from = self._latest_module_fingerprint(db, case_id, module.value)
            generation_id = self._latest_module_generation_id(db, case_id, module.value)
            if module == MedicalModule.RECOMMENDATION and available \
                    and recommendation.get("status") == "INVALIDATED":
                generation = GenerationStatus.STALE
            elif available and generated_from and generated_from != effective_fingerprint:
                generation = GenerationStatus.STALE
            elif available:
                generation = GenerationStatus.GENERATED
            elif allowed:
                generation = GenerationStatus.READY
            else:
                generation = GenerationStatus.INPUT_INSUFFICIENT
            content_fingerprint = self._module_content_fingerprint(
                db, case_id, module.value, content[module.value],
            )
            latest_review = self._latest_module_review(
                db, case_id, module.value, content[module.value],
            )
            latest = latest_review.get("decision") if latest_review else None
            review = {
                None: ModuleReviewStatus.NOT_REVIEWED,
                "APPROVED": ModuleReviewStatus.APPROVED,
                "REJECTED": ModuleReviewStatus.CHANGES_REQUESTED,
                "NEEDS_CONFIRMATION": ModuleReviewStatus.EVIDENCE_INSUFFICIENT,
            }.get(latest, ModuleReviewStatus.NOT_REVIEWED)
            if available and latest is None:
                review = ModuleReviewStatus.REVIEW_REQUIRED
            states[module.value] = ModuleState(
                module=module, generation_status=generation, review_status=review,
                blockers=blockers, warnings=warnings, result_available=available,
                current_generation_id=generation_id,
                content_fingerprint=content_fingerprint,
            ).model_dump(mode="json")
        return states

    @staticmethod
    def _without_bbox(value):
        if isinstance(value, dict):
            return {key: CaseStorage._without_bbox(item) for key, item in value.items() if key != "bbox"}
        if isinstance(value, list):
            return [CaseStorage._without_bbox(item) for item in value]
        return value

    def list_medical_cases(self, user_id, *, pending_only=False, limit=200):
        """List only assigned cases that have entered the care workflow."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT c.case_id, c.claim_event_id, c.care_status, c.updated_at, c.created_at, "
                "cp.assigned_at AS care_entered_at, customer.display_name AS customer_display_name, "
                "cr.structured_result_json, cr.pain_result_json, cr.care_result_json, "
                "(SELECT decision FROM reviews rv WHERE rv.case_id=c.case_id "
                "AND rv.target_type='combined_delivery' ORDER BY rv.rowid DESC LIMIT 1) "
                "AS latest_review_decision FROM cases c "
                "JOIN case_participants cp ON cp.case_id=c.case_id "
                "JOIN case_results cr ON cr.case_id=c.case_id "
                "LEFT JOIN users customer ON customer.user_id=c.customer_user_id "
                "WHERE cp.user_id=? AND cp.case_role='MEDICAL_REVIEWER' AND cp.status='ACTIVE' "
                "AND c.care_status NOT IN ('RECEIVED','STRUCTURED') "
                "ORDER BY c.updated_at DESC LIMIT ?",
                (user_id, max(1, min(int(limit), 500))),
            ).fetchall()
            output = []
            for row in rows:
                structured = json.loads(row["structured_result_json"]) if row["structured_result_json"] else None
                pain = json.loads(row["pain_result_json"]) if row["pain_result_json"] else None
                care = json.loads(row["care_result_json"]) if row["care_result_json"] else None
                fingerprint = self._structured_fingerprint(structured)
                handled = self._latest_field_reviews(db, row["case_id"], fingerprint) if fingerprint else {}
                pain_item_reviews = self._latest_pain_item_reviews(db, row["case_id"], pain)
                field_pending = sum(
                    item["field_path"] not in handled for item in self._field_review_candidates(structured)
                ) + sum(item.get("decision") == "UNRESOLVED" for item in handled.values())
                review_status = self._medical_review_status(row["latest_review_decision"], row["care_status"])
                if pending_only and review_status == "APPROVED":
                    continue
                output.append({
                    "case_id": row["case_id"], "claim_event_id": row["claim_event_id"],
                    "customer_display_name": self._mask_customer_label(row["customer_display_name"]),
                    "primary_diagnosis": self._primary_diagnosis(structured),
                    "care_status": row["care_status"], "review_status": review_status,
                    "analysis_ready": {"structured": structured is not None, "pain": pain is not None,
                                       "care": care is not None},
                    "pending_confirmation_count": field_pending,
                    "medical_attention_count": self._medical_pending_count(
                        structured, pain, care, handled.keys(), pain_item_reviews),
                    "care_entered_at": row["care_entered_at"], "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                })
            return output

    def medical_dashboard(self, user_id):
        cases = self.list_medical_cases(user_id)
        return {
            "pending_review": sum(item["review_status"] != "APPROVED" for item in cases),
            "new_care_cases": sum(item["care_status"] == "CARE_TRIGGERED" for item in cases),
            "needs_confirmation": sum(item["pending_confirmation_count"] > 0 for item in cases),
            "completed_reviews": sum(item["review_status"] == "APPROVED" for item in cases),
            "recent_cases": cases[:8],
        }

    def get_medical_case_detail(self, user_id, case_id):
        if not self.user_can_access_case(user_id, case_id, "MEDICAL_CASE_VIEW"):
            raise CaseNotFound(case_id)
        with self._connect() as db:
            row = db.execute(
                "SELECT c.case_id, c.claim_event_id, c.data_source, c.consent_status, "
                "c.deidentification_status, c.care_status, c.created_at, "
                "c.updated_at, customer.display_name AS customer_display_name, "
                "cr.ocr_result_json, cr.structured_result_json, cr.pain_result_json, cr.care_result_json "
                "FROM cases c JOIN case_results cr ON cr.case_id=c.case_id "
                "LEFT JOIN users customer ON customer.user_id=c.customer_user_id WHERE c.case_id=?",
                (case_id,),
            ).fetchone()
            if row is None or row["care_status"] in {"RECEIVED", "STRUCTURED"}:
                raise CaseNotFound(case_id)
            raw_structured = json.loads(row["structured_result_json"]) if row["structured_result_json"] else None
            structured = self._without_bbox(raw_structured) if raw_structured else None
            raw_pain = json.loads(row["pain_result_json"]) if row["pain_result_json"] else None
            raw_care = json.loads(row["care_result_json"]) if row["care_result_json"] else None
            pain = self._without_bbox(raw_pain) if raw_pain else None
            care = self._without_bbox(raw_care) if raw_care else None
            raw_ocr = json.loads(row["ocr_result_json"]) if row["ocr_result_json"] else []
            ocr = [{
                "document_id": item.get("document_id"), "page": item.get("page"),
                "resource_id": item.get("resource_id"),
                "media_type": item.get("media_type"),
                "preview_available": bool(item.get("resource_id") and item.get("stored_filename")),
                "human_corrected": bool(item.get("human_corrected")),
                "full_text": item.get("full_text") or "",
                "blocks": [{"text": block.get("text", ""), "confidence": block.get("confidence")}
                           for block in item.get("blocks", [])],
            } for item in raw_ocr]
            reviews = [json.loads(item[0]) for item in db.execute(
                "SELECT record_json FROM reviews WHERE case_id=? "
                "AND target_type NOT LIKE 'structured_field:%' "
                "AND target_type NOT LIKE 'pain_item:%' AND target_type<>'care_revision' "
                "ORDER BY rowid DESC", (case_id,)
            )]
            structured_fingerprint, pending_fields, field_reviews, effective_structured = \
                self._field_review_state(db, case_id, raw_structured)
            observations = []
            quality_summary = None
            for item in db.execute(
                "SELECT * FROM provider_quality_observations WHERE case_id=? ORDER BY provider_type, metric_code",
                (case_id,),
            ):
                value = dict(item)
                value["evidence"] = json.loads(value.pop("evidence_json"))
                if value["provider_type"] == "TREATMENT" and quality_summary is None:
                    quality_summary = value["evidence"].get("quality_summary")
                observations.append(value)
            historical_profiles = []
            profile_keys = {(item["provider_type"], item["provider_id"], item["disease_group"])
                            for item in observations}
            for provider_type, provider_id, disease_group in sorted(profile_keys):
                snapshot = db.execute(
                    "SELECT * FROM provider_profile_snapshots WHERE provider_type=? AND provider_id=? "
                    "AND disease_group=? AND status='ACTIVE' ORDER BY profile_version DESC LIMIT 1",
                    (provider_type, provider_id, disease_group),
                ).fetchone()
                if snapshot is None:
                    continue
                profile = dict(snapshot)
                profile["profile"] = json.loads(profile.pop("profile_json"))
                profile["metrics"] = []
                for metric_row in db.execute(
                    "SELECT * FROM provider_metric_scores WHERE snapshot_id=? ORDER BY metric_code",
                    (snapshot["snapshot_id"],),
                ):
                    metric = dict(metric_row)
                    metric["evidence"] = json.loads(metric.pop("evidence_json"))
                    profile["metrics"].append(metric)
                historical_profiles.append(profile)
            recommendation = self.load_recommendation_report(case_id)
            module_states = self._module_states(
                db, case_id, effective_structured, raw_pain, raw_care, observations, recommendation,
            )
            pain_item_reviews = self._latest_pain_item_reviews(db, case_id, raw_pain)
            care_revisions = [json.loads(item[0]) for item in db.execute(
                "SELECT record_json FROM reviews WHERE case_id=? AND target_type='care_revision' "
                "ORDER BY rowid DESC", (case_id,),
            )]
            latest_delivery_review = next(
                (item for item in reviews if item.get("target_type") == "combined_delivery"), None,
            )
            latest_decision = latest_delivery_review.get("decision") if latest_delivery_review else None
            medical_contract_enforced = bool(db.execute(
                "SELECT 1 FROM system_logs WHERE case_id=? AND action='MEDICAL_MODULE_GENERATED' "
                "LIMIT 1", (case_id,),
            ).fetchone())
            pain_review = self._latest_module_review(db, case_id, "pain", raw_pain)
            care_review = self._latest_module_review(db, case_id, "care", raw_care)
            pain_suggestions = self._pain_system_suggestions(raw_pain)
            summary_reviews = pain_item_reviews
            if (pain_review or {}).get("decision") == "APPROVED":
                pain_review = self._pain_review_with_current_items(
                    raw_pain, pain_review, pain_item_reviews,
                )
                summary_reviews = ((pain_review.get("payload") or {}).get("item_decisions") or [])
            pain_review_summary = self._pain_review_summary(raw_pain, summary_reviews)
            effective_pain = self._without_bbox(self._effective_pain_result(raw_pain, pain_review))
            effective_care = self._without_bbox(self._effective_care_result(raw_care, care_review))
            if effective_care and care_review and care_review.get("decision") == "APPROVED":
                from .care_engine import diagnosis_names, finalize_care_guide
                from .care_knowledge import match_knowledge_card
                from .care_models import CareGuideDraft, CareGuideReport
                from .models import ClaimCase
                model_case = ClaimCase.model_validate(effective_structured)
                original = CareGuideReport.model_validate(care)
                effective_care = finalize_care_guide(
                    model_case, CareGuideDraft.model_validate(effective_care["guide"]),
                    match_knowledge_card(diagnosis_names(model_case), None),
                    original.generation_method,
                ).model_dump(mode="json")
            hospitalization = (effective_structured or {}).get("hospitalization") or {}
            return {
                "case_id": row["case_id"], "claim_event_id": row["claim_event_id"],
                "customer_display_name": self._mask_customer_label(row["customer_display_name"]),
                "data_source": row["data_source"], "care_status": row["care_status"],
                "compliance_ready": (row["consent_status"] == "authorized"
                                     and row["deidentification_status"] == "confirmed"),
                "review_status": self._medical_review_status(latest_decision, row["care_status"]),
                "primary_diagnosis": self._primary_diagnosis(effective_structured),
                "admission_date": (hospitalization.get("admission_date") or {}).get("value"),
                "discharge_date": (hospitalization.get("discharge_date") or {}).get("value"),
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "pending_confirmation_count": len(pending_fields) + sum(
                    item.get("decision") == "UNRESOLVED" for item in field_reviews
                ),
                "medical_attention_count": self._medical_pending_count(
                    structured, pain, care, {item["field_path"] for item in field_reviews},
                    pain_item_reviews),
                "ocr_documents": ocr,
                # Compatibility alias contains the same sanitized view and never
                # exposes private filenames, filesystem paths, or OCR boxes.
                "ocr_result": ocr,
                "structured_result": structured, "pain_result": pain,
                "structured_effective_result": self._without_bbox(effective_structured),
                "structured_fingerprint": structured_fingerprint,
                "pending_field_reviews": self._without_bbox(pending_fields),
                "field_reviews": self._without_bbox(field_reviews),
                "field_review_summary": {
                    "pending": len(pending_fields),
                    "confirmed": sum(item.get("decision") == "CONFIRMED" for item in field_reviews),
                    "corrected": sum(item.get("decision") == "CORRECTED" for item in field_reviews),
                    "unresolved": sum(item.get("decision") == "UNRESOLVED" for item in field_reviews),
                },
                "pain_item_reviews": self._without_bbox(pain_item_reviews),
                "pain_suggested_dispositions": pain_suggestions,
                "pain_review_summary": pain_review_summary,
                "care_revisions": self._without_bbox(care_revisions),
                "pain_effective_result": effective_pain,
                "care_result": care, "care_effective_result": effective_care,
                "pain_item_decisions": ((pain_review or {}).get("payload") or {}).get("item_decisions", []),
                "care_revision": ((care_review or {}).get("payload") or {}).get("care_revision"),
                "care_evidence_options": self._care_evidence_options(effective_structured),
                "quality": {
                    "case_observations": observations,
                    "historical_profiles": historical_profiles,
                    "single_case_notice": "单病例观察，不代表该医院整体医疗质量。",
                    "summary": quality_summary,
                },
                "recommendation": recommendation, "reviews": reviews,
                "module_states": module_states,
                "medical_contract_enforced": medical_contract_enforced,
            }

    @classmethod
    def medical_final_gate(cls, detail):
        """Evaluate the frozen MedicalReviewV1 publication matrix."""
        from .contracts import MedicalFinalGateV1
        if not detail.get("medical_contract_enforced"):
            legacy_matrix = {
                "case_id": detail.get("case_id"), "mode": "legacy-grandfathered",
                "structured_fingerprint": detail.get("structured_fingerprint"),
            }
            return MedicalFinalGateV1(
                decision="READY_FOR_FINAL_REVIEW",
                reasons=["历史案件沿用原发布安全检查；重新生成任一医疗模块后将启用 MedicalReviewV1"],
                required_modules=[], approved_modules=[],
                content_fingerprint=cls._content_fingerprint(legacy_matrix),
            )
        reasons = []
        blocked = False
        if not detail.get("compliance_ready"):
            reasons.append("案件授权或脱敏状态未通过")
            blocked = True
        field_summary = detail.get("field_review_summary") or {}
        if field_summary.get("pending"):
            reasons.append(f"仍有 {field_summary['pending']} 个结构化字段待确认")
        if field_summary.get("unresolved"):
            reasons.append(f"仍有 {field_summary['unresolved']} 个结构化字段无法确认")
        pain_summary = detail.get("pain_review_summary") or {}
        if pain_summary.get("pending"):
            reasons.append(f"仍有 {pain_summary['pending']} 条痛点未完成 SHOW / EDIT / HIDE 决策")
        states = detail.get("module_states") or {}
        required = ["pain", "care"]
        for optional in ("treatment_quality", "recommendation"):
            if (states.get(optional) or {}).get("result_available"):
                required.append(optional)
        approved = []
        for module in required:
            state = states.get(module) or {}
            generation = state.get("generation_status")
            review = state.get("review_status")
            if generation != "GENERATED":
                reasons.append(f"{module} 模块不是当前可发布版本：{generation or 'NOT_GENERATED'}")
            elif review != "APPROVED":
                reasons.append(f"{module} 模块尚未审核通过：{review or 'NOT_REVIEWED'}")
            else:
                approved.append(module)
        blocked_checks = [
            item for item in (detail.get("care_result") or {}).get("safety_checks", [])
            if item.get("status") == "blocked"
        ]
        if blocked_checks:
            blocked = True
            reasons.extend("护理安全检查阻断：" + (item.get("message") or item.get("code") or "未知")
                           for item in blocked_checks)
        matrix = {
            "case_id": detail.get("case_id"), "compliance_ready": detail.get("compliance_ready"),
            "field_review_summary": field_summary, "pain_review_summary": pain_summary,
            "required_modules": required,
            "module_states": {name: states.get(name) for name in required},
        }
        return MedicalFinalGateV1(
            decision="BLOCKED" if blocked else "NOT_READY" if reasons else "READY_FOR_FINAL_REVIEW",
            reasons=list(dict.fromkeys(reasons)) or ["字段、痛点、护理和当前展示模块已全部复核通过"],
            required_modules=required, approved_modules=approved,
            content_fingerprint=cls._content_fingerprint(matrix),
        )

    def save_field_review(self, user_id, case_id, request):
        """Persist a field decision without overwriting the model extraction."""
        from .review_models import FieldReviewDecision, FieldReviewRecord
        if not self.user_can_access_case(user_id, case_id, "MEDICAL_CASE_VIEW"):
            raise CaseNotFound(case_id)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, case_id)
            structured = json.loads(row["structured_result_json"]) if row["structured_result_json"] else None
            if not structured:
                raise WorkflowConflict("结构化病例尚未生成")
            _raw_fp, _pending_before, _reviews_before, effective_before = \
                self._field_review_state(db, case_id, structured)
            old_effective_fingerprint = self._structured_fingerprint(effective_before)
            field = self._field_at_path(structured, request.field_path)
            old_effective_field = self._field_at_path(effective_before, request.field_path)
            model_value = field.get("normalized_value") \
                if field.get("normalized_value") is not None else field.get("value")
            old_effective_value = old_effective_field.get("normalized_value") \
                if old_effective_field.get("normalized_value") is not None \
                else old_effective_field.get("value")
            reason = request.reason.strip()
            if request.decision == FieldReviewDecision.CORRECTED:
                if request.reviewed_value is None or (
                    isinstance(request.reviewed_value, str) and not request.reviewed_value.strip()
                ):
                    raise WorkflowConflict("修改并确认时必须填写人工确认值")
                final_value = request.reviewed_value.strip() if isinstance(request.reviewed_value, str) else request.reviewed_value
            elif request.decision == FieldReviewDecision.CONFIRMED:
                final_value = model_value
            else:
                if not reason:
                    raise WorkflowConflict("暂无法确认时必须填写原因")
                final_value = None
            reviewer = db.execute("SELECT display_name FROM users WHERE user_id=?", (user_id,)).fetchone()
            if reviewer is None:
                raise WorkflowConflict("当前复核用户不存在")
            now = datetime.now(timezone.utc).isoformat()
            fingerprint = self._structured_fingerprint(structured)
            record = FieldReviewRecord(
                **request.model_dump(), review_id=str(uuid4()), case_id=case_id,
                field_label=self._field_label(request.field_path),
                model_value=model_value, final_value=final_value, reviewer_user_id=user_id,
                reviewer_name=reviewer["display_name"], reviewed_at=now,
                content_fingerprint=fingerprint,
                source_references=deepcopy(field.get("evidence") or []),
            )
            target = f"structured_field:{request.field_path}"
            db.execute(
                "INSERT INTO reviews(review_id, case_id, content_fingerprint, target_type, decision, "
                "record_json, created_at, comment, reviewer_user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record.review_id, case_id, fingerprint, target, record.decision.value,
                 encode(record), now, reason, user_id),
            )
            _raw_fp, _pending_after, _reviews_after, effective_after = \
                self._field_review_state(db, case_id, structured)
            new_effective_fingerprint = self._structured_fingerprint(effective_after)
            from .medical_orchestration import MedicalModule, modules_affected_by
            # Compare against the value that downstream modules actually used.
            # A second correction can differ from the previous human correction
            # even when both differ from the original model value.
            changed = request.decision == FieldReviewDecision.CORRECTED \
                and final_value != old_effective_value
            affected = modules_affected_by(request.field_path) if changed else set()
            for module in MedicalModule:
                if module in affected:
                    continue
                latest = self._latest_module_fingerprint(db, case_id, module.value)
                if latest == old_effective_fingerprint:
                    self._record_module_state(
                        db, case_id, module.value, new_effective_fingerprint,
                        user_id=user_id, action="MEDICAL_MODULE_REBASED",
                    )
            return record

    def get_medical_document_resource(self, user_id, case_id, resource_id):
        """Resolve only a resource attached to the current case's saved OCR payload."""
        if not self.user_can_access_case(user_id, case_id, "MEDICAL_CASE_VIEW"):
            raise CaseNotFound(case_id)
        if not re.fullmatch(r"[a-f0-9]{32}", resource_id or ""):
            raise WorkflowItemNotFound("医疗材料不存在")
        with self._connect() as db:
            row = self._row(db, case_id)
            documents = json.loads(row["ocr_result_json"]) if row["ocr_result_json"] else []
            document = next((item for item in documents if item.get("resource_id") == resource_id), None)
            if not document or not document.get("stored_filename"):
                raise WorkflowItemNotFound("医疗材料不存在")
            return {
                "resource_id": resource_id,
                "stored_filename": document["stored_filename"],
                "media_type": document.get("media_type") or "application/octet-stream",
                "document_id": document.get("document_id") or "医疗材料",
            }

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
            for kind, stage in (("structured", WorkflowStatus.STRUCTURED),
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

    def _require_current_effective(self, db, case_id, case, pain=None):
        """Accept the current field-review overlay for downstream profile wiring."""
        row = self._row(db, case_id)
        if row["status"] == WorkflowStatus.PROCESSING.value:
            raise WorkflowConflict("案件正在重新结构化")
        structured = json.loads(row["structured_result_json"]) if row["structured_result_json"] else None
        _fingerprint, _pending, _reviews, effective = self._field_review_state(
            db, case_id, structured,
        )
        if encode(effective) != encode(case):
            raise WorkflowConflict("请求病例不是当前有效结构化结果，请重新读取案件")
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
                                   status="DRAFT", *, effective_fingerprint=None,
                                   user_id=None):
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

        generation_id = None
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
            if effective_fingerprint:
                generation_id = self._record_module_state(
                    db, case_id, "recommendation", effective_fingerprint, user_id=user_id,
                )
        return {
            "report_id": report_id, "report_version": version,
            "status": status, "generation_id": generation_id,
        }

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
            self._require_current_effective(db, case_id, case, pain)
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
                self._require_current_effective(
                    db, payload.case.case_id, payload.case, payload.pain_report,
                )
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
        structured = json.loads(row["structured_result_json"]) if row["structured_result_json"] else None
        _fingerprint, _pending, _reviews, effective_structured = \
            self._field_review_state(db, case_id, structured)
        pain = json.loads(row["pain_result_json"]) if row["pain_result_json"] else None
        care = json.loads(row["care_result_json"]) if row["care_result_json"] else None
        pain_review = self._latest_module_review(db, case_id, "pain", pain)
        care_review = self._latest_module_review(db, case_id, "care", care)
        pain_review = self._pain_review_with_current_items(
            pain, pain_review, self._latest_pain_item_reviews(db, case_id, pain),
        )
        effective_pain = self._effective_pain_result(
            pain, pain_review if (pain_review or {}).get("decision") == "APPROVED" else None,
        )
        effective_care = self._effective_care_result(
            care, care_review if (care_review or {}).get("decision") == "APPROVED" else None,
        )
        if effective_care and care_review and care_review.get("decision") == "APPROVED":
            from .care_engine import diagnosis_names, finalize_care_guide
            from .care_knowledge import match_knowledge_card
            from .care_models import CareGuideDraft, CareGuideReport
            from .models import ClaimCase
            original = CareGuideReport.model_validate(care)
            model_case = ClaimCase.model_validate(effective_structured)
            card = match_knowledge_card(diagnosis_names(model_case), None)
            effective_care = finalize_care_guide(
                model_case, CareGuideDraft.model_validate(effective_care["guide"]), card,
                original.generation_method,
            ).model_dump(mode="json")
        return {"case_id": case_id, "consent_status": row["consent_status"],
                "deidentification_status": row["deidentification_status"], "data_source": row["data_source"],
                "portfolio": json.loads(portfolio[0]) if portfolio else None,
                "profile_context": json.loads(context[0]) if context else None,
                "results": {
                    "ocr_result": json.loads(row["ocr_result_json"]) if row["ocr_result_json"] else None,
                    "structured_result": effective_structured,
                    "pain_result": effective_pain,
                    "care_result": effective_care,
                }}

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
            combined = next((r for r in reviews if r["target_type"] == "combined_delivery"), None)
            if combined and combined.get("decision") == "REJECTED":
                return {"can_send": False, "review_status": "REJECTED", "reviews": reviews}
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
        latest = db.execute(
            "SELECT status FROM deliveries WHERE case_id=? "
            "ORDER BY COALESCE(sent_at, created_at) DESC LIMIT 1", (case_id,),
        ).fetchone()
        if latest and latest["status"] == "PUBLISHED":
            return WorkflowStatus.PUBLISHED
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
            post_states = {"PUBLISHED", "DELIVERED_MOCK", "FEEDBACK_RECEIVED", "FOLLOWUP_PENDING", "RESOLVED"}
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

    def save_review(self, prepared, request, reviewer_user_id=None,
                    allow_low_risk_combined=False):
        from .delivery_gate import GateDecision
        from .review_models import ReviewRecord
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._verify_delivery_snapshot(db, prepared)
            if request.content_fingerprint != prepared["content_fingerprint"]:
                raise WorkflowConflict("审核页面内容已过时，请重新准备并核对当前内容")
            if prepared["gate"].decision == GateDecision.BLOCKED:
                raise WorkflowConflict("BLOCKED 内容不能由批准绕过")
            if prepared["gate"].decision != GateDecision.REVIEW_REQUIRED:
                if not (allow_low_risk_combined
                        and request.target_type.value == "combined_delivery"):
                    raise WorkflowConflict("低风险内容无需人工批准")
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
                "record_json, created_at, comment, reviewer_user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record.review_id, record.case_id, record.content_fingerprint, record.target_type.value,
                 record.decision.value, encode(record), record.created_at, record.note, reviewer_user_id),
            )
            authorization = self._delivery_authorization(db, prepared)
            status = WorkflowStatus.REJECTED if authorization["review_status"] == "REJECTED" else \
                     WorkflowStatus.APPROVED if authorization["can_send"] else WorkflowStatus.REVIEW_REQUIRED
            self._status(db, record.case_id, status)
            if record.decision.value == "APPROVED" and record.target_type.value in {
                "recommendation", "combined_delivery"
            }:
                db.execute(
                    "UPDATE recommendation_reports SET status='REVIEWED', updated_at=? "
                    "WHERE case_id=? AND status='DRAFT'",
                    (record.created_at, record.case_id),
                )
            return record

    def list_reviews(self, case_id):
        with self._connect() as db:
            self._row(db, case_id)
            return [json.loads(row[0]) for row in db.execute("SELECT record_json FROM reviews WHERE case_id=? ORDER BY rowid DESC", (case_id,))]

    def send_delivery(self, prepared, *, publish=False):
        from .delivery import DeliveryRecord, DeliveryMode
        from .delivery_gate import GateDecision
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._verify_delivery_snapshot(db, prepared)
            row = self._row(db, prepared["case_id"])
            if row["status"] == "PROCESSING":
                raise WorkflowConflict("案件正在处理，不能触达旧内容")
            customer_link = db.execute(
                "SELECT 1 FROM case_participants WHERE case_id=? AND user_id=? "
                "AND case_role='CUSTOMER' AND status='ACTIVE'",
                (prepared["case_id"], row["customer_user_id"]),
            ).fetchone() if row["customer_user_id"] else None
            if customer_link is None:
                raise WorkflowConflict("案件尚未绑定有效客户，不能发送关怀结果")
            authorization = self._delivery_authorization(db, prepared)
            if not authorization["can_send"]:
                raise WorkflowConflict("拒绝触达：" + authorization["review_status"])
            existing = db.execute("SELECT record_json FROM deliveries WHERE case_id=? AND content_fingerprint=?",
                                  (prepared["case_id"], prepared["content_fingerprint"])).fetchone()
            if existing:
                if row["status"] not in {"PUBLISHED", "DELIVERED_MOCK", "FEEDBACK_RECEIVED", "FOLLOWUP_PENDING", "RESOLVED"}:
                    self._status(db, prepared["case_id"], self._delivered_case_status(db, prepared["case_id"]))
                return DeliveryRecord.model_validate_json(existing[0])
            mode = DeliveryMode.AUTO if prepared["gate"].decision == GateDecision.AUTO_DELIVER else DeliveryMode.HUMAN_APPROVED
            record = DeliveryRecord(delivery_id=str(uuid4()), case_id=prepared["case_id"],
                channel="customer_portal" if publish else "mock_web_inbox",
                status="PUBLISHED" if publish else "DELIVERED_MOCK", delivery_mode=mode,
                recommendation_included=bool(prepared["content"]["recommendation"]), risk_gate_result=prepared["gate"],
                content_fingerprint=prepared["content_fingerprint"], created_at=datetime.now(timezone.utc).isoformat(),
                content=prepared["content"])
            db.execute(
                "INSERT INTO deliveries(delivery_id, case_id, content_fingerprint, record_json, created_at, "
                "channel, status, sent_at, customer_user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record.delivery_id, record.case_id, record.content_fingerprint, encode(record), record.created_at,
                  "PORTAL" if publish else "MOCK", record.status, record.created_at, row["customer_user_id"]),
            )
            has_open_tasks = db.execute("SELECT 1 FROM follow_up_tasks WHERE case_id=? AND status='OPEN'", (record.case_id,)).fetchone()
            delivered_status = WorkflowStatus.PUBLISHED if publish else WorkflowStatus.DELIVERED_MOCK
            self._status(db, record.case_id, WorkflowStatus.FOLLOWUP_PENDING if has_open_tasks else delivered_status)
            return record

    def publish_delivery(self, prepared):
        """Persist the final-approved customer portal result without a separate send step."""
        return self.send_delivery(prepared, publish=True)

    def list_deliveries(self, case_id):
        with self._connect() as db:
            self._row(db, case_id)
            return [json.loads(row[0]) for row in db.execute("SELECT record_json FROM deliveries WHERE case_id=? ORDER BY rowid DESC", (case_id,))]

    def customer_attention_items(self, case_id):
        """Build customer-safe attention items only from current medical decisions."""
        with self._connect() as db:
            row = self._row(db, case_id)
            pain = json.loads(row["pain_result_json"]) if row["pain_result_json"] else None
            review = self._latest_module_review(db, case_id, "pain", pain)
            if (review or {}).get("decision") != "APPROVED":
                return []
            review = self._pain_review_with_current_items(
                pain, review, self._latest_pain_item_reviews(db, case_id, pain),
            )
            effective = self._effective_pain_result(pain, review)
            output = []
            for point in (effective or {}).get("pain_points", []):
                output.append({
                    "pain_point_id": point["pain_point_id"],
                    "title": point.get("title") or "需要关注",
                    "summary": point.get("description") or "请结合本次就诊材料与医生确认。",
                    "suggestion": point.get("recommended_action") or "请在下次就诊时向医生确认。",
                    "severity": point.get("severity") or "low",
                })
            return output

    def _delivery_row(self, db, delivery_id):
        row = db.execute("SELECT record_json FROM deliveries WHERE delivery_id=?", (delivery_id,)).fetchone()
        if row is None:
            raise WorkflowItemNotFound("delivery 不存在")
        return json.loads(row[0])

    def get_delivery(self, delivery_id):
        with self._connect() as db:
            return self._delivery_row(db, delivery_id)

    def save_feedback(self, delivery_id, request):
        from .feedback_models import FeedbackRecord, FeedbackTarget, FeedbackCategory, FollowUpTask
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            delivery = self._delivery_row(db, delivery_id)
            if request.target_type == FeedbackTarget.RECOMMENDATION and not delivery["recommendation_included"]:
                raise WorkflowConflict("本次触达未包含推荐，不能提交推荐内容评分")
            now = datetime.now(timezone.utc).isoformat()
            feedback = FeedbackRecord(**request.model_dump(), feedback_id=str(uuid4()), delivery_id=delivery_id,
                                      case_id=delivery["case_id"], created_at=now)
            db.execute(
                "INSERT INTO feedback(feedback_id, delivery_id, case_id, record_json, created_at, "
                "target_type, rating, helpful, category, comment, request_contact) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (feedback.feedback_id, delivery_id, feedback.case_id, encode(feedback), now,
                 feedback.target_type.value, feedback.rating, int(feedback.helpful),
                 feedback.category.value, feedback.comment,
                 int(feedback.category.value != "clear" or not feedback.helpful)),
            )
            needs_followup = (
                request.rating <= 3 or not request.helpful
                or request.category != FeedbackCategory.CLEAR
                or request.consent_to_contact
            )
            task = None
            service_route = None
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
                service = db.execute(
                    "SELECT u.user_id, u.display_name FROM case_participants cp "
                    "JOIN users u ON u.user_id=cp.user_id JOIN roles r ON r.role_id=u.role_id "
                    "WHERE cp.case_id=? AND cp.case_role='SERVICE_AGENT' AND cp.status='ACTIVE' "
                    "AND u.status='ACTIVE' AND r.role_code='SERVICE' "
                    "ORDER BY cp.assigned_at LIMIT 1",
                    (feedback.case_id,),
                ).fetchone()
                if service is None:
                    service = db.execute(
                        "SELECT u.user_id, u.display_name FROM users u JOIN roles r ON r.role_id=u.role_id "
                        "LEFT JOIN case_participants cp ON cp.user_id=u.user_id "
                        "AND cp.case_role='SERVICE_AGENT' AND cp.status='ACTIVE' "
                        "WHERE u.status='ACTIVE' AND r.role_code='SERVICE' "
                        "GROUP BY u.user_id ORDER BY COUNT(cp.case_id), u.user_id LIMIT 1"
                    ).fetchone()
                    if service is not None:
                        db.execute(
                            "INSERT INTO case_participants(case_id, user_id, case_role, status, assigned_at) "
                            "VALUES (?, ?, 'SERVICE_AGENT', 'ACTIVE', ?) "
                            "ON CONFLICT(case_id, user_id, case_role) DO UPDATE SET "
                            "status='ACTIVE', assigned_at=excluded.assigned_at, unassigned_at=NULL",
                            (feedback.case_id, service["user_id"], now),
                        )
                if service is not None:
                    service_route = {
                        "user_id": service["user_id"], "display_name": service["display_name"],
                    }
            if self._row(db, feedback.case_id)["status"] in {"PUBLISHED", "DELIVERED_MOCK", "FEEDBACK_RECEIVED", "FOLLOWUP_PENDING", "RESOLVED"}:
                self._status(db, feedback.case_id, WorkflowStatus.FEEDBACK_RECEIVED)
                if db.execute("SELECT 1 FROM follow_up_tasks WHERE case_id=? AND status='OPEN'", (feedback.case_id,)).fetchone():
                    self._status(db, feedback.case_id, WorkflowStatus.FOLLOWUP_PENDING)
            return {
                "feedback": feedback.model_dump(mode="json"),
                "follow_up": task.model_dump(mode="json") if task else None,
                "service_route": service_route,
            }

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
            if self._row(db, task.case_id)["status"] in {"PUBLISHED", "DELIVERED_MOCK", "FEEDBACK_RECEIVED", "FOLLOWUP_PENDING", "RESOLVED"}:
                open_task = db.execute("SELECT 1 FROM follow_up_tasks WHERE case_id=? AND status='OPEN'", (task.case_id,)).fetchone()
                self._status(db, task.case_id, WorkflowStatus.FOLLOWUP_PENDING if open_task else WorkflowStatus.RESOLVED)
            return task

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

    def assign_case_customer(self, case_id, customer_username):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            case = self._row(db, case_id)
            customer = db.execute(
                "SELECT u.user_id, u.username, u.display_name FROM users u "
                "JOIN roles r ON r.role_id=u.role_id "
                "WHERE u.username=? AND u.status='ACTIVE' AND r.role_code='CUSTOMER'",
                (customer_username,),
            ).fetchone()
            if customer is None:
                raise WorkflowItemNotFound("客户账号不存在或不可用")
            if case["customer_user_id"] not in (None, customer["user_id"]):
                if db.execute("SELECT 1 FROM deliveries WHERE case_id=? LIMIT 1", (case_id,)).fetchone():
                    raise WorkflowConflict("案件已向客户送达结果，不能更换绑定客户")
            db.execute(
                "UPDATE case_participants SET status='CLOSED', unassigned_at=? "
                "WHERE case_id=? AND case_role='CUSTOMER' AND user_id<>? AND status='ACTIVE'",
                (now, case_id, customer["user_id"]),
            )
            db.execute(
                "INSERT INTO case_participants(case_id, user_id, case_role, status, assigned_at) "
                "VALUES (?, ?, 'CUSTOMER', 'ACTIVE', ?) "
                "ON CONFLICT(case_id, user_id, case_role) DO UPDATE SET "
                "status='ACTIVE', assigned_at=excluded.assigned_at, unassigned_at=NULL",
                (case_id, customer["user_id"], now),
            )
            db.execute(
                "UPDATE cases SET customer_user_id=?, updated_at=? WHERE case_id=?",
                (customer["user_id"], now, case_id),
            )
            return {
                "user_id": customer["user_id"],
                "username": customer["username"],
                "display_name": self._mask_customer_label(customer["display_name"]),
            }

    def accept_claim_case(self, user_id, case_id):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT c.claim_status, c.customer_user_id FROM cases c "
                "JOIN case_participants cp ON cp.case_id=c.case_id "
                "WHERE c.case_id=? AND cp.user_id=? AND cp.case_role='CLAIM_OWNER' "
                "AND cp.status='ACTIVE'",
                (case_id, user_id),
            ).fetchone()
            if row is None:
                raise CaseNotFound(case_id)
            if row["customer_user_id"] is None:
                raise WorkflowConflict("请先绑定客户，再受理案件")
            if row["claim_status"] == "PENDING_ACCEPTANCE":
                db.execute(
                    "UPDATE cases SET claim_status='ACCEPTED', updated_at=? WHERE case_id=?",
                    (now, case_id),
                )
            elif row["claim_status"] != "ACCEPTED":
                raise WorkflowConflict("当前理赔状态不能执行受理")
            return {"case_id": case_id, "claim_status": "ACCEPTED", "message": "案件已受理"}

    def update_claim_compliance(
        self, user_id, case_id, *, consent_status, deidentification_status,
    ):
        if consent_status not in {"pending", "authorized"}:
            raise WorkflowConflict("不支持的授权状态")
        if deidentification_status not in {"pending", "confirmed"}:
            raise WorkflowConflict("不支持的脱敏状态")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT c.claim_status FROM cases c JOIN case_participants cp ON cp.case_id=c.case_id "
                "WHERE c.case_id=? AND cp.user_id=? AND cp.case_role='CLAIM_OWNER' "
                "AND cp.status='ACTIVE'",
                (case_id, user_id),
            ).fetchone()
            if row is None:
                raise CaseNotFound(case_id)
            if row["claim_status"] == "PENDING_ACCEPTANCE":
                raise WorkflowConflict("请先受理案件，再补录授权与脱敏状态")
            db.execute(
                "UPDATE cases SET consent_status=?, deidentification_status=?, updated_at=? "
                "WHERE case_id=?",
                (consent_status, deidentification_status, now, case_id),
            )
            return {
                "case_id": case_id,
                "consent_status": consent_status,
                "deidentification_status": deidentification_status,
                "message": "授权与脱敏状态已更新",
            }

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
            needs_followup = (
                request.rating <= 3 or not request.helpful
                or request.category != FeedbackCategory.CLEAR
                or request.consent_to_contact
            )
            task = None
            service_route = None
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
                service = db.execute(
                    "SELECT u.user_id, u.display_name FROM case_participants cp "
                    "JOIN users u ON u.user_id=cp.user_id JOIN roles r ON r.role_id=u.role_id "
                    "WHERE cp.case_id=? AND cp.case_role='SERVICE_AGENT' AND cp.status='ACTIVE' "
                    "AND u.status='ACTIVE' AND r.role_code='SERVICE' "
                    "ORDER BY cp.assigned_at LIMIT 1",
                    (feedback.case_id,),
                ).fetchone()
                if service is None:
                    service = db.execute(
                        "SELECT u.user_id, u.display_name FROM users u JOIN roles r ON r.role_id=u.role_id "
                        "LEFT JOIN case_participants cp ON cp.user_id=u.user_id "
                        "AND cp.case_role='SERVICE_AGENT' AND cp.status='ACTIVE' "
                        "WHERE u.status='ACTIVE' AND r.role_code='SERVICE' "
                        "GROUP BY u.user_id ORDER BY COUNT(cp.case_id), u.user_id LIMIT 1"
                    ).fetchone()
                    if service is not None:
                        db.execute(
                            "INSERT INTO case_participants(case_id, user_id, case_role, status, assigned_at) "
                            "VALUES (?, ?, 'SERVICE_AGENT', 'ACTIVE', ?) "
                            "ON CONFLICT(case_id, user_id, case_role) DO UPDATE SET "
                            "status='ACTIVE', assigned_at=excluded.assigned_at, unassigned_at=NULL",
                            (feedback.case_id, service["user_id"], now),
                        )
                if service is not None:
                    service_route = {
                        "user_id": service["user_id"], "display_name": service["display_name"],
                    }
            if self._row(db, feedback.case_id)["status"] in {"PUBLISHED", "DELIVERED_MOCK", "FEEDBACK_RECEIVED", "FOLLOWUP_PENDING", "RESOLVED"}:
                self._status(db, feedback.case_id, WorkflowStatus.FEEDBACK_RECEIVED)
                if db.execute("SELECT 1 FROM follow_up_tasks WHERE case_id=? AND status='OPEN'", (feedback.case_id,)).fetchone():
                    self._status(db, feedback.case_id, WorkflowStatus.FOLLOWUP_PENDING)
            return {
                "feedback": feedback.model_dump(mode="json"),
                "follow_up": task.model_dump(mode="json") if task else None,
                "service_route": service_route,
            }

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
            if self._row(db, task.case_id)["status"] in {"PUBLISHED", "DELIVERED_MOCK", "FEEDBACK_RECEIVED", "FOLLOWUP_PENDING", "RESOLVED"}:
                open_task = db.execute(
                    "SELECT 1 FROM follow_up_tasks WHERE case_id=? AND status IN ('OPEN','PROCESSING')",
                    (task.case_id,),
                ).fetchone()
                self._status(db, task.case_id, WorkflowStatus.FOLLOWUP_PENDING if open_task else WorkflowStatus.RESOLVED)
            return task

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
    def _customer_care_payload(care):
        if not isinstance(care, dict) or not isinstance(care.get("guide"), dict):
            return None
        guide = care["guide"]

        def section(value):
            if not isinstance(value, dict) or not value.get("content"):
                return None
            return {"title": value.get("title") or "关怀说明", "content": value["content"]}

        def actions(values):
            return [
                {
                    "action": value.get("action"),
                    "timing": value.get("timing") or "遵医嘱",
                    "reason": value.get("reason"),
                }
                for value in (values or [])
                if isinstance(value, dict) and value.get("action") and value.get("reason")
            ]

        return {"guide": {
            "disease_topic": guide.get("disease_topic"),
            "opening": section(guide.get("opening")),
            "diagnosis_explanation": section(guide.get("diagnosis_explanation")),
            "treatment_review": section(guide.get("treatment_review")),
            "cost_explanation": section(guide.get("cost_explanation")),
            "recovery_plan": actions(guide.get("recovery_plan")),
            "psychological_support": section(guide.get("psychological_support")),
            "follow_up_plan": actions(guide.get("follow_up_plan")),
            "red_flags": [str(value) for value in (guide.get("red_flags") or []) if value],
            "limitations": [str(value) for value in (guide.get("limitations") or []) if value],
        }}

    @staticmethod
    def _customer_recommendation_payload(reports):
        type_names = {
            "institution": "HOSPITAL", "clinician": "DOCTOR", "treatment_plan": "TREATMENT",
            "HOSPITAL": "HOSPITAL", "DOCTOR": "DOCTOR", "TREATMENT": "TREATMENT",
        }
        safe_reports = []
        for report in reports or []:
            if not isinstance(report, dict):
                continue
            report_type = type_names.get(report.get("entity_type"), report.get("entity_type"))
            safe_items = []
            for item in report.get("recommendations") or []:
                if not isinstance(item, dict):
                    continue
                entity_name = str(item.get("entity_name") or "").strip()
                internal_name = (
                    not entity_name or entity_name.lower() == "unavailable"
                    or entity_name.lower().startswith("pending-")
                    or re.fullmatch(r"(?:HOSP|DOC|TREAT)-[A-Z0-9_-]+", entity_name, re.IGNORECASE)
                )
                if internal_name:
                    continue
                item_type = type_names.get(item.get("item_type"), item.get("item_type") or report_type)
                if item_type not in {"HOSPITAL", "DOCTOR", "TREATMENT"}:
                    continue
                reasons = []
                for reason in item.get("reasons") or []:
                    if not isinstance(reason, dict):
                        continue
                    title = str(reason.get("title") or "推荐理由").strip()
                    explanation = str(reason.get("explanation") or "").strip()
                    if title or explanation:
                        reasons.append({"title": title, "explanation": explanation})
                if not reasons:
                    for score in item.get("scores") or []:
                        if not isinstance(score, dict) or not score.get("reason"):
                            continue
                        reasons.append({
                            "title": str(score.get("dimension_name") or "推荐理由"),
                            "explanation": str(score["reason"]),
                        })
                sources = []
                for evidence in item.get("evidence") or []:
                    if not isinstance(evidence, dict):
                        continue
                    name = str(evidence.get("source_name") or "").strip()
                    summary = str(evidence.get("evidence_summary") or evidence.get("reason") or "").strip()
                    url = str(evidence.get("source_url") or "").strip()
                    if url and not url.lower().startswith(("https://", "http://")):
                        url = ""
                    if name or summary:
                        sources.append({"name": name or None, "url": url or None, "summary": summary})
                limitations = [
                    str(value) for value in (item.get("limitations") or [])
                    if value is not None and str(value).strip()
                ]
                safe_item = {
                    "item_type": item_type,
                    "entity_name": entity_name,
                    "city": item.get("city"),
                    "department": item.get("department"),
                    "reasons": reasons,
                    "limitations": limitations,
                    "sources": sources,
                }
                safe_items.append(safe_item)
            if safe_items:
                safe_reports.append({"recommendations": safe_items})
        return safe_reports

    @staticmethod
    def _customer_delivery_payload(delivery):
        from .contracts import CustomerDeliveryV1
        content = delivery.get("content") or {}
        recommendations = CaseStorage._customer_recommendation_payload(content.get("recommendation"))
        payload = {
            "schema_version": "customer-delivery-v1",
            "delivery_id": delivery.get("delivery_id"),
            "case_id": delivery.get("case_id"),
            "created_at": delivery.get("created_at"),
            "status": delivery.get("status"),
            "recommendation_included": bool(recommendations),
            "content": {
                "care_guide": CaseStorage._customer_care_payload(content.get("care_guide")),
                "attention_items": content.get("attention_items") or [],
                "quality_observation": content.get("quality_observation"),
                "recommendation": recommendations,
                "recommendation_unavailable_reason": content.get("recommendation_unavailable_reason"),
            },
        }
        return CustomerDeliveryV1.model_validate(payload).model_dump(mode="json")

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
                   a.recommended_action, a.review_status, a.route_target_role,
                   a.route_note, a.routed_at, a.created_at,
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

    def route_service_consultation(
        self, user_id, analysis_id, *, target_role, category, priority="MEDIUM", note="",
    ):
        from .contracts import CaseTaskV1

        item = self.get_service_consultation(user_id, analysis_id)
        allowed_categories = {
            "CLAIM": {"claim_progress", "claim_material", "coverage_rule", "claim_cost"},
            "MEDICAL": {
                "medical_cost", "recovery_question", "medication_question",
                "recommendation_question",
            },
            "SERVICE": {"value_added_service", "human_service_request", "other"},
        }
        if target_role not in allowed_categories:
            raise WorkflowConflict("不支持的分流目标")
        if category not in allowed_categories[target_role]:
            raise WorkflowConflict("诉求类型与分流目标不匹配")
        if priority not in {"LOW", "MEDIUM", "HIGH", "URGENT"}:
            raise WorkflowConflict("不支持的任务优先级")
        note = note.strip()
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT review_status, route_target_role FROM service_analysis_results "
                "WHERE analysis_id=?", (analysis_id,),
            ).fetchone()
            if current is None:
                raise WorkflowItemNotFound("咨询记录不存在或当前账号无权访问")
            if current["review_status"] != "PENDING":
                if current["route_target_role"] == target_role:
                    return item
                raise WorkflowConflict("该咨询已经完成分流，不能重复改派")

            assignee_user_id = None
            review_status = "SERVICE_HANDLING"
            if target_role in {"CLAIM", "MEDICAL"}:
                participant_role = "CLAIM_OWNER" if target_role == "CLAIM" else "MEDICAL_REVIEWER"
                assignee = db.execute(
                    "SELECT u.user_id FROM case_participants cp "
                    "JOIN users u ON u.user_id=cp.user_id "
                    "JOIN roles r ON r.role_id=u.role_id "
                    "WHERE cp.case_id=? AND cp.case_role=? AND cp.status='ACTIVE' "
                    "AND u.status='ACTIVE' AND r.role_code=? "
                    "ORDER BY cp.assigned_at DESC, u.user_id LIMIT 1",
                    (item["case_id"], participant_role, target_role),
                ).fetchone()
                if assignee is None:
                    role_name = "理赔负责人" if target_role == "CLAIM" else "医疗复核人员"
                    raise WorkflowConflict(f"案件尚未分配有效的{role_name}，请先完成角色分配")
                assignee_user_id = assignee["user_id"]
                task = CaseTaskV1(
                    task_id=str(uuid4()),
                    case_id=item["case_id"],
                    source_type="SERVICE_CONSULTATION",
                    source_id=analysis_id,
                    target_role=target_role,
                    assignee_user_id=assignee_user_id,
                    category=category,
                    priority=priority,
                    status="OPEN",
                    note=note,
                    created_by=user_id,
                    created_at=now,
                ).model_dump(mode="json")
                db.execute(
                    "INSERT INTO case_tasks(task_id, case_id, source_type, source_id, target_role, "
                    "assignee_user_id, category, priority, status, note, created_by, created_at, "
                    "claimed_at, resolved_at, resolution) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    tuple(task[key] for key in (
                        "task_id", "case_id", "source_type", "source_id", "target_role",
                        "assignee_user_id", "category", "priority", "status", "note",
                        "created_by", "created_at", "claimed_at", "resolved_at", "resolution",
                    )),
                )
                review_status = "ROUTED"
            db.execute(
                "UPDATE service_analysis_results SET review_status=?, reviewer_user_id=?, "
                "route_target_role=?, route_note=?, routed_at=? WHERE analysis_id=?",
                (review_status, user_id, target_role, note, now, analysis_id),
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
                   customer.phone AS customer_phone,
                   f.comment AS feedback_comment, f.rating AS feedback_rating,
                   f.helpful AS feedback_helpful, f.request_contact
            FROM follow_up_tasks t
            JOIN cases c ON c.case_id=t.case_id
            JOIN case_participants cp ON cp.case_id=c.case_id
            LEFT JOIN users assignee ON assignee.user_id=t.assignee_user_id
            LEFT JOIN users customer ON customer.user_id=c.customer_user_id
            LEFT JOIN feedback f ON f.feedback_id=t.feedback_id
            WHERE {' AND '.join(clauses)} ORDER BY
              CASE lower(t.priority) WHEN 'urgent' THEN 0 WHEN 'high' THEN 1
                   WHEN 'medium' THEN 2 ELSE 3 END, t.created_at DESC
        """
        with self._connect() as db:
            results = []
            for row in db.execute(sql, params):
                item = json.loads(row["record_json"])
                request_contact = bool(row["request_contact"])
                item.update({
                    "status": row["status"], "priority": str(row["priority"]).lower(),
                    "assignee": row["assignee_name"], "claim_event_id": row["claim_event_id"],
                    "resolution": row["resolution"], "resolved_at": row["resolved_at"],
                    "feedback_comment": row["feedback_comment"],
                    "feedback_rating": row["feedback_rating"],
                    "feedback_helpful": bool(row["feedback_helpful"])
                    if row["feedback_helpful"] is not None else None,
                    "request_contact": request_contact,
                    "customer_phone_masked": self._mask_phone_last4(row["customer_phone"])
                    if request_contact else None,
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
