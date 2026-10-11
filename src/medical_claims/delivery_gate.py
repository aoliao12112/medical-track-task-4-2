"""Deterministic delivery policy over existing signals; no medical re-evaluation."""
from enum import Enum
from pydantic import Field
from .models import ClaimCase, StrictModel
from .care_models import CareGuideReport, GenerationMethod, SafetyStatus, ReviewStatus
from .pain_models import PainPointReport, PainPointType, PainSeverity
from .review_models import ReviewTarget


MIN_AUTO_EVIDENCE_COVERAGE = .8
MIN_AUTO_RECOMMENDATION_CONFIDENCE = .5
MIN_AUTO_RECOMMENDATION_SAMPLE = 3


class GateDecision(str, Enum):
    AUTO_DELIVER = "AUTO_DELIVER"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    BLOCKED = "BLOCKED"


class DeliveryGateResult(StrictModel):
    decision: GateDecision
    reasons: list[str]
    review_targets: list[ReviewTarget] = Field(default_factory=list)


def evaluate_delivery_gate(care: CareGuideReport, case: ClaimCase,
                           pain: PainPointReport | None, recommendation: dict) -> DeliveryGateResult:
    blocked = [f"safety_checks.{check.code}=blocked: {check.message}"
               for check in care.safety_checks if check.status == SafetyStatus.BLOCKED]
    if blocked:
        return DeliveryGateResult(decision=GateDecision.BLOCKED, reasons=blocked)
    reasons = []
    targets = set()

    def concern(text, target=ReviewTarget.CARE_GUIDE):
        reasons.append(text)
        targets.add(target)

    if care.review_required:
        concern("care_guide.review_required=true")
    if care.generation_method == GenerationMethod.RULES_FALLBACK:
        concern("generation_method=rules_fallback：降级草稿需复核")
    if care.evidence_coverage < MIN_AUTO_EVIDENCE_COVERAGE:
        concern(f"evidence_coverage={care.evidence_coverage} < {MIN_AUTO_EVIDENCE_COVERAGE}")
    if not care.safety_checks:
        concern("未提供 safety_checks，不能视为已通过检查")
    for check in care.safety_checks:
        if check.status == SafetyStatus.REVIEW:
            concern(f"safety_checks.{check.code}=review: {check.message}")
    if care.knowledge_review_status != ReviewStatus.REVIEWED:
        concern("knowledge_review_status 未经审核")
    for warning in care.warnings:
        concern("care warning: " + warning)
    # Publication safety is narrower than field-review completeness.  Only a
    # disputed primary diagnosis or an explicit high-risk medical flag belongs
    # in the case-level gate; ordinary dates, fee details and low-risk tests are
    # retained as module warnings instead of blocking the whole delivery.
    primary = [item for item in case.diagnoses if item.diagnosis_type.value == "primary"]
    primary = primary or [item for item in case.diagnoses if item.diagnosis_type.value == "discharge"][:1]
    for diagnosis in primary[:1]:
        if diagnosis.name.status.value in {"conflict", "review_required"}:
            concern(f"主诊断状态需要确认：{diagnosis.name.status.value}")
    safety_markers = ("高风险", "禁忌", "过敏", "剂量", "停药", "换药", "治疗风险", "事实冲突")
    for flag in case.quality_flags:
        if flag.severity.value == "error" or any(marker in flag.message for marker in safety_markers):
            concern(f"关键病例风险：{flag.code}: {flag.message}")
    if pain:
        medication_types = {PainPointType.MEDICATION_DIAGNOSIS_REVIEW, PainPointType.DUPLICATE_MEDICATION_REVIEW}
        for point in pain.pain_points:
            if point.type in medication_types or point.severity in {PainSeverity.HIGH, PainSeverity.CRITICAL}:
                concern(f"medical/pain concern: {point.type.value}, severity={point.severity.value}")
    # Unavailable recommendations are a valid care-only path, not a risk failure.
    for entry in recommendation.get("reports", []):
        if entry["status"] != "available":
            continue
        report = entry["report"]
        for warning in report.get("warnings", []):
            concern("recommendation warning: " + warning, ReviewTarget.RECOMMENDATION)
        for item in report.get("recommendations", []):
            entity_id = item.get("entity_id") or item.get("hospital_id") or item.get("clinician_id")
            confidence = item.get("confidence", item.get("recommendation_confidence", 0))
            if str(item.get("evidence_status", "")).upper() == "CONTRADICTED":
                return DeliveryGateResult(
                    decision=GateDecision.BLOCKED,
                    reasons=[f"recommendation {entity_id} 存在明确事实矛盾"],
                    review_targets=[ReviewTarget.RECOMMENDATION],
                )
            if confidence < MIN_AUTO_RECOMMENDATION_CONFIDENCE:
                concern(f"recommendation {entity_id} confidence={confidence} < .5", ReviewTarget.RECOMMENDATION)
            if "sample_size" in item and item["sample_size"] < MIN_AUTO_RECOMMENDATION_SAMPLE:
                concern(f"recommendation {entity_id} sample_size={item['sample_size']} < 3", ReviewTarget.RECOMMENDATION)
            item_reasons = item.get("reasons") or item.get("evidence") or []
            if len(item_reasons) < 2:
                concern("recommendation 缺少两条可追溯理由", ReviewTarget.RECOMMENDATION)
            elif item.get("reasons") and any(not r.get("evidence_fields") for r in item_reasons):
                concern("recommendation 缺少字段依据", ReviewTarget.RECOMMENDATION)
    return DeliveryGateResult(decision=GateDecision.REVIEW_REQUIRED if reasons else GateDecision.AUTO_DELIVER,
        reasons=list(dict.fromkeys(reasons)) or ["已有安全检查全部通过，非降级指引，证据覆盖>=.8，无现有高风险标记"],
        review_targets=sorted(targets, key=lambda target: target.value))
