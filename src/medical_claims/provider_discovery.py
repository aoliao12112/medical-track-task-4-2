"""通用病种医疗机构发现层。

固定目录只是一层已核验缓存。动态 provider 必须返回完整、可追溯且通过统一门槛的
能力画像；本模块本身不使用搜索摘要或大模型常识补全医院能力。
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Protocol

from .disease_provider_catalog import load_catalog
from .quality_phase1_models import (
    AssessmentStatus,
    CapabilityCacheWriteRequest,
    ClinicianCapabilityProfile,
    DiseaseCapabilityNeed,
    HospitalCapabilityProfile,
    ProviderDiscoveryQuery,
    ProviderDiscoveryResult,
)


DEFAULT_CAPABILITY_CACHE_PATH = (
    Path(__file__).resolve().parents[2] / "outputs" / "provider_capability_cache.json"
)


class DynamicProviderDiscovery(Protocol):
    """外部权威数据适配器接口；实现方负责来源抓取与字段转换。"""

    provider_name: str

    def discover(self, query: ProviderDiscoveryQuery) -> ProviderDiscoveryResult:
        ...


def resolve_disease_capability_need(query: ProviderDiscoveryQuery) -> DiseaseCapabilityNeed:
    """把明确病种转换为发现条件；未知病种只使用病例事实，不臆测所属专科。"""
    if query.recommendation_intent is not None:
        intent = query.recommendation_intent
        return DiseaseCapabilityNeed(
            disease_group=query.disease_group,
            disease_label=intent.primary_disease,
            related_specialty_terms=list(dict.fromkeys([
                intent.primary_specialty, *intent.secondary_specialties,
            ])),
            capability_terms=list(dict.fromkeys(intent.required_capabilities)),
            resolution_source="llm_recommendation_intent_validated_against_case",
        )
    benchmark_terms = {
        "pneumonia": (["呼吸内科", "感染科"], ["肺炎诊疗", "呼吸系统感染诊疗"]),
        "influenza": (["感染科", "呼吸内科"], ["流感诊疗", "感染性疾病诊疗"]),
        "hypertension": (["心血管内科", "高血压专病门诊"], ["高血压诊疗"]),
        "cataract": (["眼科"], ["白内障诊疗", "白内障手术"]),
        "diabetes": (["内分泌科"], ["糖尿病诊疗"]),
        "coronary_heart_disease": (["心血管内科"], ["冠心病诊疗"]),
        "fracture": (["骨科"], ["骨折诊疗"]),
        "cholecystitis": (["肝胆外科", "普通外科"], ["胆囊炎诊疗"]),
        "appendicitis": (["普通外科"], ["阑尾炎诊疗"]),
        "lung_nodule": (["呼吸内科", "胸外科"], ["肺结节诊疗"]),
        "urinary_calculus": (["泌尿外科"], ["泌尿系结石诊疗"]),
    }
    if query.disease_group not in benchmark_terms:
        keyword_specialties = (
            (("眼", "白内障", "青光眼"), ["眼科"]),
            (("骨折", "关节", "脊柱"), ["骨科"]),
            (("糖尿病", "甲状腺", "内分泌"), ["内分泌科"]),
            (("冠心病", "心肌", "心律", "心脏"), ["心血管内科"]),
            (("肾", "尿路", "泌尿", "结石"), ["泌尿外科"]),
        )
        inferred = next((items for words, items in keyword_specialties if any(word in query.disease_label for word in words)), [])
        specialty_terms, capability_terms = inferred, [f"{query.disease_label}诊疗能力"]
    else:
        specialty_terms, capability_terms = benchmark_terms[query.disease_group]
    return DiseaseCapabilityNeed(
        disease_group=query.disease_group,
        disease_label=query.disease_label,
        related_specialty_terms=specialty_terms,
        capability_terms=capability_terms,
        resolution_source=(
            "verified_benchmark_mapping"
            if query.disease_group in {"pneumonia", "influenza", "hypertension"}
            else "deterministic_specialty_mapping"
            if specialty_terms else "current_case_disease_fact"
        ),
    )


def _valid_public_url(url: str) -> bool:
    return url.startswith("https://") or url.startswith("http://")


def _authoritative_source_ids(sources: list) -> set[str]:
    """返回可核实的 A/B 级来源；C 级搜索线索不能单独支撑推荐。"""
    return {
        source.source_id
        for source in sources
        if _valid_public_url(source.url)
        and source.verified
        and source.evidence_grade in {"A", "B"}
    }


def hospital_profile_meets_evidence_gate(
    profile: HospitalCapabilityProfile,
    query: ProviderDiscoveryQuery,
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    foundation = profile.institution_foundation
    specialty = profile.disease_specialty
    registered = _authoritative_source_ids(profile.sources)
    if profile.disease_group != query.disease_group:
        failures.append("病种标识不匹配")
    if (
        not foundation.hospital_name
        or foundation.assessment.status != AssessmentStatus.EVALUATED
        or not foundation.assessment.source_ids
    ):
        failures.append("医院身份缺少可追溯来源")
    if not foundation.city:
        failures.append("医院城市或地区尚未核实")
    if specialty.assessment.status != AssessmentStatus.EVALUATED:
        failures.append("病种专科能力尚未完成核验")
    if not specialty.related_departments and not specialty.key_specialties and not specialty.specialty_capability_description:
        failures.append("缺少病种相关科室或专科能力")
    if not specialty.assessment.source_ids:
        failures.append("病种核心能力缺少来源")
    used = set(foundation.assessment.source_ids) | set(specialty.assessment.source_ids)
    if not used or not used.issubset(registered):
        failures.append("核心能力缺少可核实的A/B级来源")
    if profile.profile_version.endswith("-online"):
        fields = {record.evidence_field for record in profile.evidence_records}
        if not {"institution_foundation", "disease_specialty"}.issubset(fields):
            failures.append("动态医院画像缺少结构化身份或病种能力证据")
    return not failures, failures


def clinician_profile_meets_evidence_gate(
    profile: ClinicianCapabilityProfile,
    query: ProviderDiscoveryQuery,
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    registered = _authoritative_source_ids(profile.sources)
    if profile.disease_group != query.disease_group:
        failures.append("病种标识不匹配")
    if (
        not profile.basic_qualification.clinician_name
        or profile.basic_qualification.assessment.status != AssessmentStatus.EVALUATED
        or not profile.basic_qualification.assessment.source_ids
    ):
        failures.append("医生身份缺少可追溯来源")
    if (
        not profile.basic_qualification.hospital_name
        or not profile.basic_qualification.department
        or profile.basic_qualification.department == "相关专科"
    ):
        failures.append("医生所属医院或科室尚未核实")
    expertise = profile.disease_expertise
    if expertise.assessment.status != AssessmentStatus.EVALUATED or not expertise.disease_expertise:
        failures.append("病种专长尚未完成核验")
    used = set(profile.basic_qualification.assessment.source_ids) | set(expertise.assessment.source_ids)
    if not used or not used.issubset(registered):
        failures.append("医生核心能力来源未登记或链接不可核验")
    if profile.profile_version.endswith("-online"):
        fields = {record.evidence_field for record in profile.evidence_records}
        if not {"basic_qualification", "disease_expertise"}.issubset(fields):
            failures.append("动态医生画像缺少结构化身份或病种专长证据")
    return not failures, failures


class CatalogDiscoveryProvider:
    provider_name = "verified_benchmark_catalog"

    def __init__(self, catalog_path: Path):
        self.catalog_path = catalog_path

    def discover(self, query: ProviderDiscoveryQuery) -> ProviderDiscoveryResult:
        catalog = load_catalog(self.catalog_path)
        entry = next(
            (
                item for item in catalog.diseases
                if item.disease_group == query.disease_group
                or item.disease_label == query.disease_label
            ),
            None,
        )
        hospitals = entry.hospitals if entry else []
        return ProviderDiscoveryResult(
            query=query,
            hospital_profiles=hospitals,
            discovery_layers=[self.provider_name] if hospitals else [],
            coverage_complete=len(hospitals) >= query.minimum_candidates,
        )


class LocalCapabilityCache:
    provider_name = "local_verified_capability_cache"

    def __init__(self, path: Path = DEFAULT_CAPABILITY_CACHE_PATH, ttl_days: int = 90):
        self.path = path
        self.ttl_days = ttl_days

    def _read(self) -> CapabilityCacheWriteRequest:
        if not self.path.exists():
            return CapabilityCacheWriteRequest()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return CapabilityCacheWriteRequest.model_validate({
            "hospital_profiles": payload.get("hospital_profiles", []),
            "clinician_profiles": payload.get("clinician_profiles", []),
        })

    def discover(self, query: ProviderDiscoveryQuery) -> ProviderDiscoveryResult:
        payload = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        cache = self._read()
        def current_hospital_version(version: str) -> bool:
            return not version.endswith("-online") or version.endswith("-v3-online")

        def current_clinician_version(version: str) -> bool:
            return not version.endswith("-online") or version.endswith("-v4-online")

        hospitals = [
            item for item in cache.hospital_profiles
            if item.disease_group == query.disease_group and current_hospital_version(item.profile_version)
        ]
        clinicians = [
            item for item in cache.clinician_profiles
            if item.disease_group == query.disease_group and current_clinician_version(item.profile_version)
        ]
        ignored_old_online = any(
            item.disease_group == query.disease_group and not current_hospital_version(item.profile_version)
            for item in cache.hospital_profiles
        ) or any(
            item.disease_group == query.disease_group and not current_clinician_version(item.profile_version)
            for item in cache.clinician_profiles
        )
        stale = False
        updated_at = payload.get("updated_at")
        if updated_at:
            try:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                stale = age.days >= self.ttl_days
            except (TypeError, ValueError):
                stale = True
        return ProviderDiscoveryResult(
            query=query,
            hospital_profiles=hospitals,
            clinician_profiles=clinicians if query.include_clinicians else [],
            discovery_layers=[self.provider_name] if hospitals or clinicians else [],
            coverage_complete=len(hospitals) >= query.minimum_candidates and not stale,
            warnings=(
                (["本地可信能力缓存已超过刷新周期，将同时尝试在线核验"] if stale and hospitals else [])
                + (["已忽略旧版在线发现缓存，并按当前证据规则重新核验"] if ignored_old_online else [])
            ),
        )

    def write_verified(self, request: CapabilityCacheWriteRequest) -> dict[str, int]:
        current = self._read()
        hospitals = {
            (item.disease_group, item.hospital_id): item for item in current.hospital_profiles
            if not item.profile_version.endswith("-online") or item.profile_version.endswith("-v3-online")
        }
        clinicians = {
            (item.disease_group, item.clinician_id): item for item in current.clinician_profiles
            if not item.profile_version.endswith("-online") or item.profile_version.endswith("-v4-online")
        }
        for profile in request.hospital_profiles:
            query = ProviderDiscoveryQuery(
                disease_group=profile.disease_group,
                disease_label=profile.disease_group,
            )
            valid, failures = hospital_profile_meets_evidence_gate(profile, query)
            if not valid:
                raise ValueError(f"医院画像未通过可信缓存门槛：{'；'.join(failures)}")
            hospitals[(profile.disease_group, profile.hospital_id)] = profile
        for profile in request.clinician_profiles:
            query = ProviderDiscoveryQuery(
                disease_group=profile.disease_group,
                disease_label=profile.disease_group,
            )
            valid, failures = clinician_profile_meets_evidence_gate(profile, query)
            if not valid:
                raise ValueError(f"医生画像未通过可信缓存门槛：{'；'.join(failures)}")
            clinicians[(profile.disease_group, profile.clinician_id)] = profile
        payload = {
            "cache_version": "provider-capability-cache-v1",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "hospital_profiles": [item.model_dump(mode="json") for item in hospitals.values()],
            "clinician_profiles": [item.model_dump(mode="json") for item in clinicians.values()],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)
        return {"hospital_profiles": len(hospitals), "clinician_profiles": len(clinicians)}


class ProviderDiscoveryService:
    def __init__(
        self,
        trusted_providers: list[DynamicProviderDiscovery],
        dynamic_providers: list[DynamicProviderDiscovery] | None = None,
        cache_writer: LocalCapabilityCache | None = None,
    ):
        self.trusted_providers = trusted_providers
        self.dynamic_providers = dynamic_providers or []
        self.cache_writer = cache_writer

    def discover(self, query: ProviderDiscoveryQuery) -> ProviderDiscoveryResult:
        if query.capability_need is None:
            query = query.model_copy(update={
                "capability_need": resolve_disease_capability_need(query),
            })
        hospitals: dict[str, HospitalCapabilityProfile] = {}
        clinicians: dict[str, ClinicianCapabilityProfile] = {}
        layers: list[str] = []
        warnings: list[str] = []
        diagnostics = None

        refresh_requested = False

        def consume(provider: DynamicProviderDiscovery, *, persist: bool = False) -> None:
            nonlocal refresh_requested, diagnostics
            result = provider.discover(query)
            if result.diagnostics is not None:
                diagnostics = result.diagnostics
            accepted = 0
            accepted_hospitals = []
            accepted_clinicians = []
            for profile in result.hospital_profiles:
                valid, failures = hospital_profile_meets_evidence_gate(profile, query)
                if valid:
                    hospitals[profile.hospital_id] = profile
                    accepted += 1
                    accepted_hospitals.append(profile)
                else:
                    warnings.append(f"{profile.institution_foundation.hospital_name}未通过证据门槛：{'；'.join(failures)}")
            for profile in result.clinician_profiles:
                valid, failures = clinician_profile_meets_evidence_gate(profile, query)
                if valid:
                    clinicians[profile.clinician_id] = profile
                    accepted_clinicians.append(profile)
                else:
                    warnings.append(f"{profile.basic_qualification.clinician_name}未通过证据门槛：{'；'.join(failures)}")
            if accepted or result.clinician_profiles:
                layers.extend(result.discovery_layers or [provider.provider_name])
            warnings.extend(result.warnings)
            if any("超过刷新周期" in item for item in result.warnings):
                refresh_requested = True
            if persist and self.cache_writer and (accepted_hospitals or accepted_clinicians):
                try:
                    self.cache_writer.write_verified(CapabilityCacheWriteRequest(
                        hospital_profiles=accepted_hospitals,
                        clinician_profiles=accepted_clinicians,
                    ))
                except (OSError, ValueError) as exc:
                    warnings.append(f"动态发现结果已用于本次推荐，但本地可信缓存写入失败：{type(exc).__name__}")

        for provider in self.trusted_providers:
            consume(provider)

        regional = [
            item for item in hospitals.values()
            if (query.city and item.institution_foundation.city == query.city)
            or (query.province and item.institution_foundation.province == query.province)
        ]
        needs_dynamic = len(hospitals) < query.minimum_candidates or not regional or refresh_requested
        if needs_dynamic:
            for provider in self.dynamic_providers:
                consume(provider, persist=True)
        if needs_dynamic and not self.dynamic_providers:
            warnings.append("已有可信资源未完全覆盖当前病种或地区；当前尚未配置权威公开数据动态发现来源")
        if len(hospitals) < query.minimum_candidates:
            warnings.append(
                f"当前已完成公开证据核验的机构为 {len(hospitals)} 家；"
                "模型候选不会因联网失败被删除，将由推荐服务标记为待核验"
            )
        return ProviderDiscoveryResult(
            query=query,
            hospital_profiles=list(hospitals.values()),
            clinician_profiles=list(clinicians.values()) if query.include_clinicians else [],
            discovery_layers=list(dict.fromkeys(layers)),
            dynamic_discovery_attempted=needs_dynamic and bool(self.dynamic_providers),
            coverage_complete=len(hospitals) >= query.minimum_candidates,
            warnings=list(dict.fromkeys(warnings)),
            diagnostics=diagnostics,
        )

    def discover_clinicians(
        self,
        query: ProviderDiscoveryQuery,
        hospitals: list[HospitalCapabilityProfile],
    ) -> ProviderDiscoveryResult:
        clinicians: dict[str, ClinicianCapabilityProfile] = {}
        layers: list[str] = []
        warnings: list[str] = []
        for provider in [*self.trusted_providers, *self.dynamic_providers]:
            method = getattr(provider, "discover_clinicians", None)
            if method is None:
                result = provider.discover(query)
            else:
                try:
                    result = method(query, hospitals)
                except Exception as exc:
                    warnings.append(f"医生公开资料发现失败：{type(exc).__name__}")
                    continue
            accepted = []
            for profile in result.clinician_profiles:
                if profile.basic_qualification.hospital_id not in {item.hospital_id for item in hospitals}:
                    continue
                valid, failures = clinician_profile_meets_evidence_gate(profile, query)
                if valid:
                    clinicians[profile.clinician_id] = profile
                    accepted.append(profile)
                else:
                    warnings.append(f"{profile.basic_qualification.clinician_name}未通过证据门槛：{'；'.join(failures)}")
            if accepted:
                layers.extend(result.discovery_layers or [provider.provider_name])
                if provider in self.dynamic_providers and self.cache_writer:
                    try:
                        self.cache_writer.write_verified(CapabilityCacheWriteRequest(clinician_profiles=accepted))
                    except (OSError, ValueError) as exc:
                        warnings.append(f"医生能力资料已用于本次推荐，但缓存写入失败：{type(exc).__name__}")
            warnings.extend(result.warnings)
        return ProviderDiscoveryResult(
            query=query, clinician_profiles=list(clinicians.values()),
            discovery_layers=list(dict.fromkeys(layers)),
            dynamic_discovery_attempted=bool(self.dynamic_providers),
            warnings=list(dict.fromkeys(warnings)),
        )
