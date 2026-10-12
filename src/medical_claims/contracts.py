"""Frozen cross-role contracts for the four-portal workflow.

The V1 models in this module are integration boundaries.  Additive changes
require defaults; incompatible changes require a new schema version.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from .care_models import CareGuideDraft, CareGuideReport
from .models import StrictModel


CUSTOMER_DELIVERY_SCHEMA_V1 = "customer-delivery-v1"
CASE_TASK_SCHEMA_V1 = "case-task-v1"
MEDICAL_REVIEW_SCHEMA_V1 = "medical-review-v1"


class CustomerCareSectionV1(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=8000)


class CustomerCareActionV1(StrictModel):
    action: str = Field(min_length=1, max_length=1000)
    timing: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=2000)


class CustomerCareGuideV1(StrictModel):
    disease_topic: str | None = Field(default=None, max_length=200)
    opening: CustomerCareSectionV1 | None = None
    diagnosis_explanation: CustomerCareSectionV1 | None = None
    treatment_review: CustomerCareSectionV1 | None = None
    cost_explanation: CustomerCareSectionV1 | None = None
    recovery_plan: list[CustomerCareActionV1] = Field(default_factory=list, max_length=8)
    psychological_support: CustomerCareSectionV1 | None = None
    follow_up_plan: list[CustomerCareActionV1] = Field(default_factory=list, max_length=8)
    red_flags: list[str] = Field(default_factory=list, max_length=8)
    limitations: list[str] = Field(default_factory=list, max_length=8)


class CustomerCarePayloadV1(StrictModel):
    guide: CustomerCareGuideV1


class CustomerAttentionItemV1(StrictModel):
    pain_point_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=3000)
    suggestion: str = Field(min_length=1, max_length=2000)
    severity: Literal["low", "medium", "high", "critical"]


class CustomerQualityObservationV1(StrictModel):
    scope: Literal["current_case"] = "current_case"
    summary: str = Field(min_length=1, max_length=4000)
    disclaimer: str = "单次病例观察不代表医院或医生的长期质量。"


class CustomerRecommendationReasonV1(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    explanation: str = Field(default="", max_length=2000)


class CustomerRecommendationSourceV1(StrictModel):
    name: str | None = Field(default=None, max_length=500)
    url: str | None = Field(default=None, max_length=2000)
    summary: str = Field(default="", max_length=3000)


class CustomerRecommendationItemV1(StrictModel):
    item_type: Literal["HOSPITAL", "DOCTOR", "TREATMENT"]
    entity_name: str = Field(min_length=1, max_length=500)
    city: str | None = Field(default=None, max_length=100)
    department: str | None = Field(default=None, max_length=200)
    reasons: list[CustomerRecommendationReasonV1] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    sources: list[CustomerRecommendationSourceV1] = Field(default_factory=list)


class CustomerRecommendationReportV1(StrictModel):
    recommendations: list[CustomerRecommendationItemV1] = Field(default_factory=list)


class CustomerDeliveryContentV1(StrictModel):
    care_guide: CustomerCarePayloadV1 | None = None
    attention_items: list[CustomerAttentionItemV1] = Field(default_factory=list)
    quality_observation: CustomerQualityObservationV1 | None = None
    recommendation: list[CustomerRecommendationReportV1] = Field(default_factory=list)
    recommendation_unavailable_reason: str | None = Field(default=None, max_length=2000)


class CustomerDeliveryV1(StrictModel):
    schema_version: Literal["customer-delivery-v1"] = CUSTOMER_DELIVERY_SCHEMA_V1
    delivery_id: str = Field(min_length=1, max_length=200)
    case_id: str = Field(min_length=1, max_length=200)
    created_at: str
    status: str = Field(min_length=1, max_length=100)
    recommendation_included: bool
    content: CustomerDeliveryContentV1


class CaseTaskRoleV1(str, Enum):
    CLAIM = "CLAIM"
    MEDICAL = "MEDICAL"
    SERVICE = "SERVICE"


class CaseTaskStatusV1(str, Enum):
    OPEN = "OPEN"
    PROCESSING = "PROCESSING"
    RESOLVED = "RESOLVED"


class CaseTaskSourceV1(str, Enum):
    SERVICE_CONSULTATION = "SERVICE_CONSULTATION"
    CUSTOMER_FEEDBACK = "CUSTOMER_FEEDBACK"
    MANUAL_HANDOFF = "MANUAL_HANDOFF"


class CaseTaskCategoryV1(str, Enum):
    CLAIM_PROGRESS = "claim_progress"
    CLAIM_MATERIAL = "claim_material"
    COVERAGE_RULE = "coverage_rule"
    MEDICAL_COST = "medical_cost"
    CLAIM_COST = "claim_cost"
    RECOVERY_QUESTION = "recovery_question"
    MEDICATION_QUESTION = "medication_question"
    RECOMMENDATION_QUESTION = "recommendation_question"
    VALUE_ADDED_SERVICE = "value_added_service"
    HUMAN_SERVICE_REQUEST = "human_service_request"
    OTHER = "other"


class CaseTaskV1(StrictModel):
    schema_version: Literal["case-task-v1"] = CASE_TASK_SCHEMA_V1
    task_id: str = Field(min_length=1, max_length=200)
    case_id: str = Field(min_length=1, max_length=200)
    source_type: CaseTaskSourceV1
    source_id: str = Field(min_length=1, max_length=200)
    target_role: CaseTaskRoleV1
    assignee_user_id: str | None = Field(default=None, max_length=200)
    category: CaseTaskCategoryV1
    priority: Literal["LOW", "MEDIUM", "HIGH", "URGENT"] = "MEDIUM"
    status: CaseTaskStatusV1 = CaseTaskStatusV1.OPEN
    note: str = Field(default="", max_length=4000)
    created_by: str = Field(min_length=1, max_length=200)
    created_at: str
    claimed_at: str | None = None
    resolved_at: str | None = None
    resolution: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "CaseTaskV1":
        if self.status == CaseTaskStatusV1.PROCESSING and not self.claimed_at:
            raise ValueError("PROCESSING 任务必须记录 claimed_at")
        if self.status == CaseTaskStatusV1.RESOLVED and (not self.resolved_at or not self.resolution):
            raise ValueError("RESOLVED 任务必须记录 resolved_at 和 resolution")
        return self


class PainItemDecisionV1(str, Enum):
    SHOW = "SHOW"
    EDIT = "EDIT"
    HIDE = "HIDE"


class PainItemReviewRequestV1(StrictModel):
    decision: PainItemDecisionV1
    title: str | None = Field(default=None, max_length=300)
    summary: str | None = Field(default=None, max_length=3000)
    suggestion: str | None = Field(default=None, max_length=2000)
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_decision(self) -> "PainItemReviewRequestV1":
        edited = any(value and value.strip() for value in (self.title, self.summary, self.suggestion))
        if self.decision == PainItemDecisionV1.EDIT and not edited:
            raise ValueError("EDIT 决策至少要填写一个修订字段")
        if self.decision != PainItemDecisionV1.EDIT and edited:
            raise ValueError("只有 EDIT 决策可以携带修订字段")
        if self.decision == PainItemDecisionV1.HIDE and not self.note.strip():
            raise ValueError("HIDE 决策必须说明原因")
        return self


class PainItemReviewRecordV1(PainItemReviewRequestV1):
    schema_version: Literal["medical-review-v1"] = MEDICAL_REVIEW_SCHEMA_V1
    review_id: str
    case_id: str
    pain_point_id: str
    content_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    original_title: str
    original_summary: str
    original_suggestion: str
    severity: Literal["low", "medium", "high", "critical"]
    reviewer_user_id: str
    reviewer_name: str
    reviewed_at: str


class CareRevisionRequestV1(StrictModel):
    base_content_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    guide: CareGuideDraft
    note: str = Field(default="", max_length=4000)


class CareRevisionRecordV1(StrictModel):
    schema_version: Literal["medical-review-v1"] = MEDICAL_REVIEW_SCHEMA_V1
    revision_id: str
    case_id: str
    target_type: Literal["care_revision"] = "care_revision"
    decision: Literal["CORRECTED"] = "CORRECTED"
    reviewer: str
    reviewer_user_id: str
    note: str = ""
    base_content_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    original_report: CareGuideReport
    revised_report: CareGuideReport
    created_at: str


class MedicalFinalGateV1(StrictModel):
    schema_version: Literal["medical-review-v1"] = MEDICAL_REVIEW_SCHEMA_V1
    decision: Literal["READY_FOR_FINAL_REVIEW", "NOT_READY", "BLOCKED"]
    reasons: list[str] = Field(default_factory=list)
    required_modules: list[str] = Field(default_factory=list)
    approved_modules: list[str] = Field(default_factory=list)
    content_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
