"""Database migration, profile and recommendation persistence regression tests."""
from __future__ import annotations

import json
import sqlite3

from medical_claims.database_migrations import CURRENT_SCHEMA_VERSION
from medical_claims.storage import CaseStorage
from medical_claims.workflow_models import CreateCaseRequest


def _request():
    return CreateCaseRequest(
        claim_event_id="CLAIM-DB-001",
        data_source="synthetic_test",
        consent_status="authorized",
        deidentification_status="confirmed",
    )


def _tables(path):
    with sqlite3.connect(path) as db:
        return {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }


def test_fresh_database_has_versioned_complete_schema(tmp_path):
    path = tmp_path / "competition.db"
    storage = CaseStorage(path)
    info = storage.schema_info()
    assert info["current_version"] == CURRENT_SCHEMA_VERSION == 7
    assert [item["version"] for item in info["applied_migrations"]] == [1, 2, 3, 4, 5, 6, 7]
    expected = {
        "schema_migrations", "users", "roles", "permissions", "role_permissions",
        "case_participants", "cases", "case_results", "case_profile_contexts",
        "profile_case_records", "quality_portfolios", "provider_quality_observations",
        "provider_profile_snapshots", "provider_metric_scores", "recommendation_reports",
        "recommendation_items", "recommendation_scores", "recommendation_evidence",
        "reviews", "deliveries", "feedback", "follow_up_tasks", "service_sessions",
        "service_analysis_results", "case_service_links", "system_logs", "batch_jobs",
        "batch_items", "case_tasks",
    }
    assert _tables(path) == expected
    assert {row["role_code"] for row in _query(path, "SELECT role_code FROM roles")} == {
        "CLAIM", "MEDICAL", "SERVICE", "CUSTOMER", "SYSTEM_ADMIN"
    }
    assert _query(path, "SELECT COUNT(*) AS value FROM permissions")[0]["value"] >= 30


def test_migration_preserves_legacy_case_and_adds_split_status(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE cases(case_id TEXT PRIMARY KEY, claim_event_id TEXT NOT NULL, "
            "status TEXT NOT NULL, data_source TEXT NOT NULL, consent_status TEXT NOT NULL, "
            "deidentification_status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO cases VALUES ('CASE-OLD', 'CLAIM-OLD', 'CARE_READY', 'synthetic_test', "
            "'authorized', 'confirmed', '2026-01-01', '2026-01-01')"
        )
    storage = CaseStorage(path)
    case = storage.get_case("CASE-OLD")
    assert case["status"] == "CARE_READY"
    row = _query(path, "SELECT status, care_status, claim_status FROM cases WHERE case_id='CASE-OLD'")[0]
    assert row == {"status": "CARE_READY", "care_status": "CARE_READY", "claim_status": "COMPLETED"}


def test_role_permission_requires_active_case_participant(tmp_path):
    path = tmp_path / "competition.db"
    storage = CaseStorage(path)
    case = storage.create_case(_request())
    user_id = storage.create_user(
        username="demo_customer",
        password_hash="test-only-hash",
        role_code="CUSTOMER",
        display_name="演示客户",
    )
    assert not storage.user_can_access_case(user_id, case.case_id, "OWN_CASE_VIEW")
    storage.assign_case_participant(case.case_id, user_id, "CUSTOMER")
    assert storage.user_can_access_case(user_id, case.case_id, "OWN_CASE_VIEW")
    assert not storage.user_can_access_case(user_id, case.case_id, "CASE_UPDATE")


def test_quality_profile_is_separate_from_case_recommendation(tmp_path):
    path = tmp_path / "competition.db"
    storage = CaseStorage(path)
    case = storage.create_case(_request())
    storage.save_provider_quality_observation(
        case_id=case.case_id,
        provider_type="HOSPITAL",
        provider_id="HOSP-001",
        disease_group="pneumonia",
        metric_code="COST_EFFICIENCY",
        raw_value=12345.0,
        unit="CNY_PER_CASE",
        evidence={"field": "costs.total"},
        source_kind="workflow",
    )
    saved = storage.save_provider_profile_snapshot(
        provider_type="HOSPITAL",
        provider_id="HOSP-001",
        disease_group="pneumonia",
        sample_size=3,
        data_sufficiency_status="SUFFICIENT",
        composite_score=82.0,
        profile={"historical_case_count": 3},
        metrics=[{
            "metric_code": "COST_EFFICIENCY",
            "metric_name": "费用效率",
            "score": 80.0,
            "status": "CALCULATED",
            "sample_size": 3,
            "confidence": 0.8,
            "calculation": "同病种病例费用标准化",
            "evidence": {"observation_count": 3},
        }],
    )
    profile = storage.load_provider_profile("HOSPITAL", "HOSP-001", "pneumonia")
    assert profile["snapshot_id"] == saved["snapshot_id"]
    assert profile["sample_size"] == 3
    assert profile["metrics"][0]["metric_code"] == "COST_EFFICIENCY"
    assert _query(path, "SELECT COUNT(*) AS value FROM recommendation_reports")[0]["value"] == 0


def test_recommendation_reports_are_versioned_and_keep_match_scores(tmp_path):
    path = tmp_path / "competition.db"
    storage = CaseStorage(path)
    case = storage.create_case(_request())
    report = {
        "context": {"case_id": case.case_id, "disease_group": "pneumonia", "city": "上海"},
        "engine_version": "test-v2",
        "recommendation_intent": {"planner": "test-planner"},
        "candidate_profiles": [],
        "candidate_clinician_profiles": [],
        "recommendations": [{
            "rank": 1,
            "hospital_id": "HOSP-001",
            "hospital_name": "测试医院",
            "recommendation_score": 88.0,
            "recommendation_confidence": 0.6,
            "evidence_status": "pending_verification",
            "accessibility": {"hospital_city": "上海"},
            "dimension_scores": [{
                "dimension": "DISEASE_MATCH",
                "label": "病种/专科匹配",
                "score": 90.0,
                "weight": 0.35,
                "reasoning": "专科匹配",
            }],
            "reasons": [{
                "dimension": "DISEASE_MATCH",
                "title": "专科匹配",
                "explanation": "具备相关科室",
                "evidence_confidence": 0.4,
                "evidence_status": "pending_verification",
                "suggested_source_name": "医院官网",
                "suggested_source_url": "https://example.org/hospital",
                "suggested_evidence_summary": "建议核验相关科室页面",
                "evidence_record_ids": [],
            }],
        }],
    }
    first = storage.save_recommendation_report(case.case_id, report)
    second = storage.save_recommendation_report(case.case_id, report)
    assert first["report_version"] == 1
    assert second["report_version"] == 2
    assert storage.load_recommendation_report(case.case_id, 1)["status"] == "INVALIDATED"
    latest = storage.load_recommendation_report(case.case_id)
    assert latest["report_version"] == 2
    assert latest["items"][0]["scores"][0]["dimension_code"] == "DISEASE_MATCH"
    assert latest["items"][0]["evidence"][0]["evidence_status"] == "PENDING_VERIFICATION"


def test_legacy_profile_tables_remain_present(tmp_path):
    path = tmp_path / "competition.db"
    CaseStorage(path)
    tables = _tables(path)
    assert {"case_profile_contexts", "profile_case_records", "quality_portfolios"}.issubset(tables)


def _query(path, sql):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(sql)]
