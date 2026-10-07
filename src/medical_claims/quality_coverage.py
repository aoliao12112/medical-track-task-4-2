"""KR4 data readiness, separate from feature presence and final competition PASS."""
from .profile_records import SUPPORTED_DISEASE_GROUPS
from .quality_engine import recommend_profiles
from .quality_models import MetricStatus, ProfileEntityType


def audit_quality_coverage(portfolio, dataset):
    diseases = []
    for disease in SUPPORTED_DISEASE_GROUPS:
        profiles = [p for p in portfolio.profiles if p.disease_group == disease] if portfolio else []
        institutions = [p for p in profiles if p.entity_type == ProfileEntityType.INSTITUTION]
        rows = []
        for profile in profiles:
            calculated = [m for m in profile.metrics if m.status == MetricStatus.CALCULATED and m.score is not None]
            report = recommend_profiles(portfolio.model_copy(update={"profiles": [profile]}), disease, profile.entity_type)
            reasons = report.recommendations[0].reasons if report.recommendations else []
            valid_reasons = len(reasons) >= 2 and all(any(
                m.dimension == reason.dimension and m.score == reason.score and m.evidence_fields == reason.evidence_fields
                for m in calculated) for reason in reasons)
            ready = (len(calculated) >= 5 and profile.sample_size >= portfolio.minimum_sample_size
                     and profile.eligible_for_recommendation and valid_reasons)
            rows.append({"entity_id": profile.entity_id, "entity_type": profile.entity_type.value,
                         "sample_size": profile.sample_size, "available_dimension_count": len(calculated),
                         "unavailable_dimensions": [m.dimension.value for m in profile.metrics if m not in calculated],
                         "confidence": profile.composite_confidence, "recommendation_reason_count": len(reasons),
                         "reasons_from_calculated_dimensions": valid_reasons, "ready": ready})
        valid_institutions = sum(row["ready"] for row in rows if row["entity_type"] == "institution")
        subject_readiness = {
            "institution": valid_institutions >= 5,
            "clinician": any(row["ready"] for row in rows if row["entity_type"] == "clinician"),
            "treatment_plan": any(row["ready"] for row in rows if row["entity_type"] == "treatment_plan"),
        }
        diseases.append({"disease_group": disease, "institution_count": len(institutions),
                         "required_institution_count": 5, "qualified_institution_count": valid_institutions,
                         "available_dimension_count": min((row["available_dimension_count"] for row in rows
                             if row["entity_type"] == "institution"), default=0),
                         "profiles": rows, "entity_types_ready": subject_readiness,
                         "status": "READY_FOR_REVIEW" if all(subject_readiness.values()) else "NOT_READY"})
    ready_diseases = sum(d["status"] == "READY_FOR_REVIEW" for d in diseases)
    sources_ok = bool(dataset.get("accepted_cases")) and not dataset.get("contains_non_real_data", True)
    ready = ready_diseases >= 3 and sources_ok
    return {"status": "READY_FOR_REVIEW" if ready else "NOT_READY",
            "portfolio_evaluated": portfolio is not None, "required_disease_count": 3,
            "disease_count": portfolio.disease_count if portfolio else 0,
            "ready_disease_count": ready_diseases, "required_dimension_count": 5,
            "minimum_sample_size": portfolio.minimum_sample_size if portfolio else 3,
            "diseases": diseases, "dataset": dataset, "formal_kr4_pass": False,
            "limitations": ["功能存在和候选机构数不等于KR4达标；只统计已组装、可计算且满足准入的画像",
                            "数据来源目前为调用方声明，正式KR4仍需核验来源、标注及独立验收",
                            "记录规范性与数据质量是代理指标；治疗模式分组不是官方临床路径等级"]}
