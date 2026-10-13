"""DeepSeek structured-output prompt for one-case medical quality evaluation."""

from __future__ import annotations

from typing import Any

from .models import ClaimCase
from .pain_models import PainPointReport
from .quality_phase1_models import LLMQualityEvaluation


QUALITY_EVALUATION_INSTRUCTIONS = """你是一名医疗案件品质评价辅助模型。你的任务不是诊断患者，也不是提供新的治疗建议，而是仅根据输入的病例结构化信息，对本次就医过程的记录与合理性进行评价。
必须严格基于输入材料；不得补充、猜测或修改病例事实，不得输出新诊断或个体化治疗方案，不得因为资料缺失而判定医疗质量差，也不得输出最终综合分。
固定评价六个维度：diagnosis_treatment_consistency（诊疗一致性）、medication_rationality（用药合理性）、procedure_rationality（手术/操作合理性）、risk_management（风险管理）、discharge_followup（出院与随访完整性）、record_completeness（病历与诊疗完整性）。
每个维度必须输出 dimension、score、status、level、reason、evidence、limitations。只有材料足以支持判断时使用 status=SCORED 且 score 为 0-100；证据不足时必须 score=null、status=NOT_SCORED；已有部分信息但医学合理性仍需人工确认时必须 score=null、status=NEEDS_REVIEW。不得为了完整分数使用默认分。
评价诊疗一致性时核对诊断、检查、处置和治疗的逻辑；评价用药时只使用已记录的药名、剂量、频次、途径、疗程及病例背景；没有手术或操作不得作为低质量；风险管理不得创造风险或并发症；出院与随访应核对出院信息、用药、注意事项和复诊计划；病历完整性应核对关键字段、时间线和证据可追溯性。
评分参考：90-100 优秀，80-89 良好，70-79 一般，60-69 需关注，低于60 重点关注。低分必须有明确病例证据，缺资料应优先 NOT_SCORED。
reason 简要解释判断；evidence 只列输入中真实存在且直接相关的事实，优先保留可用的引用标识；limitations 说明影响判断的缺失或不确定性。
输出必须严格符合 JSON schema，不得输出 Markdown、代码块、JSON 外解释或 overall_score。"""


def _evidence(items: list[Any]) -> list[dict[str, Any]]:
    return [
        {"document_id": item.document_id, "page": item.page, "text": item.text}
        for item in items
    ]


def _field(value: Any) -> dict[str, Any]:
    raw = value.value
    if hasattr(raw, "isoformat"):
        raw = raw.isoformat()
    elif raw is not None and not isinstance(raw, (str, int, float, bool)):
        raw = str(raw)
    return {"value": raw, "evidence": _evidence(value.evidence)}


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _clean(item) for key, item in value.items()
            if key not in {"confidence", "status", "derivation", "bbox", "examination_id"}
        }
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def build_quality_case_context(
    case: ClaimCase,
    pain_report: PainPointReport | None = None,
) -> dict[str, Any]:
    """Return a minimum, deidentified fact pack; never includes images or case_id."""
    hospitalization = {
        name: _field(getattr(case.hospitalization, name))
        for name in ("admission_date", "discharge_date", "length_of_stay_days", "department")
        if hasattr(case.hospitalization, name)
    }
    context: dict[str, Any] = {
        "encounter_type": case.encounter_type.value,
        "hospitalization": hospitalization,
        "diagnoses": [
            {"name": _field(item.name), "type": item.diagnosis_type.value, "certainty": item.certainty.value}
            for item in case.diagnoses
        ],
        "procedures": [
            {"name": _field(item.name), "date": _field(item.performed_date), "body_site": _field(item.body_site)}
            for item in case.procedures
        ],
        "medications": [
            {name: _field(getattr(item, name)) for name in ("name", "dose", "frequency", "route", "duration")}
            for item in case.medications
        ],
        "examinations": [_clean(item.model_dump(mode="json")) for item in case.examinations],
        "costs": _clean(case.costs.model_dump(mode="json")),
        "medical_events": [
            {
                "event_type": item.event_type.value,
                "event_time": item.event_time.isoformat() if item.event_time else None,
                "description": item.description,
                "evidence": _evidence(item.evidence),
            }
            for item in case.events
        ],
        "quality_flags": [
            {"severity": item.severity.value, "message": item.message, "field_path": item.field_path}
            for item in case.quality_flags
        ],
    }
    if pain_report is not None:
        context["confirmed_pain_context"] = [
            {
                "dimension": item.dimension.value,
                "type": item.type.value,
                "severity": item.severity.value,
                "title": item.title,
                "description": item.description,
            }
            for item in pain_report.pain_points
        ]
    return context


def build_quality_evaluation_body(
    case: ClaimCase,
    pain_report: PainPointReport | None,
    *,
    model: str,
    max_output_tokens: int,
) -> dict[str, Any]:
    import json

    return {
        "model": model,
        "instructions": QUALITY_EVALUATION_INSTRUCTIONS,
        "input": json.dumps({
            "instruction": "只根据病例数据完成六维评价；不要输出综合分。",
            "quality_case_context": build_quality_case_context(case, pain_report),
        }, ensure_ascii=False),
        "reasoning": {"effort": "none"},
        "temperature": 0.1,
        "max_output_tokens": max_output_tokens,
        "text": {"format": {
            "type": "json_schema",
            "name": "medical_quality_evaluation",
            "schema": LLMQualityEvaluation.model_json_schema(),
        }},
    }
