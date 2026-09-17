# Copyright (c) 2026, Enfono and Contributors
# See license.txt
#
# Following the same philosophy as test_final_settlement.py: the ÷30 formula
# itself is plain Salary Structure configuration (HRMS's own formula evaluator,
# not our code) and is checked manually on a real Salary Slip. What we own and
# must verify here is enforce_production_no_paid_leave's own logic - that it
# only acts for Production-department slips, counts the right Attendance rows,
# and leaves LWP/PPL leave types alone since core already handles those.

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate

from badria_pwa.payroll.salary_slip import (
    PRODUCTION_DEPARTMENT,
    _count_leave_days_normally_paid,
    enforce_production_no_paid_leave,
)


class FakeSalarySlip:
    def __init__(self, department, employee, start_date, end_date, payment_days, leave_without_pay=0):
        self.department = department
        self.employee = employee
        self.start_date = start_date
        self.actual_end_date = end_date
        self.payment_days = payment_days
        self.leave_without_pay = leave_without_pay
        self.recalculated = False

    def calculate_net_pay(self):
        self.recalculated = True


class IntegrationTestSalarySlipProductionLeave(FrappeTestCase):
    def test_non_production_department_is_left_untouched(self):
        doc = FakeSalarySlip("SALES MAN - BS", "_TEST-EMP-001", getdate("2026-02-01"), getdate("2026-02-28"), 28)

        enforce_production_no_paid_leave(doc, "validate")

        self.assertEqual(doc.payment_days, 28)
        self.assertEqual(doc.leave_without_pay, 0)
        self.assertFalse(doc.recalculated)

    def test_production_department_with_no_leave_is_left_untouched(self):
        doc = FakeSalarySlip(PRODUCTION_DEPARTMENT, "_TEST-EMP-001", getdate("2026-02-01"), getdate("2026-02-28"), 28)

        enforce_production_no_paid_leave(doc, "validate")

        self.assertEqual(doc.payment_days, 28)
        self.assertFalse(doc.recalculated)

    def test_production_department_strips_normally_paid_leave_days(self):
        # Real Attendance + Leave Type records (rolled back after the test).
        # "Sick Leave" (is_lwp=0/is_ppl=0) must be fully stripped from payment
        # days for a Production employee - Production has no paid-leave concept.
        employee = frappe.get_all("Employee", limit=1, pluck="name")[0]
        from_date = getdate("2026-02-01")
        to_date = getdate("2026-02-28")

        attendance_days = [
            (getdate("2026-02-03"), "On Leave", "Sick Leave"),
            (getdate("2026-02-04"), "On Leave", "Sick Leave"),
        ]
        for attendance_date, status, leave_type in attendance_days:
            att = frappe.new_doc("Attendance")
            att.employee = employee
            att.attendance_date = attendance_date
            att.status = status
            att.leave_type = leave_type
            att.insert(ignore_permissions=True)
            att.submit()

        doc = FakeSalarySlip(PRODUCTION_DEPARTMENT, employee, from_date, to_date, payment_days=26)

        enforce_production_no_paid_leave(doc, "validate")

        # 2 Sick Leave days were counted as paid by core (payment_days=26 already
        # excludes the 2 leave days? no - core would have included them as paid,
        # so the incoming payment_days here represents 26 Present + 2 paid leave
        # = 28 total, but we pass 26 to isolate just the override's own math).
        self.assertEqual(doc.leave_without_pay, 2)
        self.assertEqual(doc.payment_days, 24)
        self.assertTrue(doc.recalculated)

    def test_lwp_and_ppl_leave_types_are_not_double_counted(self):
        employee = frappe.get_all("Employee", limit=1, pluck="name")[0]
        from_date = getdate("2026-03-01")
        to_date = getdate("2026-03-31")

        att = frappe.new_doc("Attendance")
        att.employee = employee
        att.attendance_date = getdate("2026-03-05")
        att.status = "On Leave"
        att.leave_type = "Leave Without Pay"
        att.insert(ignore_permissions=True)
        att.submit()

        doc = FakeSalarySlip(PRODUCTION_DEPARTMENT, employee, from_date, to_date, payment_days=30)

        enforce_production_no_paid_leave(doc, "validate")

        # Leave Without Pay is already excluded from payment_days by core itself -
        # our override must not touch it again.
        self.assertEqual(doc.payment_days, 30)
        self.assertEqual(doc.leave_without_pay, 0)
        self.assertFalse(doc.recalculated)


class UnitTestCountLeaveDaysNormallyPaid(FrappeTestCase):
    def test_counts_only_non_lwp_non_ppl_leave_types(self):
        employee = frappe.get_all("Employee", limit=1, pluck="name")[0]
        from_date = getdate("2026-04-01")
        to_date = getdate("2026-04-30")

        attendance_days = [
            (getdate("2026-04-02"), "Sick Leave"),
            (getdate("2026-04-03"), "Sick Leave"),
            (getdate("2026-04-04"), "Leave Without Pay"),
        ]
        for attendance_date, leave_type in attendance_days:
            att = frappe.new_doc("Attendance")
            att.employee = employee
            att.attendance_date = attendance_date
            att.status = "On Leave"
            att.leave_type = leave_type
            att.insert(ignore_permissions=True)
            att.submit()

        self.assertEqual(_count_leave_days_normally_paid(employee, from_date, to_date), 2.0)
