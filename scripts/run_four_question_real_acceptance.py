"""Run the four-question workflow against one deidentified official case."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import httpx

from medical_claims.models import ClaimCase
from medical_claims.storage import CaseStorage
from medical_claims.workflow_models import CreateCaseRequest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_CASE_ID = "CL0300004731476862"
SOURCE_PATH = (
    ROOT / "outputs" / "official_kr1_gold_v2" / SOURCE_CASE_ID / "gold" / "gold.final.json"
)
BASE_URL = os.getenv("PORTAL_BASE_URL", "http://127.0.0.1:8765")


def required_user(storage: CaseStorage, username: str) -> dict:
    user = storage.get_user_by_username(username)
    if user is None:
        raise RuntimeError(f"缺少验收账号：{username}")
    return user


def seed_case() -> tuple[str, str]:
    storage = CaseStorage()
    claim = required_user(storage, "demo_claim")
    medical = required_user(storage, "demo_medical")
    service = required_user(storage, "demo_service")
    required_user(storage, "demo_customer")
    source = ClaimCase.model_validate_json(SOURCE_PATH.read_text(encoding="utf-8"))
    suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
    event_id = f"REAL-4Q-{SOURCE_CASE_ID}-{suffix}"
    workflow_case = storage.create_claim_case_for_customer(
        CreateCaseRequest(
            claim_event_id=event_id,
            data_source="historical_migration",
            consent_status="authorized",
            deidentification_status="confirmed",
        ),
        claim["user_id"],
        "demo_customer",
    )
    storage.assign_medical_reviewer(workflow_case.case_id, medical["username"])
    storage.assign_case_participant(workflow_case.case_id, service["user_id"], "SERVICE_AGENT")
    storage.save_structured(
        workflow_case.case_id,
        source.model_copy(update={"case_id": workflow_case.case_id}, deep=True),
    )
    storage.trigger_care(claim["user_id"], workflow_case.case_id)
    return workflow_case.case_id, event_id


def module_summary(case: dict, module: str) -> dict:
    state = (case.get("module_states") or {}).get(module) or {}
    summary: dict = {
        "generation_status": state.get("generation_status"),
        "review_status": state.get("review_status"),
        "result_available": state.get("result_available"),
    }
    if module == "pain":
        report = case.get("pain_raw_result") or case.get("pain_result") or {}
        summary["pain_point_count"] = len(report.get("pain_points") or [])
        summary["warnings"] = report.get("warnings") or []
    elif module == "care":
        report = case.get("care_result") or {}
        summary["generation_method"] = report.get("generation_method")
        summary["warnings"] = report.get("warnings") or []
    elif module == "treatment_quality":
        quality = case.get("quality") or {}
        observations = quality.get("case_observations") or []
        summary["observation_count"] = len(observations)
        summary["summary"] = quality.get("summary")
        summary["dimensions"] = [
            {
                "metric_code": item.get("metric_code"),
                "score": item.get("raw_value"),
                "status": item.get("status"),
                "assessment_status": (item.get("evidence") or {}).get("assessment_status"),
                "reasoning": (item.get("evidence") or {}).get("reasoning"),
                "limitations": (item.get("evidence") or {}).get("limitations") or [],
            }
            for item in observations
        ]
    elif module == "recommendation":
        report = case.get("recommendation") or {}
        summary["status"] = report.get("status")
        summary["item_count"] = len(report.get("items") or report.get("recommendations") or [])
        summary["items"] = [
            {
                "item_type": item.get("item_type"),
                "entity_name": item.get("entity_name"),
                "score": item.get("score"),
                "evidence_status": item.get("evidence_status"),
            }
            for item in (report.get("items") or [])
        ]
        summary["unavailable_reason"] = report.get("unavailable_reason")
        summary["warnings"] = report.get("warnings") or []
    return summary


def main() -> int:
    password = os.getenv("DEMO_USER_PASSWORD")
    if not password:
        raise SystemExit("请设置 DEMO_USER_PASSWORD")
    existing_case_id = os.getenv("ACCEPTANCE_CASE_ID")
    if existing_case_id:
        storage = CaseStorage()
        claim = required_user(storage, "demo_claim")
        storage.trigger_care(claim["user_id"], existing_case_id)
        case_id = existing_case_id
        event_id = storage.get_case(case_id)["claim_event_id"]
    else:
        case_id, event_id = seed_case()
    report = {
        "source_case_id": SOURCE_CASE_ID,
        "case_id": case_id,
        "claim_event_id": event_id,
        "source": "official deidentified KR1 case-level conversion",
        "modules": {},
    }
    collect_only = os.getenv("ACCEPTANCE_COLLECT_ONLY") == "1"
    with httpx.Client(base_url=BASE_URL, timeout=420.0, trust_env=False) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "demo_medical", "password": password},
        )
        login.raise_for_status()
        if collect_only:
            case = client.get(f"/api/medical/cases/{case_id}")
            case.raise_for_status()
            for module in ("pain", "care", "treatment_quality", "recommendation"):
                report["modules"][module] = {
                    "outcome": {"status": module_summary(case.json(), module).get("generation_status")},
                    "case_summary": module_summary(case.json(), module),
                }
        for module in (() if collect_only else ("pain", "care", "treatment_quality", "recommendation")):
            payload = {"modules": [module], "use_llm": True}
            if module == "recommendation":
                payload.update({"province": "上海市", "city": "上海市"})
            response = client.post(
                f"/api/medical/cases/{case_id}/analysis/prepare",
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
            report["modules"][module] = {
                "outcome": body["outcomes"].get(module),
                "case_summary": module_summary(body["case"], module),
            }

    report_path = ROOT / "outputs" / "logs" / f"four-question-{case_id}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    print(f"report_path={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
