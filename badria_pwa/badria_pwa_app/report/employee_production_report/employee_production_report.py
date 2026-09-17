# Copyright (c) 2026, Enfono and contributors
# For license information, please see license.txt
#
# Reads directly from Stock Entry + Stock Entry Detail + BS Shift Employee
# on submitted Manufacture entries - no separate doctype stores report data.
# Every employee on the Shift Employees child table (custom_shift_employees)
# gets one row per finished item on that same Stock Entry, showing the full
# produced quantity - the whole shift crew is credited with the full output,
# the quantity is not split between them.

import frappe
from frappe import _


def execute(filters=None):
    filters = filters or {}
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
        {"label": _("Stock Entry"), "fieldname": "stock_entry", "fieldtype": "Link", "options": "Stock Entry", "width": 150},
        {"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 140},
        {"label": _("Shift Type"), "fieldname": "shift_type", "fieldtype": "Link", "options": "Shift Type", "width": 110},
        {"label": _("Employee"), "fieldname": "employee", "fieldtype": "Link", "options": "Employee", "width": 120},
        {"label": _("Employee Name"), "fieldname": "employee_name", "fieldtype": "Data", "width": 150},
        {"label": _("Finished Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 140},
        {"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 150},
        {"label": _("Quantity"), "fieldname": "qty", "fieldtype": "Float", "width": 100},
        {"label": _("UOM"), "fieldname": "uom", "fieldtype": "Link", "options": "UOM", "width": 90},
        {"label": _("Target Warehouse"), "fieldname": "target_warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 150},
    ]


def get_data(filters):
    StockEntry = frappe.qb.DocType("Stock Entry")
    Item = frappe.qb.DocType("Stock Entry Detail")
    ShiftEmployee = frappe.qb.DocType("BS Shift Employee")

    query = (
        frappe.qb.from_(StockEntry)
        .inner_join(Item)
        .on((Item.parent == StockEntry.name) & (Item.is_finished_item == 1))
        .inner_join(ShiftEmployee)
        .on(ShiftEmployee.parent == StockEntry.name)
        .select(
            StockEntry.posting_date,
            StockEntry.name.as_("stock_entry"),
            StockEntry.company,
            StockEntry.custom_shift_type.as_("shift_type"),
            ShiftEmployee.employee,
            ShiftEmployee.employee_name,
            Item.item_code,
            Item.item_name,
            Item.qty,
            Item.uom,
            Item.t_warehouse.as_("target_warehouse"),
        )
        .where(StockEntry.docstatus == 1)
        .where(StockEntry.stock_entry_type == "Manufacture")
        .orderby(StockEntry.posting_date)
    )

    if filters.get("from_date"):
        query = query.where(StockEntry.posting_date >= filters["from_date"])
    if filters.get("to_date"):
        query = query.where(StockEntry.posting_date <= filters["to_date"])
    if filters.get("company"):
        query = query.where(StockEntry.company == filters["company"])
    if filters.get("shift_type"):
        query = query.where(StockEntry.custom_shift_type == filters["shift_type"])
    if filters.get("employee"):
        query = query.where(ShiftEmployee.employee == filters["employee"])
    if filters.get("item"):
        query = query.where(Item.item_code == filters["item"])

    return query.run(as_dict=True)
