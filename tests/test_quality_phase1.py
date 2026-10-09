import inspect
import json
import importlib
from pathlib import Path
import pytest

from medical_claims.disease_provider_catalog import load_catalog
from medical_claims.models import (
    ClaimCase, Diagnosis, DiagnosisType, Evidence, FieldStatus, FieldValue, Medication, Procedure,
)
from medical_claims.quality_phase1_engine import (
    attach_historical_support,
    build_historical_support,
    evaluate_current_treatment,
    recommend_clinicians,
    recommend_hospitals,
)
from medical_claims.quality_phase1_models import (
    AssessmentStatus,
    BasicQualification,
    CapabilityEvidenceRecord,
    CapabilityCacheWriteRequest,
    ClinicianCapabilityProfile,
    ClinicianDiseaseExpertise,
    ClinicianTreatmentExpertise,
    CurrentCaseMatch,
    DimensionAssessment,
    EvidenceVerificationStatus,
    HistoricalEvidenceLevel,
    GeographicRecommendationStrategy,
    PatientRecommendationContext,
    PlatformCapability,
    PublicSource,
    HospitalRecommendationRequestV2,
    ProviderDiscoveryQuery,
    ProviderDiscoveryResult,
    RecommendationIntent,
    ProposedHospitalScores,
    ProposedClinicianScores,
    ProposedDimensionScore,
    SuggestedEvidenceReason,
    SuggestedHospitalCandidate,
    SuggestedClinicianCandidate,
    TreatmentEvaluationRequestV2,
)
from medical_claims.quality_phase1_service import (
    DEFAULT_VERIFIED_CAPABILITY_PATH,
    run_hospital_recommendation,
    run_provider_discovery,
    run_treatment_evaluation,
)
from medical_claims.provider_discovery import LocalCapabilityCache, ProviderDiscoveryService
from medical_claims.quality_phase1_adapters import patient_feedback_from_records
from medical_claims.quality_models import ProfileCaseRecord
from medical_claims.recommendation_intent import validate_recommendation_intent
from medical_claims.recommendation_prompt import RECOMMENDATION_PLANNER_INSTRUCTIONS
from web.backend.app import app

backend_app = importlib.import_module("web.backend.app")


ROOT = Path(__file__).resolve().parents[1]
CATALOG = load_catalog(ROOT / "knowledge" / "quality" / "disease_provider_catalog.json")


def assessed(score, reasoning, source="PUBLIC-1"):
    return DimensionAssessment(
        status=AssessmentStatus.EVALUATED,
        score=score,
        confidence=0.9,
        reasoning=reasoning,
        evidence_fields=["verified_field"],
        source_ids=[source],
    )


def test_catalog_has_three_diseases_and_at_least_five_hospitals_each():
    counts = {item.disease_group: len(item.hospitals) for item in CATALOG.diseases}
    assert counts == {"pneumonia": 9, "influenza": 7, "hypertension": 5}


def test_all_catalog_hospitals_and_capability_fields_are_traceable():
    for disease in CATALOG.diseases:
        for profile in disease.hospitals:
            known = {source.source_id for source in profile.sources}
            assert known
            assert all(source.url.startswith("https://") or source.url.startswith("http://") for source in profile.sources)
            assert set(profile.disease_specialty.assessment.source_ids).issubset(known)
            assert profile.disease_specialty.assessment.source_ids


def test_specialty_evidence_does_not_cross_diseases():
    forbidden = {
        "pneumonia": ("内分泌", "糖尿病", "高血压专病"),
        "influenza": ("内分泌", "糖尿病", "高血压专病"),
        "hypertension": ("呼吸内科", "感染科", "肺炎"),
    }
    for disease in CATALOG.diseases:
        for profile in disease.hospitals:
            specialty = profile.disease_specialty
            text = "|".join([
                *specialty.related_departments,
                *specialty.key_specialties,
                specialty.specialty_capability_description or "",
            ])
            assert specialty.disease_group == disease.disease_group
            assert not any(word in text for word in forbidden[disease.disease_group])


def test_local_hospital_precedes_equally_capable_remote_hospital():
    base = CATALOG.diseases[0].hospitals[0]
    local_foundation = base.institution_foundation.model_copy(update={
        "hospital_id": "LOCAL", "hospital_name": "本地测试医院", "province": "测试省", "city": "本地市"
    })
    remote_foundation = base.institution_foundation.model_copy(update={
        "hospital_id": "REMOTE", "hospital_name": "远程测试医院", "province": "外省", "city": "远程市"
    })
    local = base.model_copy(update={"hospital_id": "LOCAL", "institution_foundation": local_foundation}, deep=True)
    remote = base.model_copy(update={"hospital_id": "REMOTE", "institution_foundation": remote_foundation}, deep=True)
    context = PatientRecommendationContext(case_id="CASE-1", disease_group="pneumonia", province="测试省", city="本地市")
    report = recommend_hospitals(context, [remote, local])
    assert [item.hospital_id for item in report.recommendations] == ["LOCAL", "REMOTE"]
    assert report.recommendations[0].recommendation_score > report.recommendations[1].recommendation_score


def test_local_first_limits_local_to_three_without_strictly_filtering_incomplete_nearby_candidates():
    base = CATALOG.diseases[0].hospitals[0]

    def located(hospital_id, city, province, hospital_type, hospital_level):
        foundation = base.institution_foundation.model_copy(update={
            "hospital_id": hospital_id,
            "hospital_name": hospital_id,
            "city": city,
            "province": province,
            "hospital_type": hospital_type,
            "hospital_level": hospital_level,
        })
        return base.model_copy(update={
            "hospital_id": hospital_id,
            "institution_foundation": foundation,
        }, deep=True)

    profiles = [
        *(located(f"LOCAL-{index}", "重庆市", "重庆市", "综合医院", "三级甲等") for index in range(1, 5)),
        located("CHENGDU-PUBLIC-3A", "成都市", "四川省", "公立综合医院", "三级甲等"),
        located("XIAN-PUBLIC-3A", "西安市", "陕西省", "公立专科医院", "三级甲等"),
        located("CHENGDU-PRIVATE-3A", "成都市", "四川省", "民营专科医院", "三级甲等"),
        located("CHENGDU-PUBLIC-OTHER", "成都市", "四川省", "公立综合医院", "三级医院"),
        located("QINHUANGDAO-PUBLIC-3A", "秦皇岛市", "河北省", "公立综合医院", "三级甲等"),
    ]
    context = PatientRecommendationContext(
        case_id="CASE-LOCAL-NEARBY",
        disease_group="pneumonia",
        province="重庆市",
        city="重庆市",
        nearby_major_cities=["成都市", "西安市"],
        local_recommendation_limit=3,
        nearby_requires_public_tertiary_a=True,
    )

    report = recommend_hospitals(context, profiles, top_k=5)

    ids = [item.hospital_id for item in report.recommendations]
    assert len([item for item in ids if item.startswith("LOCAL-")]) == 3
    assert ids[3] in {"CHENGDU-PUBLIC-3A", "CHENGDU-PRIVATE-3A", "CHENGDU-PUBLIC-OTHER"}
    assert "CHENGDU-PRIVATE-3A" not in report.excluded
    assert "CHENGDU-PUBLIC-OTHER" not in report.excluded
    assert "QINHUANGDAO-PUBLIC-3A" not in ids


def test_high_grade_cannot_rescue_wrong_specialty():
    base = CATALOG.diseases[0].hospitals[0]
    mismatch = base.model_copy(update={"hospital_id": "WRONG", "disease_group": "influenza"}, deep=True)
    context = PatientRecommendationContext(
        case_id="CASE-2",
        disease_group="pneumonia",
        province=base.institution_foundation.province,
        city=base.institution_foundation.city,
    )
    report = recommend_hospitals(context, [mismatch])
    assert not report.recommendations
    assert "病种不匹配" in report.excluded["WRONG"]


def test_missing_history_is_not_zero_and_does_not_block_capability_recommendation():
    profile = CATALOG.diseases[0].hospitals[0]
    context = PatientRecommendationContext(
        case_id="CASE-3", disease_group="pneumonia",
        province=profile.institution_foundation.province, city=profile.institution_foundation.city,
    )
    recommendation = recommend_hospitals(context, [profile]).recommendations[0]
    assert profile.historical_evidence.assessment.status == AssessmentStatus.INSUFFICIENT_EVIDENCE
    assert profile.historical_evidence.assessment.score is None
    assert recommendation.recommendation_score > 0
    assert {item.dimension for item in recommendation.dimension_scores} == {
        "disease_specialty_match", "geographic_convenience", "hospital_professional_capability",
        "clinician_resources", "treatment_technology_capability",
    }
    assert all(reason.dimension != "historical_evidence" for reason in recommendation.reasons)


def test_history_adds_traceable_reason_without_becoming_quality_claim():
    profile = CATALOG.diseases[0].hospitals[0]
    support = build_historical_support(
        3,
        supported_metrics=["费用", "住院日", "痛点"],
        case_ids=["CASE-A", "CASE-B", "CASE-C"],
    )
    enriched = attach_historical_support(profile, support)
    context = PatientRecommendationContext(
        case_id="CASE-4", disease_group="pneumonia",
        province=profile.institution_foundation.province, city=profile.institution_foundation.city,
    )
    recommendation = recommend_hospitals(context, [enriched]).recommendations[0]
    history_reason = next(reason for reason in recommendation.reasons if reason.dimension == "historical_evidence")
    assert history_reason.source_ids == ["claim:CASE-A", "claim:CASE-B", "claim:CASE-C"]
    assert "有限历史参考" in history_reason.explanation
    assert "质量高" not in history_reason.explanation


def test_single_case_is_labeled_observation_not_long_term_performance():
    support = build_historical_support(1, supported_metrics=["费用"], case_ids=["ONLY-1"])
    assert support.evidence_level == HistoricalEvidenceLevel.SINGLE_CASE
    assert "单病例历史观察" in support.history_summary
    assert "长期" not in support.history_summary
    assert "质量" not in support.history_summary


def test_every_hospital_recommendation_has_two_traceable_reasons():
    disease = CATALOG.diseases[1]
    first = disease.hospitals[0]
    context = PatientRecommendationContext(
        case_id="CASE-5", disease_group=disease.disease_group,
        province=first.institution_foundation.province, city=first.institution_foundation.city,
    )
    report = recommend_hospitals(context, disease.hospitals)
    assert report.recommendations
    for item in report.recommendations:
        assert len(item.reasons) >= 2
        assert len(item.evidence) >= 2
        assert all(reason.evidence_fields for reason in item.reasons)
        assert all(reason.source_ids for reason in item.reasons)


def clinician_profile(clinician_id, title, disease_score, case_score, history_count=0):
    source = PublicSource(
        source_id="PUBLIC-1", title="医院官网医生页面", url="https://example.org/doctor",
        authority_type="医院官网", query_date="2026-10-07",
    )
    history = build_historical_support(
        history_count,
        supported_metrics=["治疗记录"] if history_count else [],
        case_ids=[f"{clinician_id}-CASE-{index}" for index in range(history_count)],
    )
    evidence = CapabilityEvidenceRecord(
        evidence_id=f"EVD-{clinician_id}", source_id="PUBLIC-1",
        source_name="医院官网医生页面", source_type="医院官网",
        source_url="https://example.org/doctor", query_date="2026-10-07",
        evidence_text="医生公开资料与病种专业方向", evidence_field="verified_field",
        supported_claim="支持医生身份、病种专长和相关技术能力。",
    )
    return ClinicianCapabilityProfile(
        clinician_id=clinician_id,
        disease_group="pneumonia",
        basic_qualification=BasicQualification(
            clinician_id=clinician_id, clinician_name=clinician_id, hospital_id="HOSP-1",
            hospital_name="测试医院", department="呼吸科", title=title,
            assessment=assessed(80, "基础资质来自官网"),
        ),
        disease_expertise=ClinicianDiseaseExpertise(
            disease_group="pneumonia", disease_expertise=["肺炎"], specialty_match_level="matched",
            assessment=assessed(disease_score, "公开专业方向与当前病种匹配"),
        ),
        treatment_expertise=ClinicianTreatmentExpertise(
            treatment_expertise=["感染诊疗"], assessment=assessed(75, "公开治疗专长匹配")
        ),
        platform_capability=PlatformCapability(
            department_strength="呼吸专科平台", assessment=assessed(80, "所属科室能力有来源")
        ),
        current_case_match=CurrentCaseMatch(
            disease_match="匹配", overall_case_match="匹配",
            assessment=assessed(case_score, "与当前病例病种和复杂度匹配"),
        ),
        historical_evidence=history,
        sources=[source], evidence_records=[evidence],
    )


def test_clinician_is_not_ranked_by_title_and_needs_no_history_to_have_profile():
    chief = clinician_profile("主任医生", "主任医师", 65, 60, history_count=1)
    attending = clinician_profile("主治医生", "主治医师", 95, 95, history_count=0)
    result = recommend_clinicians("pneumonia", [chief, attending], allowed_hospital_ids={"HOSP-1"})
    assert [item.clinician_id for item in result] == ["主治医生", "主任医生"]
    assert attending.historical_evidence.assessment.score is None
    assert result[0].recommendation_score > result[1].recommendation_score


def proposed(score, reasoning="模型逐维评分"):
    return ProposedDimensionScore(score=score, reasoning=reasoning)


def test_backend_recomputes_fixed_hospital_weights_and_corrects_geography():
    profile = CATALOG.diseases[0].hospitals[0]
    context = PatientRecommendationContext(
        case_id="FIXED-WEIGHTS-H", disease_group="pneumonia",
        province=profile.institution_foundation.province,
        city=profile.institution_foundation.city,
    )
    candidate = SuggestedHospitalCandidate(
        hospital_name=profile.institution_foundation.hospital_name,
        city=profile.institution_foundation.city,
        department="呼吸科",
        recommendation_reasons=[
            SuggestedEvidenceReason(
                reason="病种方向匹配",
                evidence_type="department",
                suggested_source_name="测试医院官网",
                suggested_source_url="https://hospital.example.cn/department",
                suggested_evidence_summary="核验该院是否设有与当前病种相关的科室",
            ),
            SuggestedEvidenceReason(reason="具备相关诊疗能力", evidence_type="treatment_capability"),
        ],
        dimension_scores=ProposedHospitalScores(
            disease_specialty_match=proposed(80),
            geographic_convenience=proposed(1, "故意错误的地理分"),
            hospital_professional_capability=proposed(70),
            clinician_resources=proposed(60),
            treatment_technology_capability=proposed(50),
        ),
        total_score=1,
    )
    intent = cataract_intent().model_copy(update={
        "primary_disease": "肺炎",
        "candidate_hospitals": [candidate],
    })
    recommendation = recommend_hospitals(
        context, [profile], recommendation_intent=intent,
    ).recommendations[0]
    scores = {item.dimension: item for item in recommendation.dimension_scores}
    assert scores["geographic_convenience"].score == 100
    assert recommendation.recommendation_score == 76
    assert recommendation.recommendation_score != candidate.total_score
    assert recommendation.reasons[0].suggested_source_name == "测试医院官网"
    assert recommendation.reasons[0].suggested_source_url == "https://hospital.example.cn/department"
    assert recommendation.reasons[0].suggested_evidence_summary == "核验该院是否设有与当前病种相关的科室"
    assert [scores[key].weight for key in (
        "disease_specialty_match", "geographic_convenience", "hospital_professional_capability",
        "clinician_resources", "treatment_technology_capability",
    )] == [0.35, 0.20, 0.20, 0.15, 0.10]


def test_backend_recomputes_fixed_clinician_weights_and_keeps_limited_public_data():
    profile = clinician_profile("真实医生", "主任医师", 82, 84, history_count=0)
    candidate = SuggestedClinicianCandidate(
        doctor_name="真实医生", hospital_name="测试医院", department="呼吸科", specialty="肺炎诊疗",
        recommendation_reasons=[
            SuggestedEvidenceReason(reason="病种方向匹配", evidence_type="clinician_specialty"),
            SuggestedEvidenceReason(reason="相关治疗方向匹配", evidence_type="clinician_technical_expertise"),
        ],
        dimension_scores=ProposedClinicianScores(
            disease_match=proposed(80), treatment_expertise=proposed(70),
            hospital_department_platform=proposed(60), clinical_qualification_capability=proposed(50),
        ),
        total_score=2,
    )
    intent = cataract_intent().model_copy(update={
        "primary_disease": "肺炎",
        "candidate_clinicians": [candidate],
    })
    result = recommend_clinicians(
        "pneumonia", [profile], recommendation_intent=intent,
    )[0]
    assert result.recommendation_score == 68.5
    assert result.recommendation_score != candidate.total_score
    assert [item.weight for item in result.dimension_scores] == [0.35, 0.30, 0.20, 0.15]
    assert result.evidence_status in {
        EvidenceVerificationStatus.VERIFIED,
        EvidenceVerificationStatus.PARTIALLY_VERIFIED,
        EvidenceVerificationStatus.PENDING_VERIFICATION,
    }


def extracted(value, document="case.jpg"):
    return FieldValue(
        value=value,
        normalized_value=value,
        confidence=0.95,
        status=FieldStatus.EXTRACTED,
        evidence=[Evidence(document_id=document, page=1, text=str(value))],
    )


def test_current_treatment_can_be_evaluated_without_historical_cases():
    case = ClaimCase(
        case_id="TREATMENT-1",
        diagnoses=[Diagnosis(diagnosis_id="D1", name=extracted("社区获得性肺炎"))],
        medications=[Medication(
            medication_id="M1", name=extracted("阿莫西林"), dose=extracted("0.5g"),
            frequency=extracted("每日三次"), route=extracted("口服"), duration=extracted("5天"),
        )],
    )
    result = evaluate_current_treatment(case, "pneumonia")
    assert result.pattern.readable_pattern_name == "药物治疗"
    assert not result.historical_reference.available
    assert result.historical_reference.similar_historical_case_count == 0
    assert result.treatment_completeness.status == AssessmentStatus.EVALUATED
    assert result.medication_rationality.status == AssessmentStatus.NEEDS_REVIEW
    assert "这是最佳治疗方案" not in result.pattern.treatment_description
    assert any("暂无足够相似历史案例" in item for item in result.historical_reference.limitations)


def test_new_service_uses_independent_catalog_and_engine(monkeypatch):
    monkeypatch.setenv("QUALITY_DYNAMIC_DISCOVERY_ENABLED", "false")
    first = CATALOG.diseases[0].hospitals[0]
    request = HospitalRecommendationRequestV2(
        context=PatientRecommendationContext(
            case_id="SERVICE-1",
            disease_group="pneumonia",
            province=first.institution_foundation.province,
            city=first.institution_foundation.city,
        ),
        top_k=3,
    )
    report = run_hospital_recommendation(request, historical_records=[])
    assert report.engine_version == "capability-recommendation-engine-v1"
    assert 1 <= len(report.recommendations) <= 3
    assert all(item.hospital_id in {p.hospital_id for p in CATALOG.diseases[0].hospitals} for item in report.recommendations)


def test_new_service_does_not_fallback_to_legacy_for_unknown_disease():
    request = HospitalRecommendationRequestV2(
        context=PatientRecommendationContext(
            case_id="SERVICE-2", disease_group="diagnosis:白内障", disease_label="白内障"
        )
    )
    report = run_hospital_recommendation(
        request, discovery_service=ProviderDiscoveryService([])
    )
    assert report.recommendations == []
    assert any("尚未配置权威公开数据动态发现来源" in item for item in report.warnings)
    assert all("不支持" not in item and "白名单" not in item for item in report.warnings)
    assert report.strategy == "当前没有可展示候选；仅会在病种无法确认或候选存在明确事实冲突时为空"


def arbitrary_disease_profile(disease_group="diagnosis:白内障", hospital_id="EYE-1"):
    base = CATALOG.diseases[0].hospitals[0]
    foundation = base.institution_foundation.model_copy(update={
        "hospital_id": hospital_id,
        "hospital_name": "权威眼科测试医院",
        "province": "测试省",
        "city": "测试市",
    })
    specialty = base.disease_specialty.model_copy(update={
        "disease_group": disease_group,
        "related_departments": ["眼科"],
        "key_specialties": ["眼科重点专科"],
        "specialty_capability_description": "医院官网公开白内障相关诊疗能力",
    })
    return base.model_copy(update={
        "hospital_id": hospital_id,
        "disease_group": disease_group,
        "institution_foundation": foundation,
        "disease_specialty": specialty,
    }, deep=True)


class FixtureDynamicProvider:
    provider_name = "fixture_authoritative_discovery"

    def __init__(self, profiles):
        self.profiles = profiles

    def discover(self, query):
        return ProviderDiscoveryResult(
            query=query,
            hospital_profiles=self.profiles,
            discovery_layers=[self.provider_name],
        )


def test_arbitrary_reliably_identified_disease_uses_pluggable_discovery():
    profile = arbitrary_disease_profile()
    service = ProviderDiscoveryService([], [FixtureDynamicProvider([profile])])
    request = HospitalRecommendationRequestV2(
        context=PatientRecommendationContext(
            case_id="GENERAL-DISEASE-1",
            disease_group="diagnosis:白内障",
            disease_label="白内障",
            province="测试省",
            city="测试市",
        ),
        top_k=5,
    )
    report = run_hospital_recommendation(request, discovery_service=service)
    assert [item.hospital_id for item in report.recommendations] == ["EYE-1"]
    assert report.discovery_layers == ["fixture_authoritative_discovery"]
    assert report.dynamic_discovery_attempted
    assert report.context.disease_label == "白内障"
    assert report.capability_need.capability_terms == ["白内障诊疗能力"]
    assert len(report.candidate_profiles) == 1
    assert any("当前已完成公开证据核验的机构为 1 家" in item for item in report.warnings)


def test_dynamic_discovery_rejects_profile_without_matching_disease_evidence():
    wrong = arbitrary_disease_profile(disease_group="pneumonia", hospital_id="WRONG-DISEASE")
    query = ProviderDiscoveryQuery(
        disease_group="diagnosis:白内障", disease_label="白内障", city="测试市"
    )
    result = run_provider_discovery(
        query,
        discovery_service=ProviderDiscoveryService([], [FixtureDynamicProvider([wrong])]),
    )
    assert result.hospital_profiles == []
    assert result.query.capability_need.capability_terms == ["白内障诊疗能力"]
    assert result.query.capability_need.related_specialty_terms == ["眼科"]
    assert any("病种标识不匹配" in item for item in result.warnings)


def test_verified_capability_cache_reuses_arbitrary_disease_profile(tmp_path):
    cache = LocalCapabilityCache(tmp_path / "provider-cache.json")
    profile = arbitrary_disease_profile()
    counts = cache.write_verified(CapabilityCacheWriteRequest(hospital_profiles=[profile]))
    assert counts == {"hospital_profiles": 1, "clinician_profiles": 0}
    query = ProviderDiscoveryQuery(
        disease_group="diagnosis:白内障", disease_label="白内障", city="测试市"
    )
    result = ProviderDiscoveryService([cache]).discover(query)
    assert [item.hospital_id for item in result.hospital_profiles] == ["EYE-1"]
    assert result.discovery_layers == ["local_verified_capability_cache"]


def test_verified_capability_cache_rejects_untraceable_profile(tmp_path):
    cache = LocalCapabilityCache(tmp_path / "provider-cache.json")
    valid = arbitrary_disease_profile()
    invalid_specialty = valid.disease_specialty.model_copy(update={
        "assessment": DimensionAssessment(
            status=AssessmentStatus.INSUFFICIENT_EVIDENCE,
            confidence=0,
            reasoning="尚未核验",
        )
    })
    invalid = valid.model_copy(update={"disease_specialty": invalid_specialty}, deep=True)
    import pytest
    with pytest.raises(ValueError, match="病种专科能力尚未完成核验"):
        cache.write_verified(CapabilityCacheWriteRequest(hospital_profiles=[invalid]))


def test_new_service_source_does_not_call_legacy_recommendation():
    source = inspect.getsource(run_hospital_recommendation)
    assert "recommend_profiles" not in source
    assert "quality_engine" not in source
    assert "RecommendationReport(" not in source


def test_treatment_service_uses_current_case_input():
    case = ClaimCase(
        case_id="TREATMENT-SERVICE-1",
        diagnoses=[Diagnosis(diagnosis_id="D1", name=extracted("高血压"))],
    )
    result = run_treatment_evaluation(
        TreatmentEvaluationRequestV2(case=case, disease_group="hypertension")
    )
    assert result.case_id == case.case_id
    assert result.pattern.disease_group == "hypertension"


def test_phase1_v2_routes_are_registered_separately_from_legacy_routes():
    paths = {route.path for route in app.routes}
    assert {
        "/api/quality/v2/catalog",
        "/api/quality/v2/provider-discovery",
        "/api/quality/v2/provider-cache/verified",
        "/api/quality/v2/recommendations/hospitals",
        "/api/quality/v2/recommendations/clinicians",
        "/api/quality/v2/treatment-evaluation",
    }.issubset(paths)
    assert "/api/quality/recommendations" in paths


def test_single_patient_feedback_remains_single_observation():
    record = ProfileCaseRecord(
        case=ClaimCase(case_id="FEEDBACK-1"),
        institution_id="HOSP-1",
        disease_group="pneumonia",
        patient_feedback_score=4,
    )
    feedback = patient_feedback_from_records([record], "HOSP-1", "pneumonia")
    assert feedback.feedback_count == 1
    assert feedback.overall_feedback_level == "单次反馈观察"
    assert "不推断医院长期服务表现" in feedback.assessment.reasoning
    assert feedback.assessment.source_ids == ["feedback:FEEDBACK-1"]


def cataract_intent():
    return RecommendationIntent(
        primary_disease="老年性白内障",
        treatment_goal="改善白内障导致的视物模糊并完成术后连续照护",
        primary_specialty="眼科",
        secondary_specialties=[],
        required_capabilities=["白内障超声乳化手术", "人工晶体植入术"],
        comorbidities=["高血压"],
        case_complexity="low",
        geographic_strategy=GeographicRecommendationStrategy.LOCAL_FIRST,
        nearby_major_cities=["成都市", "西安市"],
        search_queries=[
            "重庆市 老年性白内障 眼科 超声乳化 人工晶体植入 医院官网",
            "重庆市 白内障手术 临床重点专科 眼科 卫健委",
        ],
        reasoning="主诊断、眼科住院和本次白内障手术共同指向老年性白内障。",
        supporting_case_fields=["diagnoses[0].name", "procedures[0].name"],
    )


def cataract_case():
    return ClaimCase(
        case_id="CQ-CATARACT",
        diagnoses=[
            Diagnosis(
                diagnosis_id="D1", name=extracted("皮质型老年性白内障左眼"),
                diagnosis_type=DiagnosisType.PRIMARY,
            ),
            Diagnosis(
                diagnosis_id="D2", name=extracted("屈光不正"),
                diagnosis_type=DiagnosisType.ADMISSION,
            ),
            Diagnosis(
                diagnosis_id="D3", name=extracted("高血压3级"),
                diagnosis_type=DiagnosisType.SECONDARY,
            ),
        ],
        procedures=[Procedure(
            procedure_id="P1",
            name=extracted("左眼白内障超声乳化摘除+人工晶体植入术"),
        )],
    )


def test_recommendation_intent_uses_encounter_focus_and_rejects_secondary_override():
    case = cataract_case()
    context = PatientRecommendationContext(
        case_id=case.case_id, disease_group="cataract", disease_label="白内障",
        province="重庆市", city="重庆市",
    )
    validated = validate_recommendation_intent(cataract_intent(), case, context)
    assert validated.primary_disease == "老年性白内障"
    with pytest.raises(ValueError, match="本次入院、手术和出院事实不一致"):
        validate_recommendation_intent(
            cataract_intent().model_copy(update={"primary_disease": "屈光不正"}),
            case,
            context,
        )


def test_intent_drives_capability_need_without_changing_v2_scoring():
    profile = arbitrary_disease_profile(disease_group="cataract", hospital_id="CQ-EYE-1")
    service = ProviderDiscoveryService([], [FixtureDynamicProvider([profile])])
    request = HospitalRecommendationRequestV2(
        context=PatientRecommendationContext(
            case_id="CQ-CATARACT", disease_group="diagnosis:屈光不正",
            disease_label="屈光不正", province="重庆市", city="重庆市",
        ),
        recommendation_intent=cataract_intent(),
    )
    report = run_hospital_recommendation(request, discovery_service=service)
    assert report.context.disease_group == "cataract"
    assert report.context.disease_label == "老年性白内障"
    assert report.capability_need.related_specialty_terms == ["眼科"]
    assert report.capability_need.capability_terms == ["白内障超声乳化手术", "人工晶体植入术"]
    assert report.recommendation_intent == cataract_intent()


def test_verified_cataract_cache_returns_three_local_and_two_nearby_public_tertiary_a():
    service = ProviderDiscoveryService([
        LocalCapabilityCache(DEFAULT_VERIFIED_CAPABILITY_PATH, ttl_days=365),
    ])
    report = run_hospital_recommendation(
        HospitalRecommendationRequestV2(
            context=PatientRecommendationContext(
                case_id="CQ-CATARACT",
                disease_group="cataract",
                disease_label="白内障",
                province="重庆市",
                city="重庆市",
            ),
            recommendation_intent=cataract_intent(),
            top_k=5,
        ),
        discovery_service=service,
    )
    assert len(report.recommendations) == 5
    assert [item.accessibility.hospital_city for item in report.recommendations[:3]] == ["重庆市"] * 3
    assert {item.accessibility.hospital_city for item in report.recommendations[3:]} == {"成都市", "西安市"}
    profiles = {item.hospital_id: item for item in report.candidate_profiles}
    for recommendation in report.recommendations[3:]:
        foundation = profiles[recommendation.hospital_id].institution_foundation
        assert foundation.hospital_level == "三级甲等"
        assert "公立" in foundation.hospital_type
    assert all(reason.evidence_record_ids for item in report.recommendations for reason in item.reasons)


def test_hospital_with_identity_city_and_specialty_is_not_excluded_for_incomplete_enhancement_dimensions():
    profile = arbitrary_disease_profile(disease_group="cataract", hospital_id="LIGHT-GATE")
    profile = profile.model_copy(update={
        "medical_resources": profile.medical_resources.model_copy(update={
            "assessment": DimensionAssessment(
                status=AssessmentStatus.INSUFFICIENT_EVIDENCE,
                reasoning="医疗资源增强字段尚不完整",
            )
        }),
        "regional_service": profile.regional_service.model_copy(update={
            "assessment": DimensionAssessment(
                status=AssessmentStatus.INSUFFICIENT_EVIDENCE,
                reasoning="区域服务增强字段尚不完整",
            )
        }),
    }, deep=True)
    context = PatientRecommendationContext(
        case_id="LIGHT-GATE", disease_group="cataract",
        disease_label="白内障", province="测试省", city="本地市",
    )
    report = recommend_hospitals(context, [profile], top_k=5)
    assert len(report.recommendations) == 1
    assert [reason.dimension for reason in report.recommendations[0].reasons] == [
        "disease_specialty_match", "geographic_convenience",
    ]


def test_deepseek_intent_planner_receives_all_relevant_case_facts(monkeypatch):
    captured = {}

    def fake_post(body):
        captured.update(body)
        return {"output_text": cataract_intent().model_dump_json()}

    monkeypatch.setattr(backend_app, "post_deepseek", fake_post)
    case = cataract_case()
    context = PatientRecommendationContext(
        case_id=case.case_id, disease_group="diagnosis:屈光不正", disease_label="屈光不正",
        province="重庆市", city="重庆市",
    )
    intent = backend_app.recommendation_intent_with_deepseek(case, context)
    facts = json.loads(captured["input"])
    assert intent.primary_disease == "老年性白内障"
    assert {item["name"] for item in facts["diagnoses"]} >= {
        "皮质型老年性白内障左眼", "屈光不正", "高血压3级",
    }
    assert facts["procedures"][0]["name"] == "左眼白内障超声乳化摘除+人工晶体植入术"
    assert facts["recommendation_region"]["city"] == "重庆市"
    assert "禁止简单取第一条诊断" in captured["instructions"]


def test_recommendation_intent_inherits_third_question_primary_disease(monkeypatch):
    captured = {}
    upstream = "皮质型老年性白内障左眼"
    inherited = cataract_intent().model_copy(update={"primary_disease": upstream})

    def fake_post(body):
        captured.update(body)
        return {"output_text": inherited.model_dump_json()}

    monkeypatch.setattr(backend_app, "post_deepseek", fake_post)
    case = cataract_case()
    context = PatientRecommendationContext(
        case_id=case.case_id,
        disease_group="cataract",
        disease_label=upstream,
        province="重庆市",
        city="重庆市",
    )
    intent = backend_app.recommendation_intent_with_deepseek(
        case,
        context,
        upstream_primary_disease=upstream,
    )
    facts = json.loads(captured["input"])
    assert facts["upstream_clinical_context"] == {
        "primary_disease": upstream,
        "source": "care_guide.disease_topic",
        "locked": True,
    }
    assert intent.primary_disease == upstream
    assert intent.primary_disease_source == "care_guide.disease_topic"
    assert "不得重新选择、改写或用其他诊断覆盖" in captured["instructions"]

    with pytest.raises(ValueError, match="不得覆盖第三问"):
        validate_recommendation_intent(cataract_intent(), case, context, upstream)


def test_recommendation_prompt_is_centralized_generic_and_rejects_placeholder_doctors():
    assert "candidate_hospitals" in RECOMMENDATION_PLANNER_INSTRUCTIONS
    assert "candidate_clinicians" in RECOMMENDATION_PLANNER_INSTRUCTIONS
    assert "suggested_source_url" in RECOMMENDATION_PLANNER_INSTRUCTIONS
    assert "suggested_evidence_summary" in RECOMMENDATION_PLANNER_INSTRUCTIONS
    assert "待核验医生A" in RECOMMENDATION_PLANNER_INSTRUCTIONS
    assert "白内障" not in RECOMMENDATION_PLANNER_INSTRUCTIONS
    assert "3至5家" in RECOMMENDATION_PLANNER_INSTRUCTIONS
    assert "disease_specialty_match（病种/专科匹配，35%）" in RECOMMENDATION_PLANNER_INSTRUCTIONS
    assert "treatment_expertise（治疗/手术专长，30%）" in RECOMMENDATION_PLANNER_INSTRUCTIONS

    payload = cataract_intent().model_dump(mode="json")
    payload["candidate_clinicians"] = [{
        "doctor_name": "待核验医生A",
        "hospital_name": "测试医院",
        "department": "眼科",
        "specialty": "当前病种诊疗",
        "recommendation_reasons": [
            {"reason": "病种方向匹配", "evidence_type": "clinician_specialty"},
            {"reason": "科室方向匹配", "evidence_type": "department"},
        ],
    }]
    intent = RecommendationIntent.model_validate(payload)
    validated = validate_recommendation_intent(
        intent,
        cataract_case(),
        PatientRecommendationContext(
            case_id="CQ-CATARACT", disease_group="cataract",
            disease_label="老年性白内障", province="重庆市", city="重庆市",
        ),
    )
    assert validated.candidate_clinicians == []
