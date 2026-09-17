# badria_pwa.api.employee_attendance_tool - Shift-based narrowing of the
# employee list shown on the (core HRMS) Employee Attendance Tool. The tool's
# own get_employees() doesn't filter by shift at all - this lets the Employee
# Attendance Tool's Client Script narrow the already-fetched list down to only
# employees actually on the selected Shift Type for that date.

import frappe
from frappe import _

from badria_pwa.manufacturing.stock_entry import _employees_on_shift


@frappe.whitelist()
def get_shift_employees_for_date(shift, date):
    if not frappe.has_permission("Employee", "read"):
        frappe.throw(_("You are not permitted to view Employees."))

    return _employees_on_shift(shift, date)
