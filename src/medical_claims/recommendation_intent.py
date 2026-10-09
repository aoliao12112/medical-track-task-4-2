"""病例推荐意图事实包与本地约束校验。

LLM 只负责在结构化病例事实内识别本次就医重点并制定搜索计划。医院与医生是否可推荐，
仍由权威网页证据门槛和 V2 确定性评分引擎决定。
"""

from __future__ import annotations

import re

from .models import ClaimCase, FieldStatus
from .quality_phase1_models import PatientRecommendationContext, RecommendationIntent


def _field_value(field):
    if field.status not in {FieldStatus.EXTRACTED, FieldStatus.DERIVED}:
        return None
    return field.normalized_value if field.normalized_value is not None else field.value


def build_recommendation_fact_pack(
    case: ClaimCase,
    context: PatientRecommendationContext,
    upstream_primary_disease: str | None = None,
) -> dict:
    """仅发送推荐意图所需的结构化事实，不发送 OCR 全文或身份字段。"""
    return {
        "upstream_clinical_context": {
            "primary_disease": upstream_primary_disease,
            "source": "care_guide.disease_topic" if upstream_primary_disease else None,
            "locked": bool(upstream_primary_disease),
        },
        "recommendation_region": {
            "city": context.city,
            "province": context.province,
            "nearby_major_cities": context.nearby_major_cities,
        },
        "encounter_type": case.encounter_type.value,
        "hospitalization_department": _field_value(case.hospitalization.department),
        "diagnoses": [
            {
                "field_path": f"diagnoses[{index}].name",
                "name": _field_value(item.name),
                "diagnosis_type": item.diagnosis_type.value,
                "certainty": item.certainty.value,
                "confidence": item.name.confidence,
            }
            for index, item in enumerate(case.diagnoses)
            if _field_value(item.name)
        ],
        "procedures": [
            {
                "field_path": f"procedures[{index}].name",
                "name": _field_value(item.name),
                "performed_date": str(_field_value(item.performed_date) or "") or None,
                "is_historical": item.is_historical,
            }
            for index, item in enumerate(case.procedures)
            if _field_value(item.name)
        ],
        "medical_events": [
            {
                "field_path": f"events[{index}]",
                "event_type": item.event_type.value,
                "description": item.description,
                "event_time": str(item.event_time) if item.event_time else None,
            }
            for index, item in enumerate(case.events)
            if item.description
        ],
    }


def _normalized(text: str) -> str:
    return re.sub(r"[\s（）()、，,。:：/\\+-]", "", text or "").lower()


def _supported_by_diagnosis(value: str, diagnoses: list[str]) -> bool:
    target = _normalized(value)
    return any(target in _normalized(item) or _normalized(item) in target for item in diagnoses)


def _longest_common_substring_length(left: str, right: str) -> int:
    left, right = _normalized(left), _normalized(right)
    previous = [0] * (len(right) + 1)
    best = 0
    for char in left:
        current = [0]
        for index, other in enumerate(right, start=1):
            value = previous[index - 1] + 1 if char == other else 0
            current.append(value)
            best = max(best, value)
        previous = current
    return best


def _encounter_focus_score(label: str, case: ClaimCase) -> int:
    weights = {"primary": 8, "discharge": 5, "admission": 4, "secondary": 1, "unknown": 0, "history": -2}
    matching = [
        item for item in case.diagnoses
        if _field_value(item.name) and _supported_by_diagnosis(label, [str(_field_value(item.name))])
    ]
    score = max((weights[item.diagnosis_type.value] for item in matching), default=0)
    current_procedures = [
        str(_field_value(item.name)) for item in case.procedures
        if not item.is_historical and _field_value(item.name)
    ]
    if any(_longest_common_substring_length(label, item) >= 3 for item in current_procedures):
        score += 8
    encounter_events = [
        item.description for item in case.events
        if item.event_type.value in {"admission", "procedure", "discharge"}
    ]
    if any(_longest_common_substring_length(label, item) >= 3 for item in encounter_events):
        score += 4
    return score


def validate_recommendation_intent(
    intent: RecommendationIntent,
    case: ClaimCase,
    context: PatientRecommendationContext,
    upstream_primary_disease: str | None = None,
) -> RecommendationIntent:
    diagnoses = [
        str(_field_value(item.name))
        for item in case.diagnoses
        if _field_value(item.name) and item.certainty.value != "ruled_out"
    ]
    if not _supported_by_diagnosis(intent.primary_disease, diagnoses):
        raise ValueError("RecommendationIntent 主病种无法在当前病例诊断中找到依据")
    if upstream_primary_disease and _normalized(intent.primary_disease) != _normalized(upstream_primary_disease):
        raise ValueError("RecommendationIntent 不得覆盖第三问已经确认的核心病种")
    candidate_labels = list(dict.fromkeys(diagnoses))
    selected_score = _encounter_focus_score(intent.primary_disease, case)
    strongest_score = max((_encounter_focus_score(item, case) for item in candidate_labels), default=0)
    if strongest_score - selected_score >= 5:
        raise ValueError("RecommendationIntent 主病种与本次入院、手术和出院事实不一致")
    unsupported_comorbidities = [
        item for item in intent.comorbidities if not _supported_by_diagnosis(item, diagnoses)
    ]
    if unsupported_comorbidities:
        raise ValueError("RecommendationIntent 合并症包含病例中不存在的诊断")
    if context.city and not any(context.city.removesuffix("市") in query for query in intent.search_queries):
        raise ValueError("RecommendationIntent 搜索计划缺少当前推荐城市")
    if context.city and any(
        item.removesuffix("市") == context.city.removesuffix("市")
        for item in intent.nearby_major_cities
    ):
        raise ValueError("RecommendationIntent 周边城市不能包含当前推荐城市")
    required_specialty = (intent.required_specialty or intent.primary_specialty).strip()
    if not required_specialty:
        raise ValueError("RecommendationIntent 缺少主要专科")
    if not intent.reasoning.strip():
        raise ValueError("RecommendationIntent 缺少病例推理依据")
    invalid_clinician_markers = ("待核验", "某医生", "示例", "医生A", "医生B", "医生C", "医生D")
    verified_clinician_clues = [
        item for item in intent.candidate_clinicians
        if not any(marker in item.doctor_name for marker in invalid_clinician_markers)
    ]
    return intent.model_copy(update={
        "primary_disease": upstream_primary_disease or intent.primary_disease,
        "primary_disease_source": (
            "care_guide.disease_topic" if upstream_primary_disease else "structured_case_inference"
        ),
        "primary_specialty": required_specialty,
        "required_specialty": required_specialty,
        "search_queries": list(dict.fromkeys(item.strip() for item in intent.search_queries if item.strip())),
        "required_capabilities": list(dict.fromkeys(
            item.strip() for item in intent.required_capabilities if item.strip()
        )),
        "secondary_specialties": list(dict.fromkeys(
            item.strip() for item in intent.secondary_specialties if item.strip()
        )),
        "nearby_major_cities": list(dict.fromkeys(
            item.strip() for item in intent.nearby_major_cities if item.strip()
        )),
        "candidate_hospital_clues": list(dict.fromkeys([
            *intent.candidate_hospital_clues,
            *(f"{item.city}：{item.hospital_name}（{item.department}）" for item in intent.candidate_hospitals),
        ])),
        "candidate_clinicians": verified_clinician_clues,
        "candidate_clinician_clues": list(dict.fromkeys([
            *intent.candidate_clinician_clues,
            *(f"{item.hospital_name} {item.doctor_name} {item.department}" for item in verified_clinician_clues),
        ])),
    })


def disease_group_from_intent(intent: RecommendationIntent) -> str:
    text = intent.primary_disease
    known = (
        ("白内障", "cataract"), ("肺炎", "pneumonia"), ("流感", "influenza"),
        ("高血压", "hypertension"), ("冠心病", "coronary_heart_disease"),
        ("糖尿病", "diabetes"), ("胆囊炎", "cholecystitis"), ("骨折", "fracture"),
        ("肺结节", "lung_nodule"), ("阑尾炎", "appendicitis"),
        ("泌尿系结石", "urinary_calculus"),
    )
    for marker, group in known:
        if marker in text:
            return group
    normalized = re.sub(r"[^\w\u3400-\u9fff-]", "", text).lower()
    return f"diagnosis:{normalized}"
