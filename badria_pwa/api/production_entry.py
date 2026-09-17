# badria_pwa.api.production_entry - Bulk generation of Production Entry records

import json

import frappe
from frappe import _
from frappe.utils import flt, getdate


@frappe.whitelist()
def bulk_create(employee, entries, supervisor=None):
    """
    entries: JSON list of {"date": "YYYY-MM-DD", "quantity": <number>} — the exact,
    reviewed per-day values the user confirmed in the Bulk Production Entry page.
    """
    if not frappe.has_permission("Production Entry", "create"):
        frappe.throw(_("You are not permitted to create Production Entries."))

    if isinstance(entries, str):
        entries = json.loads(entries)

    if not entries:
        frappe.throw(_("No entries to create."))

    created = 0
    skipped = 0

    for row in entries:
        date = getdate(row.get("date"))
        quantity = flt(row.get("quantity"))

        if quantity <= 0:
            skipped += 1
            continue

        if frappe.db.exists(
            "Production Entry",
            {"employee": employee, "date": date, "docstatus": ["!=", 2]},
        ):
            skipped += 1
            continue

        doc = frappe.new_doc("Production Entry")
        doc.employee = employee
        doc.supervisor = supervisor
        doc.date = date
        doc.production_quantity = quantity
        doc.insert()
        doc.submit()
        created += 1

    return {"created": created, "skipped": skipped}


@frappe.whitelist()
def create_from_attendance_tool(date, entries):
    """
    entries: JSON list of {"employee": ..., "quantity": <number>} - one
    Production Entry per employee, for the given Production Date, submitted
    from the "Add Production" action on the Employee Attendance Tool.

    Mandatory fields, the quantity > 0 check and duplicate detection are all
    enforced by Production Entry's own validate() - this just loops over rows
    and turns whatever it raises into a per-row message instead of failing
    the whole batch.
    """
    if not frappe.has_permission("Production Entry", "create"):
        frappe.throw(_("You are not permitted to create Production Entries."))

    if isinstance(entries, str):
        entries = json.loads(entries)

    if not entries:
        frappe.throw(_("No entries to create."))

    date = getdate(date)
    created = []
    errors = []

    for row in entries:
        employee = row.get("employee")
        try:
            doc = frappe.new_doc("Production Entry")
            doc.employee = employee
            doc.date = date
            doc.production_quantity = flt(row.get("quantity"))
            doc.insert()
            doc.submit()
            created.append(
                {
                    "name": doc.name,
                    "employee": doc.employee,
                    "employee_name": doc.employee_name,
                    "production_quantity": doc.production_quantity,
                }
            )
        except frappe.ValidationError as e:
            frappe.clear_last_message()
            errors.append(str(e))
        except Exception:
            frappe.log_error(
                title="Add Production from Employee Attendance Tool",
                message=frappe.get_traceback(),
            )
            errors.append(_("Could not create Production Entry for {0}.").format(employee))

    return {"created": created, "errors": errors}


@frappe.whitelist()
def get_production_entries(date):
    """Production Entries recorded for a given date - used to render the
    "Production Added" summary on the Employee Attendance Tool."""
    if not frappe.has_permission("Production Entry", "read"):
        frappe.throw(_("You are not permitted to view Production Entries."))

    return frappe.get_all(
        "Production Entry",
        filters={"date": getdate(date), "docstatus": ["!=", 2]},
        fields=["name", "employee", "employee_name", "production_quantity"],
        order_by="employee_name asc",
    )
