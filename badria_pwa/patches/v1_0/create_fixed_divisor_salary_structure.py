import frappe
from frappe.utils import getdate

# The client's rule: Basic = (Monthly Basic / 30) * Payment Days, with the divisor
# always fixed at 30 (never the actual number of days in that calendar month).
# "salary struture" (the live, submitted structure) prorates Basic the stock HRMS
# way instead (Payment Days / actual calendar days), and its depends_on_payment_days/
# amount_based_on_formula flags aren't editable in place once submitted. Rather than
# cancel/amend the live structure (which would orphan its existing Salary Structure
# Assignments, since amending changes the document name), this creates a new
# structure with the formula built in from the start, and re-assigns the employees
# currently on the old one, effective the start of the next unprocessed payroll month.

NEW_STRUCTURE = "Salary Structure - Fixed 30 Divisor"
OLD_STRUCTURE = "salary struture"
EFFECTIVE_FROM = "2026-08-01"


def execute():
    if not frappe.db.exists("Salary Structure", OLD_STRUCTURE):
        return

    if not frappe.db.exists("Salary Structure", NEW_STRUCTURE):
        old = frappe.get_doc("Salary Structure", OLD_STRUCTURE)
        new = frappe.new_doc("Salary Structure")
        new.name = NEW_STRUCTURE
        new.company = old.company
        new.currency = old.currency
        new.payroll_frequency = old.payroll_frequency
        for row in old.earnings:
            new.append(
                "earnings",
                {
                    "salary_component": row.salary_component,
                    "amount": row.amount,
                    "amount_based_on_formula": 1 if row.salary_component == "Basic" else row.amount_based_on_formula,
                    "formula": "base/30*payment_days" if row.salary_component == "Basic" else row.formula,
                    "depends_on_payment_days": 0 if row.salary_component == "Basic" else row.depends_on_payment_days,
                },
            )
        for row in old.deductions:
            new.append(
                "deductions",
                {
                    "salary_component": row.salary_component,
                    "amount": row.amount,
                    "amount_based_on_formula": row.amount_based_on_formula,
                    "formula": row.formula,
                    "depends_on_payment_days": row.depends_on_payment_days,
                },
            )
        new.insert(ignore_permissions=True)
        new.submit()

    assignments = frappe.get_all(
        "Salary Structure Assignment",
        filters={"salary_structure": OLD_STRUCTURE, "docstatus": 1},
        fields=["employee", "company", "base", "currency"],
    )
    for row in assignments:
        already_assigned = frappe.db.exists(
            "Salary Structure Assignment",
            {
                "employee": row.employee,
                "salary_structure": NEW_STRUCTURE,
                "from_date": getdate(EFFECTIVE_FROM),
            },
        )
        if already_assigned:
            continue

        assignment = frappe.new_doc("Salary Structure Assignment")
        assignment.employee = row.employee
        assignment.salary_structure = NEW_STRUCTURE
        assignment.company = row.company
        assignment.currency = row.currency
        assignment.base = row.base
        assignment.from_date = getdate(EFFECTIVE_FROM)
        assignment.insert(ignore_permissions=True)
        assignment.submit()
