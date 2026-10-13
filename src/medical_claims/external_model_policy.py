"""第三问与第四问共用的外部大模型授权策略。"""

from __future__ import annotations

from enum import Enum
import os


class ExternalModelPurpose(str, Enum):
    CARE_GUIDE = "care_guide"
    RECOMMENDATION_INTENT = "recommendation_intent"
    QUALITY_EVALUATION = "quality_evaluation"


class ExternalModelAuthorizationError(PermissionError):
    pass


def require_external_model_authorization(
    case_metadata: dict,
    purpose: ExternalModelPurpose,
) -> None:
    """医疗文本模型用途共用同一案件授权；不创建用途级重复确认。"""
    if purpose not in {
        ExternalModelPurpose.CARE_GUIDE,
        ExternalModelPurpose.RECOMMENDATION_INTENT,
        ExternalModelPurpose.QUALITY_EVALUATION,
    }:
        raise ExternalModelAuthorizationError("当前外部模型用途未纳入授权范围")
    if os.getenv("EXTERNAL_MODEL_PROCESSING_ENABLED", "true").lower() in {"0", "false", "no"}:
        raise ExternalModelAuthorizationError("当前部署已关闭外部大模型处理")
    if case_metadata.get("consent_status") != "authorized":
        raise ExternalModelAuthorizationError("当前案件尚未确认材料使用及外部模型处理授权")
    if case_metadata.get("deidentification_status") != "confirmed":
        raise ExternalModelAuthorizationError("当前案件尚未确认完成身份信息脱敏")
