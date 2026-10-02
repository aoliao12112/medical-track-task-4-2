"""将第一、二问结果压缩为第三问可使用的事实包。"""

from __future__ import annotations

from typing import Any

from .care_knowledge import DiseaseKnowledgeCard, knowledge_card_payload
from .models import ClaimCase, FieldValue
from .pain_models import PainPointReport


def _field_fact(path: str, field: FieldValue[Any]) -> dict[str, object] | None:
    value = field.normalized_value if field.normalized_value is not None else field.value
    if value is None:
        return None
    return {
        "path": path,
        "value": str(value),
        "confidence": field.confidence,
        "status": field.status.value,
        "evidence": [item.model_dump(mode="json") for item in field.evidence[:1]],
    }


def build_case_fact_pack(case: ClaimCase, pain_report: PainPointReport | None) -> dict[str, object]:
    facts: list[dict[str, object]] = []
    for path, field in (
        ("hospitalization.admission_date", case.hospitalization.admission_date),
        ("hospitalization.discharge_date", case.hospitalization.discharge_date),
        ("hospitalization.length_of_stay_days", case.hospitalization.length_of_stay_days),
        ("hospitalization.department", case.hospitalization.department),
        ("costs.total_amount", case.costs.total_amount),
        ("costs.policy_scope_amount", case.costs.policy_scope_amount),
        ("costs.outside_policy_amount", case.costs.outside_policy_amount),
        ("costs.personal_burden.within_policy_self_pay", case.costs.personal_burden.within_policy_self_pay),
        ("costs.personal_burden.outside_policy_self_expense", case.costs.personal_burden.outside_policy_self_expense),
    ):
        if fact := _field_fact(path, field):
            facts.append(fact)

    for index, diagnosis in enumerate(case.diagnoses[:12]):
        if fact := _field_fact(f"diagnoses[{index}].name", diagnosis.name):
            facts.append(fact)
    for index, procedure in enumerate(case.procedures[:10]):
        if fact := _field_fact(f"procedures[{index}].name", procedure.name):
            facts.append(fact)
    for index, medication in enumerate(case.medications[:12]):
        medication_facts = []
        for name, field in (
            ("name", medication.name),
            ("dose", medication.dose),
            ("frequency", medication.frequency),
            ("route", medication.route),
            ("duration", medication.duration),
        ):
            if fact := _field_fact(f"medications[{index}].{name}", field):
                medication_facts.append(fact)
        facts.extend(medication_facts)
    for index, examination in enumerate(case.examinations[:16]):
        if fact := _field_fact(f"examinations[{index}].conclusion", examination.conclusion):
            facts.append(fact)

    pain_points = []
    if pain_report:
        pain_points = [
            {
                "type": point.type.value,
                "dimension": point.dimension.value,
                "severity": point.severity.value,
                "description": point.description,
                "recommended_action": point.recommended_action,
                "related_fields": point.related_fields,
            }
            for point in pain_report.pain_points
        ]
    return {
        "case_id": case.case_id,
        "encounter_type": case.encounter_type.value,
        "facts": facts,
        "pain_points": pain_points,
        "quality_flags": [
            {
                "code": flag.code,
                "severity": flag.severity.value,
                "message": flag.message,
                "field_path": flag.field_path,
            }
            for flag in case.quality_flags[:20]
        ],
        "cost_semantics": (
            "个人自付是目录内个人承担，个人自费是目录外个人承担；"
            "现金支付和个人账户支付只是付款渠道，禁止互相替代。"
        ),
    }


def build_care_generation_context(
    case: ClaimCase,
    pain_report: PainPointReport | None,
    card: DiseaseKnowledgeCard | None,
    disease_topic: str,
) -> dict[str, object]:
    return {
        "task": "生成理赔后个性化温度指引草稿",
        "disease_topic": disease_topic,
        "case_fact_pack": build_case_fact_pack(case, pain_report),
        "knowledge_card": knowledge_card_payload(card),
    }

