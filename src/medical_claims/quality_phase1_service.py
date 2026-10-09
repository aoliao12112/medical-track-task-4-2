"""第四问 Phase 1 的独立应用服务入口。

新主流程只读取新版候选目录、当前患者情境和旧存储中的病例事实。它不会读取旧版
``RecommendationReport``，也不会调用 ``quality_engine.recommend_profiles``。
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import os

from .quality_models import ProfileCaseRecord
from .provider_discovery import (
    CatalogDiscoveryProvider,
    DEFAULT_CAPABILITY_CACHE_PATH,
    LocalCapabilityCache,
    ProviderDiscoveryService,
)
from .quality_phase1_adapters import attach_record_history
from .online_provider_discovery import OnlineAuthoritativeProviderDiscovery
from .quality_phase1_engine import (
    build_historical_support,
    evaluate_current_treatment,
    recommend_clinicians,
    recommend_hospitals,
)
from .quality_phase1_models import (
    AssessmentStatus,
    BasicQualification,
    ClinicianCapabilityProfile,
    ClinicianDiseaseExpertise,
    ClinicianRecommendation,
    ClinicianRecommendationRequestV2,
    ClinicianTreatmentExpertise,
    CurrentCaseMatch,
    CurrentTreatmentEvaluation,
    DimensionAssessment,
    DiseaseSpecialtyCapability,
    HospitalCapabilityProfile,
    HospitalRecommendationReportV2,
    HospitalRecommendationRequestV2,
    InstitutionFoundation,
    MedicalResourceCapability,
    PatientServiceFeedback,
    PlatformCapability,
    ProviderDiscoveryQuery,
    ProviderDiscoveryResult,
    RecommendationIntent,
    RegionalServiceCapability,
    TreatmentEvaluationRequestV2,
)
from .recommendation_intent import disease_group_from_intent


DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parents[2]
    / "knowledge"
    / "quality"
    / "disease_provider_catalog.json"
)
DEFAULT_VERIFIED_CAPABILITY_PATH = (
    Path(__file__).resolve().parents[2]
    / "knowledge"
    / "quality"
    / "verified_provider_capability_cache.json"
)


def _normalized_name(value: str) -> str:
    return "".join(value.split()).replace("（", "(").replace("）", ")")


def _pending_assessment(reasoning: str, fields: list[str]) -> DimensionAssessment:
    return DimensionAssessment(
        status=AssessmentStatus.INSUFFICIENT_EVIDENCE,
        confidence=0,
        reasoning=reasoning,
        evidence_fields=fields,
        limitations=["联网公开证据暂未完成核验"],
    )


def _pending_hospital_profile(
    intent: RecommendationIntent,
    context,
    candidate,
) -> HospitalCapabilityProfile:
    """把模型候选保存为待核验画像；它不进入可信能力缓存。"""
    identity = f"{_normalized_name(candidate.hospital_name)}|{candidate.city}|{context.disease_group}"
    hospital_id = "pending-hospital-" + sha256(identity.encode("utf-8")).hexdigest()[:16]
    department = candidate.department or intent.required_specialty or intent.primary_specialty
    pending = "推荐基于当前病例与模型综合判断，公开证据暂未完成核验。"
    return HospitalCapabilityProfile(
        hospital_id=hospital_id,
        disease_group=context.disease_group,
        institution_foundation=InstitutionFoundation(
            hospital_id=hospital_id,
            hospital_name=candidate.hospital_name,
            province=context.province if candidate.city == context.city else None,
            city=candidate.city,
            assessment=_pending_assessment(pending, ["institution_foundation", "city"]),
        ),
        disease_specialty=DiseaseSpecialtyCapability(
            disease_group=context.disease_group,
            related_departments=[department] if department else [],
            specialty_capability_description=pending,
            specialty_match_level="待核验",
            assessment=_pending_assessment(pending, ["department", "disease_capability"]),
        ),
        medical_resources=MedicalResourceCapability(
            assessment=_pending_assessment(pending, ["medical_resources", "treatment_capability"]),
        ),
        regional_service=RegionalServiceCapability(
            assessment=_pending_assessment(pending, ["regional_service"]),
        ),
        patient_service_feedback=PatientServiceFeedback(
            assessment=_pending_assessment("暂无可关联的可靠患者反馈数据", ["patient_service_feedback"]),
        ),
        historical_evidence=build_historical_support(0),
        sources=[],
        evidence_records=[],
        profile_version="hospital-capability-profile-v4-pending-verification",
    )


def _merge_intent_hospital_candidates(
    profiles: list[HospitalCapabilityProfile],
    intent: RecommendationIntent | None,
    context,
) -> list[HospitalCapabilityProfile]:
    if intent is None:
        return profiles
    merged = list(profiles)
    known = {_normalized_name(item.institution_foundation.hospital_name) for item in merged}
    for candidate in intent.candidate_hospitals:
        key = _normalized_name(candidate.hospital_name)
        if key not in known:
            merged.append(_pending_hospital_profile(intent, context, candidate))
            known.add(key)
    return merged


def _pending_clinician_profile(
    intent: RecommendationIntent,
    disease_group: str,
    candidate,
    hospital: HospitalCapabilityProfile,
) -> ClinicianCapabilityProfile:
    identity = f"{candidate.doctor_name}|{candidate.hospital_name}|{candidate.department}|{disease_group}"
    clinician_id = "pending-clinician-" + sha256(identity.encode("utf-8")).hexdigest()[:16]
    pending = "推荐基于当前病例与模型综合判断，公开证据暂未完成核验。"
    return ClinicianCapabilityProfile(
        clinician_id=clinician_id,
        disease_group=disease_group,
        basic_qualification=BasicQualification(
            clinician_id=clinician_id,
            clinician_name=candidate.doctor_name,
            hospital_id=hospital.hospital_id,
            hospital_name=candidate.hospital_name,
            department=candidate.department,
            assessment=_pending_assessment(pending, ["basic_qualification", "department"]),
        ),
        disease_expertise=ClinicianDiseaseExpertise(
            disease_group=disease_group,
            disease_expertise=[candidate.specialty] if candidate.specialty else [intent.primary_disease],
            specialty_match_level="待核验",
            assessment=_pending_assessment(pending, ["disease_expertise", "clinician_specialty"]),
        ),
        treatment_expertise=ClinicianTreatmentExpertise(
            treatment_expertise=[candidate.specialty] if candidate.specialty else [],
            assessment=_pending_assessment(pending, ["treatment_expertise"]),
        ),
        platform_capability=PlatformCapability(
            department_strength="待核验",
            assessment=_pending_assessment(pending, ["platform_capability", "department"]),
        ),
        current_case_match=CurrentCaseMatch(
            disease_match="待核验",
            treatment_need_match="待核验",
            complexity_match="待核验",
            overall_case_match="待核验",
            assessment=_pending_assessment(pending, ["current_case_match"]),
        ),
        historical_evidence=build_historical_support(0),
        sources=[],
        evidence_records=[],
        profile_version="clinician-capability-profile-v4-pending-verification",
    )


def _merge_intent_clinician_candidates(
    profiles: list[ClinicianCapabilityProfile],
    intent: RecommendationIntent | None,
    hospitals: list[HospitalCapabilityProfile],
) -> list[ClinicianCapabilityProfile]:
    if intent is None:
        return profiles
    merged = list(profiles)
    known = {
        (_normalized_name(item.basic_qualification.clinician_name),
         _normalized_name(item.basic_qualification.hospital_name))
        for item in merged
    }
    by_hospital = {
        _normalized_name(item.institution_foundation.hospital_name): item for item in hospitals
    }
    for candidate in intent.candidate_clinicians:
        key = (_normalized_name(candidate.doctor_name), _normalized_name(candidate.hospital_name))
        hospital = by_hospital.get(key[1])
        if hospital is not None and key not in known:
            merged.append(_pending_clinician_profile(intent, hospital.disease_group, candidate, hospital))
            known.add(key)
    return merged


def build_default_discovery_service(
    *,
    catalog_path: Path = DEFAULT_CATALOG_PATH,
    cache_path: Path = DEFAULT_CAPABILITY_CACHE_PATH,
) -> ProviderDiscoveryService:
    """优先可信缓存；覆盖不足时自动使用权威公开网页发现层。"""
    cache = LocalCapabilityCache(cache_path)
    online_enabled = os.getenv("QUALITY_DYNAMIC_DISCOVERY_ENABLED", "true").lower() not in {"0", "false", "no"}
    trusted_providers = [CatalogDiscoveryProvider(catalog_path)]
    if DEFAULT_VERIFIED_CAPABILITY_PATH.exists():
        trusted_providers.append(LocalCapabilityCache(DEFAULT_VERIFIED_CAPABILITY_PATH, ttl_days=365))
    trusted_providers.append(cache)
    return ProviderDiscoveryService(
        trusted_providers=trusted_providers,
        dynamic_providers=[OnlineAuthoritativeProviderDiscovery()] if online_enabled else [],
        cache_writer=cache,
    )


def run_provider_discovery(
    query: ProviderDiscoveryQuery,
    *,
    discovery_service: ProviderDiscoveryService | None = None,
) -> ProviderDiscoveryResult:
    return (discovery_service or build_default_discovery_service()).discover(query)


def run_hospital_recommendation(
    request: HospitalRecommendationRequestV2,
    *,
    historical_records: list[ProfileCaseRecord] | None = None,
    catalog_path: Path = DEFAULT_CATALOG_PATH,
    cache_path: Path = DEFAULT_CAPABILITY_CACHE_PATH,
    discovery_service: ProviderDiscoveryService | None = None,
) -> HospitalRecommendationReportV2:
    """通用发现结果为主，历史病例事实仅作为可缺失的补充证据。"""
    intent = request.recommendation_intent
    context = request.context
    if intent is not None:
        context = context.model_copy(update={
            "disease_group": disease_group_from_intent(intent),
            "disease_label": intent.primary_disease,
            "complexity": intent.case_complexity,
            "comorbidities": intent.comorbidities,
            "needs_upgrade_referral": intent.geographic_strategy.value == "upgrade_referral",
            "nearby_major_cities": intent.nearby_major_cities,
            "local_recommendation_limit": intent.local_recommendation_limit,
            "nearby_requires_public_tertiary_a": intent.nearby_requires_public_tertiary_a,
        })
    query = ProviderDiscoveryQuery(
        disease_group=context.disease_group,
        disease_label=context.disease_label or context.disease_group,
        province=context.province,
        city=context.city,
        minimum_candidates=request.top_k,
        include_clinicians=True,
        nearby_major_cities=context.nearby_major_cities,
        complexity=context.complexity,
        needs_upgrade_referral=context.needs_upgrade_referral,
        recommendation_intent=intent,
    )
    service = discovery_service or build_default_discovery_service(
        catalog_path=catalog_path,
        cache_path=cache_path,
    )
    discovered = run_provider_discovery(query, discovery_service=service)
    profiles = _merge_intent_hospital_candidates(
        discovered.hospital_profiles,
        intent,
        context,
    )
    if historical_records:
        profiles = attach_record_history(profiles, historical_records)
    report = recommend_hospitals(
        context,
        profiles,
        recommendation_intent=intent,
        top_k=request.top_k,
    )
    recommended_ids = {item.hospital_id for item in report.recommendations}
    recommended_profiles = [item for item in profiles if item.hospital_id in recommended_ids]
    clinician_result = (
        service.discover_clinicians(discovered.query, recommended_profiles)
        if recommended_profiles and request.context.disease_group
        else ProviderDiscoveryResult(query=discovered.query)
    )
    clinician_profiles = _merge_intent_clinician_candidates(
        clinician_result.clinician_profiles or discovered.clinician_profiles,
        intent,
        recommended_profiles,
    )
    strategy = report.strategy
    if not profiles:
        strategy = "当前没有可展示候选；仅会在病种无法确认或候选存在明确事实冲突时为空"
    warnings = [*discovered.warnings, *clinician_result.warnings, *report.warnings]
    if intent is not None and any(not item.evidence_records for item in profiles):
        warnings.append("部分候选的联网公开证据尚未完成核验，已按待核验状态保留，可稍后重新生成以刷新证据")
    return report.model_copy(update={
        "strategy": strategy,
        "warnings": list(dict.fromkeys(warnings)),
        "candidate_profiles": profiles,
        "candidate_clinician_profiles": clinician_profiles,
        "discovery_layers": list(dict.fromkeys([*discovered.discovery_layers, *clinician_result.discovery_layers])),
        "dynamic_discovery_attempted": discovered.dynamic_discovery_attempted or clinician_result.dynamic_discovery_attempted,
        "capability_need": discovered.query.capability_need,
        "recommendation_intent": intent,
        "discovery_diagnostics": discovered.diagnostics,
    })


def run_clinician_recommendation(
    request: ClinicianRecommendationRequestV2,
) -> list[ClinicianRecommendation]:
    allowed = set(request.allowed_hospital_ids) if request.allowed_hospital_ids else None
    return recommend_clinicians(
        request.disease_group,
        request.profiles,
        allowed_hospital_ids=allowed,
        recommendation_intent=request.recommendation_intent,
        top_k=request.top_k,
    )


def run_treatment_evaluation(
    request: TreatmentEvaluationRequestV2,
) -> CurrentTreatmentEvaluation:
    return evaluate_current_treatment(
        request.case,
        request.disease_group,
        pain_report=request.pain_report,
        historical_reference=request.historical_reference,
    )
