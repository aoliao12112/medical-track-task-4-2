"""Prototype reviewer declarations, bound to concrete delivery content."""
from enum import Enum
from pydantic import Field, field_validator
from .models import StrictModel


class ReviewTarget(str, Enum):
    CARE_GUIDE = "care_guide"
    RECOMMENDATION = "recommendation"
    COMBINED_DELIVERY = "combined_delivery"


class ReviewDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ReviewRequest(StrictModel):
    target_type: ReviewTarget = ReviewTarget.COMBINED_DELIVERY
    reviewer: str = Field(min_length=1, max_length=200)
    decision: ReviewDecision
    note: str = Field(default="", max_length=4000)
    content_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("reviewer")
    @classmethod
    def reviewer_not_blank(cls, value):
        if not value.strip():
            raise ValueError("reviewer 不能为空")
        return value.strip()


class ReviewRecord(ReviewRequest):
    review_id: str
    case_id: str
    created_at: str
