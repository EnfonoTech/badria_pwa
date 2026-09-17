# Copyright (c) 2026, Enfono and Contributors
# See license.txt
#
# The settlement formula is tested by stubbing the data-fetch helpers rather
# than fabricating full HRMS payroll fixtures (Salary Structure, Salary
# Structure Assignment, Attendance, Leave Allocation, Holiday List, ...) —
# that machinery belongs to HRMS's own test suite. What we own and must
# verify is the settlement arithmetic that combines those numbers.

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate

from badria_pwa.api.final_settlement import _compute_settlement, _get_attendance


class IntegrationTestFinalSettlement(FrappeTestCase):
    def test_settlement_formula_matches_client_reference_sheet(self):
        # Reproduces the client's own reference settlement sheet exactly:
        # Basic 2000, 180 days worked, 6422 bags produced, 362 Other Additions,
        # 10000 already advanced -> Balance Due 13650.
        doc = frappe._dict(
            employee="_TEST-EMP-001",
            settlement_type="Production",
            from_date=getdate("2026-01-01"),
            to_date=getdate("2026-06-30"),
            minimum_production_per_day=20,
            incentive_rate=4,
            below_minimum_incentive_amount=0,
            other_allowances=0,
            other_additions=362,
            other_deductions=0,
        )

        with (
            patch("badria_pwa.api.final_settlement.frappe.db.exists", return_value=True),
            patch(
                "badria_pwa.api.final_settlement._get_salary_structure_assignment",
                return_value=("Test Structure", 2000),
            ),
            patch("badria_pwa.api.final_settlement._get_attendance", return_value=180),
            patch("badria_pwa.api.final_settlement._get_total_production", return_value=6422),
            patch(
                "badria_pwa.api.final_settlement._get_below_minimum_production",
                return_value=(0, 0),
            ),
            patch("badria_pwa.api.final_settlement._get_total_advance_paid", return_value=10000),
            patch("badria_pwa.api.final_settlement._get_total_payment_entry_paid", return_value=0),
        ):
            result = _compute_settlement(doc)

        self.assertEqual(result["working_days"], 180.0)
        # total_basic_salary = 2000 * (180 / 30) = 12,000
        self.assertEqual(result["total_basic_salary"], 12000.0)
        # minimum_required_production = 180 * 20 = 3600
        self.assertEqual(result["minimum_required_production"], 3600.0)
        # additional_production = 6422 - 3600 = 2822
        self.assertEqual(result["additional_production"], 2822.0)
        # incentive_amount = 2822 * 4 = 11,288
        self.assertEqual(result["incentive_amount"], 11288.0)
        # gross_settlement = 12000 + 11288 + 0 + 0 + 362 - 0 = 23,650
        self.assertEqual(result["gross_settlement"], 23650.0)
        # balance_payable = 23650 - 10000 - 0 = 13,650
        self.assertEqual(result["balance_payable"], 13650.0)
        self.assertEqual(result["settlement_status"], "Calculated")

    def test_additional_production_floors_at_zero(self):
        doc = frappe._dict(
            employee="_TEST-EMP-001",
            settlement_type="Production",
            from_date=getdate("2026-01-01"),
            to_date=getdate("2026-06-30"),
            minimum_production_per_day=20,
            incentive_rate=4,
            below_minimum_incentive_amount=0,
            other_allowances=0,
            other_additions=0,
            other_deductions=0,
        )

        with (
            patch("badria_pwa.api.final_settlement.frappe.db.exists", return_value=True),
            patch(
                "badria_pwa.api.final_settlement._get_salary_structure_assignment",
                return_value=("Test Structure", 3000),
            ),
            patch("badria_pwa.api.final_settlement._get_attendance", return_value=150),
            patch(
                "badria_pwa.api.final_settlement._get_total_production",
                return_value=1000,  # well under 150*20 = 3000
            ),
            patch(
                "badria_pwa.api.final_settlement._get_below_minimum_production",
                return_value=(0, 0),
            ),
            patch("badria_pwa.api.final_settlement._get_total_advance_paid", return_value=0),
            patch("badria_pwa.api.final_settlement._get_total_payment_entry_paid", return_value=0),
        ):
            result = _compute_settlement(doc)

        self.assertEqual(result["additional_production"], 0.0)
        self.assertEqual(result["incentive_amount"], 0.0)

    def test_below_minimum_days_do_not_reduce_working_days(self):
        # 2 of the 30 present days have a Production Entry recorded below the
        # 20/day minimum. Below-minimum days are tracked (and above_minimum_days
        # reported for reference) but do NOT reduce working_days - only actual
        # Attendance absences do that. The below_minimum_incentive_amount is a
        # manual entry that IS deducted directly from gross_settlement (it's
        # a deduction, not a separate incentive record) - but it must NOT
        # reduce total_basic_salary, which always reports the pure
        # basic/30*working_days formula.
        doc = frappe._dict(
            employee="_TEST-EMP-001",
            settlement_type="Production",
            from_date=getdate("2026-01-01"),
            to_date=getdate("2026-01-31"),
            minimum_production_per_day=20,
            incentive_rate=4,
            below_minimum_incentive_amount=50,  # manually entered by HR
            other_allowances=0,
            other_additions=0,
            other_deductions=0,
        )

        with (
            patch("badria_pwa.api.final_settlement.frappe.db.exists", return_value=True),
            patch(
                "badria_pwa.api.final_settlement._get_salary_structure_assignment",
                return_value=("Test Structure", 3000),
            ),
            patch("badria_pwa.api.final_settlement._get_attendance", return_value=30),
            patch("badria_pwa.api.final_settlement._get_total_production", return_value=700),
            patch(
                "badria_pwa.api.final_settlement._get_below_minimum_production",
                return_value=(2, 18),  # 2 days, 18 total units produced on those days
            ),
            patch("badria_pwa.api.final_settlement._get_total_advance_paid", return_value=0),
            patch("badria_pwa.api.final_settlement._get_total_payment_entry_paid", return_value=0),
        ):
            result = _compute_settlement(doc)

        self.assertEqual(result["below_minimum_days"], 2)
        self.assertEqual(result["below_minimum_quantity"], 18)
        # above_minimum_days = 30 - 2 = 28 (informational only)
        self.assertEqual(result["above_minimum_days"], 28.0)
        # working_days stays the full 30 - below-minimum does not reduce it
        self.assertEqual(result["working_days"], 30.0)
        # total_basic_salary = 3000 * (30/30) = 3000 (below-minimum does NOT apply here)
        self.assertEqual(result["total_basic_salary"], 3000.0)
        # minimum_required_production = 30 * 20 = 600; additional = 700-600=100
        self.assertEqual(result["additional_production"], 100.0)
        # incentive_amount = 100 * 4 = 400
        self.assertEqual(result["incentive_amount"], 400.0)
        # gross_settlement = 3000 + 400 + 0 + 0 - 0 - 50 (below-minimum deduction) = 3350
        self.assertEqual(result["gross_settlement"], 3350.0)
        self.assertEqual(result["balance_payable"], 3350.0)

    def test_sales_settlement_formula_matches_client_reference_sheet(self):
        # Reproduces the client's Sales employee reference settlement sheet
        # (Employee SLS_0008_5) exactly: Basic 2000, 243 days worked,
        # 1,412,361.26 cash collected at 15% VAT, 4% fixed + 1% performance
        # incentive on Net Sales, 215 Other Additions, 44822 already paid
        # -> Balance Due 33,000.01.
        doc = frappe._dict(
            employee="_TEST-EMP-SLS-001",
            settlement_type="Sales",
            from_date=getdate("2025-09-22"),
            to_date=getdate("2026-05-22"),
            total_cash_collected=1412361.26,
            vat_percentage=15,
            fixed_incentive_percentage=4,
            performance_incentive_percentage=1,
            other_allowances=0,
            other_additions=215,
            other_deductions=0,
        )

        with (
            patch("badria_pwa.api.final_settlement.frappe.db.exists", return_value=True),
            patch(
                "badria_pwa.api.final_settlement._get_salary_structure_assignment",
                return_value=("Test Structure", 2000),
            ),
            patch("badria_pwa.api.final_settlement._get_attendance", return_value=243),
            patch("badria_pwa.api.final_settlement._get_total_advance_paid", return_value=44822),
            patch("badria_pwa.api.final_settlement._get_total_payment_entry_paid", return_value=0),
        ):
            result = _compute_settlement(doc)

        # total_basic_salary = 2000 * (243 / 30) = 16,200
        self.assertEqual(result["total_basic_salary"], 16200.0)
        # net_sales = 1,412,361.26 / 1.15 = 1,228,140.23
        self.assertEqual(result["net_sales"], 1228140.23)
        # vat_amount = 1,412,361.26 - 1,228,140.23 = 184,221.03
        self.assertAlmostEqual(result["vat_amount"], 184221.03, places=2)
        # fixed_incentive_amount = 1,228,140.23 * 4% = 49,125.61
        self.assertAlmostEqual(result["fixed_incentive_amount"], 49125.61, places=2)
        # performance_incentive_amount = 1,228,140.23 * 1% = 12,281.40
        self.assertAlmostEqual(result["performance_incentive_amount"], 12281.40, places=2)
        # total_incentive = 49,125.61 + 12,281.40 = 61,407.01
        self.assertAlmostEqual(result["total_incentive"], 61407.01, places=2)
        # gross_settlement = 16200 + 61407.01 + 0 (no below-minimum for Sales) + 215 = 77,822.01
        self.assertAlmostEqual(result["gross_settlement"], 77822.01, places=2)
        # balance_payable = 77822.01 - 44822 - 0 = 33,000.01
        self.assertAlmostEqual(result["balance_payable"], 33000.01, places=2)
        # Production-only fields are zeroed out for a Sales settlement.
        self.assertEqual(result["incentive_amount"], 0.0)
        self.assertEqual(result["total_production"], 0.0)
        self.assertEqual(result["below_minimum_days"], 0.0)

    def test_sales_performance_incentive_cannot_exceed_one_percent(self):
        doc = frappe._dict(
            employee="_TEST-EMP-SLS-001",
            settlement_type="Sales",
            from_date=getdate("2026-01-01"),
            to_date=getdate("2026-01-31"),
            total_cash_collected=100000,
            vat_percentage=15,
            fixed_incentive_percentage=4,
            performance_incentive_percentage=1.5,
            other_allowances=0,
            other_additions=0,
            other_deductions=0,
        )

        with (
            patch("badria_pwa.api.final_settlement.frappe.db.exists", return_value=True),
            patch(
                "badria_pwa.api.final_settlement._get_salary_structure_assignment",
                return_value=("Test Structure", 2000),
            ),
            patch("badria_pwa.api.final_settlement._get_attendance", return_value=30),
            patch("badria_pwa.api.final_settlement._get_total_advance_paid", return_value=0),
            patch("badria_pwa.api.final_settlement._get_total_payment_entry_paid", return_value=0),
            self.assertRaises(frappe.ValidationError),
        ):
            _compute_settlement(doc)

    def test_total_advance_paid_and_payment_entry_paid_both_reduce_balance(self):
        doc = frappe._dict(
            employee="_TEST-EMP-001",
            settlement_type="Production",
            from_date=getdate("2026-01-01"),
            to_date=getdate("2026-01-31"),
            minimum_production_per_day=20,
            incentive_rate=4,
            below_minimum_incentive_amount=0,
            other_allowances=0,
            other_additions=0,
            other_deductions=0,
        )

        with (
            patch("badria_pwa.api.final_settlement.frappe.db.exists", return_value=True),
            patch(
                "badria_pwa.api.final_settlement._get_salary_structure_assignment",
                return_value=("Test Structure", 3000),
            ),
            patch("badria_pwa.api.final_settlement._get_attendance", return_value=30),
            patch("badria_pwa.api.final_settlement._get_total_production", return_value=0),
            patch(
                "badria_pwa.api.final_settlement._get_below_minimum_production",
                return_value=(0, 0),
            ),
            patch("badria_pwa.api.final_settlement._get_total_advance_paid", return_value=500),
            patch("badria_pwa.api.final_settlement._get_total_payment_entry_paid", return_value=300),
        ):
            result = _compute_settlement(doc)

        # total_basic_salary = 3000 * (30/30) = 3000
        self.assertEqual(result["total_basic_salary"], 3000.0)
        self.assertEqual(result["total_advance_paid"], 500.0)
        self.assertEqual(result["total_payment_entry_paid"], 300.0)
        # balance_payable = 3000 - 500 - 300 = 2,200
        self.assertEqual(result["balance_payable"], 2200.0)

    def test_get_attendance_sales_counts_paid_leave_but_not_lwp(self):
        # Real Attendance + Leave Type records (rolled back after the test) to
        # verify _get_attendance itself, not just the arithmetic around it.
        # "Leave Without Pay" (is_lwp=1) must NOT add to payment days for Sales;
        # a fully paid leave type ("Sick Leave", is_lwp=0/is_ppl=0) must.
        employee = frappe.get_all("Employee", limit=1, pluck="name")[0]
        from_date = getdate("2026-02-01")
        to_date = getdate("2026-02-28")

        attendance_days = [
            (getdate("2026-02-01"), "Present", None),
            (getdate("2026-02-02"), "Present", None),
            (getdate("2026-02-03"), "On Leave", "Sick Leave"),
            (getdate("2026-02-04"), "On Leave", "Leave Without Pay"),
            (getdate("2026-02-05"), "Absent", None),
        ]
        for attendance_date, status, leave_type in attendance_days:
            doc = frappe.new_doc("Attendance")
            doc.employee = employee
            doc.attendance_date = attendance_date
            doc.status = status
            if leave_type:
                doc.leave_type = leave_type
            doc.insert(ignore_permissions=True)
            doc.submit()

        # Production: only the 2 Present days count - On Leave and Absent are unpaid.
        self.assertEqual(_get_attendance(employee, from_date, to_date, "Production"), 2.0)
        # Sales: 2 Present + 1 paid Sick Leave day = 3. The LWP day and the
        # Absent day do not add to payment days.
        self.assertEqual(_get_attendance(employee, from_date, to_date, "Sales"), 3.0)
