# badria_pwa.manufacturing.stock_entry - Feeds the Employee Details tab's
# Shift Employees child table with every employee who has an active Shift
# Assignment for the selected Shift Type that actually covers the Stock
# Entry's own Posting Date, optionally narrowed to one Department.

import frappe


def _employees_on_shift(shift_type, posting_date):
    if not shift_type or not posting_date:
        return []

    return frappe.get_all(
        "Shift Assignment",
        filters={
            "shift_type": shift_type,
            "docstatus": 1,
            "status": "Active",
            "start_date": ["<=", posting_date],
        },
        or_filters=[["end_date", ">=", posting_date], ["end_date", "is", "not set"]],
        pluck="employee",
        distinct=True,
    )


@frappe.whitelist()
def get_shift_employees(shift_type, posting_date, department=None):
    """Full list of employees on the given Shift Type/Posting Date, used to
    populate the Stock Entry's Shift Employees child table. When department
    is set, only employees belonging to that department are returned."""
    employees = _employees_on_shift(shift_type, posting_date)
    if not employees:
        return []

    filters = {"name": ["in", employees]}
    if department:
        filters["department"] = department

    return frappe.get_all(
        "Employee",
        filters=filters,
        fields=["name as employee", "employee_name"],
        order_by="employee_name",
    )
