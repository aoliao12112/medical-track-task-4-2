"""第三问：个性化温度指引的稳定 JSON Schema。"""

from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from .models import ClaimCase, StrictModel
from .pain_models import PainPointReport


class ReviewStatus(str, Enum):
    DRAFT_PENDING_MEDICAL_REVIEW = "draft_pending_medical_review"
    REVIEWED = "reviewed"


class SafetyStatus(str, Enum):
    PASSED = "passed"
    REVIEW = "review"
    BLOCKED = "blocked"


class GenerationMethod(str, Enum):
    LLM = "llm"
    RULES_FALLBACK = "rules_fallback"


class CareSection(StrictModel):
    title: str
    content: str = Field(min_length=1)
    evidence_fields: list[str] = Field(default_factory=list)


class CareAction(StrictModel):
    action: str = Field(min_length=1)
    timing: str = "遵医嘱"
    reason: str = Field(min_length=1)
    evidence_fields: list[str] = Field(default_factory=list)


class CareGuideDraft(StrictModel):
    """LLM 只输出内容草稿；安全状态由本地引擎计算。"""

    case_id: str
    disease_topic: str
    opening: CareSection
    diagnosis_explanation: CareSection
    treatment_review: CareSection
    cost_explanation: CareSection
    recovery_plan: list[CareAction] = Field(min_length=1, max_length=8)
    psychological_support: CareSection
    follow_up_plan: list[CareAction] = Field(min_length=1, max_length=8)
    red_flags: list[str] = Field(default_factory=list, max_length=8)
    limitations: list[str] = Field(min_length=1, max_length=8)


class CareSafetyCheck(StrictModel):
    code: str
    status: SafetyStatus
    message: str
    field_paths: list[str] = Field(default_factory=list)


class CareGuideReport(StrictModel):
    case_id: str
    guide: CareGuideDraft
    knowledge_card_id: str | None = None
    knowledge_review_status: ReviewStatus = ReviewStatus.DRAFT_PENDING_MEDICAL_REVIEW
    generation_method: GenerationMethod
    safety_checks: list[CareSafetyCheck] = Field(default_factory=list)
    evidence_coverage: float = Field(ge=0, le=1)
    review_required: bool = True
    engine_version: str = "care-engine-v1"
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_case_id(self) -> "CareGuideReport":
        if self.case_id != self.guide.case_id:
            raise ValueError("报告 case_id 与指引 case_id 不一致")
        return self


class CareGuideRequest(StrictModel):
    case: ClaimCase
    pain_report: PainPointReport | None = None
    disease_topic: str | None = None
    use_llm: bool = True

    @model_validator(mode="after")
    def validate_case_link(self) -> "CareGuideRequest":
        if self.pain_report and self.pain_report.case_id != self.case.case_id:
            raise ValueError("痛点报告与结构化病例的 case_id 不一致")
        return self

