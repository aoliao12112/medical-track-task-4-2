"""Persisted user feedback and deterministic service follow-up."""
from enum import Enum
from pydantic import Field, field_validator, model_validator
from .models import StrictModel


class FeedbackTarget(str, Enum):
    CARE_GUIDE = "care_guide"
    RECOMMENDATION = "recommendation"
    OVERALL_SERVICE = "overall_service"


class FeedbackCategory(str, Enum):
    CLEAR = "clear"
    NOT_CLEAR = "not_clear"
    NOT_PERSONALIZED = "not_personalized"
    COST_QUESTION = "cost_question"
    RECOVERY_QUESTION = "recovery_question"
    MEDICATION_QUESTION = "medication_question"
    RECOMMENDATION_QUESTION = "recommendation_question"
    CLAIM_PROGRESS = "claim_progress"
    CLAIM_MATERIAL = "claim_material"
    COVERAGE_RULE = "coverage_rule"
    VALUE_ADDED_SERVICE = "value_added_service"
    HUMAN_SERVICE_REQUEST = "human_service_request"
    OTHER = "other"


class FeedbackRequest(StrictModel):
    target_type: FeedbackTarget = FeedbackTarget.OVERALL_SERVICE
    rating: int = Field(ge=1, le=5, strict=True)
    helpful: bool = Field(strict=True)
    category: FeedbackCategory
    comment: str = Field(default="", max_length=4000)
    clarity_score: int | None = Field(default=None, ge=1, le=5, strict=True)
    reassurance_score: int | None = Field(default=None, ge=1, le=5, strict=True)
    actionability_score: int | None = Field(default=None, ge=1, le=5, strict=True)
    consent_to_contact: bool = Field(default=False, strict=True)


class FeedbackRecord(FeedbackRequest):
    feedback_id: str
    delivery_id: str
    case_id: str
    created_at: str


class FollowUpStatus(str, Enum):
    OPEN = "OPEN"
    PROCESSING = "PROCESSING"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class FollowUpSourceType(str, Enum):
    DELIVERY_FEEDBACK = "delivery_feedback"
    SERVICE_SESSION = "service_session"
    MANUAL = "manual"


class FollowUpPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class FollowUpTask(StrictModel):
    task_id: str
    case_id: str | None = None
    feedback_id: str | None = None
    service_analysis_id: str | None = None
    source_type: FollowUpSourceType = FollowUpSourceType.DELIVERY_FEEDBACK
    category: FeedbackCategory
    priority: FollowUpPriority = FollowUpPriority.MEDIUM
    status: FollowUpStatus = FollowUpStatus.OPEN
    assignee: str | None = None
    note: str
    resolution: str | None = None
    created_at: str
    resolved_at: str | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "FollowUpTask":
        if self.source_type == FollowUpSourceType.DELIVERY_FEEDBACK:
            if not self.feedback_id or self.service_analysis_id is not None:
                raise ValueError("delivery_feedback 待办必须且只能关联 feedback_id")
        elif self.source_type == FollowUpSourceType.SERVICE_SESSION:
            if not self.service_analysis_id or self.feedback_id is not None:
                raise ValueError("service_session 待办必须且只能关联 service_analysis_id")
        elif self.feedback_id is not None or self.service_analysis_id is not None:
            raise ValueError("manual 待办不能冒充反馈或会话分析来源")
        return self


class ResolveFollowUpRequest(StrictModel):
    resolution: str = Field(min_length=1, max_length=4000)

    @field_validator("resolution")
    @classmethod
    def resolution_not_blank(cls, value):
        if not value.strip():
            raise ValueError("resolution 不能为空")
        return value.strip()
