"""Reviewer declarations bound to immutable content fingerprints."""
from enum import Enum
import re
from typing import Any
from pydantic import Field, field_validator
from .models import StrictModel


class ReviewTarget(str, Enum):
    PAIN_REPORT = "pain_report"
    CARE_GUIDE = "care_guide"
    TREATMENT_QUALITY = "treatment_quality"
    RECOMMENDATION = "recommendation"
    COMBINED_DELIVERY = "combined_delivery"


class ReviewDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"


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


class FieldReviewDecision(str, Enum):
    CONFIRMED = "CONFIRMED"
    CORRECTED = "CORRECTED"
    UNRESOLVED = "UNRESOLVED"


class FieldReviewRequest(StrictModel):
    field_path: str = Field(min_length=1, max_length=300)
    decision: FieldReviewDecision
    reviewed_value: Any | None = None
    reason: str = Field(default="", max_length=2000)

    @field_validator("field_path")
    @classmethod
    def field_path_is_safe(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*(?:\[\d+\])?(?:\.[A-Za-z_][A-Za-z0-9_]*(?:\[\d+\])?)*",
            value,
        ):
            raise ValueError("字段路径不合法")
        return value


class FieldReviewRecord(FieldReviewRequest):
    review_id: str
    case_id: str
    field_label: str
    model_value: Any | None = None
    final_value: Any | None = None
    reviewer_user_id: str
    reviewer_name: str
    reviewed_at: str
    content_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_references: list[dict[str, Any]] = Field(default_factory=list)
