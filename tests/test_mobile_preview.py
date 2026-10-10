import hashlib
import json
import sqlite3
from unittest.mock import patch

from fastapi.testclient import TestClient

from medical_claims.care_models import (
    CareAction,
    CareGuideDraft,
    CareGuideReport,
    CareSection,
    GenerationMethod,
)
from medical_claims.models import ClaimCase
from medical_claims.pain_models import PainPointReport, PainPointSummary
from medical_claims.storage import CaseStorage
from medical_claims.workflow_models import CreateCaseRequest
from web.backend.app import app
from web.mobile_gateway import app as mobile_gateway_app


def saved_care_case(path):
    storage = CaseStorage(path)
    workflow = storage.create_case(
        CreateCaseRequest(
            claim_event_id="claim-mobile-1",
            data_source="synthetic_test",
            consent_status="authorized",
            deidentification_status="confirmed",
        )
    )
    case = ClaimCase(case_id=workflow.case_id)
    pain = PainPointReport(
        case_id=workflow.case_id,
        summary=PainPointSummary(total=0, clinical=0, cost=0, experience=0, high_priority=0),
    )
    section = CareSection(title="说明", content="这是脱敏后的测试内容。")
    action = CareAction(action="记录问题", timing="复诊前", reason="方便沟通")
    draft = CareGuideDraft(
        case_id=workflow.case_id,
        disease_topic="测试病种",
        opening=section,
        diagnosis_explanation=section,
        treatment_review=section,
        cost_explanation=section,
        recovery_plan=[action],
        psychological_support=section,
        follow_up_plan=[action],
        limitations=["不替代医生诊断。"],
    )
    care = CareGuideReport(
        case_id=workflow.case_id,
        guide=draft,
        generation_method=GenerationMethod.RULES_FALLBACK,
        evidence_coverage=0,
    )
    storage.save_structured(workflow.case_id, case)
    storage.save_pain(workflow.case_id, pain, case)
    storage.save_care(workflow.case_id, care, case, pain)
    return storage, workflow


def test_mobile_preview_is_ephemeral_and_hides_real_case_id(tmp_path):
    database = tmp_path / "preview.db"
    storage, workflow = saved_care_case(database)

    created = storage.create_mobile_preview(workflow.case_id, ttl_minutes=30)
    publication = storage.get_mobile_preview(created["token"])

    assert publication["status"] == "preview"
    assert publication["case_id"].startswith("PREVIEW-")
    assert publication["case_id"] != workflow.case_id
    assert publication["report"]["guide"]["case_id"] == publication["case_id"]

    with sqlite3.connect(database) as db:
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )}
    assert len(tables) == 28
    stored_hash = hashlib.sha256(created["token"].encode()).hexdigest()
    stored_json = json.dumps(CaseStorage._preview_cache[stored_hash], ensure_ascii=False)
    assert created["token"] not in stored_json
    assert workflow.case_id not in stored_json


def test_mobile_preview_api_returns_local_qr_and_header_protected_content(tmp_path):
    database = tmp_path / "preview-api.db"
    _, workflow = saved_care_case(database)
    with patch.dict("os.environ", {"COMPETITION_DB_PATH": str(database)}), patch(
        "web.backend.app._discover_mobile_preview_host", return_value="192.168.1.20"
    ):
        client = TestClient(app)
        response = client.post(
            f"/api/workflow/cases/{workflow.case_id}/mobile-preview",
            json={"expires_in_minutes": 30},
        )
        assert response.status_code == 201
        payload = response.json()
        assert payload["preview_url"].startswith(
            "http://192.168.1.20:8766/patient.html#"
        )
        assert payload["qr_code_data_url"].startswith("data:image/svg+xml;base64,")

        fragment = payload["preview_url"].split("#", 1)[1]
        from urllib.parse import parse_qs

        values = parse_qs(fragment)
        preview = client.get(
            "/api/mobile-preview",
            headers={"X-Preview-Token": values["preview_token"][0]},
        )
        assert preview.status_code == 200
        assert preview.json()["report"]["guide"]["disease_topic"] == "测试病种"
        assert client.get("/api/mobile-preview").status_code == 422


def test_mobile_gateway_exposes_only_patient_assets_and_token_preview(tmp_path):
    database = tmp_path / "preview-gateway.db"
    storage, workflow = saved_care_case(database)
    created = storage.create_mobile_preview(workflow.case_id, ttl_minutes=30)

    with patch.dict("os.environ", {"COMPETITION_DB_PATH": str(database)}):
        client = TestClient(mobile_gateway_app)
        assert client.get("/health").json()["service"] == "mobile-preview-only"
        assert client.get("/docs").status_code == 404
        assert client.get("/index.html").status_code == 404
        patient = client.get("/patient.html")
        assert patient.status_code == 200
        assert patient.headers["cache-control"] == "no-store"
        preview = client.get(
            "/api/mobile-preview",
            headers={"X-Preview-Token": created["token"]},
        )
        assert preview.status_code == 200
        assert preview.json()["status"] == "preview"
