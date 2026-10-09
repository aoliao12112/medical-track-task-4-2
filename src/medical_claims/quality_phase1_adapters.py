"""新第四问对既有数据的只读适配层。

这里只复用旧结构化病例记录中的事实，不调用旧画像分数、旧候选池或旧推荐结果。
"""

from __future__ import annotations

from .models import FieldStatus
from .quality_models import ProfileCaseRecord
from .quality_phase1_engine import attach_historical_support, build_historical_support
from .quality_phase1_models import (
    AssessmentStatus,
    DimensionAssessment,
    HospitalCapabilityProfile,
    PatientServiceFeedback,
)


def _has_value(field) -> bool:
    return field.status in {FieldStatus.EXTRACTED, FieldStatus.DERIVED} and (
        field.normalized_value is not None or field.value is not None
    )


def historical_support_from_records(
    records: list[ProfileCaseRecord],
    hospital_id: str,
    disease_group: str,
):
    relevant = [
        record for record in records
        if record.institution_id == hospital_id and record.disease_group == disease_group
    ]
    all_hospital = [record for record in records if record.institution_id == hospital_id]
    metrics = []
    if any(_has_value(record.case.costs.total_amount) for record in relevant):
        metrics.append("费用")
    if any(record.case.medications for record in relevant):
        metrics.append("用药")
    if any(record.case.procedures or record.case.events for record in relevant):
        metrics.append("治疗记录")
    if any(_has_value(record.case.hospitalization.length_of_stay_days) for record in relevant):
        metrics.append("住院日")
    if any(record.pain_report and record.pain_report.pain_points for record in relevant):
        metrics.append("痛点")
    if any(record.readmitted_within_30_days is not None for record in relevant):
        metrics.append("再入院")
    if any(record.patient_feedback_score is not None for record in relevant):
        metrics.append("用户反馈")
    return build_historical_support(
        len(relevant),
        total_case_count=len(all_hospital),
        supported_metrics=metrics,
        case_ids=[record.case.case_id for record in relevant],
    )


def patient_feedback_from_records(
    records: list[ProfileCaseRecord],
    hospital_id: str,
    disease_group: str,
) -> PatientServiceFeedback:
    relevant = [
        record for record in records
        if record.institution_id == hospital_id
        and record.disease_group == disease_group
        and record.patient_feedback_score is not None
    ]
    if not relevant:
        return PatientServiceFeedback(
            assessment=DimensionAssessment(
                status=AssessmentStatus.INSUFFICIENT_EVIDENCE,
                reasoning="没有可关联的真实患者反馈；缺失不计为低分",
                evidence_fields=["patient_feedback_score"],
            )
        )
    average = sum(record.patient_feedback_score for record in relevant if record.patient_feedback_score) / len(relevant)
    source_ids = [f"feedback:{record.case.case_id}" for record in relevant]
    single = len(relevant) == 1
    return PatientServiceFeedback(
        feedback_count=len(relevant),
        overall_feedback_level="单次反馈观察" if single else "有限反馈参考" if len(relevant) < 5 else "多例反馈参考",
        assessment=DimensionAssessment(
            status=AssessmentStatus.EVALUATED,
            score=round(average * 20, 2),
            level="single_observation" if single else "limited" if len(relevant) < 5 else "substantial",
            confidence=round(min(1.0, len(relevant) / 5), 4),
            reasoning=(
                "仅有 1 例真实反馈，作为单次观察展示，不推断医院长期服务表现"
                if single else f"基于 {len(relevant)} 例可关联真实反馈的有限汇总"
            ),
            evidence_fields=["patient_feedback_score"],
            source_ids=source_ids,
            limitations=["反馈样本可能存在选择偏倚", "该维度当前不参与医院推荐权重"],
        ),
    )


def attach_record_history(
    profiles: list[HospitalCapabilityProfile],
    records: list[ProfileCaseRecord],
) -> list[HospitalCapabilityProfile]:
    enriched = []
    for profile in profiles:
        with_history = attach_historical_support(
            profile,
            historical_support_from_records(records, profile.hospital_id, profile.disease_group),
        )
        enriched.append(
            with_history.model_copy(
                update={
                    "patient_service_feedback": patient_feedback_from_records(
                        records, profile.hospital_id, profile.disease_group
                    )
                },
                deep=True,
            )
        )
    return enriched
