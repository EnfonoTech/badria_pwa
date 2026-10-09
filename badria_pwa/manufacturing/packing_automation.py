# badria_pwa.manufacturing.packing_automation - Auto-consumes packing
# materials (boxes, pouches, ...) on a Manufacture or Production (Repack)
# Stock Entry, so the operator only ever types the finished goods quantity.
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
#   qty_per_unit is in the rule's own UOM; it is converted to the packing
#   material's stock UOM before it is written to the Stock Entry.
# A finished item with no "Packing Setting" record is simply not automated.
#
# Example (matches the "Samoosa Hadi" packing rules):
#   Pieces Per Carton = 8
#   Pouch | Per Piece | 1    -> 1 Pouch per piece, boxed or loose
#   Box   | Per Pouch | 10   -> 1 Box per 10 Pouch (ceil), boxes hold Pouches
#
# 45 finished pieces  -> Pouch = 45         -> Box = ceil(45  / 10) = 5
# 205 finished pieces -> Pouch = 205        -> Box = ceil(205 / 10) = 21
#
# Finished quantities are always read in stock UOM (qty x conversion factor),
# so a "25 Carton" row counts as 200 pieces, not 25.
#
# A row entered in the stock UOM (e.g. "8 Nos") is LOOSE pieces: it gets its
# Per Piece material (the pouch) but no box - the Per Carton and Per Pouch
# rules, which both describe the box the pieces are packed in, apply only to
# rows entered in a packed UOM such as Carton.
#
# The Stock Entry gets ONE auto row per packing material (flagged
# custom_is_auto_packing) holding the combined qty. On a Production entry the
# per-product split is also written to custom_packing_allocation, which
# production_costing uses to charge each product with its own packing - it is
# stored rather than recomputed so a later backdated repost splits the cost
# exactly as it was split on the day, even if the Packing Setting has changed.
# Extra or damaged packing is entered as a normal (unflagged) row: it is left
# alone here and costed with the common raw material.

import math

import frappe
from frappe import _
from frappe.utils import cint, flt

from badria_pwa.manufacturing import is_production_entry


def apply_packing_rules(doc, method=None):
	"""doc_events hook: Stock Entry before_validate.

	Runs on every save, including the save that happens as part of submit,
	so the auto packing rows always reflect the latest finished qty and an
	insufficient-stock error blocks submission the same way any other
	mandatory validation would.

	Only Manufacture entries and Production (Repack) entries qualify. The
	finished rows are the "Is Finished Item" rows on a Manufacture entry, and
	every target-only row on a Production entry (core Repack marks those as
	finished later in validate(), after this hook has run).

	Quantities are summed per packing material across all finished items, so
	two products that both consume "Box" end up sharing one Box row.
	"""
	production = is_production_entry(doc)
	if doc.purpose != "Manufacture" and not production:
		return

	required_qty = {}  # packing_material_item -> total qty (stock UOM)
	allocation = {}  # (finished item, packing_material_item) -> qty (stock UOM)
	source_warehouse_by_item = {}

	for row in doc.items:
		if not _is_finished_row(row, production):
			continue

		finished_qty = _stock_qty(row)
		if finished_qty <= 0 or not frappe.db.exists("Packing Setting", row.item_code):
			continue

		for packing_item, qty, rule in _packing_requirements(row.item_code, finished_qty, _is_loose(row)):
			required_qty[packing_item] = required_qty.get(packing_item, 0) + qty
			key = (row.item_code, packing_item)
			allocation[key] = allocation.get(key, 0) + qty
			source_warehouse_by_item.setdefault(
				packing_item,
				_get_source_warehouse(doc, packing_item, rule.get("warehouse")),
			)

	_sync_auto_rows(doc, required_qty, source_warehouse_by_item)
	if production:
		_set_packing_allocation(doc, allocation)

	shortages = []
	for item_code, qty in required_qty.items():
		source_warehouse = source_warehouse_by_item[item_code]
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


def _is_finished_row(row, production):
	if row.get("custom_is_auto_packing"):
		return False
	if production:
		return bool(row.t_warehouse and not row.s_warehouse)
	return bool(row.is_finished_item)


def _stock_qty(row):
	# Runs before core validate() has filled conversion_factor, so a row
	# created through the API with only uom = "Carton" must be resolved here.
	factor = flt(row.conversion_factor) or _uom_factor(row.item_code, row.uom)
	return flt(row.qty) * factor


def _is_loose(row):
	stock_uom = row.stock_uom or frappe.get_cached_value("Item", row.item_code, "stock_uom")
	return not row.uom or row.uom == stock_uom


def _packing_requirements(finished_item, finished_qty, loose=False):
	"""Yield (packing_material_item, qty in its stock UOM, rule) for one
	finished row of `finished_qty` pieces. Loose rows skip the box rules."""
	setting = frappe.get_cached_doc("Packing Setting", finished_item)
	rules = setting.get("packing_materials") or []
	if not rules:
		return

	per_piece_rules = [r for r in rules if r.rule_type == "Per Piece"]
	per_pouch_rules = [r for r in rules if r.rule_type == "Per Pouch"]
	if per_pouch_rules and len(per_piece_rules) != 1:
		frappe.throw(
			_(
				"Item {0}: a 'Per Pouch' packing rule needs exactly one 'Per Piece' rule to"
				" define the Pouch quantity it divides into (found {1})."
			).format(frappe.bold(finished_item), len(per_piece_rules))
		)
	pouch_qty = finished_qty * flt(per_piece_rules[0].qty_per_unit or 0) if per_piece_rules else 0

	carton_count = 0
	if any(r.rule_type == "Per Carton" for r in rules):
		pieces_per_carton = cint(setting.pieces_per_carton)
		if pieces_per_carton <= 0:
			frappe.throw(
				_("Item {0} has a 'Per Carton' packing rule but Pieces Per Carton is not set.").format(
					frappe.bold(finished_item)
				)
			)
		carton_count = int(finished_qty // pieces_per_carton)

	for rule in rules:
		if loose and rule.rule_type in ("Per Carton", "Per Pouch"):
			continue
		if rule.rule_type == "Per Carton":
			qty = flt(carton_count) * flt(rule.qty_per_unit or 0)
		elif rule.rule_type == "Per Piece":
			qty = finished_qty * flt(rule.qty_per_unit or 0)
		else:  # Per Pouch
			pouches_per_unit = flt(rule.qty_per_unit or 0)
			qty = math.ceil(pouch_qty / pouches_per_unit) if pouches_per_unit > 0 else 0

		qty = qty * _uom_factor(rule.packing_material_item, rule.uom)
		if qty > 0:
			yield rule.packing_material_item, qty, rule


def _uom_factor(item_code, uom):
	"""How many stock-UOM units one `uom` of this item is (1 when uom is blank
	or already the stock UOM)."""
	from erpnext.stock.get_item_details import get_conversion_factor

	if not uom:
		return 1.0
	return flt(get_conversion_factor(item_code, uom).get("conversion_factor")) or 1.0


def _get_source_warehouse(doc, item_code, rule_warehouse=None):
	"""Where to consume this packing material from.

	Priority: the Warehouse set on the Packing Setting row (if it belongs to
	this Stock Entry's company), else the Stock Entry's own "Default Source
	Warehouse" header field (if the operator set one), else the packing material Item's own default
	warehouse for this company (Item > item_defaults, the standard ERPNext
	place to configure "this item always comes from warehouse X") - so the
	common case needs no per-entry setup at all.
	"""
	if rule_warehouse and frappe.db.get_value("Warehouse", rule_warehouse, "company") == doc.company:
		return rule_warehouse

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


def _sync_auto_rows(doc, required_qty, source_warehouse_by_item):
	"""Make the flagged auto rows match required_qty exactly: update the row
	of each required material, add missing ones, and drop auto rows that are
	no longer needed (finished qty went down, product removed, rule changed).
	Unflagged rows are the operator's own and are never touched."""
	seen = set()
	stale = []
	for row in doc.items:
		if not row.get("custom_is_auto_packing"):
			continue
		if row.item_code not in required_qty or row.item_code in seen:
			stale.append(row)
			continue
		seen.add(row.item_code)
		_set_packing_row(row, required_qty[row.item_code], source_warehouse_by_item[row.item_code])

	if stale:
		doc.items = [row for row in doc.items if row not in stale]
		for idx, row in enumerate(doc.items, start=1):
			row.idx = idx

	for item_code, qty in required_qty.items():
		if item_code in seen:
			continue
		item_meta = frappe.get_cached_doc("Item", item_code)
		row = doc.append(
			"items",
			{
				"item_code": item_code,
				"item_name": item_meta.item_name,
				"stock_uom": item_meta.stock_uom,
				"is_finished_item": 0,
				"custom_is_auto_packing": 1,
				"cost_center": item_meta.get("buying_cost_center"),
				"expense_account": item_meta.get("expense_account"),
			},
		)
		_set_packing_row(row, qty, source_warehouse_by_item[item_code])


def _set_packing_row(row, qty, source_warehouse):
	row.qty = qty
	row.uom = row.stock_uom or frappe.get_cached_value("Item", row.item_code, "stock_uom")
	row.conversion_factor = 1
	row.transfer_qty = qty
	row.s_warehouse = source_warehouse
	row.t_warehouse = None


def _set_packing_allocation(doc, allocation):
	doc.set("custom_packing_allocation", [])
	for (finished_item, packing_item), qty in sorted(allocation.items()):
		doc.append(
			"custom_packing_allocation",
			{
				"finished_item": finished_item,
				"packing_material_item": packing_item,
				"qty": qty,
				"stock_uom": frappe.get_cached_value("Item", packing_item, "stock_uom"),
			},
		)
