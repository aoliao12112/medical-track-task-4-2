"""第三问温度指引的本地编排、安全校验与降级输出。"""

from __future__ import annotations

import re
from typing import Iterable

from .care_knowledge import DiseaseKnowledgeCard
from .care_models import (
    CareAction,
    CareGuideDraft,
    CareGuideReport,
    CareSafetyCheck,
    CareSection,
    GenerationMethod,
    SafetyStatus,
)
from .models import ClaimCase
from .pain_models import PainPointReport


ENGINE_VERSION = "care-engine-v1"
REQUIRED_LIMITATIONS = (
    "本指引仅依据本次提交的理赔医疗材料生成，不能替代医生诊断。",
    "不要据此自行停药、换药或调整剂量。",
)
UNSAFE_PATTERNS = {
    "UNSUPPORTED_CERTAINTY": re.compile(r"(?:确定|肯定|已经)(?:痊愈|治愈|恶性|良性)"),
}

MEDICATION_ACTION = r"(?:停药|换药|减量|加量|调整剂量|停止服用(?:这个药)?|(?:这个药)?(?:可以)?直接停掉|换成另一种药|(?:自行)?(?:增加|减少)(?:一倍|一半)?剂量|把剂量(?:增加|减少)一(?:倍|半))"
PROTECTIVE_MEDICATION = re.compile(
    r"(?:不要|请勿|切勿|不得|不可|不能|禁止)(?:据此)?(?:自行|擅自|自己|随意|直接)?"
    + MEDICATION_ACTION + r"(?:(?:、|或|和|及)(?:自行|擅自)?" + MEDICATION_ACTION + r")*"
)
MEDICATION_INSTRUCTION = re.compile(MEDICATION_ACTION)
NO_PHYSICIAN = re.compile(r"(?:不需要咨询医生|不用问医生|无需咨询医生)")

def unsafe_medication_instruction(text: str) -> bool:
    # Remove only tightly bound prohibitions, not an entire sentence containing
    # a safe disclaimer. An unsafe instruction elsewhere remains detectable.
    text = re.sub(r"(?:如需|如果需要)(?:调整用药|调整剂量|停药|换药)[，,]?\s*请(?:先)?咨询医生", "", text)
    for clause in re.split(r"[。；;，,！？!?\n]|但是|但|然而", text):
        remainder = PROTECTIVE_MEDICATION.sub("", clause)
        if MEDICATION_INSTRUCTION.search(remainder) or NO_PHYSICIAN.search(remainder):
            return True
    return False


def diagnosis_names(case: ClaimCase) -> list[str]:
    return [
        str(item.name.normalized_value or item.name.value)
        for item in case.diagnoses
        if item.name.value is not None
    ]


def infer_disease_topic(case: ClaimCase, requested: str | None, card: DiseaseKnowledgeCard | None) -> str:
    if requested:
        return requested
    if card:
        return card.disease
    names = diagnosis_names(case)
    return names[0] if names else "本次就医情况"


def _all_evidence_paths(draft: CareGuideDraft) -> list[str]:
    sections: Iterable[CareSection] = (
        draft.opening,
        draft.diagnosis_explanation,
        draft.treatment_review,
        draft.cost_explanation,
        draft.psychological_support,
    )
    paths = [path for section in sections for path in section.evidence_fields]
    paths.extend(path for action in draft.recovery_plan for path in action.evidence_fields)
    paths.extend(path for action in draft.follow_up_plan for path in action.evidence_fields)
    return paths


def _path_exists(case: ClaimCase, path: str) -> bool:
    current: object = case
    for token in path.split("."):
        match = re.fullmatch(r"([A-Za-z_]+)(?:\[(\d+)\])?", token)
        if not match or not hasattr(current, match.group(1)):
            return False
        current = getattr(current, match.group(1))
        if match.group(2) is not None:
            if not isinstance(current, list) or int(match.group(2)) >= len(current):
                return False
            current = current[int(match.group(2))]
    value = getattr(current, "value", current)
    status = getattr(current, "status", None)
    return value is not None and getattr(status, "value", status) not in {"review_required", "conflict"}


def _all_text(draft: CareGuideDraft) -> str:
    sections = [
        draft.opening.content,
        draft.diagnosis_explanation.content,
        draft.treatment_review.content,
        draft.cost_explanation.content,
        draft.psychological_support.content,
        *(item.action + " " + item.timing + " " + item.reason for item in draft.recovery_plan),
        *(item.action + " " + item.timing + " " + item.reason for item in draft.follow_up_plan),
        *draft.red_flags,
        *draft.limitations,
    ]
    return " ".join(sections)


def validate_care_guide(
    case: ClaimCase,
    draft: CareGuideDraft,
    card: DiseaseKnowledgeCard | None,
) -> tuple[list[CareSafetyCheck], float, list[str]]:
    checks: list[CareSafetyCheck] = []
    warnings: list[str] = []
    paths = _all_evidence_paths(draft)
    valid_paths = [path for path in paths if _path_exists(case, path)]
    invalid_paths = sorted(set(paths) - set(valid_paths))
    coverage = len(valid_paths) / len(paths) if paths else 0.0
    checks.append(
        CareSafetyCheck(
            code="EVIDENCE_PATHS",
            status=SafetyStatus.PASSED if paths and not invalid_paths else SafetyStatus.REVIEW,
            message=(
                "所有引用字段均可在结构化病例中定位"
                if paths and not invalid_paths
                else "存在无法定位的引用字段或尚未提供字段引用"
            ),
            field_paths=invalid_paths,
        )
    )

    text = _all_text(draft)
    unsafe_codes = [code for code, pattern in UNSAFE_PATTERNS.items() if pattern.search(text)]
    if unsafe_medication_instruction(text):
        unsafe_codes.append("MEDICATION_CHANGE")
    checks.append(
        CareSafetyCheck(
            code="MEDICAL_BOUNDARY",
            status=SafetyStatus.BLOCKED if unsafe_codes else SafetyStatus.PASSED,
            message="发现越界确定性结论或自行调整用药建议" if unsafe_codes else "未发现越界诊断或自行调整用药建议",
            field_paths=unsafe_codes,
        )
    )

    limitations_ok = all(any(required in item for item in draft.limitations) for required in REQUIRED_LIMITATIONS)
    checks.append(
        CareSafetyCheck(
            code="LIMITATIONS",
            status=SafetyStatus.PASSED if limitations_ok else SafetyStatus.REVIEW,
            message="安全边界完整" if limitations_ok else "缺少不能替代诊断或不得自行调整用药的提示",
        )
    )

    card_reviewed = card is not None and card.review_status.value == "reviewed"
    checks.append(
        CareSafetyCheck(
            code="KNOWLEDGE_REVIEW",
            status=SafetyStatus.PASSED if card_reviewed else SafetyStatus.REVIEW,
            message="病种知识卡已经医学审核" if card_reviewed else "病种知识卡尚未完成医学审核",
        )
    )
    if coverage < 0.6:
        warnings.append("事实字段引用覆盖率低于60%，必须人工补充或复核。")
    return checks, round(coverage, 4), warnings


def build_fallback_draft(
    case: ClaimCase,
    pain_report: PainPointReport | None,
    card: DiseaseKnowledgeCard | None,
    disease_topic: str,
) -> CareGuideDraft:
    names = diagnosis_names(case)
    diagnosis_paths = [f"diagnoses[{index}].name" for index, item in enumerate(case.diagnoses) if item.name.value]
    diagnosis_text = "、".join(names[:3]) if names else "本次材料没有提取到可确认的诊断名称"
    procedure_names = [str(item.name.normalized_value or item.name.value) for item in case.procedures if item.name.value]
    procedure_paths = [f"procedures[{index}].name" for index, item in enumerate(case.procedures) if item.name.value]
    medication_names = [str(item.name.normalized_value or item.name.value) for item in case.medications if item.name.value]
    medication_paths = [f"medications[{index}].name" for index, item in enumerate(case.medications) if item.name.value]
    treatment_parts = []
    if procedure_names:
        treatment_parts.append("操作或治疗包括" + "、".join(procedure_names[:4]))
    if medication_names:
        treatment_parts.append("材料记录的药品包括" + "、".join(medication_names[:4]))
    treatment_text = (
        "；".join(treatment_parts) + "。具体治疗目的和后续安排请以经治医生说明为准。"
        if treatment_parts
        else "本次材料未记录可确认的手术、操作或药品，具体治疗经过请以经治医生说明为准。"
    )
    total = case.costs.total_amount.value
    cost_text = f"材料记录的医疗总费用为{total}元。" if total is not None else "当前材料没有提取到可靠的医疗总费用。"
    cost_text += "个人自付与个人自费必须分别核对，不能由现金支付金额直接推断。"
    pain_actions = []
    if pain_report:
        pain_actions = [
            CareAction(
                action=point.recommended_action,
                timing="下一次复诊或咨询时",
                reason=point.description,
                evidence_fields=point.related_fields[:3],
            )
            for point in pain_report.pain_points[:3]
            if point.related_fields
        ]
    if not pain_actions:
        pain_actions = [CareAction(action="携带本次病历向经治医生确认后续安排", reason="当前材料不足以安全推断具体复查周期")]
    return CareGuideDraft(
        case_id=case.case_id,
        disease_topic=disease_topic,
        opening=CareSection(title="想先对您说", content="经历检查、治疗和理赔整理后感到疲惫或担心是很自然的。下面只根据本次材料梳理已知情况和下一步可确认事项。", evidence_fields=diagnosis_paths[:1]),
        diagnosis_explanation=CareSection(title="诊断怎么理解", content=f"本次材料记录了：{diagnosis_text}。{card.plain_explanation if card else '由于尚未匹配已审核病种知识卡，具体含义需要由医生结合完整病情解释。'}", evidence_fields=diagnosis_paths[:3]),
        treatment_review=CareSection(title="本次治疗回顾", content=treatment_text, evidence_fields=[*procedure_paths[:3], *medication_paths[:3]]),
        cost_explanation=CareSection(title="费用怎么理解", content=cost_text, evidence_fields=["costs.total_amount"] if total is not None else []),
        recovery_plan=pain_actions,
        psychological_support=CareSection(title="给您的提醒", content="看见专业术语或异常标记时感到紧张并不罕见；单个词语或指标不能代替医生对完整病情的判断。", evidence_fields=diagnosis_paths[:1]),
        follow_up_plan=[CareAction(action="把未明确的复查时间、检查项目和用药问题列成清单，复诊时逐项确认", timing="复诊前", reason="避免遗漏关键问题", evidence_fields=diagnosis_paths[:1])],
        red_flags=card.red_flags[:6] if card else ["如果症状明显加重或出现紧急不适，请及时联系医疗机构。"],
        limitations=list(REQUIRED_LIMITATIONS),
    )


def finalize_care_guide(
    case: ClaimCase,
    draft: CareGuideDraft,
    card: DiseaseKnowledgeCard | None,
    method: GenerationMethod,
    extra_warnings: list[str] | None = None,
) -> CareGuideReport:
    if draft.case_id != case.case_id:
        raise ValueError("温度指引与病例 case_id 不一致")
    checks, coverage, warnings = validate_care_guide(case, draft, card)
    warnings.extend(extra_warnings or [])
    review_required = method == GenerationMethod.RULES_FALLBACK or bool(warnings) or any(
        check.status != SafetyStatus.PASSED for check in checks
    )
    return CareGuideReport(
        case_id=case.case_id,
        guide=draft,
        knowledge_card_id=card.card_id if card else None,
        knowledge_review_status=card.review_status if card else "draft_pending_medical_review",
        generation_method=method,
        safety_checks=checks,
        evidence_coverage=coverage,
        review_required=review_required,
        engine_version=ENGINE_VERSION,
        warnings=warnings,
    )
