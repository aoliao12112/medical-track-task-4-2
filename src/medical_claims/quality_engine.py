"""第四问品质画像聚合与可解释推荐。

所有得分均由结构化字段或第二问证据计算。缺数据时明确返回不可用，不进行
LLM 补分，也不把文档完整度冒充临床疗效。
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
from statistics import mean, median
from typing import Callable, Iterable

from .models import FieldStatus, FieldValue
from .pain_models import PainPointType
from .quality_models import (
    MetricStatus,
    ProfileCaseRecord,
    ProfileEntityType,
    QualityDimension,
    QualityMetric,
    QualityPortfolio,
    QualityProfile,
    RecommendationItem,
    RecommendationReason,
    RecommendationReport,
)


DIMENSION_WEIGHTS = {
    QualityDimension.COST_EFFICIENCY: 0.25,
    QualityDimension.MEDICATION_RATIONALITY: 0.20,
    QualityDimension.TREATMENT_STANDARDIZATION: 0.20,
    QualityDimension.READMISSION_CONTROL: 0.15,
    QualityDimension.PATIENT_FEEDBACK: 0.15,
    QualityDimension.DATA_QUALITY: 0.05,
}


def _field_value(field: FieldValue) -> object | None:
    if field.status not in {FieldStatus.EXTRACTED, FieldStatus.DERIVED}:
        return None
    return field.normalized_value if field.normalized_value is not None else field.value


def _numeric(field: FieldValue) -> float | None:
    value = _field_value(field)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError, InvalidOperation):
        return None


def derive_treatment_plan_id(record: ProfileCaseRecord) -> str | None:
    if record.treatment_plan_id:
        return record.treatment_plan_id
    components = []
    components.extend(
        str(_field_value(item.name)).strip()
        for item in record.case.procedures
        if _field_value(item.name)
    )
    components.extend(
        str(_field_value(item.name)).strip()
        for item in record.case.medications
        if _field_value(item.name)
    )
    if not components:
        return None
    digest = hashlib.sha256("|".join(sorted(set(components))).encode("utf-8")).hexdigest()[:12]
    return f"PLAN-{digest.upper()}"


def _case_cost_basis(record: ProfileCaseRecord) -> float | None:
    total = _numeric(record.case.costs.total_amount)
    if total is None or total < 0:
        return None
    days = _numeric(record.case.hospitalization.length_of_stay_days)
    if days is not None and days > 0:
        return total / days
    return total


def _percentile_efficiency(value: float, cohort: list[float]) -> float:
    if len(cohort) <= 1:
        return 50.0
    lower = sum(item < value for item in cohort)
    equal = sum(item == value for item in cohort)
    percentile = (lower + 0.5 * equal) / len(cohort)
    return round(100 * (1 - percentile), 2)


def _status(sample_size: int, minimum: int) -> MetricStatus:
    return MetricStatus.CALCULATED if sample_size >= minimum else MetricStatus.INSUFFICIENT_SAMPLE


def _sample_confidence(sample_size: int, minimum: int) -> float:
    return round(min(1.0, sample_size / max(minimum * 3, 1)), 4)


def _cost_metric(
    records: list[ProfileCaseRecord],
    disease_cohort_costs: list[float],
    minimum: int,
) -> QualityMetric:
    values = [value for record in records if (value := _case_cost_basis(record)) is not None]
    if not values or len(disease_cohort_costs) < 3:
        return QualityMetric(
            dimension=QualityDimension.COST_EFFICIENCY,
            label="费用效率",
            status=MetricStatus.DATA_UNAVAILABLE,
            calculation="同病种费用中位数的反向百分位；住院病例优先使用日均费用",
            evidence_fields=["costs.total_amount", "hospitalization.length_of_stay_days"],
            limitations=["缺少足够的同病种可比费用，未计算分数"],
        )
    raw = float(median(values))
    score = _percentile_efficiency(raw, disease_cohort_costs)
    return QualityMetric(
        dimension=QualityDimension.COST_EFFICIENCY,
        label="费用效率",
        score=score,
        status=_status(len(values), minimum),
        sample_size=len(values),
        confidence=_sample_confidence(len(values), minimum),
        raw_value=round(raw, 2),
        unit="CNY/住院日或CNY/次",
        calculation="同病种病例费用中位数的反向百分位，数值越低得分越高",
        evidence_fields=["costs.total_amount", "hospitalization.length_of_stay_days"],
        limitations=["尚未进行疾病严重度、年龄和合并症风险调整"],
    )


def _medication_metric(records: list[ProfileCaseRecord], minimum: int) -> QualityMetric:
    eligible = [record for record in records if record.case.medications and record.pain_report]
    if not eligible:
        return QualityMetric(
            dimension=QualityDimension.MEDICATION_RATIONALITY,
            label="用药合理性",
            status=MetricStatus.DATA_UNAVAILABLE,
            calculation="根据有证据的用药-诊断复核与重复用药痛点扣分",
            evidence_fields=["medications", "pain_report.pain_points"],
            limitations=["缺少用药记录或第二问痛点报告，不能因未发现问题而默认满分"],
        )
    watched = {
        PainPointType.MEDICATION_DIAGNOSIS_REVIEW,
        PainPointType.DUPLICATE_MEDICATION_REVIEW,
    }
    scores = []
    for record in eligible:
        penalties = [
            point.severity_score
            for point in record.pain_report.pain_points
            if point.type in watched
        ]
        scores.append(max(0.0, 100.0 - min(100.0, sum(penalties) * 0.7)))
    return QualityMetric(
        dimension=QualityDimension.MEDICATION_RATIONALITY,
        label="用药合理性",
        score=round(mean(scores), 2),
        status=_status(len(eligible), minimum),
        sample_size=len(eligible),
        confidence=_sample_confidence(len(eligible), minimum),
        raw_value=round(mean(scores), 2),
        unit="分",
        calculation="每病例以100分起评，仅按第二问有原文证据的用药复核痛点扣分",
        evidence_fields=["medications", "pain_report.pain_points"],
        limitations=["该指标是风险筛查结果，不等同于药师处方点评"],
    )


def _record_standardization_score(record: ProfileCaseRecord) -> float:
    case = record.case
    earned = 0.0
    total = 0.0

    def check(weight: float, condition: bool) -> None:
        nonlocal earned, total
        total += weight
        if condition:
            earned += weight

    check(25, any(_field_value(item.name) for item in case.diagnoses))
    check(10, case.encounter_type.value != "unknown")
    if case.encounter_type.value == "inpatient":
        check(15, _field_value(case.hospitalization.admission_date) is not None)
        check(15, _field_value(case.hospitalization.discharge_date) is not None)
        check(10, _field_value(case.hospitalization.length_of_stay_days) is not None)
    if case.procedures:
        check(10, all(_field_value(item.name) for item in case.procedures))
        check(5, all(_field_value(item.performed_date) for item in case.procedures))
    if case.medications:
        check(10, all(_field_value(item.name) for item in case.medications))
        check(
            10,
            all(
                _field_value(item.dose) and _field_value(item.frequency) and _field_value(item.route)
                for item in case.medications
            ),
        )
    check(10, bool(case.events) and all(event.evidence for event in case.events))
    return round(100 * earned / total, 2) if total else 0.0


def _standardization_metric(records: list[ProfileCaseRecord], minimum: int) -> QualityMetric:
    scores = [_record_standardization_score(record) for record in records]
    return QualityMetric(
        dimension=QualityDimension.TREATMENT_STANDARDIZATION,
        label="治疗记录规范性（代理指标）",
        score=round(mean(scores), 2),
        status=_status(len(scores), minimum),
        sample_size=len(scores),
        confidence=_sample_confidence(len(scores), minimum),
        raw_value=round(mean(scores), 2),
        unit="分",
        calculation="按诊断、住院日期、手术、用药要素和事件证据完整度加权",
        evidence_fields=["diagnoses", "hospitalization", "procedures", "medications", "events"],
        limitations=["当前是记录完整度代理指标，不能冒充临床指南依从性"],
    )


def _readmission_metric(records: list[ProfileCaseRecord], minimum: int) -> QualityMetric:
    eligible = [
        record
        for record in records
        if record.stable_patient_token and record.readmitted_within_30_days is not None
    ]
    if not eligible:
        return QualityMetric(
            dimension=QualityDimension.READMISSION_CONTROL,
            label="30日再入院控制",
            status=MetricStatus.DATA_UNAVAILABLE,
            calculation="1减去同病种风险调整后30日再入院率",
            evidence_fields=["stable_patient_token", "readmitted_within_30_days"],
            limitations=["当前案件号不是稳定患者标识，无法关联30日再入院"],
        )
    rate = mean(record.readmitted_within_30_days for record in eligible)
    return QualityMetric(
        dimension=QualityDimension.READMISSION_CONTROL,
        label="30日再入院控制",
        score=round(100 * (1 - rate), 2),
        status=_status(len(eligible), minimum),
        sample_size=len(eligible),
        confidence=_sample_confidence(len(eligible), minimum),
        raw_value=round(rate, 4),
        unit="再入院率",
        calculation="100 × (1 - 30日再入院率)",
        evidence_fields=["stable_patient_token", "readmitted_within_30_days"],
        limitations=["正式比较前仍需进行疾病严重度与合并症风险调整"],
    )


def _feedback_metric(records: list[ProfileCaseRecord], minimum: int) -> QualityMetric:
    values = [
        record.patient_feedback_score
        for record in records
        if record.patient_feedback_score is not None
    ]
    if not values:
        return QualityMetric(
            dimension=QualityDimension.PATIENT_FEEDBACK,
            label="患者反馈",
            status=MetricStatus.DATA_UNAVAILABLE,
            calculation="5分制反馈均值换算为百分制",
            evidence_fields=["patient_feedback_score"],
            limitations=["当前病例图片不含患者反馈，不能由大模型生成"],
        )
    raw = mean(values)
    return QualityMetric(
        dimension=QualityDimension.PATIENT_FEEDBACK,
        label="患者反馈",
        score=round(raw / 5 * 100, 2),
        status=_status(len(values), minimum),
        sample_size=len(values),
        confidence=_sample_confidence(len(values), minimum),
        raw_value=round(raw, 3),
        unit="5分制",
        calculation="反馈均值 ÷ 5 × 100",
        evidence_fields=["patient_feedback_score"],
        limitations=["需防止极少量反馈和选择偏差"],
    )


def _all_field_confidences(record: ProfileCaseRecord) -> list[float]:
    payload = record.case.model_dump(mode="json")
    confidences: list[float] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            confidence = value.get("confidence")
            status = value.get("status")
            if confidence is not None and status in {"extracted", "derived", "review_required"}:
                confidences.append(float(confidence))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return confidences


def _data_quality_metric(records: list[ProfileCaseRecord], minimum: int) -> QualityMetric:
    case_scores = []
    for record in records:
        confidences = _all_field_confidences(record)
        base = mean(confidences) * 100 if confidences else 0.0
        penalty = min(25, 4 * len(record.case.quality_flags))
        case_scores.append(max(0.0, base - penalty))
    return QualityMetric(
        dimension=QualityDimension.DATA_QUALITY,
        label="数据质量",
        score=round(mean(case_scores), 2),
        status=_status(len(case_scores), minimum),
        sample_size=len(case_scores),
        confidence=_sample_confidence(len(case_scores), minimum),
        raw_value=round(mean(case_scores), 2),
        unit="分",
        calculation="字段置信度均值减去质量标记惩罚",
        evidence_fields=["field.confidence", "quality_flags"],
        limitations=["该维度衡量数据可信度，不代表医疗质量"],
    )


def _profile(
    entity_id: str,
    entity_type: ProfileEntityType,
    disease: str,
    records: list[ProfileCaseRecord],
    disease_costs: list[float],
    minimum: int,
) -> QualityProfile:
    metrics = [
        _cost_metric(records, disease_costs, minimum),
        _medication_metric(records, minimum),
        _standardization_metric(records, minimum),
        _readmission_metric(records, minimum),
        _feedback_metric(records, minimum),
        _data_quality_metric(records, minimum),
    ]
    calculated = [metric for metric in metrics if metric.status == MetricStatus.CALCULATED and metric.score is not None]
    warnings = []
    if any(metric.status == MetricStatus.DATA_UNAVAILABLE for metric in metrics):
        warnings.append("部分维度因数据不可用未计分，禁止由模型补分")
    if len(records) < minimum:
        warnings.append(f"样本量{len(records)}低于准入阈值{minimum}，仅展示候选画像")
    composite = None
    confidence = 0.0
    if len(calculated) >= 3 and len(records) >= minimum:
        weight_sum = sum(DIMENSION_WEIGHTS[metric.dimension] for metric in calculated)
        composite = round(
            sum(metric.score * DIMENSION_WEIGHTS[metric.dimension] for metric in calculated)
            / weight_sum,
            2,
        )
        confidence = round(
            sum(metric.confidence * DIMENSION_WEIGHTS[metric.dimension] for metric in calculated)
            / weight_sum,
            4,
        )
    else:
        warnings.append("可计算且样本达标的维度不足3个，不生成综合分")
    eligible = composite is not None and confidence >= 0.33
    return QualityProfile(
        entity_id=entity_id,
        entity_type=entity_type,
        disease_group=disease,
        sample_size=len(records),
        metrics=metrics,
        composite_score=composite,
        composite_confidence=confidence,
        eligible_for_recommendation=eligible,
        warnings=warnings,
    )


def build_quality_portfolio(
    records: list[ProfileCaseRecord], minimum_sample_size: int = 3
) -> QualityPortfolio:
    disease_costs: dict[str, list[float]] = defaultdict(list)
    for record in records:
        value = _case_cost_basis(record)
        if value is not None:
            disease_costs[record.disease_group].append(value)

    groups: dict[tuple[ProfileEntityType, str, str], list[ProfileCaseRecord]] = defaultdict(list)
    for record in records:
        groups[(ProfileEntityType.INSTITUTION, record.institution_id, record.disease_group)].append(record)
        for clinician_id in set(record.clinician_ids):
            groups[(ProfileEntityType.CLINICIAN, clinician_id, record.disease_group)].append(record)
        plan_id = derive_treatment_plan_id(record)
        if plan_id:
            groups[(ProfileEntityType.TREATMENT_PLAN, plan_id, record.disease_group)].append(record)

    profiles = [
        _profile(entity_id, entity_type, disease, grouped, disease_costs[disease], minimum_sample_size)
        for (entity_type, entity_id, disease), grouped in sorted(
            groups.items(), key=lambda item: (item[0][0].value, item[0][2], item[0][1])
        )
    ]
    institution_ids = {record.institution_id for record in records}
    clinician_ids = {item for record in records for item in record.clinician_ids}
    plan_ids = {plan for record in records if (plan := derive_treatment_plan_id(record))}
    warnings = []
    if not any(record.stable_patient_token for record in records):
        warnings.append("未提供稳定患者标识，再入院率维度整体不可用")
    if not any(record.patient_feedback_score is not None for record in records):
        warnings.append("未提供患者反馈，患者反馈维度整体不可用")
    return QualityPortfolio(
        profiles=profiles,
        case_count=len({record.case.case_id for record in records}),
        institution_count=len(institution_ids),
        clinician_count=len(clinician_ids),
        treatment_plan_count=len(plan_ids),
        disease_count=len({record.disease_group for record in records}),
        minimum_sample_size=minimum_sample_size,
        warnings=warnings,
    )


def recommend_profiles(
    portfolio: QualityPortfolio,
    disease_group: str,
    entity_type: ProfileEntityType = ProfileEntityType.INSTITUTION,
    top_k: int = 5,
) -> RecommendationReport:
    relevant = [
        profile
        for profile in portfolio.profiles
        if profile.disease_group == disease_group and profile.entity_type == entity_type
    ]
    eligible = [profile for profile in relevant if profile.eligible_for_recommendation]
    eligible.sort(
        key=lambda profile: (
            profile.composite_score or 0,
            profile.composite_confidence,
            profile.sample_size,
        ),
        reverse=True,
    )
    items: list[RecommendationItem] = []
    for rank, profile in enumerate(eligible[:top_k], start=1):
        scored_metrics = [
            metric
            for metric in profile.metrics
            if metric.status == MetricStatus.CALCULATED
            and metric.score is not None
            and metric.dimension != QualityDimension.DATA_QUALITY
        ]
        scored_metrics.sort(key=lambda metric: (metric.score, metric.confidence), reverse=True)
        if len(scored_metrics) < 2:
            continue
        reasons = [
            RecommendationReason(
                dimension=metric.dimension,
                title=metric.label,
                explanation=(
                    f"该维度得分{metric.score:.1f}，基于{metric.sample_size}个同口径样本；"
                    f"计算方式：{metric.calculation}。"
                ),
                score=metric.score,
                evidence_fields=metric.evidence_fields,
            )
            for metric in scored_metrics[:2]
        ]
        limitations = sorted({item for metric in profile.metrics for item in metric.limitations})
        items.append(
            RecommendationItem(
                entity_id=profile.entity_id,
                rank=rank,
                composite_score=profile.composite_score or 0,
                confidence=profile.composite_confidence,
                sample_size=profile.sample_size,
                reasons=reasons,
                limitations=limitations,
            )
        )
    warnings = []
    if not items:
        warnings.append("没有满足样本量、可计算维度和置信度门槛的候选，系统拒绝强行推荐")
    return RecommendationReport(
        disease_group=disease_group,
        entity_type=entity_type,
        recommendations=items,
        eligible_candidates=len(eligible),
        excluded_candidates=len(relevant) - len(eligible),
        generated=bool(items),
        warnings=warnings,
    )
