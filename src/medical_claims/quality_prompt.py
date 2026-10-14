"""DeepSeek structured-output prompt for one-case medical quality evaluation."""

from __future__ import annotations

from typing import Any

from .models import ClaimCase
from .pain_models import PainPointReport
from .quality_phase1_models import LLMQualityEvaluation


QUALITY_EVALUATION_INSTRUCTIONS = """你是一名医疗案件品质评价辅助模型。你的任务不是诊断患者，也不是提供新的治疗建议，而是仅根据输入的病例结构化信息，对本次就医过程的记录与合理性进行评价。
必须严格基于输入材料；不得补充、猜测或修改病例事实，不得输出新诊断或个体化治疗方案，不得因为资料缺失而判定医疗质量差，也不得输出最终综合分。
固定评价六个维度：diagnosis_treatment_consistency（诊疗一致性）、medication_rationality（用药合理性）、procedure_rationality（手术/操作合理性）、risk_management（风险管理）、discharge_followup（出院与随访完整性）、record_completeness（病历与诊疗完整性）。
每个维度必须输出 dimension、score、status、level、reason、evidence、limitations。材料足以支持该维度中“可观察部分”的判断且无需额外医学确认时，使用 status=SCORED、score=0-100；材料足以形成有证据的初步评价，但最终结论仍需医生确认时，使用 status=NEEDS_REVIEW、score=0-100，并同时写明证据、限制和待确认原因；证据不足以形成任何可靠方向性评价时，必须 score=null、status=NOT_SCORED。不得为了完整分数使用默认分，也不得因为不能证明全部临床正确性，就把能够由记录直接核验的维度一律改成 NOT_SCORED。
评价诊疗一致性时核对诊断、检查、处置和治疗的逻辑；评价用药时只使用已记录的药名、剂量、频次、途径、疗程及病例背景；没有手术或操作不得作为低质量；风险管理不得创造风险或并发症；出院与随访应核对出院信息、用药、注意事项和复诊计划；病历完整性应核对关键字段、时间线和证据可追溯性。
按以下证据边界判断，不得无条件保守降级：
- record_completeness：只评价材料记录质量。存在诊断、治疗/操作、时间线和可追溯短证据时应当 SCORED；缺少个别临床字段应降低分数并写入 limitations，而不是要求证明治疗绝对正确。
- discharge_followup：存在明确出院事件，并记录出院用药、注意事项、复诊时间或危险信号中的有效组合时可以 SCORED；仅有“已出院”而无后续安排才 NOT_SCORED 或 NEEDS_REVIEW。
- medication_rationality：药名、剂量、频次、途径、疗程及对应诊断较完整时，可以对“处方记录完整性和表面一致性”SCORED；缺少过敏史、禁忌证或个体化信息必须在 limitations 中声明，并据缺失程度降分。核心处方要素、适应证背景严重不足，无法形成可靠方向性评价时必须 NOT_SCORED，不得从费用清单推断剂量、频次、途径或疗程。
- risk_management：有异常检查、风险标志、痛点、危险信号及对应处理/复诊安排时可以对已记录的风险响应 SCORED；证据能支持初步判断但仍需医生确认时可 NEEDS_REVIEW 并保留分数；没有任何风险信息或响应记录时必须 NOT_SCORED，不得猜测。
- diagnosis_treatment_consistency 与 procedure_rationality 涉及临床合理性；诊断、检查、处置、手术指征或病程记录足以支持方向性评价但最终结论仍需医生确认时，可 NEEDS_REVIEW 并保留分数；仅有名称、缺少检查依据、适应证或关键病情而无法形成可靠方向时必须 NOT_SCORED。
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
    normalized = value.normalized_value
    if hasattr(raw, "isoformat"):
        raw = raw.isoformat()
    elif raw is not None and not isinstance(raw, (str, int, float, bool)):
        raw = str(raw)
    if hasattr(normalized, "isoformat"):
        normalized = normalized.isoformat()
    elif normalized is not None and not isinstance(normalized, (str, int, float, bool)):
        normalized = str(normalized)
    return {
        "value": raw,
        "normalized_value": normalized,
        "status": value.status.value,
        "warnings": list(value.warnings),
        "evidence": _evidence(value.evidence),
    }


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _clean(item) for key, item in value.items()
            if key not in {"confidence", "derivation", "bbox", "examination_id"}
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
            {"name": _field(item.name), "code": _field(item.code),
             "type": item.diagnosis_type.value, "certainty": item.certainty.value}
            for item in case.diagnoses
        ],
        "procedures": [
            {"name": _field(item.name), "date": _field(item.performed_date),
             "body_site": _field(item.body_site), "is_historical": item.is_historical}
            for item in case.procedures
        ],
        "medications": [
            {name: _field(getattr(item, name)) for name in (
                "name", "dose", "frequency", "route", "duration", "usage_condition",
                "max_daily_dose", "additional_instructions",
            )}
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
                "related_entity_ids": item.related_entity_ids,
            }
            for item in case.events
        ],
        "quality_flags": [
            {"code": item.code, "severity": item.severity.value, "message": item.message,
             "field_path": item.field_path, "document_id": item.document_id}
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
                "evidence": _clean(item.model_dump(mode="json").get("evidence", [])),
                "related_fields": item.related_fields,
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
        "temperature": 0,
        "max_output_tokens": max_output_tokens,
        "text": {"format": {
            "type": "json_schema",
            "name": "medical_quality_evaluation",
            "schema": LLMQualityEvaluation.model_json_schema(),
        }},
    }
