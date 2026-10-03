"""第四问：医院、医生来源上下文的脱敏数据模型。

这些模型只保存可公开用于画像建模的伪匿名编号。真实名称只允许出现在
本机、被 git 忽略的审计私有目录中。
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from .models import StrictModel


class ClinicianRole(str, Enum):
    ATTENDING_PHYSICIAN = "attending_physician"
    TREATING_PHYSICIAN = "treating_physician"
    RESIDENT_PHYSICIAN = "resident_physician"
    SURGEON = "surgeon"
    ORDERING_PHYSICIAN = "ordering_physician"
    REPORTING_PHYSICIAN = "reporting_physician"
    DIAGNOSTIC_PHYSICIAN = "diagnostic_physician"
    LABORATORY_OPERATOR = "laboratory_operator"
    REVIEWER = "reviewer"
    SIGNATURE_UNKNOWN = "signature_unknown"


class ProviderSource(StrictModel):
    """不包含 OCR 原文的安全证据定位。"""

    document_id: str
    page: int = Field(ge=1)
    confidence: float | None = Field(default=None, ge=0, le=1)


class InstitutionReference(StrictModel):
    institution_id: str
    confidence: float = Field(ge=0, le=1)
    sources: list[ProviderSource] = Field(default_factory=list)
    review_required: bool = False


class ClinicianReference(StrictModel):
    clinician_id: str
    institution_id: str | None = None
    role: ClinicianRole
    confidence: float = Field(ge=0, le=1)
    ranking_eligible: bool = False
    sources: list[ProviderSource] = Field(default_factory=list)
    review_required: bool = False


class CaseProviderContext(StrictModel):
    case_id: str
    institution: InstitutionReference | None = None
    clinicians: list[ClinicianReference] = Field(default_factory=list)
    disease_groups: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

