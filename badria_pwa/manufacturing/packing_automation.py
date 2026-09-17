# badria_pwa.manufacturing.packing_automation - Auto-consumes packing
# materials (boxes, pouches, ...) on a Manufacture Stock Entry, so the
# operator only ever types the finished goods quantity.
#
# Configured via the standalone "Packing Setting" DocType, one record per
# finished Item (name = the Item code):
#   pieces_per_carton   Int     - carton capacity
#   packing_materials   Table   - Packing Material Rule rows, each one of:
#     Per Carton  qty_per_unit = units of this material per full carton of
#                 pieces (uses pieces_per_carton, rounded DOWN - a partial
#                 carton does not get one)
#     Per Piece   qty_per_unit = units of this material per finished piece
#                 (exactly one such rule is treated as defining "the Pouch",
#                 so a "Per Pouch" rule below has something to divide)
#     Per Pouch   qty_per_unit = how many Pouches this material holds (e.g.
#                 Pouches per Box), rounded UP - a part-full box still needs
#                 a whole box
# A finished item with no "Packing Setting" record is simply not automated.
#
# Example (matches the "Samoosa Hadi" packing rules):
#   Pieces Per Carton = 8
#   Pouch | Per Piece | 1    -> 1 Pouch per piece, boxed or loose
#   Box   | Per Pouch | 10   -> 1 Box per 10 Pouch (ceil), boxes hold Pouches
#
# 45 finished pieces  -> Pouch = 45         -> Box = ceil(45  / 10) = 5
# 205 finished pieces -> Pouch = 205        -> Box = ceil(205 / 10) = 21

import math

import frappe
from frappe import _
from frappe.utils import cint, flt


def apply_packing_rules(doc, method=None):
	"""doc_events hook: Stock Entry validate.

	Runs on every save, including the save that happens as part of submit,
	so packing rows always reflect the latest finished qty and an
	insufficient-stock error blocks submission the same way any other
	mandatory validation would.

	Only Manufacture entries qualify, and only rows with "Is Finished Item"
	ticked are read for packing rules - a plain consumption/transfer row
	never triggers this, and other Stock Entry purposes (Material Transfer,
	Material Issue, Repack, ...) are skipped entirely via the purpose check
	below.

	A single entry can produce more than one finished item (e.g. two flavors
	in one batch); each ticked row is resolved against its own Item's
	packing rules and carton size, then the packing-material quantities are
	summed across finished items before writing rows - so if two finished
	items both consume "Box", the Stock Entry ends up with one Box row for
	the combined qty instead of the second finished item's rules
	overwriting the first's.
	"""
	if doc.purpose != "Manufacture":
		return

	finished_rows = [row for row in doc.items if row.is_finished_item and flt(row.qty) > 0]
	if not finished_rows:
		return

	required_qty = {}  # packing_material_item -> total qty across all finished items
	source_warehouse_by_item = {}

	for row in finished_rows:
		if not frappe.db.exists("Packing Setting", row.item_code):
			continue

		setting = frappe.get_cached_doc("Packing Setting", row.item_code)
		rules = setting.get("packing_materials") or []
		if not rules:
			continue

		per_piece_rules = [r for r in rules if r.rule_type == "Per Piece"]
		per_pouch_rules = [r for r in rules if r.rule_type == "Per Pouch"]
		if per_pouch_rules and len(per_piece_rules) != 1:
			frappe.throw(
				_(
					"Item {0}: a 'Per Pouch' packing rule needs exactly one 'Per Piece' rule to"
					" define the Pouch quantity it divides into (found {1})."
				).format(frappe.bold(row.item_code), len(per_piece_rules))
			)
		pouch_qty = flt(row.qty) * flt(per_piece_rules[0].qty_per_unit or 0) if per_piece_rules else 0

		carton_count = 0
		if any(r.rule_type == "Per Carton" for r in rules):
			pieces_per_carton = cint(setting.pieces_per_carton)
			if pieces_per_carton <= 0:
				frappe.throw(
					_("Item {0} has a 'Per Carton' packing rule but Pieces Per Carton is not set.").format(
						frappe.bold(row.item_code)
					)
				)
			carton_count = int(flt(row.qty) // pieces_per_carton)

		for rule in rules:
			if rule.rule_type == "Per Carton":
				qty = flt(carton_count) * flt(rule.qty_per_unit or 0)
			elif rule.rule_type == "Per Piece":
				qty = flt(row.qty) * flt(rule.qty_per_unit or 0)
			else:  # Per Pouch
				pouches_per_unit = flt(rule.qty_per_unit or 0)
				qty = math.ceil(pouch_qty / pouches_per_unit) if pouches_per_unit > 0 else 0

			if qty <= 0:
				continue

			required_qty[rule.packing_material_item] = required_qty.get(rule.packing_material_item, 0) + qty
			source_warehouse_by_item.setdefault(
				rule.packing_material_item, _get_source_warehouse(doc, rule.packing_material_item)
			)

	if not required_qty:
		return

	shortages = []
	for item_code, qty in required_qty.items():
		source_warehouse = source_warehouse_by_item[item_code]
		_upsert_packing_row(doc, item_code, qty, source_warehouse)

		available_qty = flt(
			frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": source_warehouse}, "actual_qty")
		)
		if qty > available_qty:
			shortages.append(
				_("{0}: required {1}, available {2} in {3}").format(
					frappe.bold(item_code), qty, available_qty, source_warehouse
				)
			)

	if shortages and not cint(frappe.db.get_single_value("Stock Settings", "allow_negative_stock")):
		frappe.throw(
			_("Insufficient stock to auto-consume the following packing material(s):<br>{0}").format(
				"<br>".join(shortages)
			),
			title=_("Insufficient Packing Material Stock"),
		)


def _get_source_warehouse(doc, item_code):
	"""Where to consume this packing material from.

	Priority: the Stock Entry's own "Default Source Warehouse" header field
	(if the operator set one), else the packing material Item's own default
	warehouse for this company (Item > item_defaults, the standard ERPNext
	place to configure "this item always comes from warehouse X") - so the
	common case needs no per-entry setup at all.
	"""
	if doc.from_warehouse:
		return doc.from_warehouse

	default_warehouse = frappe.db.get_value(
		"Item Default", {"parent": item_code, "company": doc.company}, "default_warehouse"
	)
	if default_warehouse:
		return default_warehouse

	frappe.throw(
		_(
			"Cannot determine a source warehouse for packing material {0}. Either set a Default "
			"Warehouse for it on the Item (Item > Company Defaults), or set Default Source "
			"Warehouse on this Stock Entry."
		).format(frappe.bold(item_code))
	)


def _upsert_packing_row(doc, item_code, qty, source_warehouse):
	"""Update the row for this packing material if it is already on the
	Stock Entry, else append a new consumption row."""
	for row in doc.items:
		if row.item_code == item_code and not row.is_finished_item:
			row.qty = qty
			row.transfer_qty = flt(qty) * flt(row.conversion_factor or 1)
			row.s_warehouse = source_warehouse
			return row

	item_meta = frappe.get_cached_doc("Item", item_code)
	return doc.append(
		"items",
		{
			"item_code": item_code,
			"item_name": item_meta.item_name,
			"qty": qty,
			"stock_uom": item_meta.stock_uom,
			"uom": item_meta.stock_uom,
			"conversion_factor": 1,
			"transfer_qty": qty,
			"s_warehouse": source_warehouse,
			"is_finished_item": 0,
			"cost_center": item_meta.get("buying_cost_center"),
			"expense_account": item_meta.get("expense_account"),
		},
	)
