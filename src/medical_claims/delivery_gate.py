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
    for flag in case.quality_flags:
        if flag.severity.value in {"warning", "error"}:
            concern(f"case quality flag: {flag.code}: {flag.message}")

    def walk(value, path="case"):
        if isinstance(value, dict):
            if value.get("status") in {"conflict", "review_required"}:
                concern(f"{path}.status={value['status']}")
            for key, child in value.items():
                walk(child, path + "." + key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")

    walk(case.model_dump(mode="json"))
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
            if item["confidence"] < MIN_AUTO_RECOMMENDATION_CONFIDENCE:
                concern(f"recommendation {item['entity_id']} confidence={item['confidence']} < .5", ReviewTarget.RECOMMENDATION)
            if item["sample_size"] < MIN_AUTO_RECOMMENDATION_SAMPLE:
                concern(f"recommendation {item['entity_id']} sample_size={item['sample_size']} < 3", ReviewTarget.RECOMMENDATION)
            if len(item.get("reasons", [])) < 2 or any(not r.get("evidence_fields") for r in item.get("reasons", [])):
                concern("recommendation 缺少两条有字段依据的理由", ReviewTarget.RECOMMENDATION)
    return DeliveryGateResult(decision=GateDecision.REVIEW_REQUIRED if reasons else GateDecision.AUTO_DELIVER,
        reasons=list(dict.fromkeys(reasons)) or ["已有安全检查全部通过，非降级指引，证据覆盖>=.8，无现有高风险标记"],
        review_targets=sorted(targets, key=lambda target: target.value))
