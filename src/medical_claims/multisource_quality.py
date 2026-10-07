"""第四问多源医院画像与推荐候选池。

本模块不计算医院排名或最终推荐。公开机构事实与历史理赔观察分层保存，
候选准入只使用可追溯、与病种相关的公开证据；医院等级和单病例表现
均不能单独形成准入或医院质量结论。
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import Field

from .models import StrictModel
from .quality_models import ProfileEntityType, QualityPortfolio


SUPPORTED_PUBLIC_DISEASES = ("pneumonia", "influenza", "hypertension")


class EvidenceSufficiency(str, Enum):
    SUFFICIENT = "sufficient"
    PARTIALLY_SUFFICIENT = "partially_sufficient"
    INSUFFICIENT = "insufficient"


class ProfileEvidence(StrictModel):
    field: str
    label: str
    value: str | list[str] | int | float | None = None
    source_type: str
    source_ids: list[str] = Field(default_factory=list)
    sample_size: int = Field(default=0, ge=0)
    sufficiency: EvidenceSufficiency
    usable_for_recommendation: bool = False
    note: str | None = None


class PublicSource(StrictModel):
    source_id: str
    title: str
    url: str | None = None
    authority_type: str
    queried_at: str


class HistoricalClaimProfile(StrictModel):
    official_deidentified_case_count: int = Field(ge=0)
    structured_history_case_count: int = Field(default=0, ge=0)
    case_ids: list[str] = Field(default_factory=list)
    available_dimensions: list[str] = Field(default_factory=list)
    pending_dimensions: list[str] = Field(default_factory=list)
    evidence: list[ProfileEvidence] = Field(default_factory=list)
    sufficiency: EvidenceSufficiency
    stable_quality_conclusion_available: bool = False
    usable_for_recommendation: bool = False
    note: str


class MultiSourceHospitalProfile(StrictModel):
    hospital_id: str
    disease_group: str
    standard_name: str
    region: str | None = None
    hospital_type: str | None = None
    hospital_grade: str | None = None
    related_departments: list[str] = Field(default_factory=list)
    national_key_specialties: list[str] = Field(default_factory=list)
    provincial_key_specialties: list[str] = Field(default_factory=list)
    specialist_capability: str | None = None
    queried_at: str
    public_evidence: list[ProfileEvidence] = Field(default_factory=list)
    historical_claims: HistoricalClaimProfile
    sources: list[PublicSource] = Field(default_factory=list)


class CandidateHospital(StrictModel):
    hospital_id: str
    disease_group: str
    evidence_sufficiency: EvidenceSufficiency
    eligible_for_candidate_pool: bool
    satisfied_conditions: list[str] = Field(default_factory=list)
    failed_conditions: list[str] = Field(default_factory=list)
    recommendation_evidence_fields: list[str] = Field(default_factory=list)
    reason: str
    profile: MultiSourceHospitalProfile


class DiseaseCandidatePool(StrictModel):
    disease_group: str
    hospital_total: int = Field(ge=0)
    sufficient_count: int = Field(ge=0)
    partially_sufficient_count: int = Field(ge=0)
    insufficient_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    candidates: list[CandidateHospital] = Field(default_factory=list)
    excluded: list[CandidateHospital] = Field(default_factory=list)
    generated_final_recommendation: bool = False
    policy_version: str = "multisource-candidate-gate-v1"


class MultiSourceCandidatePools(StrictModel):
    diseases: list[DiseaseCandidatePool]
    generated_final_recommendation: bool = False
    source_audit_query_date: str
    warnings: list[str] = Field(default_factory=list)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取多源画像数据：{path.name}") from exc


def load_multisource_inputs(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        _load_json(root / "outputs" / "hospital_public_data_audit.json"),
        _load_json(root / "outputs" / "dataset_audit_summary.json"),
    )


def _source_ids_are_traceable(source_ids: list[str], source_map: dict[str, dict[str, Any]]) -> bool:
    """推荐公开证据必须落到实际公开 URL；本地 OCR 名称证据不充当推荐理由。"""
    return bool(source_ids) and all(source_id in source_map for source_id in source_ids) and any(
        source_map[source_id].get("url") for source_id in source_ids
    )


def _field_value(record: dict[str, Any], name: str) -> Any:
    return record.get("fields", {}).get(name, {}).get("value")


def _existing_history(
    hospital_id: str,
    disease_group: str,
    audit_record: dict[str, Any],
    portfolio: QualityPortfolio | None,
    disease_case_ids: list[str],
) -> HistoricalClaimProfile:
    structured_profile = None
    if portfolio:
        structured_profile = next(
            (
                profile
                for profile in portfolio.profiles
                if profile.entity_type == ProfileEntityType.INSTITUTION
                and profile.entity_id == hospital_id
                and profile.disease_group == disease_group
            ),
            None,
        )
    structured_count = structured_profile.sample_size if structured_profile else 0
    available_dimensions = []
    if structured_profile:
        available_dimensions = [
            metric.dimension.value
            for metric in structured_profile.metrics
            if metric.score is not None and metric.status.value == "calculated"
        ]
    stable = bool(structured_profile and structured_profile.eligible_for_recommendation)
    official_count = len(disease_case_ids)
    sufficient = EvidenceSufficiency.SUFFICIENT if stable else (
        EvidenceSufficiency.PARTIALLY_SUFFICIENT if structured_count else EvidenceSufficiency.INSUFFICIENT
    )
    historical_evidence = []
    if structured_profile:
        for metric in structured_profile.metrics:
            calculated = metric.score is not None and metric.status.value == "calculated"
            partial = metric.score is not None and metric.status.value == "insufficient_sample"
            historical_evidence.append(ProfileEvidence(
                field=f"historical.{metric.dimension.value}",
                label=metric.label,
                value=metric.score,
                source_type="official_deidentified_claims",
                source_ids=metric.evidence_fields,
                sample_size=metric.sample_size,
                sufficiency=(EvidenceSufficiency.SUFFICIENT if calculated else
                             EvidenceSufficiency.PARTIALLY_SUFFICIENT if partial else
                             EvidenceSufficiency.INSUFFICIENT),
                usable_for_recommendation=bool(stable and calculated and metric.evidence_fields),
                note="沿用历史画像原计算公式；公开资质不参与该指标补分。",
            ))
    else:
        for field_name, label in (
            ("cost", "费用"), ("medication", "用药"),
            ("treatment_standardization", "治疗规范性"), ("hospitalization", "住院信息"),
            ("pain_points", "痛点"), ("outcome_feedback", "结局/反馈"),
        ):
            historical_evidence.append(ProfileEvidence(
                field=f"historical.{field_name}",
                label=label,
                source_type="official_deidentified_claims",
                sample_size=0,
                sufficiency=EvidenceSufficiency.INSUFFICIENT,
                usable_for_recommendation=False,
                note="待授权结构化处理或独立真实标签补充。",
            ))
    return HistoricalClaimProfile(
        official_deidentified_case_count=official_count,
        structured_history_case_count=structured_count,
        case_ids=disease_case_ids,
        available_dimensions=available_dimensions,
        pending_dimensions=["费用", "用药", "治疗规范性", "住院信息", "痛点", "结局/反馈"]
        if not structured_profile else [],
        evidence=historical_evidence,
        sufficiency=sufficient,
        stable_quality_conclusion_available=stable,
        usable_for_recommendation=stable,
        note=(
            "存在达到原历史画像门槛的结构化记录；其指标仍需按原计算公式和样本量解释。"
            if stable
            else "当前官方脱敏病例仅作历史病例参考；样本不足或尚未结构化，不能形成医院整体质量结论。"
        ),
    )


def _public_field(
    field: str,
    label: str,
    value: str | list[str] | int | None,
    source_ids: list[str],
    source_map: dict[str, dict[str, Any]],
    *,
    recommendable: bool,
    note: str | None = None,
) -> ProfileEvidence:
    traceable = value is not None and _source_ids_are_traceable(source_ids, source_map)
    return ProfileEvidence(
        field=field,
        label=label,
        value=value,
        source_type="authoritative_public_data",
        source_ids=source_ids if value is not None else [],
        sufficiency=EvidenceSufficiency.SUFFICIENT if traceable else EvidenceSufficiency.INSUFFICIENT,
        usable_for_recommendation=bool(traceable and recommendable),
        note=note,
    )


def _profile_for(
    record: dict[str, Any],
    disease_group: str,
    portfolio: QualityPortfolio | None,
    disease_case_ids: list[str],
) -> tuple[MultiSourceHospitalProfile, list[str], list[str]]:
    source_map = {item["source_id"]: item for item in record.get("sources", [])}
    disease = next(
        (item for item in record.get("disease_public_evidence", []) if item.get("disease_group") == disease_group),
        None,
    )
    disease_source_ids = disease.get("source_ids", []) if disease else []
    departments = disease.get("related_departments", []) if disease else []
    national = disease.get("national_key_specialties", []) if disease else []
    provincial = disease.get("provincial_key_specialties", []) if disease else []
    capability = disease.get("capability_note") if disease else None
    standard_name_field = record["fields"]["standard_name"]
    public_evidence = [
        _public_field("standard_name", "标准医院名称", standard_name_field.get("value"), standard_name_field.get("source_ids", []), source_map, recommendable=False),
        _public_field("region", "所在地区", _field_value(record, "region"), record["fields"]["region"].get("source_ids", []), source_map, recommendable=False),
        _public_field("hospital_type", "医院类型", _field_value(record, "hospital_type"), record["fields"]["hospital_type"].get("source_ids", []), source_map, recommendable=False),
        _public_field("hospital_grade", "医院等级", _field_value(record, "hospital_grade"), record["fields"]["hospital_grade"].get("source_ids", []), source_map, recommendable=False, note="医院等级只展示，不能单独构成候选准入或推荐理由。"),
        _public_field("disease_related_departments", "病种相关科室", departments or None, disease_source_ids, source_map, recommendable=True),
        _public_field("national_key_specialties", "国家级临床重点专科", national or None, disease_source_ids, source_map, recommendable=True),
        _public_field("provincial_key_specialties", "省级临床重点专科", provincial or None, disease_source_ids, source_map, recommendable=True),
        _public_field("specialist_capability", "可验证专科能力", capability, disease_source_ids, source_map, recommendable=True),
    ]
    recommendable_fields = [item.field for item in public_evidence if item.usable_for_recommendation]
    failures = []
    if record.get("name_confirmation_status") != "confirmed" or not standard_name_field.get("value"):
        failures.append("医院名称未可靠确认")
    if not departments or not _source_ids_are_traceable(disease_source_ids, source_map):
        failures.append("缺少当前病种可追溯的相关科室或专科能力证据")
    if len(set(recommendable_fields)) < 2:
        failures.append("少于2个彼此独立、可追溯的推荐证据字段")
    profile = MultiSourceHospitalProfile(
        hospital_id=record["hospital_id"],
        disease_group=disease_group,
        standard_name=standard_name_field.get("value") or record.get("ocr_candidate_name") or record["hospital_id"],
        region=_field_value(record, "region"),
        hospital_type=_field_value(record, "hospital_type"),
        hospital_grade=_field_value(record, "hospital_grade"),
        related_departments=departments,
        national_key_specialties=national,
        provincial_key_specialties=provincial,
        specialist_capability=capability,
        queried_at=record.get("queried_at", "unknown"),
        public_evidence=public_evidence,
        historical_claims=_existing_history(record["hospital_id"], disease_group, record, portfolio, disease_case_ids),
        sources=[PublicSource.model_validate(item) for item in record.get("sources", [])],
    )
    conditions = []
    if not any("名称" in item for item in failures):
        conditions.append("医院名称可靠确认")
    if not any("相关科室" in item for item in failures):
        conditions.append("当前病种有可追溯的相关科室或专科能力证据")
    if len(set(recommendable_fields)) >= 2:
        conditions.append("至少2个独立推荐证据字段可追溯")
    conditions.extend(["医院等级不作为单独准入依据", "单病例历史表现不形成医院质量结论"])
    return profile, conditions, failures


def build_multisource_candidate_pools(
    public_audit: dict[str, Any],
    dataset_audit: dict[str, Any],
    portfolio: QualityPortfolio | None = None,
) -> MultiSourceCandidatePools:
    records = public_audit.get("hospitals", [])
    disease_case_ids_by_hospital: dict[tuple[str, str], list[str]] = {}
    for case in dataset_audit.get("cases", []):
        hospital_id = case.get("institution", {}).get("institution_id")
        for disease_group in case.get("local_disease_candidates", []):
            disease_case_ids_by_hospital.setdefault((hospital_id, disease_group), []).append(case["case_id"])
    case_disease_groups = {
        item.get("disease_group")
        for item in dataset_audit.get("aggregate", {}).get("disease", {}).get("case_counts", [])
    }
    pools = []
    for disease_group in SUPPORTED_PUBLIC_DISEASES:
        if disease_group not in case_disease_groups:
            relevant = []
        else:
            relevant = [
                record
                for record in records
                if disease_group in {
                    item.get("disease_group")
                    for item in record.get("disease_public_evidence", [])
                }
                or any(
                    label == disease_group
                    for label in _raw_disease_keys(record, public_audit)
                )
            ]
        items = []
        for record in relevant:
            profile, conditions, failures = _profile_for(
                record,
                disease_group,
                portfolio,
                disease_case_ids_by_hospital.get((record["hospital_id"], disease_group), []),
            )
            evidence_count = len({
                item.field for item in profile.public_evidence if item.usable_for_recommendation
            })
            if not failures:
                status = EvidenceSufficiency.SUFFICIENT
            elif profile.related_departments and evidence_count:
                status = EvidenceSufficiency.PARTIALLY_SUFFICIENT
            else:
                status = EvidenceSufficiency.INSUFFICIENT
            eligible = status == EvidenceSufficiency.SUFFICIENT
            items.append(CandidateHospital(
                hospital_id=record["hospital_id"],
                disease_group=disease_group,
                evidence_sufficiency=status,
                eligible_for_candidate_pool=eligible,
                satisfied_conditions=conditions,
                failed_conditions=failures,
                recommendation_evidence_fields=[
                    item.field for item in profile.public_evidence if item.usable_for_recommendation
                ],
                reason=(
                    "达到多源证据门槛，进入后续可解释推荐候选池；当前未生成排名或最终推荐。"
                    if eligible else "；".join(failures)
                ),
                profile=profile,
            ))
        sufficient = [item for item in items if item.evidence_sufficiency == EvidenceSufficiency.SUFFICIENT]
        partial = [item for item in items if item.evidence_sufficiency == EvidenceSufficiency.PARTIALLY_SUFFICIENT]
        insufficient = [item for item in items if item.evidence_sufficiency == EvidenceSufficiency.INSUFFICIENT]
        pools.append(DiseaseCandidatePool(
            disease_group=disease_group,
            hospital_total=len(items),
            sufficient_count=len(sufficient),
            partially_sufficient_count=len(partial),
            insufficient_count=len(insufficient),
            candidate_count=len(sufficient),
            candidates=sufficient,
            excluded=partial + insufficient,
        ))
    return MultiSourceCandidatePools(
        diseases=pools,
        source_audit_query_date=public_audit.get("generated_at", "unknown"),
        warnings=[
            "候选池不是医院排名或最终推荐。",
            "官方病例的本地OCR病种仅为候选标签，正式个案使用前须由可靠结构化诊断确认。",
            "历史单病例只作参考，不形成医院整体质量结论。",
        ],
    )


def _raw_disease_keys(record: dict[str, Any], public_audit: dict[str, Any]) -> list[str]:
    """把审计中的中文候选标签映射回固定病种键，避免依赖自由文本推断。"""
    labels = {"肺炎": "pneumonia", "流感": "influenza", "高血压": "hypertension"}
    return [labels[label] for label in record.get("local_case_disease_candidates", []) if label in labels]
