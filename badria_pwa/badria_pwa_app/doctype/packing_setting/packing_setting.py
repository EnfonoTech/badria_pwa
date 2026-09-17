# Copyright (c) 2026, Enfono and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class PackingSetting(Document):
	pass


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_packing_item_uoms(doctype, txt, searchfield, start, page_len, filters):
	"""Only return UOMs listed in the selected Item's own UOM table."""
	item_code = filters.get("item_code")
	if not item_code:
		return []

	query_filters = {"parent": item_code}
	if txt:
		query_filters["uom"] = ["like", f"%{txt}%"]

	return frappe.get_all(
		"UOM Conversion Detail",
		filters=query_filters,
		fields=["uom"],
		limit_start=start,
		limit_page_length=page_len,
		order_by="idx",
		as_list=1,
	)
