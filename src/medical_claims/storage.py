"""Local SQLite case storage, without report versions or event sourcing."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
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

    def create_case(self, request: CreateCaseRequest) -> WorkflowCase:
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
                open_task = db.execute("SELECT 1 FROM follow_up_tasks WHERE case_id=? AND status='OPEN'", (task.case_id,)).fetchone()
                self._status(db, task.case_id, WorkflowStatus.FOLLOWUP_PENDING if open_task else WorkflowStatus.RESOLVED)
            return task
