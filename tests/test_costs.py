from __future__ import annotations

import unittest
from decimal import Decimal

from medical_claims.models import ClaimCase, FieldStatus, FieldValue
from medical_claims.validation import validate_case


def derived(value: str, formula: str) -> FieldValue[Decimal]:
    return FieldValue[Decimal](
        value=Decimal(value),
        status=FieldStatus.DERIVED,
        derivation=formula,
    )


class CostSemanticsTest(unittest.TestCase):
    def make_case(self) -> ClaimCase:
        case = ClaimCase(case_id="cost-001")
        case.costs.total_amount = derived("4500", "source total")
        case.costs.policy_scope_amount = derived("4200", "source policy scope")
        case.costs.outside_policy_amount = derived("300", "source outside policy")
        case.costs.fund_payments.total_fund_payment = derived("3600", "source fund total")
        case.costs.personal_burden.within_policy_self_pay = derived("600", "4200 - 3600")
        case.costs.personal_burden.outside_policy_self_expense = derived("300", "source outside policy")
        case.costs.personal_burden.total_personal_burden = derived("900", "600 + 300")
        case.costs.payment_channels.personal_account_payment = derived("0", "source personal account")
        case.costs.payment_channels.family_account_payment = derived("0", "source family account")
        case.costs.payment_channels.cash_payment = derived("900", "source cash")
        return case

    def test_split_costs_reconcile(self) -> None:
        case = self.make_case()
        flags = validate_case(case)
        self.assertEqual(flags, [])
        self.assertEqual(case.costs.reconciliation.status.value, "passed")
        self.assertEqual(len(case.costs.reconciliation.checked_equations), 3)

    def test_personal_burden_mismatch_is_flagged(self) -> None:
        case = self.make_case()
        case.costs.personal_burden.total_personal_burden = derived("800", "bad fixture")
        flags = validate_case(case)
        self.assertIn("PERSONAL_BURDEN_SUM_MISMATCH", {flag.code for flag in flags})
        self.assertEqual(case.costs.reconciliation.status.value, "warning")

    def test_derived_field_requires_formula(self) -> None:
        with self.assertRaises(ValueError):
            FieldValue[Decimal](value=Decimal("1"), status=FieldStatus.DERIVED)

    def test_personal_burden_cannot_be_derived_from_payment_channels_only(self) -> None:
        case = ClaimCase(case_id="cost-002")
        case.costs.personal_burden.total_personal_burden = derived(
            "452", "personal_account_payment + cash_payment"
        )
        flags = validate_case(case)
        self.assertIn("PERSONAL_BURDEN_BASIS_INCOMPLETE", {flag.code for flag in flags})


if __name__ == "__main__":
    unittest.main()
