# Copyright (c) 2026, Enfono and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


class ProductionTemplate(Document):
	def validate(self):
		self.validate_rows()
		self.validate_finished_item()

	def validate_rows(self):
		"""Every row is either consumed (source only) or produced (target only).

		A row carrying both warehouses is an internal transfer. ERPNext accepts
		it, but it is neither consumption nor production, so it drops out of
		every section of the Daily Production Data Sheet without any warning.
		"""
		for row in self.items:
			if row.s_warehouse and row.t_warehouse:
				frappe.throw(
					_(
						"Row #%d: a template row cannot have both a Source and a Target Warehouse."
						" Leave one blank — a row is either consumed or produced."
					)
					% row.idx
				)

			if row.is_finished_item and row.is_scrap_item:
				frappe.throw(
					_("Row #%d: a row cannot be both a finished item and a scrap item.") % row.idx
				)

			if flt(row.qty) < 0:
				frappe.throw(_("Row #%d: Qty cannot be negative.") % row.idx)

	def validate_finished_item(self):
		"""A Manufacture Stock Entry refuses to save without a finished-item row.

		Catching it here means the template author sees it, rather than the
		operator hitting FinishedGoodError after loading the template.
		"""
		if not self.stock_entry_type:
			return

		purpose = frappe.db.get_value("Stock Entry Type", self.stock_entry_type, "purpose")
		if purpose == "Repack":
			# Repack treats every target-only row as a finished good. A flagged
			# row with no warehouse of its own is produced too: get_template_items
			# puts it into the default target warehouse.
			if not any(
				(row.t_warehouse or row.is_finished_item or row.is_scrap_item) and not row.s_warehouse
				for row in self.items
			):
				frappe.throw(
					_(
						"A Repack template needs at least one produced row (Target Warehouse only),"
						" otherwise the Stock Entry has no finished goods."
					)
				)
			return

		if purpose != "Manufacture":
			return

		if not any(cint(row.is_finished_item) for row in self.items):
			frappe.throw(
				_(
					"A Manufacture template needs at least one row flagged as <b>Is Finished Item</b>,"
					" otherwise the Stock Entry cannot be submitted."
				)
			)


@frappe.whitelist()
def get_template_items(template: str) -> list[dict]:
	"""Return template rows ready to drop straight into Stock Entry Detail.

	Resolving this server-side in one call is deliberate:

	* `stock_uom`, `conversion_factor` and `transfer_qty` are MANDATORY on
	  Stock Entry Detail and are normally filled by ERPNext's `item_code`
	  client handler. A bulk loader bypasses that handler, so without these the
	  grid blocks the save with "Mandatory fields required in table Items".
	* `erpnext...get_conversion_factor` is not whitelisted, so the browser
	  cannot call it; doing it here reuses core logic instead of reimplementing
	  the UOM Conversion Detail -> UOM Conversion Factor -> 1.0 fallback.

	Permission is checked explicitly — this is a public HTTP endpoint.
	"""
	from erpnext.stock.get_item_details import get_conversion_factor

	if not frappe.has_permission("Production Template", "read", doc=template):
		frappe.throw(_("Not permitted to read this Production Template"), frappe.PermissionError)

	doc = frappe.get_cached_doc("Production Template", template)
	if doc.disabled:
		frappe.throw(_("This Production Template is disabled."))

	stock_uoms = dict(
		frappe.get_all(
			"Item",
			filters={"name": ["in", [row.item_code for row in doc.items]]},
			fields=["name", "stock_uom"],
			as_list=True,
		)
	)

	rows = []
	for row in doc.items:
		produces = bool(row.t_warehouse or row.is_finished_item or row.is_scrap_item)
		stock_uom = stock_uoms.get(row.item_code)
		uom = row.uom or stock_uom
		factor = flt(get_conversion_factor(row.item_code, uom).get("conversion_factor")) or 1.0

		rows.append(
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"qty": flt(row.qty),
				"uom": uom,
				"stock_uom": stock_uom or uom,
				"conversion_factor": factor,
				"transfer_qty": flt(row.qty) * factor,
				"s_warehouse": row.s_warehouse or (None if produces else doc.default_source_warehouse),
				"t_warehouse": row.t_warehouse or (doc.default_target_warehouse if produces else None),
				"is_finished_item": cint(row.is_finished_item),
				"is_scrap_item": cint(row.is_scrap_item),
			}
		)
	return rows
