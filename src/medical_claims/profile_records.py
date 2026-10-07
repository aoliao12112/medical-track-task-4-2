"""Confirmed provider + structured case adapter for the existing quality engine."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator

from .models import ClaimCase, Certainty, DiagnosisType, FieldStatus, StrictModel
from .pain_models import PainPointReport, PainPointType
from .provider_extraction import detect_disease_groups
from .provider_models import CaseProviderContext, ClinicianRole
from .providers import OcrBlock, OcrDocument
from .quality_engine import derive_treatment_plan_id
from .quality_models import ProfileCaseRecord


SUPPORTED_DISEASE_GROUPS = ("fracture", "pneumonia", "diabetes")
ELIGIBLE_ROLES = {ClinicianRole.ATTENDING_PHYSICIAN, ClinicianRole.TREATING_PHYSICIAN, ClinicianRole.SURGEON}


class ConfirmedProviderContext(StrictModel):
    context: CaseProviderContext
    institution_confirmed: bool = False
    confirmed_clinician_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_confirmation_targets(self):
        if self.institution_confirmed and self.context.institution is None:
            raise ValueError("确认医院时必须提供医院来源上下文")
        known = {item.clinician_id for item in self.context.clinicians}
        if not set(self.confirmed_clinician_ids).issubset(known):
            raise ValueError("确认医生必须存在于来源上下文")
        return self


class ProfileRecordInput(StrictModel):
    case: ClaimCase
    pain_report: PainPointReport | None = None
    provider: ConfirmedProviderContext
    data_source: str = Field(min_length=1)
    stable_patient_token: str | None = None
    readmitted_within_30_days: bool | None = None
    patient_feedback_score: float | None = Field(default=None, ge=1, le=5)

    @model_validator(mode="after")
    def check_case_links(self):
        if self.provider.context.case_id != self.case.case_id:
            raise ValueError("医院/医生上下文与病例 case_id 不一致")
        if self.pain_report and self.pain_report.case_id != self.case.case_id:
            raise ValueError("痛点报告与病例 case_id 不一致")
        if self.readmitted_within_30_days is not None and not self.stable_patient_token:
            raise ValueError("再入院标签必须有稳定脱敏患者 token")
        return self


class ProfileAssembly(StrictModel):
    case_id: str
    disease_group: str = "unavailable"
    institution_id: str = "unavailable"
    clinician_ids: list[str] = Field(default_factory=list)
    record: ProfileCaseRecord | None = None
    warnings: list[str] = Field(default_factory=list)


class HistoricalProfileImport(StrictModel):
    records: list[ProfileRecordInput] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cases(self):
        ids = [item.case.case_id for item in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("同一历史案件只能导入一次，禁止扩大样本量")
        return self


class StoredPortfolioBuildRequest(StrictModel):
    use_saved_records: Literal[True] = True
    minimum_sample_size: int = Field(default=3, ge=2, le=100)


class CaseProfileContextRequest(StrictModel):
    provider: ConfirmedProviderContext
    stable_patient_token: str | None = None
    readmitted_within_30_days: bool | None = None
    patient_feedback_score: float | None = Field(default=None, ge=1, le=5)

    @model_validator(mode="after")
    def stable_readmission_link(self):
        if self.readmitted_within_30_days is not None and not self.stable_patient_token:
            raise ValueError("再入院标签必须有稳定脱敏患者 token")
        return self


def resolve_disease_group(case: ClaimCase) -> str:
    """Primary confirmed diagnosis, otherwise first confirmed discharge diagnosis.

    Do not infer a primary disease from secondary/history/suspected diagnoses,
    provider OCR keywords, or an operator-entered free-form disease label.
    """
    diagnoses = [d for d in case.diagnoses if d.certainty == Certainty.CONFIRMED
                 and d.name.status == FieldStatus.EXTRACTED and d.name.confidence is not None
                 and d.name.confidence >= 0.8 and d.name.evidence]
    primary = [d for d in diagnoses if d.diagnosis_type == DiagnosisType.PRIMARY]
    selected = primary if primary else [d for d in diagnoses if d.diagnosis_type == DiagnosisType.DISCHARGE][:1]
    if not selected:
        return "unavailable"
    groups = set()
    for diagnosis in selected:
        name = diagnosis.name.normalized_value or diagnosis.name.value or ""
        found = detect_disease_groups([OcrDocument(document_id="structured-diagnosis", blocks=[
            OcrBlock(text=name, page=1, confidence=diagnosis.name.confidence)])])
        if len(found) != 1 or found[0] not in SUPPORTED_DISEASE_GROUPS:
            return "unavailable"
        groups.update(found)
    return next(iter(groups)) if len(groups) == 1 else "unavailable"


def build_profile_case_record(payload: ProfileRecordInput) -> ProfileAssembly:
    case = payload.case
    provider = payload.provider
    output = ProfileAssembly(case_id=case.case_id, disease_group=resolve_disease_group(case))
    institution = provider.context.institution
    if (institution and provider.institution_confirmed and not institution.review_required
            and institution.confidence >= 0.8 and institution.sources
            and re.fullmatch(r"HOSP-[A-F0-9]{12}", institution.institution_id)):
        output.institution_id = institution.institution_id
    else:
        output.warnings.append("医院未确认、存在来源冲突或缺少可信伪匿名来源，不进入画像比较")
    if output.disease_group == "unavailable":
        output.warnings.append("无法从可靠的本次主诊断/首个出院诊断确定受支持病种")
    for clinician in provider.context.clinicians:
        if (clinician.clinician_id in provider.confirmed_clinician_ids
                and clinician.role in ELIGIBLE_ROLES and clinician.ranking_eligible
                and not clinician.review_required and clinician.confidence >= 0.8
                and clinician.sources and clinician.institution_id == output.institution_id
                and re.fullmatch(r"DOC-[A-F0-9]{12}", clinician.clinician_id)):
            output.clinician_ids.append(clinician.clinician_id)
    output.clinician_ids = sorted(set(output.clinician_ids))
    if not output.clinician_ids:
        output.warnings.append("没有已确认的合格经治/主治/手术医生，不创建医生画像")
    if output.institution_id == "unavailable" or output.disease_group == "unavailable":
        return output
    watched = {PainPointType.MEDICATION_DIAGNOSIS_REVIEW, PainPointType.DUPLICATE_MEDICATION_REVIEW}
    # Preserve the source report in the assembly input, but do not let an empty
    # or rule-only negative screen become the existing engine's default 100.
    medication_report = payload.pain_report if payload.pain_report and any(
        point.type in watched and point.evidence for point in payload.pain_report.pain_points) else None
    if medication_report is None:
        output.warnings.append("没有有证据的用药复核信号，用药维度 data_unavailable；未检出不代表治疗优秀")
    record = ProfileCaseRecord(
        case=case, institution_id=output.institution_id, disease_group=output.disease_group,
        clinician_ids=output.clinician_ids, pain_report=medication_report,
        stable_patient_token=payload.stable_patient_token,
        readmitted_within_30_days=payload.readmitted_within_30_days,
        patient_feedback_score=payload.patient_feedback_score,
    )
    record.treatment_plan_id = derive_treatment_plan_id(record)
    output.record = record
    return output
