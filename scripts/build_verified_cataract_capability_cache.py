"""Build the source-backed cataract capability cache used by V2 discovery.

This file records verified public facts. It does not contain a final rank or a
hard-coded recommendation result; the V2 engine still computes qualification,
geography and ordering for each case.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from medical_claims.quality_phase1_models import (
    AssessmentStatus,
    CapabilityEvidenceRecord,
    DimensionAssessment,
    DiseaseSpecialtyCapability,
    HistoricalEvidenceSupport,
    HospitalCapabilityProfile,
    InstitutionFoundation,
    MedicalResourceCapability,
    PatientServiceFeedback,
    PublicSource,
    RegionalServiceCapability,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "knowledge" / "quality" / "verified_provider_capability_cache.json"
QUERY_DATE = "2026-10-08"


def assessment(score: float, reasoning: str, fields: list[str], source_ids: list[str]) -> DimensionAssessment:
    return DimensionAssessment(
        status=AssessmentStatus.EVALUATED,
        score=score,
        level="high" if score >= 85 else "medium",
        confidence=0.95,
        reasoning=reasoning,
        evidence_fields=fields,
        source_ids=source_ids,
        limitations=["公开资料证明机构能力存在，不代表个体疗效或医院综合排名"],
    )


def unavailable(reason: str, field: str) -> DimensionAssessment:
    return DimensionAssessment(
        status=AssessmentStatus.INSUFFICIENT_EVIDENCE,
        reasoning=reason,
        evidence_fields=[field],
        limitations=["缺失不等于低质量，不参与推荐分的分子或分母"],
    )


def evidence(
    hospital_id: str,
    suffix: str,
    source: PublicSource,
    field: str,
    text: str,
    claim: str,
) -> CapabilityEvidenceRecord:
    return CapabilityEvidenceRecord(
        evidence_id=f"{hospital_id}-E-{suffix}",
        source_id=source.source_id,
        source_name=source.title,
        source_type=source.authority_type,
        source_url=source.url,
        query_date=source.query_date,
        evidence_text=text,
        evidence_field=field,
        supported_claim=claim,
    )


def profile(
    *,
    hospital_id: str,
    name: str,
    province: str,
    city: str,
    base_source: PublicSource,
    specialty_source: PublicSource,
    specialty_text: str,
    capability: str,
    regional_role: str | None = None,
) -> HospitalCapabilityProfile:
    base_ids = [base_source.source_id]
    specialty_ids = [specialty_source.source_id]
    institution = InstitutionFoundation(
        hospital_id=hospital_id,
        hospital_name=name,
        province=province,
        city=city,
        hospital_level="三级甲等",
        hospital_type="公立综合医院",
        institution_role="大学附属三级甲等综合医院",
        regional_medical_center=bool(regional_role) or None,
        teaching_hospital=True,
        assessment=assessment(
            90,
            "医院官方或政府公开资料确认机构身份、公立属性与三级甲等资质",
            ["hospital_name", "province", "city", "hospital_level", "hospital_type"],
            base_ids,
        ),
    )
    specialty = DiseaseSpecialtyCapability(
        disease_group="cataract",
        related_departments=["眼科"],
        key_specialties=["白内障诊疗与手术能力"],
        disease_specific_clinic="白内障专病或手术单元",
        specialty_capability_description=capability,
        specialty_match_level="high",
        assessment=assessment(
            95,
            "医院官网明确公开眼科白内障诊疗、超声乳化或人工晶体植入能力",
            ["related_departments", "key_specialties", "specialty_capability_description"],
            specialty_ids,
        ),
    )
    resources = MedicalResourceCapability(
        surgery_or_intervention_capability=capability,
        key_equipment_or_technology=["白内障超声乳化", "人工晶体植入"],
        resource_capability_level="high",
        assessment=assessment(
            90,
            "医院官网公开的白内障手术技术可作为专业资源能力证据",
            ["surgery_or_intervention_capability", "key_equipment_or_technology"],
            specialty_ids,
        ),
    )
    if regional_role:
        regional = RegionalServiceCapability(
            regional_referral_role=regional_role,
            complex_case_capability="公开资料支持疑难或复杂眼病诊疗能力",
            referral_suitability="复杂病例可作为区域转诊备选",
            regional_role_level="documented",
            assessment=assessment(
                85,
                "医院官方资料明确说明区域服务或疑难眼病诊疗角色",
                ["regional_referral_role", "complex_case_capability"],
                [base_source.source_id, specialty_source.source_id],
            ),
        )
    else:
        regional = RegionalServiceCapability(
            assessment=unavailable("公开资料不足以单独确认区域转诊角色", "regional_service")
        )
    records = [
        evidence(
            hospital_id, "BASE", base_source, "institution_foundation",
            f"{name}公开资料确认其为{city}公立三级甲等综合医院。",
            "支持医院身份、地区、公立属性及三级甲等资质。",
        ),
        evidence(
            hospital_id, "SPEC", specialty_source, "disease_specialty",
            specialty_text,
            "支持该院具有白内障相关眼科与手术能力。",
        ),
        evidence(
            hospital_id, "RES", specialty_source, "medical_resources",
            specialty_text,
            "支持该院具备白内障超声乳化和人工晶体植入相关技术资源。",
        ),
    ]
    if regional_role:
        records.append(evidence(
            hospital_id, "REG", base_source, "regional_service",
            regional_role,
            "支持该院承担区域医疗或疑难病例服务角色。",
        ))
    return HospitalCapabilityProfile(
        hospital_id=hospital_id,
        disease_group="cataract",
        institution_foundation=institution,
        disease_specialty=specialty,
        medical_resources=resources,
        regional_service=regional,
        patient_service_feedback=PatientServiceFeedback(
            assessment=unavailable("没有可关联且口径一致的真实患者反馈", "patient_feedback")
        ),
        historical_evidence=HistoricalEvidenceSupport(
            history_summary="暂无可关联同病种历史病例",
            assessment=unavailable("没有可追溯的同病种历史病例", "historical_case_count"),
        ),
        sources=[base_source, specialty_source],
        evidence_records=records,
        profile_version="hospital-capability-profile-v1",
    )


def source(source_id: str, title: str, url: str, authority_type: str = "医院官网") -> PublicSource:
    return PublicSource(
        source_id=source_id,
        title=title,
        url=url,
        authority_type=authority_type,
        query_date=QUERY_DATE,
    )


def build_profiles() -> list[HospitalCapabilityProfile]:
    return [
        profile(
            hospital_id="HOSP-CQ-CQMU1",
            name="重庆医科大学附属第一医院",
            province="重庆市",
            city="重庆市",
            base_source=source("CQ-CQMU1-BASE", "重庆医科大学附属第一医院简介", "https://www.fahcqmu.cn/gw_yygk_yyjj"),
            specialty_source=source("CQ-CQMU1-EYE", "重庆医科大学附属第一医院眼科简介", "https://www.fahcqmu.cn/gw_ksjs_yk"),
            specialty_text="医院官网介绍眼科在白内障领域达到国内先进水平，并承担重庆市眼科医疗质量控制中心工作。",
            capability="白内障诊疗、白内障手术质量控制及复杂眼病诊疗",
            regional_role="重庆市眼科医疗质量控制中心依托单位",
        ),
        profile(
            hospital_id="HOSP-CQ-CQMU2",
            name="重庆医科大学附属第二医院",
            province="重庆市",
            city="重庆市",
            base_source=source("CQ-CQMU2-BASE", "重庆医科大学附属第二医院简介", "https://www.cqsahcqmu.cn/index.php?c=category&id=11"),
            specialty_source=source("CQ-CQMU2-EYE", "重庆医科大学附属第二医院白内障手术技术介绍", "https://www.cqsahcqmu.cn/mobile/index.php?c=show&id=3619"),
            specialty_text="医院官网公开白内障超声乳化联合功能型人工晶体植入技术，并说明相关技术处于重庆领先水平。",
            capability="白内障超声乳化、功能型人工晶体植入及复杂白内障手术",
        ),
        profile(
            hospital_id="HOSP-CQ-THREE-GORGES",
            name="重庆大学附属三峡医院",
            province="重庆市",
            city="重庆市",
            base_source=source("CQ-THREE-GORGES-BASE", "重庆大学附属三峡医院简介", "https://sxyy.cqu.edu.cn/yygaikuang/yyjj.htm"),
            specialty_source=source("CQ-THREE-GORGES-EYE", "重庆大学附属三峡医院眼科简介", "https://sxyy.cqu.edu.cn/ksdh/wkpq/yk1/ksjj.htm"),
            specialty_text="医院官网介绍眼科常规开展白内障超声乳化等技术，并设有白内障相关区域诊疗能力。",
            capability="白内障超声乳化、人工晶体植入与区域眼病诊疗",
            regional_role="省级区域医疗中心，承担渝东北及周边地区医疗服务",
        ),
        profile(
            hospital_id="HOSP-SC-WCH",
            name="四川大学华西医院",
            province="四川省",
            city="成都市",
            base_source=source("SC-WCH-BASE", "四川大学华西医院三级医院等级评审信息", "https://www.wchscu.cn/comprehensive/71522.html"),
            specialty_source=source("SC-WCH-EYE", "四川大学华西医院白内障专业组", "https://www.wchscu.cn/public/department/yzyz/detail/32256.html"),
            specialty_text="医院官网白内障专业组公开开展白内障超声乳化、人工晶体植入及复杂白内障诊疗。",
            capability="白内障超声乳化、人工晶体植入与复杂白内障诊疗",
            regional_role="西南地区疑难危重眼病诊疗平台",
        ),
        profile(
            hospital_id="HOSP-SX-XJTU1",
            name="西安交通大学第一附属医院",
            province="陕西省",
            city="西安市",
            base_source=source("SX-XJTU1-BASE", "陕西省三级公立医院名录", "https://sxwjw.shaanxi.gov.cn/sy/tzgg/201906/P020201104551288445130.pdf", "省级卫健委"),
            specialty_source=source("SX-XJTU1-EYE", "西安交通大学第一附属医院眼科简介", "https://www.dyyy.xjtu.edu.cn/gzbsy/ksdh/wkxt/y_k.htm"),
            specialty_text="医院官网介绍眼科设有白内障日间手术中心，开展微切口超声乳化和人工晶体植入及复杂白内障手术。",
            capability="微切口白内障超声乳化、人工晶体植入与复杂白内障手术",
            regional_role="西北地区大型三级甲等综合医院眼科平台",
        ),
    ]


def main() -> int:
    profiles = build_profiles()
    payload = {
        "cache_version": "verified-provider-capability-cache-v1",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "hospital_profiles": [item.model_dump(mode="json") for item in profiles],
        "clinician_profiles": [],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "hospital_profiles": len(profiles)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
