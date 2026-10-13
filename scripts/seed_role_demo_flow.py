"""Seed one idempotent local case that demonstrates cross-role routing."""
from __future__ import annotations

from medical_claims.storage import CaseStorage
from medical_claims.workflow_models import CreateCaseRequest


CLAIM_EVENT_ID = "DEMO-ROLE-FLOW-20261013"


def _required_user(storage: CaseStorage, username: str) -> dict:
    user = storage.get_user_by_username(username)
    if user is None:
        raise SystemExit(f"缺少演示账号：{username}")
    return user


def main() -> int:
    storage = CaseStorage()
    claim = _required_user(storage, "demo_claim")
    medical = _required_user(storage, "demo_medical")
    service = _required_user(storage, "demo_service")
    _required_user(storage, "demo_customer")

    with storage._connect() as db:  # Local demo seeding only.
        row = db.execute(
            "SELECT case_id FROM cases WHERE claim_event_id=? ORDER BY created_at LIMIT 1",
            (CLAIM_EVENT_ID,),
        ).fetchone()
    if row:
        print(f"existing_case={row['case_id']}")
        return 0

    case = storage.create_claim_case_for_customer(
        CreateCaseRequest(
            claim_event_id=CLAIM_EVENT_ID,
            data_source="customer_service_entry",
            consent_status="authorized",
            deidentification_status="confirmed",
        ),
        claim["user_id"],
        "demo_customer",
    )
    storage.assign_medical_reviewer(case.case_id, "demo_medical")
    storage.assign_case_participant(case.case_id, service["user_id"], "SERVICE_AGENT")

    claim_consultation = storage.create_service_consultation(
        service["user_id"],
        case_id=case.case_id,
        channel="ONLINE",
        deidentified_text="演示咨询：客户希望核实理赔进度与材料状态。",
        category="claim_progress",
        priority="medium",
    )
    storage.route_service_consultation(
        service["user_id"],
        claim_consultation["analysis_id"],
        target_role="CLAIM",
        category="claim_progress",
        priority="MEDIUM",
        note="请核实理赔进度，并通过脱敏联系方式模拟回访。",
    )

    medical_consultation = storage.create_service_consultation(
        service["user_id"],
        case_id=case.case_id,
        channel="ONLINE",
        deidentified_text="演示咨询：客户希望确认出院用药注意事项。",
        category="medication_question",
        priority="high",
    )
    storage.route_service_consultation(
        service["user_id"],
        medical_consultation["analysis_id"],
        target_role="MEDICAL",
        category="medication_question",
        priority="HIGH",
        note="请结合案件资料复核用药问题，并记录处理反馈。",
    )

    print(f"created_case={case.case_id}")
    print(f"claim_assignee={claim['username']}")
    print(f"medical_assignee={medical['username']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
