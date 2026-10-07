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
from uuid import uuid4

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
    def __init__(self, path=None):
        self.path = Path(path or os.getenv("COMPETITION_DB_PATH") or DEFAULT_DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cases (
                    case_id TEXT PRIMARY KEY, claim_event_id TEXT NOT NULL,
                    status TEXT NOT NULL, data_source TEXT NOT NULL,
                    consent_status TEXT NOT NULL, deidentification_status TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS case_results (
                    case_id TEXT PRIMARY KEY REFERENCES cases(case_id),
                    ocr_result_json TEXT, structured_result_json TEXT,
                    pain_result_json TEXT, care_result_json TEXT, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS case_profile_contexts (
                    case_id TEXT PRIMARY KEY REFERENCES cases(case_id),
                    context_json TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profile_case_records (
                    case_id TEXT PRIMARY KEY, input_json TEXT NOT NULL,
                    record_json TEXT, assembly_json TEXT NOT NULL,
                    source_kind TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS quality_portfolios (
                    portfolio_key TEXT PRIMARY KEY, portfolio_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    review_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(case_id),
                    content_fingerprint TEXT NOT NULL, target_type TEXT NOT NULL,
                    decision TEXT NOT NULL, record_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    delivery_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(case_id),
                    content_fingerprint TEXT NOT NULL, record_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(case_id, content_fingerprint)
                );
                CREATE TABLE IF NOT EXISTS feedback (
                    feedback_id TEXT PRIMARY KEY, delivery_id TEXT NOT NULL REFERENCES deliveries(delivery_id),
                    case_id TEXT NOT NULL REFERENCES cases(case_id), record_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS follow_up_tasks (
                    task_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(case_id),
                    feedback_id TEXT NOT NULL UNIQUE REFERENCES feedback(feedback_id),
                    status TEXT NOT NULL, record_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mobile_previews (
                    token_hash TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(case_id),
                    preview_json TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

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

    def create_case(self, request: CreateCaseRequest) -> WorkflowCase:
        now = datetime.now(timezone.utc).isoformat()
        case = WorkflowCase(**request.model_dump(), case_id=str(uuid4()),
                            status=WorkflowStatus.RECEIVED, created_at=now, updated_at=now)
        with self._connect() as db:
            db.execute("INSERT INTO cases VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (case.case_id, case.claim_event_id, case.status.value, case.data_source,
                        case.consent_status, case.deidentification_status, now, now))
            db.execute("INSERT INTO case_results(case_id, updated_at) VALUES (?, ?)",
                       (case.case_id, now))
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

    def _status(self, db, case_id, status):
        now = datetime.now(timezone.utc).isoformat()
        db.execute("UPDATE cases SET status=?, updated_at=? WHERE case_id=?",
                   (status.value, now, case_id))
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

    def create_mobile_preview(self, case_id: str, ttl_minutes: int = 30):
        """Create an expiring, read-only snapshot without exposing the real case id."""

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=ttl_minutes)
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        public_case_id = f"PREVIEW-{secrets.token_hex(4).upper()}"
        publication_id = f"MP-{secrets.token_hex(8).upper()}"
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, case_id)
            if row["consent_status"] != "authorized" or row["deidentification_status"] != "confirmed":
                raise WorkflowConflict("手机预览仅允许用于已授权并确认脱敏的案件")
            if row["care_result_json"] is None:
                raise WorkflowConflict("请先生成温度指引")
            care = json.loads(row["care_result_json"])
            guide = care.get("guide")
            if not isinstance(guide, dict):
                raise WorkflowConflict("温度指引缺少可预览内容")
            public_guide = json.loads(json.dumps(guide, ensure_ascii=False))
            public_guide["case_id"] = public_case_id
            publication = {
                "version": "patient-mobile-preview-v1",
                "publication_id": publication_id,
                "status": "preview",
                "case_id": public_case_id,
                "created_at": now.isoformat(),
                "published_at": None,
                "expires_at": expires_at.isoformat(),
                "report": {"case_id": public_case_id, "guide": public_guide},
            }
            db.execute("DELETE FROM mobile_previews WHERE expires_at<=?", (now.isoformat(),))
            db.execute(
                "INSERT INTO mobile_previews VALUES (?, ?, ?, ?, ?)",
                (token_hash, case_id, encode(publication), expires_at.isoformat(), now.isoformat()),
            )
        return {
            "token": token,
            "publication_id": publication_id,
            "expires_at": expires_at.isoformat(),
        }

    def get_mobile_preview(self, token: str):
        """Resolve a mobile preview by a raw token; only its hash is stored."""

        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        with self._connect() as db:
            row = db.execute(
                "SELECT preview_json, expires_at FROM mobile_previews WHERE token_hash=?",
                (token_hash,),
            ).fetchone()
            if row is None:
                raise WorkflowItemNotFound("手机预览链接不存在或已失效")
            if datetime.fromisoformat(row["expires_at"]) <= now:
                db.execute("DELETE FROM mobile_previews WHERE token_hash=?", (token_hash,))
                raise WorkflowItemNotFound("手机预览链接已过期")
            return json.loads(row["preview_json"])

    def _invalidate_profile(self, db, case_id, clear_context=False):
        changed = db.execute("DELETE FROM profile_case_records WHERE case_id=? AND source_kind='workflow'",
                             (case_id,)).rowcount
        if clear_context:
            db.execute("DELETE FROM case_profile_contexts WHERE case_id=?", (case_id,))
        if changed:
            db.execute("DELETE FROM quality_portfolios")

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
            db.execute("INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (record.review_id, record.case_id, record.content_fingerprint, record.target_type.value,
                        record.decision.value, encode(record), record.created_at))
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
            db.execute("INSERT INTO deliveries VALUES (?, ?, ?, ?, ?)",
                       (record.delivery_id, record.case_id, record.content_fingerprint, encode(record), record.created_at))
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
            db.execute("INSERT INTO feedback VALUES (?, ?, ?, ?, ?)",
                       (feedback.feedback_id, delivery_id, feedback.case_id, encode(feedback), now))
            needs_followup = request.rating <= 3 or not request.helpful or request.category != FeedbackCategory.CLEAR
            task = None
            if needs_followup:
                task = FollowUpTask(task_id=str(uuid4()), case_id=feedback.case_id, feedback_id=feedback.feedback_id,
                                    category=feedback.category, note=f"rating={feedback.rating}, helpful={feedback.helpful}, category={feedback.category.value}", created_at=now)
                db.execute("INSERT INTO follow_up_tasks VALUES (?, ?, ?, ?, ?, ?)",
                           (task.task_id, task.case_id, task.feedback_id, task.status.value, encode(task), now))
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
            db.execute("UPDATE follow_up_tasks SET status='RESOLVED', record_json=? WHERE task_id=?", (encode(task), task_id))
            if self._row(db, task.case_id)["status"] in {"DELIVERED_MOCK", "FEEDBACK_RECEIVED", "FOLLOWUP_PENDING", "RESOLVED"}:
                open_task = db.execute("SELECT 1 FROM follow_up_tasks WHERE case_id=? AND status='OPEN'", (task.case_id,)).fetchone()
                self._status(db, task.case_id, WorkflowStatus.FOLLOWUP_PENDING if open_task else WorkflowStatus.RESOLVED)
            return task
