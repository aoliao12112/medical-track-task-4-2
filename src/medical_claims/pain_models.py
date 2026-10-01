"""第二问：多维痛点识别的稳定 JSON Schema。"""

from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from .models import ClaimCase, Evidence, StrictModel


class PainDimension(str, Enum):
    CLINICAL = "clinical"
    COST = "cost"
    EXPERIENCE = "experience"


class PainSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PainPointType(str, Enum):
    MEDICATION_DIAGNOSIS_REVIEW = "MEDICATION_DIAGNOSIS_REVIEW"
    DUPLICATE_MEDICATION_REVIEW = "DUPLICATE_MEDICATION_REVIEW"
    ABNORMAL_EXAMINATION_FOLLOWUP = "ABNORMAL_EXAMINATION_FOLLOWUP"
    HIGH_OUTSIDE_POLICY_RATIO = "HIGH_OUTSIDE_POLICY_RATIO"
    HIGH_PERSONAL_BURDEN_RATIO = "HIGH_PERSONAL_BURDEN_RATIO"
    COST_RECONCILIATION_ANOMALY = "COST_RECONCILIATION_ANOMALY"
    DIAGNOSIS_TERM_HARD_TO_UNDERSTAND = "DIAGNOSIS_TERM_HARD_TO_UNDERSTAND"
    MEDICATION_INSTRUCTION_INCOMPLETE = "MEDICATION_INSTRUCTION_INCOMPLETE"
    CARE_GUIDANCE_INCOMPLETE = "CARE_GUIDANCE_INCOMPLETE"


class DetectorMethod(str, Enum):
    RULE = "rule"
    LLM = "llm"
    RULE_LLM_HYBRID = "rule_llm_hybrid"


class DetectorInfo(StrictModel):
    method: DetectorMethod
    version: str
    rule_id: str | None = None


class PainPoint(StrictModel):
    pain_point_id: str
    dimension: PainDimension
    type: PainPointType
    title: str
    severity: PainSeverity
    severity_score: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    description: str
    evidence: list[Evidence] = Field(min_length=1)
    related_fields: list[str] = Field(default_factory=list)
    recommended_action: str
    review_required: bool = True
    detector: DetectorInfo


class PainPointSummary(StrictModel):
    total: int = Field(ge=0)
    clinical: int = Field(ge=0)
    cost: int = Field(ge=0)
    experience: int = Field(ge=0)
    high_priority: int = Field(ge=0)


class PainPointReport(StrictModel):
    case_id: str
    summary: PainPointSummary
    pain_points: list[PainPoint] = Field(default_factory=list)
    engine_version: str = "pain-engine-v2"
    analysis_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_summary(self) -> "PainPointReport":
        expected = {
            "total": len(self.pain_points),
            "clinical": sum(p.dimension == PainDimension.CLINICAL for p in self.pain_points),
            "cost": sum(p.dimension == PainDimension.COST for p in self.pain_points),
            "experience": sum(p.dimension == PainDimension.EXPERIENCE for p in self.pain_points),
            "high_priority": sum(
                p.severity in {PainSeverity.HIGH, PainSeverity.CRITICAL}
                for p in self.pain_points
            ),
        }
        if self.summary.model_dump() != expected:
            raise ValueError("summary 与 pain_points 的实际统计不一致")
        return self


class SemanticPainPointBatch(StrictModel):
    """LLM 只输出语义候选；汇总和去重由本地引擎负责。"""

    case_id: str
    pain_points: list[PainPoint] = Field(default_factory=list)


class PainAnalysisRequest(StrictModel):
    case: ClaimCase
    use_llm: bool = True
