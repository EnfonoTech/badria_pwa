# Copyright (c) 2026, Enfono and contributors
# For license information, please see license.txt

from frappe import _

from badria_pwa.api.sales_collection import get_permitted_warehouses_for_user, get_sales_collection_data


def execute(filters=None):
	filters = filters or {}
	columns = get_columns()

	warehouse = filters.get("warehouse")
	user = filters.get("user")

	if user and not warehouse:
		# A user filter with nothing assigned to them yet (no User Permission on
		# Warehouse) has nothing to show - not an error, just empty.
		if not get_permitted_warehouses_for_user(user):
			return columns, [], None, None, _summary_cards(0, 0, 0, 0)

	if not warehouse and not user:
		# Nothing to scope by yet (first load, before a filter is picked) - show
		# empty results instead of erroring.
		return columns, [], None, None, _summary_cards(0, 0, 0, 0)

	result = get_sales_collection_data(
		from_date=filters.get("from_date"),
		to_date=filters.get("to_date"),
		warehouse=warehouse,
		user=user,
		company=filters.get("company"),
		customer=filters.get("customer"),
	)
	totals = result["totals"]
	report_summary = _summary_cards(
		totals["total_sales_amount"],
		totals["total_payment_collected"],
		totals["total_outstanding_amount"],
		totals["collection_percentage"],
	)
	return columns, result["rows"], None, None, report_summary


def _summary_cards(total_sales_amount, total_payment_collected, total_outstanding_amount, collection_percentage):
	return [
		{"label": _("Total Sales Amount"), "value": total_sales_amount, "datatype": "Currency"},
		{"label": _("Total Payment Collected"), "value": total_payment_collected, "datatype": "Currency"},
		{"label": _("Total Outstanding"), "value": total_outstanding_amount, "datatype": "Currency"},
		{"label": _("Collection %"), "value": collection_percentage, "datatype": "Percent"},
	]


def get_columns():
	return [
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 120},
		{"label": _("Customer Name"), "fieldname": "customer_name", "fieldtype": "Data", "width": 200},
		{"label": _("Assigned User"), "fieldname": "assigned_user", "fieldtype": "Link", "options": "User", "width": 160},
		{"label": _("Sales Invoice"), "fieldname": "sales_invoice", "fieldtype": "Link", "options": "Sales Invoice", "width": 160},
		{"label": _("Sales Return Ref"), "fieldname": "sales_return", "fieldtype": "Link", "options": "Sales Invoice", "width": 160},
		{"label": _("Return Date(s)"), "fieldname": "return_dates", "fieldtype": "Data", "width": 120},
		{"label": _("Return Amt (VAT Incl.)"), "fieldname": "return_grand_total", "fieldtype": "Currency", "width": 180},
		{"label": _("Return Amt (VAT Excl.)"), "fieldname": "return_net_amount", "fieldtype": "Currency", "width": 180},
		{"label": _("Invoice Amt (VAT Incl.)"), "fieldname": "invoice_grand_total", "fieldtype": "Currency", "width": 180},
		{"label": _("Invoice Amt (VAT Excl.)"), "fieldname": "invoice_net_amount", "fieldtype": "Currency", "width": 180},
		{"label": _("Discount On"), "fieldname": "apply_discount_on", "fieldtype": "Data", "width": 100},
		{"label": _("Discount Amt"), "fieldname": "discount_amount", "fieldtype": "Currency", "width": 120},
		{"label": _("Payment Entry"), "fieldname": "payment_entry", "fieldtype": "Data", "width": 160},
		{"label": _("Collected (VAT Incl.)"), "fieldname": "collected_grand_total", "fieldtype": "Currency", "width": 180},
		{"label": _("Collected (VAT Excl.)"), "fieldname": "collected_net_amount", "fieldtype": "Currency", "width": 190},
		{"label": _("Outstanding Amt"), "fieldname": "outstanding_amount", "fieldtype": "Currency", "width": 150},
	]
