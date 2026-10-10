from pathlib import Path
import httpx

from medical_claims.online_provider_discovery import (
    OnlineAuthoritativeProviderDiscovery,
    PageDocument,
    SearchHit,
    SoHtmlSearch,
    _hospital_name,
    _authority,
    _source_quality,
    _clinician_mentions,
    _entity_bound_capability_context,
)
from medical_claims.provider_discovery import LocalCapabilityCache, ProviderDiscoveryService
from medical_claims.quality_phase1_models import (
    ClinicianRecommendationRequestV2,
    EvidenceVerificationStatus,
    GeographicRecommendationStrategy,
    HospitalRecommendationRequestV2,
    PatientRecommendationContext,
    ProviderDiscoveryQuery,
    RecommendationIntent,
    ProposedClinicianScores,
    ProposedDimensionScore,
    ProposedHospitalScores,
    SuggestedClinicianCandidate,
    SuggestedEvidenceReason,
    SuggestedHospitalCandidate,
)
from medical_claims.quality_phase1_service import run_clinician_recommendation, run_hospital_recommendation


class FixtureSearch:
    def search(self, query: str, limit: int = 8):
        if "医生" in query:
            return [SearchHit("测试市人民医院医生介绍", "https://hospital.example.cn/doctor-zhang")]
        return [SearchHit("测试市人民医院眼科", "https://hospital.example.cn/ophthalmology")]


class FixtureFetcher:
    def fetch(self, url: str):
        if "doctor" in url:
            return PageDocument(
                url,
                "测试市人民医院-张三主任医师",
                "测试市人民医院 版权所有 张三主任医师，眼科，擅长白内障诊疗与手术治疗。",
            )
        return PageDocument(
            url,
            "测试市人民医院-眼科",
            "测试市人民医院 版权所有 地址位于测试市。医院设有眼科，开展白内障诊疗与相关手术治疗。",
        )


def test_online_discovery_uses_original_authoritative_pages_and_builds_evidence(tmp_path: Path):
    online = OnlineAuthoritativeProviderDiscovery(FixtureSearch(), FixtureFetcher())
    cache = LocalCapabilityCache(tmp_path / "provider-cache.json")
    service = ProviderDiscoveryService([], [online], cache_writer=cache)
    request = HospitalRecommendationRequestV2(
        context=PatientRecommendationContext(
            case_id="ONLINE-1", disease_group="cataract", disease_label="白内障",
            province="测试省", city="测试市",
        ),
        top_k=5,
    )
    report = run_hospital_recommendation(request, discovery_service=service)

    assert len(report.recommendations) == 1
    assert report.dynamic_discovery_attempted
    assert report.discovery_layers == ["online_authoritative_public_sources"]
    assert report.candidate_profiles[0].profile_version.endswith("-online")
    assert {item.evidence_field for item in report.candidate_profiles[0].evidence_records} >= {
        "institution_foundation", "disease_specialty", "medical_resources"
    }
    assert all(reason.evidence_record_ids for reason in report.recommendations[0].reasons)
    assert report.candidate_clinician_profiles
    assert report.candidate_clinician_profiles[0].basic_qualification.clinician_name == "张三"
    assert {item.evidence_field for item in report.candidate_clinician_profiles[0].evidence_records} >= {
        "basic_qualification", "disease_expertise", "treatment_expertise"
    }

    cached = cache._read()
    assert len(cached.hospital_profiles) == 1
    assert len(cached.clinician_profiles) == 1


def test_search_hit_summary_is_never_used_as_capability_evidence(tmp_path: Path):
    class SummaryOnlyFetcher:
        def fetch(self, url: str):
            return PageDocument(url, "无关页面", "没有医院身份或病种能力原文")

    online = OnlineAuthoritativeProviderDiscovery(FixtureSearch(), SummaryOnlyFetcher())
    service = ProviderDiscoveryService([], [online], cache_writer=LocalCapabilityCache(tmp_path / "cache.json"))
    report = run_hospital_recommendation(
        HospitalRecommendationRequestV2(context=PatientRecommendationContext(
            case_id="ONLINE-2", disease_group="cataract", disease_label="白内障",
            province="测试省", city="测试市",
        )),
        discovery_service=service,
    )
    assert report.recommendations == []
    assert any("未同时满足" in item for item in report.warnings)


def test_so_search_extracts_direct_result_urls_without_using_summary(monkeypatch):
    html = '''
    <li class="res-list"><h3><a href="https://www.so.com/link?x=1"
      data-mdurl="https://hospital.example.cn/eye"><em>测试市</em>人民医院眼科</a></h3>
      <p class="res-desc">这段搜索摘要不能成为能力证据</p></li>
    '''

    class Response:
        text = html

        def raise_for_status(self):
            return None

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("medical_claims.online_provider_discovery.httpx.Client", lambda **kwargs: Client())
    monkeypatch.setattr("medical_claims.online_provider_discovery._public_url", lambda url: True)
    hits = SoHtmlSearch().search("测试查询")
    assert hits == [SearchHit("测试市人民医院眼科", "https://hospital.example.cn/eye")]
    assert "搜索摘要" not in hits[0].title


def test_connect_timeout_keeps_all_llm_candidates_as_pending_verification(tmp_path: Path):
    class FailedSearch:
        def search(self, query: str, limit: int = 8):
            raise httpx.ConnectTimeout("network unavailable")

    def proposed(score: float):
        return ProposedDimensionScore(score=score, reasoning="模型按当前病例给出的候选评分")

    hospitals = []
    clinicians = []
    for index in range(1, 6):
        hospital_name = f"测试市第{index}医院"
        hospitals.append(SuggestedHospitalCandidate(
            hospital_name=hospital_name,
            city="测试市",
            department="眼科",
            recommendation_reasons=[
                SuggestedEvidenceReason(reason="与当前白内障治疗目标匹配", evidence_type="disease_capability"),
                SuggestedEvidenceReason(reason="位于患者当前城市", evidence_type="city"),
            ],
            dimension_scores=ProposedHospitalScores(
                disease_specialty_match=proposed(85 - index),
                geographic_convenience=proposed(90),
                hospital_professional_capability=proposed(78 - index),
                clinician_resources=proposed(70 - index),
                treatment_technology_capability=proposed(76 - index),
            ),
            total_score=1,
        ))
        clinicians.append(SuggestedClinicianCandidate(
            doctor_name=f"测试医生{index}",
            hospital_name=hospital_name,
            department="眼科",
            specialty="白内障诊疗",
            recommendation_reasons=[
                SuggestedEvidenceReason(reason="专业方向与当前病种匹配", evidence_type="clinician_specialty"),
                SuggestedEvidenceReason(reason="治疗方向与当前目标匹配", evidence_type="clinician_technical_expertise"),
            ],
            dimension_scores=ProposedClinicianScores(
                disease_match=proposed(82), treatment_expertise=proposed(78),
                hospital_department_platform=proposed(72), clinical_qualification_capability=proposed(68),
            ),
            total_score=1,
        ))
    intent = RecommendationIntent(
        primary_disease="老年性白内障",
        treatment_goal="白内障手术评估",
        primary_specialty="眼科",
        required_specialty="眼科",
        required_capabilities=["白内障诊疗", "超声乳化与人工晶体植入"],
        case_complexity="low",
        geographic_strategy=GeographicRecommendationStrategy.LOCAL_FIRST,
        nearby_major_cities=["邻近甲市", "邻近乙市"],
        search_queries=["白内障 测试市 眼科 医院"],
        reasoning="当前病例的核心就医需求是白内障诊疗。",
        candidate_hospitals=hospitals,
        candidate_clinicians=clinicians,
        planner="llm_scoring_planner_v3",
    )

    online = OnlineAuthoritativeProviderDiscovery(FailedSearch(), FixtureFetcher())
    service = ProviderDiscoveryService([], [online], cache_writer=LocalCapabilityCache(tmp_path / "cache.json"))
    report = run_hospital_recommendation(
        HospitalRecommendationRequestV2(
            context=PatientRecommendationContext(
                case_id="ONLINE-3", disease_group="cataract", disease_label="白内障",
                province="测试省", city="测试市",
            ),
            recommendation_intent=intent,
            top_k=5,
        ),
        discovery_service=service,
    )
    assert len(report.recommendations) == 5
    assert len(report.candidate_profiles) == 5
    assert all(item.evidence_status == EvidenceVerificationStatus.PENDING_VERIFICATION for item in report.recommendations)
    assert all(item.recommendation_confidence <= 0.1 for item in report.recommendations)
    assert all("公开证据暂未完成核验" in item.reasons[0].explanation for item in report.recommendations)
    assert any("联网检索失败：ConnectTimeout" in item for item in report.warnings)
    assert len(report.candidate_clinician_profiles) == 5
    doctor_results = run_clinician_recommendation(ClinicianRecommendationRequestV2(
        disease_group="cataract",
        profiles=report.candidate_clinician_profiles,
        allowed_hospital_ids=[item.hospital_id for item in report.recommendations],
        recommendation_intent=intent,
        top_k=5,
    ))
    assert len(doctor_results) == 5
    assert all(item.evidence_status == EvidenceVerificationStatus.PENDING_VERIFICATION for item in doctor_results)


def test_hospital_name_prefers_official_title_and_rejects_marketing_sentence():
    page = PageDocument(
        "https://www.eyehx.com/",
        "杭州眼科医院-全飞秒激光-杭州近视手术-杭州华厦眼科医院【官网】",
        "在全国22省60多个城市开设100多家眼科医院。杭州华厦眼科医院设有白内障专科。",
    )
    query = ProviderDiscoveryQuery(
        disease_group="cataract", disease_label="白内障", city="上海市"
    )
    assert _hospital_name(page, query) == "杭州华厦眼科医院"


def test_medical_aggregator_is_not_treated_as_hospital_official_site():
    page = PageDocument(
        "https://aggregator.example/123",
        "上海白内障专科医院推荐_找医院",
        "第三方医疗信息平台 版权所有。页面介绍多家医院。",
    )
    assert _authority(page) is None


def test_known_medical_aggregator_cannot_impersonate_hospital_official_site():
    page = PageDocument(
        "https://wapjbk.39.net/yiyuanfengcai/example/",
        "重庆眼科医院官方网站",
        "重庆眼科医院 版权所有，开展白内障诊疗。",
    )
    assert _authority(page) is None


def test_regular_medical_platform_is_b_grade_for_objective_facts_only():
    page = PageDocument(
        "https://www.haodf.com/hospital/test-eye.html",
        "测试市人民医院眼科医生与科室信息",
        "测试市人民医院位于测试市。科室为眼科，医生专业方向包括白内障诊疗。",
    )
    assert _authority(page) is None
    source_type, _, grade, confidence = _source_quality(page)
    assert source_type == "正规医疗信息平台"
    assert grade == "B"
    assert 0.5 < confidence < 0.9


def test_b_grade_objective_page_can_admit_hospital_with_lower_confidence(tmp_path: Path):
    class BSearch:
        def search(self, query: str, limit: int = 8):
            return [SearchHit("测试市人民医院眼科", "https://www.haodf.com/hospital/test-eye.html")]

    class BFetcher:
        def fetch(self, url: str):
            return PageDocument(
                url,
                "测试市人民医院眼科医生与科室信息",
                "测试市人民医院位于测试市。该院设有眼科，开展白内障诊疗项目。",
            )

    report = run_hospital_recommendation(
        HospitalRecommendationRequestV2(context=PatientRecommendationContext(
            case_id="B-GRADE", disease_group="cataract", disease_label="白内障",
            province="测试省", city="测试市",
        )),
        discovery_service=ProviderDiscoveryService(
            [], [OnlineAuthoritativeProviderDiscovery(BSearch(), BFetcher())],
            cache_writer=LocalCapabilityCache(tmp_path / "cache.json"),
        ),
    )
    assert len(report.recommendations) == 1
    profile = report.candidate_profiles[0]
    assert profile.sources[0].evidence_grade == "B"
    assert profile.sources[0].evidence_confidence == 0.7
    assert all(record.verified for record in profile.evidence_records)


def test_aggregator_title_containing_university_is_not_treated_as_university_official_site():
    page = PageDocument(
        "https://yyk.example.com/hospital/70",
        "重庆医科大学附属第一医院医生排班",
        "第三方医疗信息平台提供医院医生排班。",
    )
    assert _authority(page) is None


def test_descriptive_insurance_title_is_not_accepted_as_hospital_name():
    page = PageDocument(
        "https://hospital.example.cn/cataract",
        "重庆市医保定点眼科医院-白内障治疗",
        "网站介绍白内障治疗服务 版权所有。",
    )
    query = ProviderDiscoveryQuery(disease_group="cataract", disease_label="白内障", city="重庆市")
    assert _hospital_name(page, query) is None


def test_llm_candidate_name_must_be_confirmed_in_page_and_prevents_sentence_fragment_name():
    intent = RecommendationIntent(
        primary_disease="老年性白内障", treatment_goal="白内障诊疗", primary_specialty="眼科",
        required_capabilities=["白内障超声乳化术"], case_complexity="low",
        geographic_strategy=GeographicRecommendationStrategy.LOCAL_FIRST,
        nearby_major_cities=["成都市", "西安市"],
        search_queries=["重庆市 白内障 眼科 医院"], reasoning="病例以白内障为核心。",
        candidate_hospital_clues=["成都市：成都市第三人民医院（眼科）"],
    )
    query = ProviderDiscoveryQuery(
        disease_group="cataract", disease_label="白内障", city="重庆市",
        recommendation_intent=intent,
    )
    page = PageDocument(
        "https://medical.example.edu.cn/chengdu-third",
        "附属医院介绍",
        "成都市第三人民医院始建于1941年，是四川省首批现代医院管理制度试点单位。",
    )
    assert _hospital_name(page, query) == "成都市第三人民医院"


def test_non_hospital_page_does_not_bind_another_hospitals_cataract_text():
    intent = RecommendationIntent(
        primary_disease="老年性白内障", treatment_goal="白内障诊疗", primary_specialty="眼科",
        required_capabilities=["白内障超声乳化术"], case_complexity="low",
        geographic_strategy=GeographicRecommendationStrategy.LOCAL_FIRST,
        nearby_major_cities=["成都市", "西安市"],
        search_queries=["重庆市 白内障 眼科 医院"], reasoning="病例以白内障为核心。",
        candidate_hospital_clues=["成都市：四川省革命伤残军人医院（眼科）"],
    )
    query = ProviderDiscoveryQuery(
        disease_group="cataract", disease_label="白内障", city="重庆市",
        capability_need={
            "disease_group": "cataract", "disease_label": "白内障",
            "related_specialty_terms": ["眼科"], "capability_terms": ["白内障超声乳化术"],
            "resolution_source": "test",
        },
        recommendation_intent=intent,
    )
    page = PageDocument(
        "https://government.example.gov.cn/hospitals", "医院名单",
        "四川省白内障专科医院，设有眼科并开展白内障超声乳化术。"
        "四川省革命伤残军人医院，二级乙等。",
    )
    text, _ = _entity_bound_capability_context(page, query, "四川省革命伤残军人医院")
    assert text is None


def test_university_page_prefers_complete_affiliated_hospital_name():
    page = PageDocument(
        "https://medical.example.edu.cn/hospital/first",
        "附属第一人民医院",
        "上海交通大学医学院附属第一人民医院的白内障、人工晶体植入术相关病种排名靠前。",
    )
    query = ProviderDiscoveryQuery(
        disease_group="cataract", disease_label="白内障", city="上海市",
        capability_need={
            "disease_group": "cataract",
            "disease_label": "白内障",
            "related_specialty_terms": ["眼科"],
            "capability_terms": ["白内障诊疗能力"],
            "resolution_source": "test",
        },
    )
    assert _hospital_name(page, query) == "上海交通大学医学院附属第一人民医院"


def test_clinician_mentions_rejects_staff_counts_and_strips_role_suffix():
    text = (
        "眼科现有在职医师11人，其中主任医师2人。"
        "科主任唐建明主任擅长白内障手术，眼科副主任忽俊主任擅长白内障诊疗。"
    )
    assert _clinician_mentions(text) == [
        ("唐建明", "科室负责人"),
        ("忽俊", "科室负责人"),
    ]


def test_old_online_cache_is_ignored_and_reverified(tmp_path: Path):
    cache_path = tmp_path / "cache.json"
    online = OnlineAuthoritativeProviderDiscovery(FixtureSearch(), FixtureFetcher())
    current_cache = LocalCapabilityCache(cache_path)
    query = ProviderDiscoveryQuery(disease_group="cataract", disease_label="白内障", city="测试市")
    report = run_hospital_recommendation(
        HospitalRecommendationRequestV2(context=PatientRecommendationContext(
            case_id="CACHE-1", disease_group="cataract", disease_label="白内障", city="测试市"
        )),
        discovery_service=ProviderDiscoveryService([], [online], cache_writer=current_cache),
    )
    stale_profile = report.candidate_profiles[0].model_copy(update={
        "profile_version": "hospital-capability-profile-v2-online"
    })
    cache_path.write_text(
        '{"hospital_profiles": [' + stale_profile.model_dump_json() + '], "clinician_profiles": []}',
        encoding="utf-8",
    )
    discovered = current_cache.discover(query)
    assert discovered.hospital_profiles == []
    assert any("旧版在线发现缓存" in warning for warning in discovered.warnings)


def test_intent_search_plan_uses_local_city_specialty_and_required_technology(tmp_path: Path):
    class RecordingSearch(FixtureSearch):
        def __init__(self):
            self.queries = []

        def search(self, query: str, limit: int = 8):
            self.queries.append(query)
            return super().search(query, limit)

    search = RecordingSearch()
    online = OnlineAuthoritativeProviderDiscovery(search, FixtureFetcher())
    service = ProviderDiscoveryService([], [online], cache_writer=LocalCapabilityCache(tmp_path / "cache.json"))
    intent = RecommendationIntent(
        primary_disease="老年性白内障", treatment_goal="改善视物模糊",
        primary_specialty="眼科", required_capabilities=["白内障超声乳化手术", "人工晶体植入术"],
        comorbidities=["高血压"], case_complexity="low",
        geographic_strategy=GeographicRecommendationStrategy.LOCAL_FIRST,
        nearby_major_cities=["成都市", "西安市"],
        search_queries=["重庆市 老年性白内障 眼科 超声乳化 人工晶体植入 医院官网"],
        reasoning="病例主诊断和本次手术均指向白内障。",
    )
    run_hospital_recommendation(HospitalRecommendationRequestV2(
        context=PatientRecommendationContext(
            case_id="CQ-1", disease_group="cataract", disease_label="白内障",
            province="重庆市", city="重庆市",
        ),
        recommendation_intent=intent,
    ), discovery_service=service)
    assert search.queries[0] == intent.search_queries[0]
    assert all(
        any(city in query for city in ("重庆", "成都", "西安"))
        for query in search.queries if "医生" not in query
    )
    assert any("成都" in query and "公立 三级甲等" in query for query in search.queries)
    assert any("西安" in query and "公立 三级甲等" in query for query in search.queries)
    assert any("白内障超声乳化手术" in query and "人工晶体植入术" in query for query in search.queries)
    assert not any("秦皇岛" in query for query in search.queries)
