"""Persisted user feedback and deterministic service follow-up."""
from enum import Enum
from pydantic import Field, field_validator
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
    OTHER = "other"


class FeedbackRequest(StrictModel):
    target_type: FeedbackTarget = FeedbackTarget.OVERALL_SERVICE
    rating: int = Field(ge=1, le=5, strict=True)
    helpful: bool = Field(strict=True)
    category: FeedbackCategory
    comment: str = Field(default="", max_length=4000)


class FeedbackRecord(FeedbackRequest):
    feedback_id: str
    delivery_id: str
    case_id: str
    created_at: str


class FollowUpStatus(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class FollowUpTask(StrictModel):
    task_id: str
    case_id: str
    feedback_id: str
    category: FeedbackCategory
    status: FollowUpStatus = FollowUpStatus.OPEN
    note: str
    resolution: str | None = None
    created_at: str
    resolved_at: str | None = None


class ResolveFollowUpRequest(StrictModel):
    resolution: str = Field(min_length=1, max_length=4000)

    @field_validator("resolution")
    @classmethod
    def resolution_not_blank(cls, value):
        if not value.strip():
            raise ValueError("resolution 不能为空")
        return value.strip()
