"""Phase 1 病种候选医院目录。

运行时读取独立、已去除病例标识的公开能力目录。旧公开审计 JSON 仅作为构建
该目录的数据来源，不参与新推荐引擎的运行时打分。
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from .quality_phase1_models import (
    AssessmentStatus,
    CapabilityEvidenceRecord,
    DimensionAssessment,
    DiseaseCatalogEntry,
    DiseaseProviderCatalog,
    DiseaseSpecialtyCapability,
    HistoricalEvidenceLevel,
    HistoricalEvidenceSupport,
    HospitalCapabilityProfile,
    InstitutionFoundation,
    MedicalResourceCapability,
    PatientServiceFeedback,
    PublicSource,
    RegionalServiceCapability,
)


PHASE1_DISEASES = {
    "pneumonia": "肺炎",
    "influenza": "流感",
    "hypertension": "高血压",
}


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _field(record: dict[str, Any], name: str) -> tuple[Any, list[str]]:
    item = record.get("fields", {}).get(name, {})
    return item.get("value"), list(item.get("source_ids") or [])


def _split_region(region: str | None) -> tuple[str | None, str | None]:
    if not region:
        return None, None
    municipality = next((name for name in ("北京市", "上海市", "天津市", "重庆市") if region.startswith(name)), None)
    if municipality:
        return municipality, municipality
    province_match = re.search(r"^(.+?(?:省|自治区|特别行政区))", region)
    city_match = re.search(r"(?:省|自治区)(.+?市)", region)
    return (
        province_match.group(1) if province_match else None,
        city_match.group(1) if city_match else None,
    )


def _grade_score(grade: str | None) -> float | None:
    if not grade:
        return None
    if "三级甲等" in grade:
        return 90
    if "三级" in grade:
        return 80
    if "二级甲等" in grade or "二甲" in grade:
        return 70
    if "二级" in grade:
        return 60
    return 50


def _level(score: float) -> str:
    if score >= 85:
        return "high"
    if score >= 65:
        return "medium"
    return "basic"


def _unavailable(reason: str, fields: list[str] | None = None) -> DimensionAssessment:
    return DimensionAssessment(
        status=AssessmentStatus.INSUFFICIENT_EVIDENCE,
        reasoning=reason,
        evidence_fields=fields or [],
        limitations=["缺失不等于低质量，不参与推荐分的分子或分母"],
    )


def empty_historical_support() -> HistoricalEvidenceSupport:
    return HistoricalEvidenceSupport(
        history_summary="暂无可关联历史案例",
        assessment=_unavailable("当前目录只保存公开机构能力，尚未附加已确认历史病例"),
    )


def _source_registry(record: dict[str, Any], used_source_ids: list[str]) -> list[PublicSource]:
    wanted = set(used_source_ids)
    sources = []
    for item in record.get("sources", []):
        if item.get("source_id") not in wanted or not item.get("url"):
            continue
        sources.append(
            PublicSource(
                source_id=item["source_id"],
                title=item.get("title") or item["source_id"],
                url=item["url"],
                authority_type=item.get("authority_type") or "权威公开来源",
                query_date=item.get("queried_at") or record.get("queried_at") or "unknown",
            )
        )
    return sources


def _build_profile(record: dict[str, Any], disease: dict[str, Any]) -> HospitalCapabilityProfile:
    name, name_sources = _field(record, "standard_name")
    region, region_sources = _field(record, "region")
    hospital_type, type_sources = _field(record, "hospital_type")
    hospital_level, grade_sources = _field(record, "hospital_grade")
    province, city = _split_region(region)
    disease_sources = list(disease.get("source_ids") or [])
    departments = _unique(list(disease.get("related_departments") or []))
    national = _unique(list(disease.get("national_key_specialties") or []))
    provincial = _unique(list(disease.get("provincial_key_specialties") or []))
    other = _unique(list(disease.get("other_key_specialties") or []))
    specialties = _unique([*national, *provincial, *other])
    capability = disease.get("capability_note")
    used_sources = _unique([*name_sources, *region_sources, *type_sources, *grade_sources, *disease_sources])
    sources = _source_registry(record, used_sources)
    known_source_ids = {source.source_id for source in sources}

    def valid(ids: list[str]) -> list[str]:
        return [item for item in _unique(ids) if item in known_source_ids]

    institution_source_ids = valid([*name_sources, *region_sources, *type_sources, *grade_sources])
    grade_score = _grade_score(hospital_level)
    foundation_score = grade_score if grade_score is not None else 55.0
    foundation_fields = [name for name, value in (
        ("hospital_name", name), ("province", province), ("city", city),
        ("hospital_level", hospital_level), ("hospital_type", hospital_type),
    ) if value]
    foundation_confidence = min(1.0, len(foundation_fields) / 5)
    teaching = bool(name and ("大学附属" in name or "医科大学" in name)) or None
    institution = InstitutionFoundation(
        hospital_id=record["hospital_id"],
        hospital_name=name or record.get("ocr_candidate_name") or record["hospital_id"],
        province=province,
        city=city,
        hospital_level=hospital_level,
        hospital_type=hospital_type,
        institution_role="大学附属医院" if teaching else hospital_type,
        teaching_hospital=teaching,
        assessment=DimensionAssessment(
            status=AssessmentStatus.EVALUATED,
            score=foundation_score,
            level=_level(foundation_score),
            confidence=foundation_confidence,
            reasoning="按有来源的机构等级、类型和区域字段描述基础能力；医院等级不能单独决定推荐资格",
            evidence_fields=foundation_fields,
            source_ids=institution_source_ids,
            limitations=["该分数描述机构基础条件，不等同于疗效或医院综合排名"],
        ),
    )

    specialty_score = min(100.0, (60 if departments else 0) + (20 if specialties else 0) + (20 if capability else 0))
    specialty_fields = [name for name, value in (
        ("related_departments", departments), ("key_specialties", specialties),
        ("specialty_capability_description", capability),
    ) if value]
    specialty_source_ids = valid(disease_sources)
    specialty = DiseaseSpecialtyCapability(
        disease_group=disease["disease_group"],
        related_departments=departments,
        key_specialties=specialties,
        key_specialty_level=("国家级" if national else "省级" if provincial else "其他公开重点专科" if other else None),
        specialty_capability_description=capability,
        specialty_match_level=_level(specialty_score),
        assessment=DimensionAssessment(
            status=AssessmentStatus.EVALUATED,
            score=specialty_score,
            level=_level(specialty_score),
            confidence=min(1.0, len(specialty_fields) / 3),
            reasoning="只按当前病种的相关科室、重点专科和可公开验证能力计算，不复用其他病种专科",
            evidence_fields=specialty_fields,
            source_ids=specialty_source_ids,
            limitations=["公开专科能力说明不等同于个体治疗效果"],
        ),
    )

    resource_points = (
        (0.4 * grade_score if grade_score is not None else 0)
        + (15 if hospital_type else 0)
        + (25 if specialties else 0)
        + (20 if capability else 0)
        + (4 if teaching else 0)
    )
    resource_fields = [name for name, value in (
        ("hospital_type", hospital_type), ("hospital_level", hospital_level),
        ("key_specialties", specialties), ("specialty_capability_description", capability),
    ) if value]
    resource_source_ids = valid([*type_sources, *grade_sources, *disease_sources])
    resources = MedicalResourceCapability(
        resource_capability_level=_level(resource_points),
        assessment=DimensionAssessment(
            status=AssessmentStatus.EVALUATED,
            score=float(resource_points),
            level=_level(resource_points),
            confidence=min(1.0, len(resource_fields) / 4),
            reasoning="按有权威来源的机构等级、类型、重点专科、公开能力和教学平台信息计算资源能力",
            evidence_fields=resource_fields,
            source_ids=resource_source_ids,
            limitations=["当前没有统一的医生数量、设备数量和 ICU 容量数据；缺失字段保持未知"],
        ),
    )

    regional_keywords = [word for word in ("区域医疗中心", "转诊", "急救", "危重症") if capability and word in capability]
    if regional_keywords:
        regional = RegionalServiceCapability(
            regional_referral_role="；".join(regional_keywords),
            referral_suitability="公开资料支持区域或危重症服务能力",
            regional_role_level="documented",
            assessment=DimensionAssessment(
                status=AssessmentStatus.EVALUATED,
                score=75,
                level="medium",
                confidence=0.7,
                reasoning="仅使用公开能力说明中明确出现的区域、转诊、急救或危重症信息",
                evidence_fields=["specialty_capability_description"],
                source_ids=specialty_source_ids,
                limitations=["未公开的信息保持未知，不推断实际转诊范围"],
            ),
        )
    else:
        regional = RegionalServiceCapability(
            assessment=_unavailable("公开资料未提供可核验的区域转诊或急救角色", ["regional_service"])
        )

    feedback = PatientServiceFeedback(
        assessment=_unavailable("当前目录没有独立、可关联的真实患者反馈", ["patient_feedback"])
    )
    return HospitalCapabilityProfile(
        hospital_id=record["hospital_id"],
        disease_group=disease["disease_group"],
        institution_foundation=institution,
        disease_specialty=specialty,
        medical_resources=resources,
        regional_service=regional,
        patient_service_feedback=feedback,
        historical_evidence=empty_historical_support(),
        sources=sources,
    )


def build_catalog_from_public_audit(payload: dict[str, Any]) -> DiseaseProviderCatalog:
    """把旧审计中的公开事实转换为新目录；不读取旧候选池或推荐结果。"""
    entries: list[DiseaseCatalogEntry] = []
    for disease_group, disease_label in PHASE1_DISEASES.items():
        profiles = []
        for record in payload.get("hospitals", []):
            if record.get("name_confirmation_status") != "confirmed":
                continue
            disease = next(
                (item for item in record.get("disease_public_evidence", []) if item.get("disease_group") == disease_group),
                None,
            )
            if not disease:
                continue
            profile = _build_profile(record, disease)
            if profile.disease_specialty.assessment.source_ids and len(profile.sources) >= 1:
                profiles.append(profile)
        entries.append(
            DiseaseCatalogEntry(
                disease_group=disease_group,
                disease_label=disease_label,
                hospitals=sorted(profiles, key=lambda item: item.institution_foundation.hospital_name),
            )
        )
    return DiseaseProviderCatalog(
        diseases=entries,
        generated_from="权威公开医院资料审计的已确认机构字段；不含病例标识或旧推荐结果",
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def load_catalog(path: Path) -> DiseaseProviderCatalog:
    catalog = DiseaseProviderCatalog.model_validate_json(path.read_text(encoding="utf-8"))
    entries = []
    for entry in catalog.diseases:
        profiles = [_with_evidence_records(profile) for profile in entry.hospitals]
        entries.append(entry.model_copy(update={"hospitals": profiles}, deep=True))
    return catalog.model_copy(update={"diseases": entries}, deep=True)


def _with_evidence_records(profile: HospitalCapabilityProfile) -> HospitalCapabilityProfile:
    """把已审计目录字段提升为可展示的结构化证据，不重新推断网页事实。"""
    if profile.evidence_records:
        return profile
    sources = {source.source_id: source for source in profile.sources}
    dimensions = [
        ("institution_foundation", profile.institution_foundation.assessment,
         f"{profile.institution_foundation.hospital_name}，{profile.institution_foundation.city or '地区待确认'}，{profile.institution_foundation.hospital_level or '等级未公开'}",
         "公开资料支持该机构身份及基础信息。"),
        ("disease_specialty", profile.disease_specialty.assessment,
         "；".join([*profile.disease_specialty.related_departments, *profile.disease_specialty.key_specialties,
                   profile.disease_specialty.specialty_capability_description or ""]),
         f"公开资料支持该院具备{profile.disease_specialty.disease_group}相关科室或专科能力。"),
        ("medical_resources", profile.medical_resources.assessment,
         profile.medical_resources.assessment.reasoning,
         "公开资料支持该院相应医疗资源能力字段。"),
        ("regional_service", profile.regional_service.assessment,
         profile.regional_service.assessment.reasoning,
         "公开资料支持该院区域服务能力字段。"),
    ]
    records = []
    for field, assessment, evidence_text, claim in dimensions:
        if assessment.status != AssessmentStatus.EVALUATED:
            continue
        text = (evidence_text or assessment.reasoning).strip()[:500]
        for source_id in assessment.source_ids:
            source = sources.get(source_id)
            if not source:
                continue
            digest = sha256(f"{profile.hospital_id}|{field}|{source_id}".encode("utf-8")).hexdigest()[:16]
            records.append(CapabilityEvidenceRecord(
                evidence_id=f"EVD-{digest}", source_id=source_id,
                source_name=source.title, source_type=source.authority_type,
                source_url=source.url, query_date=source.query_date,
                evidence_text=text, evidence_field=field, supported_claim=claim,
            ))
    return profile.model_copy(update={"evidence_records": records}, deep=True)


def write_catalog(catalog: DiseaseProviderCatalog, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(catalog.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
