"""第一问的领域模型与 JSON Schema 定义。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """禁止静默接受未知字段，避免接口变化污染结果。"""

    model_config = ConfigDict(extra="forbid")


class FieldStatus(str, Enum):
    EXTRACTED = "extracted"
    DERIVED = "derived"
    NOT_APPLICABLE = "not_applicable"
    NOT_PRESENT = "not_present_in_document"
    CONFLICT = "conflict"
    REVIEW_REQUIRED = "review_required"


class Evidence(StrictModel):
    document_id: str
    page: int | None = Field(default=None, ge=1)
    text: str = ""
    bbox: tuple[float, float, float, float] | None = None


ValueT = TypeVar("ValueT")


class FieldValue(StrictModel, Generic[ValueT]):
    """字段级值；置信度与原文证据必须跟随具体字段。"""

    value: ValueT | None = None
    normalized_value: ValueT | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: FieldStatus = FieldStatus.NOT_PRESENT
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    derivation: str | None = None

    @model_validator(mode="after")
    def validate_extracted_value(self) -> "FieldValue[ValueT]":
        if self.status == FieldStatus.EXTRACTED:
            if self.value is None:
                raise ValueError("status=extracted 时 value 不能为空")
            if self.confidence is None:
                raise ValueError("status=extracted 时必须提供字段置信度")
            if not self.evidence:
                raise ValueError("status=extracted 时必须提供原文证据")
        if self.status == FieldStatus.DERIVED:
            if self.value is None:
                raise ValueError("status=derived 时 value 不能为空")
            if not self.derivation:
                raise ValueError("status=derived 时必须说明计算过程")
        return self


class EncounterType(str, Enum):
    INPATIENT = "inpatient"
    OUTPATIENT = "outpatient"
    EMERGENCY = "emergency"
    UNKNOWN = "unknown"


class Hospitalization(StrictModel):
    admission_date: FieldValue[date] = Field(default_factory=FieldValue[date])
    discharge_date: FieldValue[date] = Field(default_factory=FieldValue[date])
    length_of_stay_days: FieldValue[int] = Field(default_factory=FieldValue[int])
    department: FieldValue[str] = Field(default_factory=FieldValue[str])


class DiagnosisType(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    ADMISSION = "admission"
    DISCHARGE = "discharge"
    HISTORY = "history"
    UNKNOWN = "unknown"


class Certainty(str, Enum):
    CONFIRMED = "confirmed"
    SUSPECTED = "suspected"
    RULED_OUT = "ruled_out"
    UNKNOWN = "unknown"


class Diagnosis(StrictModel):
    diagnosis_id: str
    name: FieldValue[str]
    code: FieldValue[str] = Field(default_factory=FieldValue[str])
    diagnosis_type: DiagnosisType = DiagnosisType.UNKNOWN
    certainty: Certainty = Certainty.UNKNOWN


class Procedure(StrictModel):
    procedure_id: str
    name: FieldValue[str]
    performed_date: FieldValue[date] = Field(default_factory=FieldValue[date])
    body_site: FieldValue[str] = Field(default_factory=FieldValue[str])
    is_historical: bool = False


class Medication(StrictModel):
    medication_id: str
    name: FieldValue[str]
    dose: FieldValue[str] = Field(default_factory=FieldValue[str])
    frequency: FieldValue[str] = Field(default_factory=FieldValue[str])
    route: FieldValue[str] = Field(default_factory=FieldValue[str])
    duration: FieldValue[str] = Field(default_factory=FieldValue[str])
    usage_condition: FieldValue[str] = Field(default_factory=FieldValue[str])
    max_daily_dose: FieldValue[str] = Field(default_factory=FieldValue[str])
    additional_instructions: FieldValue[str] = Field(default_factory=FieldValue[str])


class ExaminationCategory(str, Enum):
    LABORATORY = "laboratory"
    IMAGING = "imaging"
    PATHOLOGY = "pathology"
    FUNCTIONAL = "functional"
    OTHER = "other"


class AbnormalFlag(str, Enum):
    HIGH = "high"
    LOW = "low"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    ABNORMAL = "abnormal"
    NORMAL = "normal"
    UNKNOWN = "unknown"


class ExaminationResult(StrictModel):
    item_name: FieldValue[str]
    value: FieldValue[str]
    unit: FieldValue[str] = Field(default_factory=FieldValue[str])
    reference_range: FieldValue[str] = Field(default_factory=FieldValue[str])
    abnormal_flag: AbnormalFlag = AbnormalFlag.UNKNOWN


class Examination(StrictModel):
    examination_id: str
    category: ExaminationCategory = ExaminationCategory.OTHER
    name: FieldValue[str]
    performed_date: FieldValue[date] = Field(default_factory=FieldValue[date])
    results: list[ExaminationResult] = Field(default_factory=list)
    conclusion: FieldValue[str] = Field(default_factory=FieldValue[str])


class CostItem(StrictModel):
    category: FieldValue[str]
    amount: FieldValue[Decimal]


class FundPayments(StrictModel):
    """由医保基金、救助或商业保险等第三方承担的金额。"""

    basic_pool_payment: FieldValue[Decimal] = Field(default_factory=FieldValue[Decimal])
    major_illness_payment: FieldValue[Decimal] = Field(default_factory=FieldValue[Decimal])
    medical_assistance_payment: FieldValue[Decimal] = Field(default_factory=FieldValue[Decimal])
    civil_servant_subsidy: FieldValue[Decimal] = Field(default_factory=FieldValue[Decimal])
    supplementary_insurance_payment: FieldValue[Decimal] = Field(default_factory=FieldValue[Decimal])
    commercial_insurance_payment: FieldValue[Decimal] = Field(default_factory=FieldValue[Decimal])
    other_payment: FieldValue[Decimal] = Field(default_factory=FieldValue[Decimal])
    total_fund_payment: FieldValue[Decimal] = Field(default_factory=FieldValue[Decimal])


class PersonalBurden(StrictModel):
    """费用责任属性：医保目录内自付与目录外自费不得合并。"""

    within_policy_self_pay: FieldValue[Decimal] = Field(default_factory=FieldValue[Decimal])
    outside_policy_self_expense: FieldValue[Decimal] = Field(default_factory=FieldValue[Decimal])
    total_personal_burden: FieldValue[Decimal] = Field(default_factory=FieldValue[Decimal])


class PaymentChannels(StrictModel):
    """实际付款渠道，不等同于费用责任属性。"""

    personal_account_payment: FieldValue[Decimal] = Field(default_factory=FieldValue[Decimal])
    family_account_payment: FieldValue[Decimal] = Field(default_factory=FieldValue[Decimal])
    cash_payment: FieldValue[Decimal] = Field(default_factory=FieldValue[Decimal])


class CostRatios(StrictModel):
    within_policy_self_pay_ratio: FieldValue[float] = Field(default_factory=FieldValue[float])
    outside_policy_self_expense_ratio: FieldValue[float] = Field(default_factory=FieldValue[float])
    total_personal_burden_ratio: FieldValue[float] = Field(default_factory=FieldValue[float])


class ReconciliationStatus(str, Enum):
    NOT_CHECKED = "not_checked"
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class CostReconciliation(StrictModel):
    status: ReconciliationStatus = ReconciliationStatus.NOT_CHECKED
    checked_equations: list[str] = Field(default_factory=list)
    difference: Decimal | None = None
    warnings: list[str] = Field(default_factory=list)


class Costs(StrictModel):
    currency: str = "CNY"
    total_amount: FieldValue[Decimal] = Field(default_factory=FieldValue[Decimal])
    policy_scope_amount: FieldValue[Decimal] = Field(default_factory=FieldValue[Decimal])
    outside_policy_amount: FieldValue[Decimal] = Field(default_factory=FieldValue[Decimal])
    fund_payments: FundPayments = Field(default_factory=FundPayments)
    personal_burden: PersonalBurden = Field(default_factory=PersonalBurden)
    payment_channels: PaymentChannels = Field(default_factory=PaymentChannels)
    ratios: CostRatios = Field(default_factory=CostRatios)
    breakdown: list[CostItem] = Field(default_factory=list)
    reconciliation: CostReconciliation = Field(default_factory=CostReconciliation)


class EventType(str, Enum):
    VISIT = "visit"
    ADMISSION = "admission"
    DIAGNOSIS = "diagnosis"
    TEST = "test"
    MEDICATION = "medication"
    PROCEDURE = "procedure"
    DISCHARGE = "discharge"
    OTHER = "other"


class MedicalEvent(StrictModel):
    event_id: str
    event_type: EventType
    event_time: date | datetime | None = None
    description: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence] = Field(default_factory=list)
    related_entity_ids: list[str] = Field(default_factory=list)


class FlagSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class QualityFlag(StrictModel):
    code: str
    severity: FlagSeverity
    message: str
    field_path: str | None = None
    document_id: str | None = None


class ClaimCase(StrictModel):
    """第一问的案件级标准输出。"""

    case_id: str
    encounter_type: EncounterType = EncounterType.UNKNOWN
    hospitalization: Hospitalization = Field(default_factory=Hospitalization)
    diagnoses: list[Diagnosis] = Field(default_factory=list)
    procedures: list[Procedure] = Field(default_factory=list)
    medications: list[Medication] = Field(default_factory=list)
    examinations: list[Examination] = Field(default_factory=list)
    costs: Costs = Field(default_factory=Costs)
    events: list[MedicalEvent] = Field(default_factory=list)
    quality_flags: list[QualityFlag] = Field(default_factory=list)
