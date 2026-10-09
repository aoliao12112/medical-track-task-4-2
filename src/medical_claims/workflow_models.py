"""Minimal competition case metadata; medical schemas remain unchanged."""
from enum import Enum

from pydantic import Field

from medical_claims.models import StrictModel


class WorkflowStatus(str, Enum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    OCR_READY = "OCR_READY"
    STRUCTURED = "STRUCTURED"
    PAIN_READY = "PAIN_READY"
    CARE_READY = "CARE_READY"
    DELIVERY_READY = "DELIVERY_READY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    DELIVERED_MOCK = "DELIVERED_MOCK"
    FEEDBACK_RECEIVED = "FEEDBACK_RECEIVED"
    FOLLOWUP_PENDING = "FOLLOWUP_PENDING"
    RESOLVED = "RESOLVED"


class CreateCaseRequest(StrictModel):
    claim_event_id: str = Field(min_length=1, max_length=200)
    data_source: str = Field(min_length=1, max_length=200)
    consent_status: str = Field(min_length=1, max_length=100)
    deidentification_status: str = Field(min_length=1, max_length=100)


class WorkflowCase(CreateCaseRequest):
    case_id: str
    status: WorkflowStatus
    created_at: str
    updated_at: str
