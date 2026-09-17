import frappe

# create_fixed_divisor_salary_structure.py created "Salary Structure - Fixed 30
# Divisor" by targeting the old "salary struture", but all real employees had
# already been moved directly onto "Saary Structure - Daily Wage" (fixed
# in-place by the client). No Salary Structure Assignment ever pointed to this
# structure, so it's unused clutter - cancel it rather than leave a
# submitted-but-orphaned structure sitting in the system.

STRUCTURE = "Salary Structure - Fixed 30 Divisor"


def execute():
    if not frappe.db.exists("Salary Structure", STRUCTURE):
        return

    if frappe.db.exists("Salary Structure Assignment", {"salary_structure": STRUCTURE, "docstatus": 1}):
        # Something got assigned to it after all - leave it alone.
        return

    doc = frappe.get_doc("Salary Structure", STRUCTURE)
    if doc.docstatus == 1:
        doc.cancel()
