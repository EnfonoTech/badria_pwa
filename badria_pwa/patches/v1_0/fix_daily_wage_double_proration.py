import frappe

# "Saary Structure - Daily Wage" (the real, actively-assigned structure) had its
# Basic component set up with BOTH amount_based_on_formula=1 (formula
# "base/30*payment_days") AND depends_on_payment_days=1. Having both together
# double-divides by payment_days: the formula computes the correct amount once,
# then HRMS's own proration (amount * payment_days / total_working_days)
# divides it again. This was invisible in the one period tested (payment_days
# happened to equal total_working_days that month) but produces an
# under-calculated Basic Salary the moment there's any absence/unpaid leave.
# depends_on_payment_days must be off since the formula already accounts for
# payment_days itself.

STRUCTURE = "Saary Structure - Daily Wage"


def execute():
    if not frappe.db.exists("Salary Structure", STRUCTURE):
        return

    doc = frappe.get_doc("Salary Structure", STRUCTURE)
    changed = False
    for row in doc.earnings:
        if row.salary_component == "Basic" and row.amount_based_on_formula and row.depends_on_payment_days:
            row.depends_on_payment_days = 0
            changed = True

    if changed:
        doc.flags.ignore_validate_update_after_submit = True
        doc.save(ignore_permissions=True)
