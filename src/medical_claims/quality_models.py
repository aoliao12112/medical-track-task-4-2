"""第四问：医院、医生与治疗方案品质画像及可解释推荐 Schema。"""

from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from .models import ClaimCase, StrictModel
from .pain_models import PainPointReport


class ProfileEntityType(str, Enum):
    INSTITUTION = "institution"
    CLINICIAN = "clinician"
    TREATMENT_PLAN = "treatment_plan"


class QualityDimension(str, Enum):
    COST_EFFICIENCY = "cost_efficiency"
    MEDICATION_RATIONALITY = "medication_rationality"
    TREATMENT_STANDARDIZATION = "treatment_standardization"
    READMISSION_CONTROL = "readmission_control"
    PATIENT_FEEDBACK = "patient_feedback"
    DATA_QUALITY = "data_quality"


class MetricStatus(str, Enum):
    CALCULATED = "calculated"
    INSUFFICIENT_SAMPLE = "insufficient_sample"
    DATA_UNAVAILABLE = "data_unavailable"
    REVIEW_REQUIRED = "review_required"


class QualityMetric(StrictModel):
    dimension: QualityDimension
    label: str
    score: float | None = Field(default=None, ge=0, le=100)
    status: MetricStatus
    sample_size: int = Field(default=0, ge=0)
    confidence: float = Field(default=0, ge=0, le=1)
    raw_value: float | None = None
    unit: str | None = None
    calculation: str
    evidence_fields: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class QualityProfile(StrictModel):
    entity_id: str
    entity_type: ProfileEntityType
    disease_group: str
    sample_size: int = Field(ge=1)
    metrics: list[QualityMetric] = Field(min_length=5)
    composite_score: float | None = Field(default=None, ge=0, le=100)
    composite_confidence: float = Field(default=0, ge=0, le=1)
    eligible_for_recommendation: bool = False
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_recommendation_gate(self) -> "QualityProfile":
        if self.eligible_for_recommendation and self.composite_score is None:
            raise ValueError("进入推荐的画像必须有综合分")
        if len({metric.dimension for metric in self.metrics}) != len(self.metrics):
            raise ValueError("同一画像不能重复计算同一维度")
        return self


class ProfileCaseRecord(StrictModel):
    """画像聚合的最小病例记录；所有主体编号必须已脱敏。"""

    case: ClaimCase
    institution_id: str
    disease_group: str
    clinician_ids: list[str] = Field(default_factory=list)
    treatment_plan_id: str | None = None
    stable_patient_token: str | None = None
    readmitted_within_30_days: bool | None = None
    patient_feedback_score: float | None = Field(default=None, ge=1, le=5)
    pain_report: PainPointReport | None = None

    @model_validator(mode="after")
    def validate_links(self) -> "ProfileCaseRecord":
        if self.pain_report and self.pain_report.case_id != self.case.case_id:
            raise ValueError("痛点报告与病例 case_id 不一致")
        if self.readmitted_within_30_days is not None and not self.stable_patient_token:
            raise ValueError("计算再入院必须提供稳定脱敏患者标识")
        return self


class QualityPortfolioRequest(StrictModel):
    records: list[ProfileCaseRecord] = Field(min_length=1)
    minimum_sample_size: int = Field(default=3, ge=2, le=100)

    @model_validator(mode="after")
    def reject_duplicate_case_disease_rows(self) -> "QualityPortfolioRequest":
        keys = [(record.case.case_id, record.disease_group) for record in self.records]
        if len(keys) != len(set(keys)):
            raise ValueError("同一病例在同一病种中只能出现一次，禁止重复扩大样本量")
        return self


class QualityPortfolio(StrictModel):
    profiles: list[QualityProfile] = Field(default_factory=list)
    case_count: int = Field(ge=0)
    institution_count: int = Field(ge=0)
    clinician_count: int = Field(ge=0)
    treatment_plan_count: int = Field(ge=0)
    disease_count: int = Field(ge=0)
    minimum_sample_size: int = Field(ge=2)
    engine_version: str = "quality-profile-engine-v1"
    warnings: list[str] = Field(default_factory=list)


class RecommendationReason(StrictModel):
    dimension: QualityDimension
    title: str
    explanation: str
    score: float = Field(ge=0, le=100)
    evidence_fields: list[str] = Field(default_factory=list)


class RecommendationItem(StrictModel):
    entity_id: str
    rank: int = Field(ge=1)
    composite_score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    sample_size: int = Field(ge=1)
    reasons: list[RecommendationReason] = Field(min_length=2)
    limitations: list[str] = Field(default_factory=list)


class RecommendationRequest(StrictModel):
    portfolio: QualityPortfolio
    disease_group: str
    entity_type: ProfileEntityType = ProfileEntityType.INSTITUTION
    top_k: int = Field(default=5, ge=1, le=20)


class RecommendationReport(StrictModel):
    disease_group: str
    entity_type: ProfileEntityType
    recommendations: list[RecommendationItem] = Field(default_factory=list)
    eligible_candidates: int = Field(ge=0)
    excluded_candidates: int = Field(ge=0)
    generated: bool = False
    warnings: list[str] = Field(default_factory=list)
    engine_version: str = "explainable-recommendation-v1"
