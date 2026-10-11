"""Mock inbox preparation over saved content, with no real messaging channels."""
from enum import Enum
import hashlib
from typing import Literal
from pydantic import Field
from .models import ClaimCase, StrictModel
from .care_models import CareGuideReport
from .pain_models import PainPointReport
from .delivery_gate import DeliveryGateResult, GateDecision, evaluate_delivery_gate
from .quality_integration import case_recommendations
from .storage import CaseStorage, WorkflowConflict, encode


UNAVAILABLE_RECOMMENDATION_NOTICE = "当前历史数据不足，暂不提供医院、医生或治疗方案推荐。"


class DeliveryMode(str, Enum):
    AUTO = "AUTO"
    HUMAN_APPROVED = "HUMAN_APPROVED"


class SendDeliveryRequest(StrictModel):
    content_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class DeliveryRecord(StrictModel):
    delivery_id: str
    case_id: str
    channel: Literal["mock_web_inbox"] = "mock_web_inbox"
    delivery_mode: DeliveryMode
    care_included: bool = True
    recommendation_included: bool
    risk_gate_result: DeliveryGateResult
    content_fingerprint: str
    created_at: str
    status: Literal["DELIVERED_MOCK"] = "DELIVERED_MOCK"
    content: dict


def prepare_delivery_content(storage: CaseStorage, case_id: str):
    snapshot = storage.delivery_input_snapshot(case_id)
    results = snapshot["results"]
    if results["care_result"] is None:
        raise WorkflowConflict("请先生成并保存 Care Guide")
    persisted_recommendation = storage.load_recommendation_report(case_id)
    if persisted_recommendation and persisted_recommendation.get("status") != "INVALIDATED" \
            and persisted_recommendation.get("items"):
        recommendation = {"reports": [{
            "status": "available",
            "report": {
                "report_id": persisted_recommendation["report_id"],
                "warnings": (persisted_recommendation.get("report") or {}).get("warnings", []),
                "recommendations": persisted_recommendation["items"],
            },
        }]}
    else:
        recommendation = case_recommendations(storage, case_id)
    if encode(snapshot) != encode(storage.delivery_input_snapshot(case_id)):
        raise WorkflowConflict("准备触达期间案件或画像已改变，请重试")
    care = CareGuideReport.model_validate(results["care_result"])
    case = ClaimCase.model_validate(results["structured_result"])
    pain = PainPointReport.model_validate(results["pain_result"]) if results["pain_result"] else None
    gate = evaluate_delivery_gate(care, case, pain, recommendation)
    metadata = storage.get_case(case_id)
    restrictions = []
    if metadata["status"] == "PROCESSING":
        restrictions.append("案件正在重新结构化，不能触达旧结果")
    if snapshot["consent_status"] != "authorized" or snapshot["deidentification_status"] != "confirmed":
        restrictions.append("案件未声明已授权且已确认脱敏")
    if restrictions:
        gate = DeliveryGateResult(decision=GateDecision.BLOCKED, reasons=restrictions + gate.reasons)
    available = [entry["report"] for entry in recommendation["reports"]
                 if entry["status"] == "available" and entry["report"]["recommendations"]]
    unavailable = [entry["report"] for entry in recommendation["reports"] if entry["status"] != "available"]
    content = {"care_guide": care.model_dump(mode="json"), "recommendation": available,
               "recommendation_unavailable_reason": UNAVAILABLE_RECOMMENDATION_NOTICE if unavailable else None,
               "unavailable_recommendation_reports": unavailable,
               "data_source": snapshot["data_source"], "mock_notice": "模拟用户触达 · mock_web_inbox"}
    fingerprint = hashlib.sha256(encode({"snapshot": snapshot, "recommendation": recommendation}).encode()).hexdigest()
    return {"case_id": case_id, "gate": gate, "content_fingerprint": fingerprint,
            "content": content, "snapshot": snapshot}
