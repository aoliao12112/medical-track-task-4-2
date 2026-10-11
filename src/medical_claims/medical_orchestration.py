"""MEDICAL workflow orchestration metadata and module-level eligibility.

This module does not implement medical algorithms.  It decides which existing
algorithm can run from the current effective structured case and keeps
generation, review and delivery states separate.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from .models import ClaimCase, FieldStatus, StrictModel


class MedicalModule(str, Enum):
    PAIN = "pain"
    CARE = "care"
    TREATMENT_QUALITY = "treatment_quality"
    RECOMMENDATION = "recommendation"


class GenerationStatus(str, Enum):
    NOT_GENERATED = "NOT_GENERATED"
    READY = "READY"
    GENERATING = "GENERATING"
    GENERATED = "GENERATED"
    INPUT_INSUFFICIENT = "INPUT_INSUFFICIENT"
    STALE = "STALE"


class ModuleReviewStatus(str, Enum):
    NOT_REVIEWED = "NOT_REVIEWED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"


class ModuleState(StrictModel):
    module: MedicalModule
    generation_status: GenerationStatus
    review_status: ModuleReviewStatus = ModuleReviewStatus.NOT_REVIEWED
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    result_available: bool = False
    content_fingerprint: str | None = None


def _value(field: Any) -> Any | None:
    if field is None or field.status not in {
        FieldStatus.EXTRACTED, FieldStatus.DERIVED, FieldStatus.REVIEW_REQUIRED,
    }:
        return None
    return field.normalized_value if field.normalized_value is not None else field.value


def available_case_facts(case: ClaimCase) -> dict[str, bool]:
    """Return coarse facts only; a missing dimension never erases other facts."""
    hospitalization = case.hospitalization
    return {
        "diagnosis": any(_value(item.name) for item in case.diagnoses),
        "procedure": any(_value(item.name) for item in case.procedures),
        "medication": any(_value(item.name) for item in case.medications),
        "examination": any(_value(item.name) or _value(item.conclusion) for item in case.examinations),
        "cost": _value(case.costs.total_amount) is not None or bool(case.costs.breakdown),
        "hospitalization": any(_value(field) is not None for field in (
            hospitalization.admission_date, hospitalization.discharge_date,
            hospitalization.length_of_stay_days, hospitalization.department,
        )),
        "event": bool(case.events),
    }


def primary_disease(case: ClaimCase) -> str | None:
    candidates = [item for item in case.diagnoses if _value(item.name)]
    primary = next((item for item in candidates if item.diagnosis_type.value == "primary"), None)
    selected = primary or next(
        (item for item in candidates if item.diagnosis_type.value == "discharge"), None
    )
    selected = selected or (candidates[0] if candidates else None)
    if selected is None or selected.certainty.value in {"ruled_out", "suspected"}:
        return None
    if selected.name.status in {FieldStatus.CONFLICT, FieldStatus.REVIEW_REQUIRED}:
        return None
    return str(_value(selected.name))


def eligibility(
    module: MedicalModule,
    case: ClaimCase,
    *,
    pain_available: bool,
    city: str | None = None,
) -> tuple[bool, list[str], list[str]]:
    facts = available_case_facts(case)
    warnings: list[str] = []
    if module == MedicalModule.PAIN:
        if not any(facts.values()):
            return False, ["结构化病例中没有可用于分析的病例事实"], warnings
        if not facts["cost"]:
            warnings.append("费用信息不足，费用维度将按证据不足处理")
        if not facts["medication"]:
            warnings.append("未获得可评价用药，不影响其他痛点维度")
        return True, [], warnings
    if module == MedicalModule.CARE:
        blockers = []
        # A diagnosis that is merely pending confirmation is still a usable
        # provisional care topic.  Recommendation needs a reliable primary
        # disease, while Care can be generated cautiously from any diagnosis
        # fact and reviewed afterwards.
        if not (facts["diagnosis"] or facts["procedure"] or facts["event"]):
            blockers.append("缺少可识别的诊疗主题")
        if not pain_available:
            blockers.append("痛点分析尚未生成")
        if not facts["cost"]:
            warnings.append("费用解释将标记为证据不足")
        return not blockers, blockers, warnings
    if module == MedicalModule.TREATMENT_QUALITY:
        if not (facts["diagnosis"] or facts["procedure"] or facts["medication"]):
            return False, ["缺少诊断或治疗事实"], warnings
        return True, [], warnings
    if module == MedicalModule.RECOMMENDATION:
        blockers = []
        if not primary_disease(case):
            blockers.append("主病种无法可靠确定")
        if not (facts["procedure"] or facts["medication"] or facts["event"]):
            blockers.append("基本治疗目标缺少病例事实支持")
        if not (city or "").strip():
            blockers.append("当前推荐地区尚未提供")
        return not blockers, blockers, warnings
    raise ValueError(f"未知医疗模块：{module}")


# Module-level invalidation is intentionally conservative.  A corrected field
# only stales modules that consume that category; unchanged modules are rebased
# to the new effective-case fingerprint by storage.
DEPENDENCIES: dict[MedicalModule, tuple[str, ...]] = {
    MedicalModule.PAIN: (
        "diagnoses", "procedures", "medications", "examinations", "costs", "hospitalization", "events",
    ),
    MedicalModule.CARE: (
        "diagnoses", "procedures", "medications", "examinations", "costs", "hospitalization", "events",
    ),
    MedicalModule.TREATMENT_QUALITY: (
        "diagnoses", "procedures", "medications", "costs", "hospitalization", "events",
    ),
    MedicalModule.RECOMMENDATION: (
        "diagnoses", "procedures", "events", "hospitalization.department",
    ),
}


def modules_affected_by(field_path: str) -> set[MedicalModule]:
    return {
        module for module, prefixes in DEPENDENCIES.items()
        if any(field_path == prefix or field_path.startswith(prefix + ".")
               or field_path.startswith(prefix + "[") for prefix in prefixes)
    }
