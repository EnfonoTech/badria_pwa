# badria_pwa.payroll.salary_slip - Enforces Production-department employees getting
# no pay credit for any On Leave day (paid or not), matching the Final Settlement rule.
# Sales-department employees need no override: HRMS's own attendance-based payroll
# already treats non-LWP/PPL leave types as fully paid, which is the desired behavior.

import frappe
from frappe.query_builder.functions import Count
from frappe.utils import flt

PRODUCTION_DEPARTMENT = "Production - BS"


def enforce_production_no_paid_leave(doc, method):
    if doc.department != PRODUCTION_DEPARTMENT:
        return

    extra_unpaid_days = _count_leave_days_normally_paid(doc.employee, doc.start_date, doc.actual_end_date)
    if not extra_unpaid_days:
        return

    doc.leave_without_pay = flt(doc.leave_without_pay) + extra_unpaid_days
    doc.payment_days = flt(doc.payment_days) - extra_unpaid_days
    doc.calculate_net_pay()


def _count_leave_days_normally_paid(employee, start_date, end_date):
    Attendance = frappe.qb.DocType("Attendance")
    rows = (
        frappe.qb.from_(Attendance)
        .select(Attendance.leave_type, Count(Attendance.name).as_("count"))
        .where(Attendance.employee == employee)
        .where(Attendance.docstatus == 1)
        .where(Attendance.status == "On Leave")
        .where(Attendance.attendance_date.between(start_date, end_date))
        .groupby(Attendance.leave_type)
    ).run(as_dict=True)

    if not rows:
        return 0.0

    leave_types = [r.leave_type for r in rows if r.leave_type]
    reduces_pay = set()
    if leave_types:
        reduces_pay = {
            lt.name
            for lt in frappe.get_all(
                "Leave Type",
                filters={"name": ["in", leave_types]},
                or_filters={"is_lwp": 1, "is_ppl": 1},
                fields=["name"],
            )
        }

    return sum(flt(r["count"]) for r in rows if r.leave_type not in reduces_pay)
