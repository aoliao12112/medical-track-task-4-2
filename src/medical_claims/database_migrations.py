"""Versioned SQLite migrations for the competition application.

The migration layer is deliberately small: the project still uses SQLite, but
schema changes are now explicit, ordered and checksum protected.  Legacy
tables remain available until their callers have been migrated.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import sqlite3
from typing import Callable


MigrationApply = Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    fingerprint: str
    apply: MigrationApply

    @property
    def checksum(self) -> str:
        payload = f"{self.version}:{self.name}:{self.fingerprint}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in db.execute(f'PRAGMA table_info("{table}")')}


def _add_column(db: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0].strip('"')
    if name not in _columns(db, table):
        db.execute(f'ALTER TABLE "{table}" ADD COLUMN {definition}')


def _migration_001_legacy_core(db: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE IF NOT EXISTS cases (
            case_id TEXT PRIMARY KEY, claim_event_id TEXT NOT NULL,
            status TEXT NOT NULL, data_source TEXT NOT NULL,
            consent_status TEXT NOT NULL, deidentification_status TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS case_results (
            case_id TEXT PRIMARY KEY REFERENCES cases(case_id),
            ocr_result_json TEXT, structured_result_json TEXT,
            pain_result_json TEXT, care_result_json TEXT, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS case_profile_contexts (
            case_id TEXT PRIMARY KEY REFERENCES cases(case_id),
            context_json TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS profile_case_records (
            case_id TEXT PRIMARY KEY, input_json TEXT NOT NULL,
            record_json TEXT, assembly_json TEXT NOT NULL,
            source_kind TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS quality_portfolios (
            portfolio_key TEXT PRIMARY KEY, portfolio_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS reviews (
            review_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(case_id),
            content_fingerprint TEXT NOT NULL, target_type TEXT NOT NULL,
            decision TEXT NOT NULL, record_json TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS deliveries (
            delivery_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(case_id),
            content_fingerprint TEXT NOT NULL, record_json TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(case_id, content_fingerprint)
        )""",
        """CREATE TABLE IF NOT EXISTS feedback (
            feedback_id TEXT PRIMARY KEY, delivery_id TEXT NOT NULL REFERENCES deliveries(delivery_id),
            case_id TEXT NOT NULL REFERENCES cases(case_id), record_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS follow_up_tasks (
            task_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(case_id),
            feedback_id TEXT NOT NULL UNIQUE REFERENCES feedback(feedback_id),
            status TEXT NOT NULL, record_json TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
    )
    for statement in statements:
        db.execute(statement)
    db.execute(
        "INSERT INTO case_results(case_id, updated_at) "
        "SELECT c.case_id, c.updated_at FROM cases c "
        "LEFT JOIN case_results r ON r.case_id=c.case_id WHERE r.case_id IS NULL"
    )


ROLE_ROWS = (
    (1, "CLAIM", "理赔人员", "确认材料、授权、理赔状态并触发关怀"),
    (2, "MEDICAL", "医疗复核人员", "复核结构化结果、痛点、指引和推荐"),
    (3, "SERVICE", "客服人员", "处理用户反馈、服务会话和后续任务"),
    (4, "CUSTOMER", "客户", "查看本人案件、关怀内容并提交反馈"),
    (5, "SYSTEM_ADMIN", "系统管理员", "管理账号权限与审计配置，不默认读取病历"),
)

PERMISSIONS_BY_ROLE = {
    "CLAIM": (
        "CASE_CREATE", "CASE_VIEW", "CASE_UPDATE", "DOCUMENT_UPLOAD", "DOCUMENT_VIEW",
        "CONSENT_CONFIRM", "CLAIM_STATUS_UPDATE", "CARE_TRIGGER",
    ),
    "MEDICAL": (
        "MEDICAL_CASE_VIEW", "STRUCTURED_VIEW", "STRUCTURED_EDIT", "PAIN_VIEW",
        "PAIN_REVIEW", "CARE_VIEW", "CARE_REVIEW", "RECOMMEND_VIEW",
        "RECOMMEND_REVIEW", "DELIVERY_APPROVE", "DELIVERY_BLOCK",
    ),
    "SERVICE": (
        "SERVICE_CASE_SUMMARY_VIEW", "FEEDBACK_VIEW", "SERVICE_SESSION_VIEW",
        "SERVICE_SESSION_CREATE", "FOLLOWUP_VIEW", "FOLLOWUP_ASSIGN",
        "FOLLOWUP_HANDLE", "FOLLOWUP_RESOLVE",
    ),
    "CUSTOMER": (
        "OWN_CASE_VIEW", "OWN_CARE_VIEW", "OWN_RECOMMEND_VIEW", "FEEDBACK_SUBMIT",
        "SUPPLEMENT_DOCUMENT", "REQUEST_CONTACT",
    ),
    "SYSTEM_ADMIN": (
        "USER_MANAGE", "ROLE_MANAGE", "PERMISSION_MANAGE", "AUDIT_VIEW", "BATCH_MANAGE",
    ),
}


def _migration_002_identity_and_case_scope(db: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE IF NOT EXISTS roles (
            role_id INTEGER PRIMARY KEY,
            role_code TEXT NOT NULL UNIQUE,
            role_name TEXT NOT NULL,
            role_desc TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS permissions (
            perm_id INTEGER PRIMARY KEY AUTOINCREMENT,
            perm_code TEXT NOT NULL UNIQUE,
            perm_name TEXT NOT NULL,
            module TEXT NOT NULL,
            perm_desc TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role_id INTEGER NOT NULL REFERENCES roles(role_id),
            display_name TEXT NOT NULL,
            phone TEXT,
            email TEXT,
            status TEXT NOT NULL CHECK(status IN ('ACTIVE','DISABLED','LOCKED')),
            last_login_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS role_permissions (
            role_id INTEGER NOT NULL REFERENCES roles(role_id),
            perm_id INTEGER NOT NULL REFERENCES permissions(perm_id),
            PRIMARY KEY(role_id, perm_id)
        )""",
        """CREATE TABLE IF NOT EXISTS case_participants (
            case_id TEXT NOT NULL REFERENCES cases(case_id),
            user_id TEXT NOT NULL REFERENCES users(user_id),
            case_role TEXT NOT NULL CHECK(case_role IN
                ('CUSTOMER','CLAIM_OWNER','MEDICAL_REVIEWER','SERVICE_AGENT')),
            status TEXT NOT NULL CHECK(status IN ('ACTIVE','CLOSED')),
            assigned_at TEXT NOT NULL,
            unassigned_at TEXT,
            PRIMARY KEY(case_id, user_id, case_role)
        )""",
    )
    for statement in statements:
        db.execute(statement)

    for row in ROLE_ROWS:
        db.execute(
            "INSERT OR IGNORE INTO roles(role_id, role_code, role_name, role_desc) VALUES (?, ?, ?, ?)",
            row,
        )

    modules = {
        "CLAIM": "理赔", "MEDICAL": "医疗复核", "SERVICE": "客服",
        "CUSTOMER": "客户", "SYSTEM_ADMIN": "系统管理",
    }
    for role_code, permission_codes in PERMISSIONS_BY_ROLE.items():
        for code in permission_codes:
            db.execute(
                "INSERT OR IGNORE INTO permissions(perm_code, perm_name, module, perm_desc) "
                "VALUES (?, ?, ?, ?)",
                (code, code, modules[role_code], f"{modules[role_code]}权限：{code}"),
            )
            db.execute(
                "INSERT OR IGNORE INTO role_permissions(role_id, perm_id) "
                "SELECT r.role_id, p.perm_id FROM roles r, permissions p "
                "WHERE r.role_code=? AND p.perm_code=?",
                (role_code, code),
            )

    _add_column(db, "cases", "customer_user_id TEXT REFERENCES users(user_id)")
    _add_column(db, "cases", "claim_status TEXT NOT NULL DEFAULT 'COMPLETED'")
    _add_column(db, "cases", "care_status TEXT NOT NULL DEFAULT 'RECEIVED'")
    _add_column(db, "cases", "patient_city TEXT")
    db.execute("UPDATE cases SET care_status=status WHERE care_status='RECEIVED' AND status<>'RECEIVED'")

    _add_column(db, "reviews", "reviewer_user_id TEXT REFERENCES users(user_id)")
    _add_column(db, "reviews", "comment TEXT")
    _add_column(db, "deliveries", "customer_user_id TEXT REFERENCES users(user_id)")
    _add_column(db, "deliveries", "channel TEXT NOT NULL DEFAULT 'MOCK'")
    _add_column(db, "deliveries", "status TEXT NOT NULL DEFAULT 'DELIVERED_MOCK'")
    _add_column(db, "deliveries", "sent_at TEXT")
    _add_column(db, "deliveries", "read_at TEXT")
    _add_column(db, "feedback", "customer_user_id TEXT REFERENCES users(user_id)")
    _add_column(db, "feedback", "target_type TEXT")
    _add_column(db, "feedback", "rating INTEGER")
    _add_column(db, "feedback", "helpful INTEGER")
    _add_column(db, "feedback", "category TEXT")
    _add_column(db, "feedback", "comment TEXT")
    _add_column(db, "feedback", "request_contact INTEGER NOT NULL DEFAULT 0")

    db.execute("CREATE INDEX IF NOT EXISTS idx_users_role_status ON users(role_id, status)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_case_participants_user ON case_participants(user_id, status)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_cases_customer ON cases(customer_user_id)")


def _migration_003_recommendation_persistence(db: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE IF NOT EXISTS provider_quality_observations (
            observation_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL,
            provider_type TEXT NOT NULL CHECK(provider_type IN ('HOSPITAL','DOCTOR','TREATMENT')),
            provider_id TEXT NOT NULL,
            disease_group TEXT NOT NULL,
            metric_code TEXT NOT NULL,
            raw_value REAL,
            unit TEXT,
            status TEXT NOT NULL CHECK(status IN
                ('OBSERVED','DATA_UNAVAILABLE','REVIEW_REQUIRED')),
            evidence_json TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            observed_at TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(case_id, provider_type, provider_id, disease_group, metric_code)
        )""",
        """CREATE TABLE IF NOT EXISTS provider_profile_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            provider_type TEXT NOT NULL CHECK(provider_type IN ('HOSPITAL','DOCTOR','TREATMENT')),
            provider_id TEXT NOT NULL,
            disease_group TEXT NOT NULL,
            profile_version INTEGER NOT NULL,
            sample_size INTEGER NOT NULL CHECK(sample_size >= 0),
            data_sufficiency_status TEXT NOT NULL CHECK(data_sufficiency_status IN
                ('SUFFICIENT','PARTIALLY_SUFFICIENT','INSUFFICIENT')),
            composite_score REAL CHECK(composite_score BETWEEN 0 AND 100),
            profile_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('ACTIVE','SUPERSEDED','INVALIDATED')),
            valid_from TEXT NOT NULL,
            valid_to TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(provider_type, provider_id, disease_group, profile_version)
        )""",
        """CREATE TABLE IF NOT EXISTS provider_metric_scores (
            metric_score_id TEXT PRIMARY KEY,
            snapshot_id TEXT NOT NULL REFERENCES provider_profile_snapshots(snapshot_id),
            metric_code TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            score REAL CHECK(score BETWEEN 0 AND 100),
            status TEXT NOT NULL CHECK(status IN
                ('CALCULATED','INSUFFICIENT_SAMPLE','DATA_UNAVAILABLE','REVIEW_REQUIRED')),
            sample_size INTEGER NOT NULL CHECK(sample_size >= 0),
            confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
            calculation TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(snapshot_id, metric_code)
        )""",
        """CREATE TABLE IF NOT EXISTS recommendation_reports (
            report_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES cases(case_id),
            report_version INTEGER NOT NULL,
            disease_group TEXT NOT NULL,
            patient_city TEXT,
            status TEXT NOT NULL CHECK(status IN ('DRAFT','REVIEWED','PUBLISHED','INVALIDATED')),
            planner_version TEXT NOT NULL,
            report_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(case_id, report_version)
        )""",
        """CREATE TABLE IF NOT EXISTS recommendation_items (
            item_id TEXT PRIMARY KEY,
            report_id TEXT NOT NULL REFERENCES recommendation_reports(report_id),
            item_type TEXT NOT NULL CHECK(item_type IN ('HOSPITAL','DOCTOR','TREATMENT')),
            entity_id TEXT,
            entity_name TEXT NOT NULL,
            city TEXT,
            department TEXT,
            profile_snapshot_id TEXT REFERENCES provider_profile_snapshots(snapshot_id),
            rank_no INTEGER NOT NULL CHECK(rank_no >= 1),
            total_score REAL NOT NULL CHECK(total_score BETWEEN 0 AND 100),
            confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
            evidence_status TEXT NOT NULL CHECK(evidence_status IN
                ('VERIFIED','PARTIALLY_VERIFIED','PENDING_VERIFICATION','CONTRADICTED')),
            created_at TEXT NOT NULL,
            UNIQUE(report_id, item_type, rank_no)
        )""",
        """CREATE TABLE IF NOT EXISTS recommendation_scores (
            score_id TEXT PRIMARY KEY,
            item_id TEXT NOT NULL REFERENCES recommendation_items(item_id),
            dimension_code TEXT NOT NULL,
            dimension_name TEXT NOT NULL,
            score REAL NOT NULL CHECK(score BETWEEN 0 AND 100),
            weight REAL NOT NULL CHECK(weight > 0 AND weight <= 1),
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(item_id, dimension_code)
        )""",
        """CREATE TABLE IF NOT EXISTS recommendation_evidence (
            evidence_id TEXT PRIMARY KEY,
            item_id TEXT NOT NULL REFERENCES recommendation_items(item_id),
            reason TEXT NOT NULL,
            evidence_type TEXT,
            source_name TEXT,
            source_url TEXT,
            evidence_summary TEXT,
            evidence_status TEXT NOT NULL CHECK(evidence_status IN
                ('VERIFIED','PARTIALLY_VERIFIED','PENDING_VERIFICATION','CONTRADICTED')),
            evidence_confidence REAL NOT NULL CHECK(evidence_confidence BETWEEN 0 AND 1),
            verified_at TEXT,
            created_at TEXT NOT NULL
        )""",
    )
    for statement in statements:
        db.execute(statement)
    db.execute("CREATE INDEX IF NOT EXISTS idx_quality_observations_provider ON provider_quality_observations(provider_type, provider_id, disease_group, metric_code)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_profile_snapshots_provider ON provider_profile_snapshots(provider_type, provider_id, disease_group, status)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_provider_metric_snapshot ON provider_metric_scores(snapshot_id, metric_code)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_recommendation_reports_case_status ON recommendation_reports(case_id, status, report_version)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_recommendation_items_report_type ON recommendation_items(report_id, item_type, rank_no)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_recommendation_evidence_item ON recommendation_evidence(item_id, evidence_status)")


def _migration_004_service_and_audit(db: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE IF NOT EXISTS service_sessions (
            session_id_hash TEXT PRIMARY KEY,
            customer_user_id TEXT REFERENCES users(user_id),
            channel TEXT NOT NULL CHECK(channel IN ('WECHAT','INBOUND_CALL','ONLINE','APP_CHAT')),
            session_time TEXT NOT NULL,
            deidentified_text TEXT NOT NULL,
            text_fingerprint TEXT NOT NULL,
            data_source TEXT NOT NULL,
            consent_status TEXT NOT NULL,
            deidentification_status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS service_analysis_results (
            analysis_id TEXT PRIMARY KEY,
            session_id_hash TEXT NOT NULL REFERENCES service_sessions(session_id_hash),
            primary_intent TEXT NOT NULL,
            secondary_intents_json TEXT NOT NULL,
            has_friction INTEGER NOT NULL CHECK(has_friction IN (0,1)),
            friction_types_json TEXT NOT NULL,
            severity TEXT NOT NULL,
            severity_score REAL NOT NULL CHECK(severity_score BETWEEN 0 AND 100),
            confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
            evidence_json TEXT NOT NULL,
            recommended_action TEXT,
            engine_version TEXT NOT NULL,
            review_status TEXT NOT NULL,
            reviewer_user_id TEXT REFERENCES users(user_id),
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS case_service_links (
            link_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES cases(case_id),
            session_id_hash TEXT NOT NULL REFERENCES service_sessions(session_id_hash),
            link_method TEXT NOT NULL,
            confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
            reviewed INTEGER NOT NULL CHECK(reviewed IN (0,1)),
            reviewer_user_id TEXT REFERENCES users(user_id),
            created_at TEXT NOT NULL,
            UNIQUE(case_id, session_id_hash)
        )""",
        """CREATE TABLE IF NOT EXISTS system_logs (
            log_id TEXT PRIMARY KEY,
            user_id TEXT REFERENCES users(user_id),
            case_id TEXT REFERENCES cases(case_id),
            action TEXT NOT NULL,
            module TEXT NOT NULL,
            request_path TEXT,
            ip_address TEXT,
            request_params_json TEXT NOT NULL,
            result TEXT NOT NULL,
            error_message TEXT,
            created_at TEXT NOT NULL
        )""",
    )
    for statement in statements:
        db.execute(statement)

    _add_column(db, "follow_up_tasks", "service_analysis_id TEXT REFERENCES service_analysis_results(analysis_id)")
    _add_column(db, "follow_up_tasks", "source_type TEXT NOT NULL DEFAULT 'DELIVERY_FEEDBACK'")
    _add_column(db, "follow_up_tasks", "category TEXT NOT NULL DEFAULT 'OTHER'")
    _add_column(db, "follow_up_tasks", "priority TEXT NOT NULL DEFAULT 'MEDIUM'")
    _add_column(db, "follow_up_tasks", "assignee_user_id TEXT REFERENCES users(user_id)")
    _add_column(db, "follow_up_tasks", "note TEXT NOT NULL DEFAULT ''")
    _add_column(db, "follow_up_tasks", "resolved_at TEXT")
    _add_column(db, "follow_up_tasks", "resolution TEXT")

    db.execute("CREATE INDEX IF NOT EXISTS idx_service_sessions_customer_time ON service_sessions(customer_user_id, session_time)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_service_analysis_session ON service_analysis_results(session_id_hash, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_case_service_links_case ON case_service_links(case_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_follow_up_status_assignee ON follow_up_tasks(status, assignee_user_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_system_logs_case_time ON system_logs(case_id, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_system_logs_user_time ON system_logs(user_id, created_at)")


def _migration_005_batch_persistence(db: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE IF NOT EXISTS batch_jobs (
            batch_id TEXT PRIMARY KEY,
            created_by TEXT REFERENCES users(user_id),
            batch_name TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('WAITING','PROCESSING','COMPLETED','PARTIAL','FAILED')),
            total_count INTEGER NOT NULL CHECK(total_count >= 0),
            success_count INTEGER NOT NULL DEFAULT 0 CHECK(success_count >= 0),
            failed_count INTEGER NOT NULL DEFAULT 0 CHECK(failed_count >= 0),
            created_at TEXT NOT NULL,
            finished_at TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS batch_items (
            batch_id TEXT NOT NULL REFERENCES batch_jobs(batch_id),
            case_id TEXT NOT NULL REFERENCES cases(case_id),
            status TEXT NOT NULL CHECK(status IN ('WAITING','PROCESSING','COMPLETED','REVIEW_REQUIRED','FAILED')),
            error_message TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count >= 0),
            PRIMARY KEY(batch_id, case_id)
        )""",
    )
    for statement in statements:
        db.execute(statement)
    db.execute("CREATE INDEX IF NOT EXISTS idx_batch_items_status ON batch_items(batch_id, status)")


def _migration_006_case_task_routing(db: sqlite3.Connection) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS case_tasks (
            task_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES cases(case_id),
            source_type TEXT NOT NULL CHECK(source_type IN
                ('SERVICE_CONSULTATION','CUSTOMER_FEEDBACK','MANUAL_HANDOFF')),
            source_id TEXT NOT NULL,
            target_role TEXT NOT NULL CHECK(target_role IN ('CLAIM','MEDICAL','SERVICE')),
            assignee_user_id TEXT REFERENCES users(user_id),
            category TEXT NOT NULL CHECK(category IN
                ('claim_progress','claim_material','coverage_rule','medical_cost','claim_cost',
                 'recovery_question','medication_question','recommendation_question',
                 'value_added_service','human_service_request','other')),
            priority TEXT NOT NULL CHECK(priority IN ('LOW','MEDIUM','HIGH','URGENT')),
            status TEXT NOT NULL CHECK(status IN ('OPEN','PROCESSING','RESOLVED')),
            note TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL REFERENCES users(user_id),
            created_at TEXT NOT NULL,
            claimed_at TEXT,
            resolved_at TEXT,
            resolution TEXT,
            UNIQUE(source_type, source_id, target_role)
        )"""
    )
    _add_column(db, "service_analysis_results", "route_target_role TEXT")
    _add_column(db, "service_analysis_results", "route_note TEXT NOT NULL DEFAULT ''")
    _add_column(db, "service_analysis_results", "routed_at TEXT")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_case_tasks_assignee_status "
        "ON case_tasks(assignee_user_id, status, created_at)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_case_tasks_case_role_status "
        "ON case_tasks(case_id, target_role, status)"
    )


MIGRATIONS = (
    Migration(1, "legacy_competition_core", "legacy-core-v1", _migration_001_legacy_core),
    Migration(2, "identity_permissions_and_case_scope", "identity-scope-v1", _migration_002_identity_and_case_scope),
    Migration(3, "quality_profiles_and_recommendation_persistence", "quality-recommendation-persistence-v2", _migration_003_recommendation_persistence),
    Migration(4, "service_sessions_followup_and_audit", "service-audit-v1", _migration_004_service_and_audit),
    Migration(5, "batch_job_persistence", "batch-persistence-v1", _migration_005_batch_persistence),
    Migration(6, "case_task_routing", "case-task-routing-v1", _migration_006_case_task_routing),
)

CURRENT_SCHEMA_VERSION = MIGRATIONS[-1].version


def apply_migrations(db: sqlite3.Connection) -> None:
    """Apply pending migrations atomically and verify applied checksums."""

    db.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            migration_name TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            checksum TEXT NOT NULL
        )"""
    )
    applied = {
        row["version"]: row
        for row in db.execute(
            "SELECT version, migration_name, applied_at, checksum FROM schema_migrations"
        )
    }
    for migration in MIGRATIONS:
        existing = applied.get(migration.version)
        if existing:
            if existing["migration_name"] != migration.name or existing["checksum"] != migration.checksum:
                raise RuntimeError(
                    f"数据库 migration {migration.version} 与代码不一致，请勿继续写入"
                )
            continue
        try:
            db.execute("BEGIN IMMEDIATE")
            migration.apply(db)
            db.execute(
                "INSERT INTO schema_migrations(version, migration_name, applied_at, checksum) "
                "VALUES (?, ?, ?, ?)",
                (
                    migration.version,
                    migration.name,
                    datetime.now(timezone.utc).isoformat(),
                    migration.checksum,
                ),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
