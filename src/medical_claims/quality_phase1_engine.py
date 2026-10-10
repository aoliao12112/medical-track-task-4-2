"""第四问 V2 推荐与治疗方案评价引擎。

LLM 提出候选、逐维评分和待核验理由；联网层补充事实证据；本模块固定权重、
纠正可确定的地理事实并重新计算总分。旧 RecommendationReport 不参与主流程。
"""

from __future__ import annotations

from hashlib import sha256
from statistics import mean
from typing import Iterable

from .models import ClaimCase, FieldStatus, FieldValue
from .pain_models import PainDimension, PainPointReport, PainPointType, PainSeverity
from .quality_phase1_models import (
    AccessibilityLevel,
    AssessmentStatus,
    ClinicianCapabilityProfile,
    ClinicianRecommendation,
    ComplexityLevel,
    CurrentTreatmentEvaluation,
    DimensionAssessment,
    EvidenceVerificationStatus,
    GeographicAccessibility,
    HistoricalEvidenceLevel,
    HistoricalEvidenceSupport,
    HistoricalTreatmentReference,
    HospitalCapabilityProfile,
    HospitalRecommendationReportV2,
    HospitalRecommendationV2,
    PatientRecommendationContext,
    RecommendationDimensionScore,
    RecommendationIntent,
    RecommendationReasonV2,
    TreatmentPattern,
)


HOSPITAL_RECOMMENDATION_WEIGHTS = {
    "disease_specialty_match": 0.35,
    "geographic_convenience": 0.20,
    "hospital_professional_capability": 0.20,
    "clinician_resources": 0.15,
    "treatment_technology_capability": 0.10,
}

CLINICIAN_RECOMMENDATION_WEIGHTS = {
    "disease_match": 0.35,
    "treatment_expertise": 0.30,
    "hospital_department_platform": 0.20,
    "clinical_qualification_capability": 0.15,
}


def _available(field: FieldValue) -> object | None:
    if field.status not in {FieldStatus.EXTRACTED, FieldStatus.DERIVED}:
        return None
    return field.normalized_value if field.normalized_value is not None else field.value


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def build_historical_support(
    disease_specific_case_count: int,
    *,
    total_case_count: int | None = None,
    supported_metrics: list[str] | None = None,
    case_ids: list[str] | None = None,
) -> HistoricalEvidenceSupport:
    """历史数量只衡量证据支撑程度，不代表医院质量高低。"""
    total = total_case_count if total_case_count is not None else disease_specific_case_count
    metrics = _unique(supported_metrics or [])
    identifiers = _unique(case_ids or [])
    source_ids = [f"claim:{value}" for value in identifiers]
    if disease_specific_case_count <= 0:
        return HistoricalEvidenceSupport(
            historical_case_count=max(0, total),
            history_summary="暂无可关联历史案例",
            supported_metrics=metrics,
            assessment=DimensionAssessment(
                status=AssessmentStatus.INSUFFICIENT_EVIDENCE,
                reasoning="没有可追溯的同病种历史病例；该维度不计 0 分，也不进入推荐分母",
                limitations=["没有历史病例不影响能力画像成立"],
            ),
        )
    if disease_specific_case_count == 1:
        level, label, score = HistoricalEvidenceLevel.SINGLE_CASE, "单病例历史观察", 40.0
    elif disease_specific_case_count < 5:
        level, label, score = HistoricalEvidenceLevel.LIMITED, "有限历史参考", 65.0
    else:
        level, label, score = HistoricalEvidenceLevel.SUBSTANTIAL, "较充分历史参考", 90.0
    coverage = min(1.0, (len(metrics) / 6) * min(1.0, disease_specific_case_count / 5))
    return HistoricalEvidenceSupport(
        historical_case_count=max(total, disease_specific_case_count),
        disease_specific_case_count=disease_specific_case_count,
        available_history=True,
        evidence_level=level,
        history_summary=f"{label}：可关联 {disease_specific_case_count} 例同病种历史病例",
        supported_metrics=metrics,
        evidence_coverage=round(coverage, 4),
        source_ids=source_ids,
        assessment=DimensionAssessment(
            status=AssessmentStatus.EVALUATED,
            score=score,
            level=level.value,
            confidence=round(min(1.0, disease_specific_case_count / 5) * (0.5 + 0.5 * min(1.0, len(metrics) / 3)), 4),
            reasoning="分数仅表示历史证据充分度，不表示医院治疗质量",
            evidence_fields=metrics or ["historical_case_count"],
            source_ids=source_ids,
            limitations=["少量病例不能推断医院长期表现", "病例严重度和人群差异尚未完成风险调整"],
        ),
    )


def attach_historical_support(
    profile: HospitalCapabilityProfile,
    support: HistoricalEvidenceSupport,
) -> HospitalCapabilityProfile:
    return profile.model_copy(update={"historical_evidence": support}, deep=True)


def assess_geographic_accessibility(
    context: PatientRecommendationContext,
    profile: HospitalCapabilityProfile,
) -> GeographicAccessibility:
    foundation = profile.institution_foundation
    if not context.city or not foundation.city:
        return GeographicAccessibility(
            patient_city=context.city,
            patient_province=context.province,
            hospital_city=foundation.city,
            hospital_province=foundation.province,
            travel_level=AccessibilityLevel.UNKNOWN,
            accessibility_level="位置证据不足",
            assessment=DimensionAssessment(
                status=AssessmentStatus.INSUFFICIENT_EVIDENCE,
                reasoning="患者城市或医院城市缺失，无法计算地理可达性",
                evidence_fields=["context.city", "institution_foundation.city"],
                limitations=["地理缺失不能默认按远距离或零分处理"],
            ),
        )
    same_city = context.city == foundation.city
    same_province = bool(context.province and foundation.province and context.province == foundation.province)
    nearby = foundation.city in context.nearby_major_cities
    if same_city:
        level, label, score = AccessibilityLevel.SAME_CITY, "同城", 100.0
    elif same_province:
        level, label, score = AccessibilityLevel.SAME_PROVINCE, "同省", 80.0
    elif nearby:
        level, label, score = AccessibilityLevel.NEARBY_REGIONAL, "周边区域中心城市", 70.0
    else:
        level, label, score = AccessibilityLevel.CROSS_REGION, "跨区域", 45.0
    return GeographicAccessibility(
        patient_city=context.city,
        patient_province=context.province,
        hospital_city=foundation.city,
        hospital_province=foundation.province,
        same_city=same_city,
        same_province=same_province,
        nearby_major_city=nearby,
        travel_level=level,
        accessibility_level=label,
        assessment=DimensionAssessment(
            status=AssessmentStatus.EVALUATED,
            score=score,
            level=level.value,
            confidence=0.95,
            reasoning="按患者与医院的城市、省份及明确配置的周边中心城市分层",
            evidence_fields=["context.city", "context.province", "institution_foundation.city", "institution_foundation.province"],
            source_ids=foundation.assessment.source_ids,
            limitations=["当前未接入可靠道路距离和实时交通数据"],
        ),
    )


def _professional_capability(profile: HospitalCapabilityProfile) -> DimensionAssessment:
    candidates = [
        profile.institution_foundation.assessment,
        profile.medical_resources.assessment,
        profile.regional_service.assessment,
    ]
    available = [item for item in candidates if item.status == AssessmentStatus.EVALUATED and item.score is not None]
    source_ids = _unique(source for item in available for source in item.source_ids)
    if len(available) < 2 or not source_ids:
        return DimensionAssessment(
            status=AssessmentStatus.INSUFFICIENT_EVIDENCE,
            reasoning="医院专业能力至少需要两个有来源的能力维度",
            evidence_fields=[field for item in available for field in item.evidence_fields],
            source_ids=source_ids,
            limitations=["医院等级不能单独作为推荐依据"],
        )
    score = mean(item.score for item in available if item.score is not None)
    return DimensionAssessment(
        status=AssessmentStatus.EVALUATED,
        score=round(score, 2),
        level="high" if score >= 85 else "medium" if score >= 65 else "basic",
        confidence=round(mean(item.confidence for item in available), 4),
        reasoning="综合机构基础、公开医疗资源和可用区域服务能力；医院等级不单独准入",
        evidence_fields=_unique(field for item in available for field in item.evidence_fields),
        source_ids=source_ids,
        limitations=["这是公开能力证据评分，不是医院疗效排名"],
    )


def _preference_assessment(
    context: PatientRecommendationContext,
    profile: HospitalCapabilityProfile,
) -> DimensionAssessment:
    preferences = context.preferred_hospital_types
    hospital_type = profile.institution_foundation.hospital_type
    if not preferences or not hospital_type:
        return DimensionAssessment(
            status=AssessmentStatus.INSUFFICIENT_EVIDENCE,
            reasoning="当前没有可计算的医院类型偏好",
            evidence_fields=["context.preferred_hospital_types", "institution_foundation.hospital_type"],
            limitations=["偏好缺失不计 0 分"],
        )
    matched = any(item in hospital_type or hospital_type in item for item in preferences)
    return DimensionAssessment(
        status=AssessmentStatus.EVALUATED,
        score=100 if matched else 60,
        level="matched" if matched else "not_preferred",
        confidence=0.9,
        reasoning="仅比较患者声明的医院类型偏好与医院公开类型",
        evidence_fields=["context.preferred_hospital_types", "institution_foundation.hospital_type"],
        source_ids=profile.institution_foundation.assessment.source_ids,
        limitations=["未采集的偏好不作推断"],
    )


def _reason(
    dimension: str,
    title: str,
    explanation: str,
    assessment: DimensionAssessment,
    evidence_records: list,
) -> RecommendationReasonV2:
    assert assessment.score is not None
    dimension_fields = {
        "disease_specialty_match": {"disease_specialty", "department", "key_specialty", "disease_capability"},
        "geographic_convenience": {"institution_foundation", "city", "regional_service"},
        "hospital_professional_capability": {"institution_foundation", "medical_resources", "regional_service"},
        "clinician_resources": {"medical_resources", "clinician_specialty"},
        "treatment_technology_capability": {"medical_resources", "disease_specialty", "treatment_capability"},
        "disease_match": {"disease_expertise", "current_case_match", "clinician_specialty"},
        "treatment_expertise": {"treatment_expertise", "clinician_technical_expertise"},
        "hospital_department_platform": {"basic_qualification", "platform_capability", "department"},
        "clinical_qualification_capability": {"basic_qualification", "clinician_specialty"},
    }
    allowed_fields = dimension_fields.get(dimension, set()) | set(assessment.evidence_fields)
    record_ids = [
        record.evidence_id for record in evidence_records
        if record.source_id in assessment.source_ids
        and (record.evidence_field in allowed_fields
             or record.evidence_field.split(".")[0] in {field.split(".")[0] for field in allowed_fields})
    ]
    matched_records = [record for record in evidence_records if record.evidence_id in record_ids]
    evidence_confidence = mean(record.evidence_confidence for record in matched_records) if matched_records else assessment.confidence
    verified = bool(record_ids) or any(item.startswith(("claim:", "feedback:")) for item in assessment.source_ids)
    return RecommendationReasonV2(
        dimension=dimension,
        title=title,
        explanation=explanation,
        score=assessment.score,
        evidence_fields=assessment.evidence_fields or [dimension],
        source_ids=assessment.source_ids,
        evidence_record_ids=_unique(record_ids),
        evidence_confidence=round(evidence_confidence, 4),
        verified=verified,
        evidence_status=_evidence_status(evidence_confidence, verified),
    )


def _normal_name(value: str) -> str:
    return "".join(value.split()).replace("（", "(").replace("）", ")")


def _evidence_for_fields(evidence_records: list, fields: set[str]) -> list:
    roots = {item.split(".")[0] for item in fields}
    return [
        record for record in evidence_records
        if record.evidence_field in fields or record.evidence_field.split(".")[0] in roots
    ]


def _evidence_status(confidence: float, verified: bool) -> EvidenceVerificationStatus:
    if verified and confidence >= 0.75:
        return EvidenceVerificationStatus.VERIFIED
    if verified:
        return EvidenceVerificationStatus.PARTIALLY_VERIFIED
    return EvidenceVerificationStatus.PENDING_VERIFICATION


def _dimension(
    dimension: str,
    label: str,
    score: float,
    weight: float,
    reasoning: str,
    assessment: DimensionAssessment,
    evidence_records: list,
    fields: set[str],
) -> RecommendationDimensionScore:
    records = _evidence_for_fields(evidence_records, fields)
    source_ids = _unique([*assessment.source_ids, *(record.source_id for record in records)])
    record_ids = _unique(record.evidence_id for record in records)
    confidence = (
        mean(record.evidence_confidence for record in records)
        if records else min(assessment.confidence, 0.35)
    )
    verified = any(record.verified and record.evidence_grade in {"A", "B"} for record in records)
    return RecommendationDimensionScore(
        dimension=dimension,
        label=label,
        score=round(max(0, min(100, score)), 2),
        weight=weight,
        reasoning=reasoning,
        evidence_confidence=round(confidence, 4),
        evidence_status=_evidence_status(confidence, verified),
        source_ids=source_ids,
        evidence_record_ids=record_ids,
    )


def _hospital_candidate(intent: RecommendationIntent | None, hospital_name: str):
    if intent is None:
        return None
    target = _normal_name(hospital_name)
    return next(
        (item for item in intent.candidate_hospitals if _normal_name(item.hospital_name) == target),
        None,
    )


def _clinician_candidate(intent: RecommendationIntent | None, clinician_name: str, hospital_name: str):
    if intent is None:
        return None
    clinician = _normal_name(clinician_name)
    hospital = _normal_name(hospital_name)
    return next(
        (
            item for item in intent.candidate_clinicians
            if _normal_name(item.doctor_name) == clinician and _normal_name(item.hospital_name) == hospital
        ),
        None,
    )


def _fallback_score(assessment: DimensionAssessment, default: float = 60.0) -> float:
    return assessment.score if assessment.status == AssessmentStatus.EVALUATED and assessment.score is not None else default


def _ensure_scored(assessment: DimensionAssessment, default: float, reasoning: str) -> DimensionAssessment:
    if assessment.score is not None:
        return assessment
    return assessment.model_copy(update={
        "status": AssessmentStatus.EVALUATED,
        "score": default,
        "confidence": min(assessment.confidence, 0.25),
        "reasoning": reasoning,
        "limitations": _unique([*assessment.limitations, "公开资料有限，建议进一步确认"]),
    })


def _candidate_reasons(candidate, dimensions: list[RecommendationDimensionScore], evidence_records: list) -> list[RecommendationReasonV2]:
    by_dimension = {item.dimension: item for item in dimensions}

    def resolve_dimension(evidence_type: str) -> str:
        value = evidence_type.lower()
        if any(term in value for term in ("地理", "地区", "位置", "交通", "city", "regional")):
            return "geographic_convenience"
        if any(term in value for term in ("医生", "专家", "clinician", "doctor")):
            return "clinician_resources" if "clinician_resources" in by_dimension else "clinical_qualification_capability"
        if any(term in value for term in ("技术", "设备", "手术", "诊疗项目", "treatment", "technology")):
            return "treatment_technology_capability" if "treatment_technology_capability" in by_dimension else "treatment_expertise"
        if any(term in value for term in ("科室", "专科", "病种", "department", "specialty", "disease")):
            return "disease_specialty_match" if "disease_specialty_match" in by_dimension else "disease_match"
        return "hospital_professional_capability" if "hospital_professional_capability" in by_dimension else dimensions[0].dimension

    output = []
    for proposed in candidate.recommendation_reasons[:3]:
        dimension_name = resolve_dimension(proposed.evidence_type)
        dimension = by_dimension.get(dimension_name, dimensions[0])
        record_ids = dimension.evidence_record_ids
        source_ids = dimension.source_ids
        verified = bool(record_ids)
        explanation = proposed.reason
        if not verified:
            explanation += "（推荐基于当前病例与模型综合判断，公开证据暂未完成核验。）"
        output.append(RecommendationReasonV2(
            dimension=dimension.dimension,
            title=dimension.label,
            explanation=explanation,
            score=dimension.score,
            evidence_fields=[proposed.evidence_type],
            source_ids=source_ids,
            evidence_record_ids=record_ids,
            evidence_confidence=dimension.evidence_confidence,
            verified=verified,
            evidence_status=_evidence_status(dimension.evidence_confidence, verified),
            suggested_source_name=(
                proposed.suggested_source_name or candidate.suggested_source_name
            ),
            suggested_source_url=(
                proposed.suggested_source_url or candidate.suggested_source_url
            ),
            suggested_evidence_summary=proposed.suggested_evidence_summary,
        ))
    return output


def recommend_hospitals(
    context: PatientRecommendationContext,
    profiles: list[HospitalCapabilityProfile],
    *,
    recommendation_intent: RecommendationIntent | None = None,
    top_k: int = 5,
) -> HospitalRecommendationReportV2:
    """按固定五维权重重算；资料不足降低可信度，只有明确事实冲突才移除。"""
    staged: list[tuple[HospitalRecommendationV2, HospitalCapabilityProfile]] = []
    excluded: dict[str, list[str]] = {}
    for profile in profiles:
        failures: list[str] = []
        if profile.disease_group != context.disease_group:
            failures.append("病种不匹配")
        geography = assess_geographic_accessibility(context, profile)
        if failures:
            excluded[profile.hospital_id] = failures
            continue
        specialty = profile.disease_specialty.assessment
        professional = _professional_capability(profile)
        resources = profile.medical_resources.assessment
        candidate = _hospital_candidate(recommendation_intent, profile.institution_foundation.hospital_name)
        proposed = candidate.dimension_scores if candidate else None
        specialties = "、".join(profile.disease_specialty.related_departments) or "相关科室资料有限"
        dimension_specs = [
            ("disease_specialty_match", "病种/专科匹配", proposed.disease_specialty_match if proposed else None,
             specialty, _fallback_score(specialty), {"disease_specialty", "department", "key_specialty", "disease_capability"},
             f"与当前病种相关的科室或专科：{specialties}"),
            ("geographic_convenience", "地理与就医便利", None, geography.assessment,
             _fallback_score(geography.assessment, 50), {"institution_foundation", "city", "regional_service"},
             f"医院位于{profile.institution_foundation.city or '地区待确认'}，属于{geography.accessibility_level}"),
            ("hospital_professional_capability", "医院专业能力", proposed.hospital_professional_capability if proposed else None,
             professional, _fallback_score(professional), {"institution_foundation", "medical_resources", "regional_service"},
             professional.reasoning),
            ("clinician_resources", "医生/专家资源", proposed.clinician_resources if proposed else None,
             resources, _fallback_score(resources, 55), {"medical_resources", "clinician_specialty"},
             resources.reasoning if resources.reasoning else "公开医生与专家资源资料有限"),
            ("treatment_technology_capability", "技术与治疗能力", proposed.treatment_technology_capability if proposed else None,
             resources, _fallback_score(resources, 55), {"medical_resources", "treatment_capability", "disease_specialty"},
             resources.reasoning if resources.reasoning else "公开技术与治疗能力资料有限"),
        ]
        dimensions = [
            _dimension(name, label, fallback if llm_score is None else llm_score.score,
                       HOSPITAL_RECOMMENDATION_WEIGHTS[name], fallback_reason if llm_score is None else llm_score.reasoning,
                       assessment, profile.evidence_records, fields)
            for name, label, llm_score, assessment, fallback, fields, fallback_reason in dimension_specs
        ]
        score = sum(item.score * item.weight for item in dimensions)
        confidence = sum(item.evidence_confidence * item.weight for item in dimensions)
        coverage = sum(item.weight for item in dimensions if item.evidence_record_ids)
        reasons = _candidate_reasons(candidate, dimensions, profile.evidence_records) if candidate else [
            _reason("disease_specialty_match", "病种/专科匹配", dimension_specs[0][6],
                    _ensure_scored(specialty, 60, "公开病种专科资料有限"), profile.evidence_records),
            _reason("geographic_convenience", "地理与就医便利", dimension_specs[1][6],
                    _ensure_scored(geography.assessment, 50, "医院或患者地区资料有限"), profile.evidence_records),
        ]
        if len(reasons) < 2:
            reasons.append(_reason(
                "hospital_professional_capability", "医院专业能力", professional.reasoning,
                professional if professional.score is not None else DimensionAssessment(
                    status=AssessmentStatus.EVALUATED, score=60, confidence=0.2,
                    reasoning="公开资料有限", limitations=["建议进一步核验"],
                ), profile.evidence_records,
            ))
        history = profile.historical_evidence.assessment
        if candidate is None and history.status == AssessmentStatus.EVALUATED and history.score is not None:
            reasons.append(_reason(
                "historical_evidence", "历史案例参考", profile.historical_evidence.history_summary,
                history, profile.evidence_records,
            ))
        source_evidence = _unique(source for item in dimensions for source in item.source_ids)
        field_evidence = _unique(field for item in dimensions for field in [item.dimension])
        evidence = _unique(
            [*(f"source:{item}" for item in source_evidence), *(f"field:{item}" for item in field_evidence)]
        )
        if len(evidence) < 2:
            evidence.extend(["status:pending_verification", f"hospital:{profile.hospital_id}"])
        limitations = _unique(
            [
                "推荐分用于当前患者的就医优先级，不是医院质量总分或全国排名",
                "历史病例、患者反馈和完整六维画像不参与本次准入",
                *([] if confidence >= 0.5 else ["公开资料有限，部分理由仍需进一步确认"]),
            ]
        )
        staged.append((
            HospitalRecommendationV2(
                rank=1,
                hospital_id=profile.hospital_id,
                hospital_name=profile.institution_foundation.hospital_name,
                recommendation_score=round(score, 2),
                recommendation_confidence=round(confidence * (0.7 + 0.3 * coverage), 4),
                evidence_coverage=round(coverage, 4),
                evidence_status=_evidence_status(confidence, bool(coverage)),
                accessibility=geography,
                reasons=reasons,
                dimension_scores=dimensions,
                evidence=evidence,
                limitations=limitations,
            ),
            profile,
        ))

    local_capable = any(
        item.accessibility.travel_level == AccessibilityLevel.SAME_CITY
        and item.recommendation_score >= 60
        for item, _ in staged
    )
    upgrade = context.needs_upgrade_referral or context.complexity == ComplexityLevel.HIGH or not local_capable
    travel_order = {
        AccessibilityLevel.SAME_CITY: 0,
        AccessibilityLevel.SAME_PROVINCE: 1,
        AccessibilityLevel.NEARBY_REGIONAL: 2,
        AccessibilityLevel.CROSS_REGION: 3,
        AccessibilityLevel.UNKNOWN: 4,
    }
    if upgrade:
        staged.sort(key=lambda pair: pair[0].recommendation_score, reverse=True)
        strategy = "建议升级转诊：按病种能力、专业能力、可达性和证据综合排序"
    else:
        staged.sort(key=lambda pair: (travel_order[pair[0].accessibility.travel_level], -pair[0].recommendation_score))
        strategy = "本地优先：优先展示本地机构，再补充周边区域中心的公立三甲强专科"
        if context.local_recommendation_limit is not None:
            local = [pair for pair in staged if pair[0].accessibility.travel_level == AccessibilityLevel.SAME_CITY]
            regional = [pair for pair in staged if pair[0].accessibility.travel_level != AccessibilityLevel.SAME_CITY]
            staged = [
                *local[:context.local_recommendation_limit],
                *regional,
                *local[context.local_recommendation_limit:],
            ]
    recommendations = [
        item.model_copy(update={"rank": rank})
        for rank, (item, _) in enumerate(staged[:top_k], start=1)
    ]
    return HospitalRecommendationReportV2(
        context=context,
        strategy=strategy,
        recommendations=recommendations,
        excluded=excluded,
        warnings=[
            "固定五维权重由后端重新计算，不采信模型返回的总分",
            "公开资料有限时降低证据可信度；只有明确事实冲突才移除候选",
        ],
    )


def recommend_clinicians(
    disease_group: str,
    profiles: list[ClinicianCapabilityProfile],
    *,
    allowed_hospital_ids: set[str] | None = None,
    recommendation_intent: RecommendationIntent | None = None,
    top_k: int = 5,
) -> list[ClinicianRecommendation]:
    """医生按固定四维权重重算；职称仅作为公开资历信息，不单独决定排序。"""
    output = []
    for profile in profiles:
        if profile.disease_group != disease_group:
            continue
        if allowed_hospital_ids and profile.basic_qualification.hospital_id not in allowed_hospital_ids:
            continue
        candidate = _clinician_candidate(
            recommendation_intent,
            profile.basic_qualification.clinician_name,
            profile.basic_qualification.hospital_name,
        )
        proposed = candidate.dimension_scores if candidate else None
        current = profile.current_case_match.assessment
        disease = profile.disease_expertise.assessment
        treatment = profile.treatment_expertise.assessment
        platform = profile.platform_capability.assessment
        qualification = profile.basic_qualification.assessment
        dimension_specs = [
            ("disease_match", "当前病种匹配", proposed.disease_match if proposed else None,
             current if current.status == AssessmentStatus.EVALUATED else disease,
             max(_fallback_score(current), _fallback_score(disease)), {"disease_expertise", "current_case_match", "clinician_specialty"}),
            ("treatment_expertise", "治疗/手术专长", proposed.treatment_expertise if proposed else None,
             treatment, _fallback_score(treatment, 55), {"treatment_expertise", "clinician_technical_expertise"}),
            ("hospital_department_platform", "所属医院/科室平台", proposed.hospital_department_platform if proposed else None,
             platform, _fallback_score(platform, 55), {"basic_qualification", "platform_capability", "department"}),
            ("clinical_qualification_capability", "临床资历与公开能力", proposed.clinical_qualification_capability if proposed else None,
             qualification, _fallback_score(qualification, 55), {"basic_qualification", "clinician_specialty"}),
        ]
        dimensions = [
            _dimension(name, label, fallback if llm_score is None else llm_score.score,
                       CLINICIAN_RECOMMENDATION_WEIGHTS[name],
                       assessment.reasoning if llm_score is None else llm_score.reasoning,
                       assessment, profile.evidence_records, fields)
            for name, label, llm_score, assessment, fallback, fields in dimension_specs
        ]
        score = sum(item.score * item.weight for item in dimensions)
        confidence = sum(item.evidence_confidence * item.weight for item in dimensions)
        reasons = _candidate_reasons(candidate, dimensions, profile.evidence_records) if candidate else [
            _reason("disease_match", "当前病种匹配", disease.reasoning,
                    _ensure_scored(disease, 60, "公开病种专长资料有限"), profile.evidence_records),
            _reason("treatment_expertise", "治疗/手术专长", treatment.reasoning,
                    treatment if treatment.score is not None else DimensionAssessment(
                        status=AssessmentStatus.EVALUATED, score=55, confidence=0.2,
                        reasoning="公开专业资料有限", limitations=["建议进一步核验"],
                    ), profile.evidence_records),
        ]
        output.append(
            ClinicianRecommendation(
                rank=1,
                clinician_id=profile.clinician_id,
                clinician_name=profile.basic_qualification.clinician_name,
                hospital_id=profile.basic_qualification.hospital_id,
                recommendation_score=round(score, 2),
                recommendation_confidence=round(confidence, 4),
                reasons=reasons,
                dimension_scores=dimensions,
                evidence_confidence=round(confidence, 4),
                evidence_status=_evidence_status(confidence, any(item.evidence_record_ids for item in dimensions)),
                limitations=[
                    "医生职称仅展示，不单独决定推荐排序",
                    "公开资料有限时保留候选并降低可信度；就诊前请确认排班和执业信息",
                ],
            )
        )
    output.sort(key=lambda item: item.recommendation_score, reverse=True)
    return [item.model_copy(update={"rank": rank}) for rank, item in enumerate(output[:top_k], start=1)]


def _assessment(
    status: AssessmentStatus,
    reasoning: str,
    fields: list[str],
    *,
    score: float | None = None,
    confidence: float = 0,
    limitations: list[str] | None = None,
) -> DimensionAssessment:
    return DimensionAssessment(
        status=status,
        score=score,
        confidence=confidence,
        reasoning=reasoning,
        evidence_fields=fields,
        limitations=limitations or [],
    )


def build_treatment_pattern(case: ClaimCase, disease_group: str) -> TreatmentPattern:
    procedures = _unique(str(value) for item in case.procedures if (value := _available(item.name)))
    medications = _unique(str(value) for item in case.medications if (value := _available(item.name)))
    discharge_plan = []
    for index, item in enumerate(case.medications):
        name = _available(item.name)
        if not name:
            continue
        details = [str(value) for field in (item.dose, item.frequency, item.route, item.duration) if (value := _available(field))]
        if details:
            discharge_plan.append(f"{name}：{'，'.join(details)}")
    components = []
    if medications:
        components.append("药物治疗")
    if procedures:
        components.append("操作/手术治疗")
    if case.events:
        components.append("病程支持与随访记录")
    if not components:
        components.append("现有材料中的治疗记录")
    readable = " + ".join(components)
    signature = "|".join([disease_group, *procedures, *medications])
    return TreatmentPattern(
        pattern_id="TP-" + sha256(signature.encode("utf-8")).hexdigest()[:12].upper(),
        disease_group=disease_group,
        readable_pattern_name=readable,
        treatment_description=f"当前材料记录：{readable}。该名称仅概括实际记录，不代表最佳治疗方案。",
        major_treatment=components,
        procedures=procedures,
        core_medications=medications,
        discharge_plan=discharge_plan,
        evidence_fields=_unique([
            *(f"procedures[{index}]" for index, _ in enumerate(procedures)),
            *(f"medications[{index}]" for index, _ in enumerate(medications)),
            *(f"events[{index}]" for index, _ in enumerate(case.events)),
        ]),
    )


def evaluate_current_treatment(
    case: ClaimCase,
    disease_group: str,
    pain_report: PainPointReport | None = None,
    historical_reference: HistoricalTreatmentReference | None = None,
) -> CurrentTreatmentEvaluation:
    """对本病例记录做保守评价；医学合理性项证据不足时明确待复核。"""
    if pain_report and pain_report.case_id != case.case_id:
        raise ValueError("痛点报告与治疗方案病例不一致")
    pattern = build_treatment_pattern(case, disease_group)
    diagnoses = [str(value) for item in case.diagnoses if (value := _available(item.name))]
    has_treatment = bool(pattern.procedures or pattern.core_medications)
    clinical_points = pain_report.pain_points if pain_report else []
    medication_concerns = [
        point for point in clinical_points
        if point.type in {PainPointType.MEDICATION_DIAGNOSIS_REVIEW, PainPointType.DUPLICATE_MEDICATION_REVIEW, PainPointType.MEDICATION_INSTRUCTION_INCOMPLETE}
    ]
    high_risk = [
        point for point in clinical_points
        if point.dimension == PainDimension.CLINICAL and point.severity in {PainSeverity.HIGH, PainSeverity.CRITICAL}
    ]

    if diagnoses and has_treatment:
        diagnosis_match = _assessment(
            AssessmentStatus.NEEDS_REVIEW,
            "已找到诊断和治疗记录，但病种-治疗合理性需要医学规则或专业人员复核",
            ["diagnoses", *pattern.evidence_fields],
            confidence=0.6,
            limitations=["不根据药名或操作名称自动宣称治疗正确"],
        )
    else:
        diagnosis_match = _assessment(
            AssessmentStatus.INSUFFICIENT_EVIDENCE,
            "诊断或治疗记录缺失，暂不能评价诊断与治疗匹配",
            ["diagnoses", "procedures", "medications"],
        )

    if not pattern.core_medications:
        medication = _assessment(AssessmentStatus.NOT_APPLICABLE, "当前材料未记录可评价的用药", ["medications"])
    elif medication_concerns:
        medication = _assessment(
            AssessmentStatus.NEEDS_REVIEW,
            "第二问发现用药相关关注点，需要结合原文和临床背景复核",
            ["medications", "pain_report.pain_points"],
            confidence=max(point.confidence for point in medication_concerns),
            limitations=["风险筛查不等同于处方点评或医生治疗错误"],
        )
    else:
        medication = _assessment(
            AssessmentStatus.NEEDS_REVIEW,
            "用药记录存在，但没有足够的适应证、禁忌证和患者个体信息完成医学合理性判断",
            ["medications"],
            confidence=0.5,
            limitations=["未发现规则命中不能解释为用药合理性满分"],
        )

    procedure = (
        _assessment(
            AssessmentStatus.NEEDS_REVIEW,
            "操作/手术已有病例证据，但其临床合理性需要结合适应证和完整病情复核",
            ["procedures"],
            confidence=0.55,
            limitations=["不根据操作名称直接判断必要性"],
        )
        if pattern.procedures else
        _assessment(AssessmentStatus.NOT_APPLICABLE, "当前材料未记录手术或操作", ["procedures"])
    )

    completeness_components = {
        "diagnoses": bool(diagnoses),
        "treatment": has_treatment,
        "events": bool(case.events),
        "hospitalization": _available(case.hospitalization.length_of_stay_days) is not None,
        "discharge_plan": bool(pattern.discharge_plan),
    }
    completeness_score = 100 * sum(completeness_components.values()) / len(completeness_components)
    completeness = _assessment(
        AssessmentStatus.EVALUATED,
        "按诊断、实际治疗、医疗事件、住院信息和后续用药记录的材料覆盖度评价",
        list(completeness_components),
        score=round(completeness_score, 2),
        confidence=0.85,
        limitations=["该项评价记录完整性，不代表临床治疗效果"],
    )

    if pattern.discharge_plan:
        continuity_score = min(100.0, 50 + 50 * sum(
            bool(_available(field))
            for item in case.medications
            for field in (item.frequency, item.route, item.duration)
        ) / max(1, len(case.medications) * 3))
        continuity = _assessment(
            AssessmentStatus.EVALUATED,
            "按病例中可追溯的用药频次、途径和疗程记录评价出院衔接信息完整度",
            ["medications.frequency", "medications.route", "medications.duration"],
            score=round(continuity_score, 2),
            confidence=0.75,
            limitations=["当前 Schema 未单列完整康复和复诊计划，评价仅覆盖已有字段"],
        )
    else:
        continuity = _assessment(
            AssessmentStatus.INSUFFICIENT_EVIDENCE,
            "没有足够的出院用药或后续计划字段，暂不能评价连续照护",
            ["medications", "events"],
        )

    if high_risk:
        risk = _assessment(
            AssessmentStatus.NEEDS_REVIEW,
            "存在高风险临床关注点，需要核对是否有对应处理",
            ["pain_report.pain_points", "events"],
            confidence=max(point.confidence for point in high_risk),
            limitations=["高风险关注点不等同于医疗事故"],
        )
    elif pain_report:
        risk = _assessment(
            AssessmentStatus.NEEDS_REVIEW,
            "当前规则未发现高风险痛点，但材料不足以证明所有风险均已处理",
            ["pain_report.pain_points", "events"],
            confidence=0.5,
            limitations=["未命中规则不能解释为无风险"],
        )
    else:
        risk = _assessment(
            AssessmentStatus.INSUFFICIENT_EVIDENCE,
            "尚无痛点报告，暂不能评价风险管理",
            ["pain_report", "events"],
        )

    dimensions = [diagnosis_match, medication, procedure, completeness, continuity, risk]
    evaluated = [item for item in dimensions if item.status == AssessmentStatus.EVALUATED]
    field_confidences = [
        field.confidence for collection in (case.diagnoses, case.procedures, case.medications)
        for item in collection for field in ([item.name]) if field.confidence is not None
    ]
    review_reasons = _unique(
        item.reasoning for item in dimensions if item.status == AssessmentStatus.NEEDS_REVIEW
    )
    history = historical_reference or HistoricalTreatmentReference(
        limitations=["暂无足够相似历史案例，当前评价主要基于本次病例材料"]
    )
    return CurrentTreatmentEvaluation(
        case_id=case.case_id,
        pattern=pattern,
        treatment_diagnosis_match=diagnosis_match,
        medication_rationality=medication,
        procedure_rationality=procedure,
        treatment_completeness=completeness,
        discharge_continuity=continuity,
        risk_management=risk,
        evidence_coverage=round(len([item for item in dimensions if item.status != AssessmentStatus.INSUFFICIENT_EVIDENCE]) / 6, 4),
        overall_confidence=round(mean(field_confidences), 4) if field_confidences else 0,
        key_findings=[f"治疗记录完整度：{completeness.score:.1f}分", *(f"需要复核：{reason}" for reason in review_reasons[:3])],
        review_reasons=review_reasons,
        historical_reference=history,
    )
