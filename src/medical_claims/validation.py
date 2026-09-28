"""不依赖模型的确定性质量校验。"""

from __future__ import annotations

from decimal import Decimal

from .models import ClaimCase, FieldStatus, FlagSeverity, QualityFlag


def validate_case(case: ClaimCase) -> list[QualityFlag]:
    flags: list[QualityFlag] = []

    admission = case.hospitalization.admission_date.value
    discharge = case.hospitalization.discharge_date.value
    stay_days = case.hospitalization.length_of_stay_days.value

    if admission and discharge and discharge < admission:
        flags.append(
            QualityFlag(
                code="INVALID_DATE_ORDER",
                severity=FlagSeverity.ERROR,
                message="出院日期早于入院日期",
                field_path="hospitalization.discharge_date",
            )
        )

    if admission and discharge and stay_days is not None:
        calculated = (discharge - admission).days
        if calculated != stay_days:
            flags.append(
                QualityFlag(
                    code="LOS_MISMATCH",
                    severity=FlagSeverity.WARNING,
                    message=f"住院天数为 {stay_days}，日期差为 {calculated}",
                    field_path="hospitalization.length_of_stay_days",
                )
            )

    total = case.costs.total_amount.value
    insurance = case.costs.insurance_payment.value
    self_payment = case.costs.self_payment.value
    if total is not None and insurance is not None and self_payment is not None:
        if abs(total - insurance - self_payment) > Decimal("0.01"):
            flags.append(
                QualityFlag(
                    code="PAYMENT_SUM_MISMATCH",
                    severity=FlagSeverity.WARNING,
                    message="医保支付与自费金额之和不等于总费用",
                    field_path="costs",
                )
            )

    if case.encounter_type.value in {"outpatient", "emergency"}:
        if case.hospitalization.length_of_stay_days.status == FieldStatus.NOT_PRESENT:
            flags.append(
                QualityFlag(
                    code="LOS_SHOULD_BE_NOT_APPLICABLE",
                    severity=FlagSeverity.INFO,
                    message="门急诊病例的住院天数应标记为 not_applicable",
                    field_path="hospitalization.length_of_stay_days",
                )
            )

    return flags

