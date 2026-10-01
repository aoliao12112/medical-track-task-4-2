"""规则与 LLM 语义候选的合并、证据校验、去重和分级。"""

from __future__ import annotations

import re
from copy import deepcopy

from .models import ClaimCase, Evidence
from .pain_models import (
    DetectorInfo,
    DetectorMethod,
    PainDimension,
    PainPoint,
    PainPointReport,
    PainPointSummary,
    PainPointType,
    PainSeverity,
)
from .pain_rules import detect_rule_pain_points


ENGINE_VERSION = "pain-engine-v3"
SEMANTIC_TYPES = {
    PainPointType.MEDICATION_DIAGNOSIS_REVIEW,
    PainPointType.DIAGNOSIS_TERM_HARD_TO_UNDERSTAND,
    PainPointType.CARE_GUIDANCE_INCOMPLETE,
}
SEMANTIC_DIMENSIONS = {
    PainPointType.MEDICATION_DIAGNOSIS_REVIEW: PainDimension.CLINICAL,
    PainPointType.DIAGNOSIS_TERM_HARD_TO_UNDERSTAND: PainDimension.EXPERIENCE,
    PainPointType.CARE_GUIDANCE_INCOMPLETE: PainDimension.EXPERIENCE,
}
SEVERITY_RANK = {
    PainSeverity.LOW: 1,
    PainSeverity.MEDIUM: 2,
    PainSeverity.HIGH: 3,
    PainSeverity.CRITICAL: 4,
}
TYPE_LIMITS = {
    PainPointType.ABNORMAL_EXAMINATION_FOLLOWUP: 5,
    PainPointType.MEDICATION_INSTRUCTION_INCOMPLETE: 3,
    PainPointType.DIAGNOSIS_TERM_HARD_TO_UNDERSTAND: 1,
    PainPointType.DUPLICATE_MEDICATION_REVIEW: 1,
    PainPointType.MEDICATION_DIAGNOSIS_REVIEW: 1,
    PainPointType.CARE_GUIDANCE_INCOMPLETE: 2,
}
TOTAL_POINT_LIMIT = 12


def _source_evidence(case: ClaimCase) -> list[Evidence]:
    items: list[Evidence] = []

    def visit(node: object) -> None:
        if isinstance(node, dict):
            evidence = node.get("evidence")
            if isinstance(evidence, list):
                for item in evidence:
                    if isinstance(item, dict) and item.get("document_id") and item.get("text"):
                        items.append(Evidence.model_validate(item))
            for key, value in node.items():
                if key != "evidence":
                    visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(case.model_dump(mode="json"))
    unique: dict[tuple[str, int | None, str], Evidence] = {}
    for item in items:
        unique[(item.document_id, item.page, item.text)] = item
    return list(unique.values())


def _verified_evidence(candidate: Evidence, sources: list[Evidence]) -> Evidence | None:
    candidate_text = candidate.text.strip()
    if not candidate_text:
        return None
    for source in sources:
        if source.document_id != candidate.document_id:
            continue
        if candidate.page is not None and source.page is not None and candidate.page != source.page:
            continue
        source_text = source.text.strip()
        if candidate_text in source_text or source_text in candidate_text:
            return Evidence(
                document_id=source.document_id,
                page=source.page,
                text=candidate_text if candidate_text in source_text else source_text,
                bbox=source.bbox,
            )
    return None


def _sanitize_semantic_points(case: ClaimCase, points: list[PainPoint]) -> tuple[list[PainPoint], list[str]]:
    sources = _source_evidence(case)
    accepted: list[PainPoint] = []
    warnings: list[str] = []
    for point in points:
        if point.type not in SEMANTIC_TYPES:
            warnings.append(f"已忽略未授权的LLM痛点类型：{point.type.value}")
            continue
        verified = [
            matched
            for item in point.evidence
            if (matched := _verified_evidence(item, sources)) is not None
        ]
        if not verified:
            warnings.append(f"已忽略缺少可溯源证据的LLM痛点：{point.type.value}")
            continue
        if point.confidence < 0.65:
            warnings.append(f"已忽略低置信度LLM痛点：{point.type.value}")
            continue
        if point.type == PainPointType.MEDICATION_DIAGNOSIS_REVIEW:
            has_medication = any(
                re.search(r"medications\[\d+\]", path) for path in point.related_fields
            )
            has_diagnosis = any(
                re.search(r"diagnoses\[\d+\]", path) for path in point.related_fields
            )
            if not (has_medication and has_diagnosis and len(verified) >= 2):
                warnings.append("已忽略未定位到具体药品和诊断的宽泛用药复核候选")
                continue
        cleaned = point.model_copy(deep=True)
        cleaned.evidence = verified[:4]
        cleaned.dimension = SEMANTIC_DIMENSIONS[cleaned.type]
        if cleaned.severity == PainSeverity.CRITICAL:
            cleaned.severity = PainSeverity.HIGH
            cleaned.severity_score = min(cleaned.severity_score, 85)
        cleaned.confidence = min(cleaned.confidence, 0.9)
        cleaned.review_required = True
        cleaned.detector = DetectorInfo(
            method=DetectorMethod.LLM,
            version=point.detector.version or "deepseek-semantic-v1",
            rule_id=None,
        )
        accepted.append(cleaned)
    return accepted, warnings


def _dedupe_key(point: PainPoint) -> tuple[str, tuple[str, ...]]:
    if point.type in {
        PainPointType.DIAGNOSIS_TERM_HARD_TO_UNDERSTAND,
        PainPointType.CARE_GUIDANCE_INCOMPLETE,
        PainPointType.MEDICATION_DIAGNOSIS_REVIEW,
    }:
        return point.type.value, ()
    related = tuple(sorted(point.related_fields))
    if not related:
        related = tuple(
            sorted(f"{item.document_id}:{item.page}:{item.text}" for item in point.evidence)
        )
    return point.type.value, related


def _merge_pair(left: PainPoint, right: PainPoint) -> PainPoint:
    merged = deepcopy(left)
    if SEVERITY_RANK[right.severity] > SEVERITY_RANK[merged.severity]:
        merged.severity = right.severity
    merged.severity_score = max(merged.severity_score, right.severity_score)
    merged.confidence = round(
        min(0.99, 1 - (1 - merged.confidence) * (1 - right.confidence)), 4
    )
    merged.review_required = merged.review_required or right.review_required
    merged.detector = DetectorInfo(
        method=DetectorMethod.RULE_LLM_HYBRID,
        version=ENGINE_VERSION,
        rule_id=merged.detector.rule_id or right.detector.rule_id,
    )
    evidence = {
        (item.document_id, item.page, item.text): item
        for item in [*merged.evidence, *right.evidence]
    }
    merged.evidence = list(evidence.values())[:4]
    merged.related_fields = sorted(set(merged.related_fields + right.related_fields))
    return merged


def merge_pain_points(rule_points: list[PainPoint], semantic_points: list[PainPoint]) -> list[PainPoint]:
    merged: dict[tuple[str, tuple[str, ...]], PainPoint] = {}
    for point in [*rule_points, *semantic_points]:
        key = _dedupe_key(point)
        merged[key] = _merge_pair(merged[key], point) if key in merged else point
    return sorted(
        merged.values(),
        key=lambda point: (
            -SEVERITY_RANK[point.severity],
            point.dimension.value,
            point.type.value,
            point.pain_point_id,
        ),
    )


def _limit_points(points: list[PainPoint]) -> tuple[list[PainPoint], list[str]]:
    """按类型和总量限制输出，保留严重程度与置信度最高的可行动结果。"""

    by_type: dict[PainPointType, list[PainPoint]] = {}
    for point in points:
        by_type.setdefault(point.type, []).append(point)
    kept: list[PainPoint] = []
    dropped = 0
    for point_type, candidates in by_type.items():
        candidates.sort(
            key=lambda point: (
                -SEVERITY_RANK[point.severity],
                -point.severity_score,
                -point.confidence,
            )
        )
        limit = TYPE_LIMITS.get(point_type, 2)
        kept.extend(candidates[:limit])
        dropped += max(0, len(candidates) - limit)
    kept.sort(
        key=lambda point: (
            -SEVERITY_RANK[point.severity],
            -point.severity_score,
            -point.confidence,
            point.dimension.value,
            point.type.value,
        )
    )
    if len(kept) > TOTAL_POINT_LIMIT:
        dropped += len(kept) - TOTAL_POINT_LIMIT
        kept = kept[:TOTAL_POINT_LIMIT]
    warnings = []
    if dropped:
        warnings.append(f"已归并或隐藏{dropped}条重复、低优先级候选，仅展示最可行动结果。")
    return kept, warnings


def build_report(
    case_id: str,
    points: list[PainPoint],
    warnings: list[str] | None = None,
) -> PainPointReport:
    summary = PainPointSummary(
        total=len(points),
        clinical=sum(p.dimension == PainDimension.CLINICAL for p in points),
        cost=sum(p.dimension == PainDimension.COST for p in points),
        experience=sum(p.dimension == PainDimension.EXPERIENCE for p in points),
        high_priority=sum(
            p.severity in {PainSeverity.HIGH, PainSeverity.CRITICAL} for p in points
        ),
    )
    return PainPointReport(
        case_id=case_id,
        summary=summary,
        pain_points=points,
        engine_version=ENGINE_VERSION,
        analysis_warnings=warnings or [],
    )


def analyze_pain_points(
    case: ClaimCase,
    semantic_points: list[PainPoint] | None = None,
    warnings: list[str] | None = None,
) -> PainPointReport:
    """生成第二问报告；LLM 候选必须通过本地证据校验后才会进入结果。"""

    rule_points = detect_rule_pain_points(case)
    accepted, evidence_warnings = _sanitize_semantic_points(case, semantic_points or [])
    merged = merge_pain_points(rule_points, accepted)
    points, limit_warnings = _limit_points(merged)
    return build_report(
        case.case_id,
        points,
        [*(warnings or []), *evidence_warnings, *limit_warnings],
    )
