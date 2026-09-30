"""不依赖模型的确定性质量校验。"""

from __future__ import annotations

from decimal import Decimal

from .models import (
    ClaimCase,
    FieldStatus,
    FlagSeverity,
    QualityFlag,
    ReconciliationStatus,
)


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

    cost_flags: list[QualityFlag] = []
    checked_equations: list[str] = []
    differences: list[Decimal] = []

    total = case.costs.total_amount.value
    fund_total = case.costs.fund_payments.total_fund_payment.value
    personal_account = case.costs.payment_channels.personal_account_payment.value
    family_account = case.costs.payment_channels.family_account_payment.value
    cash = case.costs.payment_channels.cash_payment.value
    if None not in {total, fund_total, personal_account, family_account, cash}:
        difference = total - fund_total - personal_account - family_account - cash
        checked_equations.append("total_amount = total_fund_payment + personal_account_payment + family_account_payment + cash_payment")
        differences.append(difference)
        if abs(difference) > Decimal("0.01"):
            cost_flags.append(
                QualityFlag(
                    code="PAYMENT_CHANNEL_SUM_MISMATCH",
                    severity=FlagSeverity.WARNING,
                    message="基金支付与各实际付款渠道之和不等于费用总额",
                    field_path="costs.payment_channels",
                )
            )

    within_self_pay = case.costs.personal_burden.within_policy_self_pay.value
    outside_self_expense = case.costs.personal_burden.outside_policy_self_expense.value
    burden_total = case.costs.personal_burden.total_personal_burden.value
    if None not in {within_self_pay, outside_self_expense, burden_total}:
        difference = burden_total - within_self_pay - outside_self_expense
        checked_equations.append("total_personal_burden = within_policy_self_pay + outside_policy_self_expense")
        differences.append(difference)
        if abs(difference) > Decimal("0.01"):
            cost_flags.append(
                QualityFlag(
                    code="PERSONAL_BURDEN_SUM_MISMATCH",
                    severity=FlagSeverity.WARNING,
                    message="个人负担合计不等于个人自付与个人自费之和",
                    field_path="costs.personal_burden",
                )
            )

    if burden_total is not None and (within_self_pay is None or outside_self_expense is None):
        if case.costs.personal_burden.total_personal_burden.status == FieldStatus.DERIVED:
            cost_flags.append(
                QualityFlag(
                    code="PERSONAL_BURDEN_BASIS_INCOMPLETE",
                    severity=FlagSeverity.WARNING,
                    message="个人负担合计缺少个人自付或个人自费依据，不得仅按付款渠道反推",
                    field_path="costs.personal_burden.total_personal_burden",
                )
            )

    outside_policy = case.costs.outside_policy_amount.value
    if outside_policy is not None and outside_self_expense is not None:
        difference = outside_policy - outside_self_expense
        checked_equations.append("outside_policy_amount = outside_policy_self_expense")
        differences.append(difference)
        if abs(difference) > Decimal("0.01"):
            cost_flags.append(
                QualityFlag(
                    code="OUTSIDE_POLICY_AMOUNT_MISMATCH",
                    severity=FlagSeverity.WARNING,
                    message="目录外费用与个人自费金额不一致",
                    field_path="costs.outside_policy_amount",
                )
            )

    flags.extend(cost_flags)
    case.costs.reconciliation.checked_equations = checked_equations
    case.costs.reconciliation.warnings = [flag.message for flag in cost_flags]
    if checked_equations:
        case.costs.reconciliation.status = (
            ReconciliationStatus.WARNING if cost_flags else ReconciliationStatus.PASSED
        )
        case.costs.reconciliation.difference = max(differences, key=abs)
    else:
        case.costs.reconciliation.status = ReconciliationStatus.NOT_CHECKED
        case.costs.reconciliation.difference = None

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
