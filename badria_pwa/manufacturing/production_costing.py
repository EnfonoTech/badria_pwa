# badria_pwa.manufacturing.production_costing - Weight-based cost allocation
# for the "Production" Stock Entry Type (purpose Repack).
#
# One production batch turns a pool of raw material into many finished goods
# (each usually twice, as a Carton row and a loose Nos row) plus wastage.
# Core Repack gives every finished unit the same rate, which is meaningless
# when a "piece" of one product weighs twice as much as another. Here:
#
#   * each product's own packing (auto packing rows, split per product in
#     custom_packing_allocation) is charged to that product only, at the
#     packing material's average rate on this entry;
#   * everything else consumed (raw material, manual packing rows) is the
#     common pool, split over every output row - wastage included - by kg
#     (stock qty x kg per stock unit, stored on the row as custom_weight_kg);
#   * the Additional Costs table keeps core's split by value; only its
#     rounding is fixed.
#
# Rounding remainders go to the heaviest row, so the outputs carry exactly the
# input cost plus additional costs and nothing is left on Stock Adjustment.
#
# This lives in the controller (override_doctype_class) and not in a
# doc_event because a backdated repost re-runs calculate_rate_and_amount()
# directly (erpnext stock_ledger.recalculate_amounts_in_stock_entry); a hook
# would never run there and the finished goods would keep stale rates. On
# that repost path the stored custom_weight_kg is reused, so editing an
# Item's weight later does not re-split old entries.

import frappe
from frappe import _
from frappe.utils import flt

from erpnext.accounts.general_ledger import process_gl_map
from erpnext.stock.doctype.item.item import get_uom_conv_factor
from erpnext.stock.doctype.stock_entry.stock_entry import StockEntry

from badria_pwa.manufacturing import is_production_entry

KG = "Kg"


class BadriaStockEntry(StockEntry):
	def validate_repack_entry(self):
		# Core insists on "Set Basic Rate Manually" for a multi-product
		# Repack; a Production entry's rates are set by the allocation below.
		if is_production_entry(self):
			return
		super().validate_repack_entry()

	def set_basic_rate(self, reset_outgoing_rate=True, raise_error_if_no_rate=True):
		if not is_production_entry(self):
			return super().set_basic_rate(reset_outgoing_rate, raise_error_if_no_rate)

		self.set_rate_for_outgoing_items(reset_outgoing_rate, raise_error_if_no_rate)
		# reset_outgoing_rate is False only when the stock ledger recalculates
		# a submitted entry during a repost.
		allocate_production_cost(self, repost=not reset_outgoing_rate)

	def distribute_additional_costs(self):
		super().distribute_additional_costs()
		if is_production_entry(self):
			fix_additional_cost_rounding(self)

	def calculate_rate_and_amount(self, reset_outgoing_rate=True, raise_error_if_no_rate=True):
		super().calculate_rate_and_amount(reset_outgoing_rate, raise_error_if_no_rate)
		if is_production_entry(self) and reset_outgoing_rate:
			check_fully_allocated(self)

	def get_gl_entries(self, warehouse_account):
		# Core re-splits the Additional Costs table per row by basic_amount and
		# rounds every line without carrying the remainder, so a 60.00 table
		# can be credited as 60.01 and leave 0.01 on Stock Adjustment. Post
		# those lines from the rows' additional_cost instead, which already
		# add up to the table exactly (fix_additional_cost_rounding).
		if not is_production_entry(self) or not self.get("additional_costs"):
			return super().get_gl_entries(warehouse_account)

		additional_costs = self.additional_costs
		self.additional_costs = []
		try:
			gl_entries = super().get_gl_entries(warehouse_account)
		finally:
			self.additional_costs = additional_costs

		return gl_entries + process_gl_map(
			get_additional_cost_gl_entries(self),
			from_repost=frappe.flags.through_repost_item_valuation,
		)


def allocate_production_cost(doc, repost=False):
	outputs = [d for d in doc.items if d.t_warehouse and not d.s_warehouse]
	if not outputs:
		return

	precision = outputs[0].precision("basic_amount")
	inputs = [d for d in doc.items if d.s_warehouse and not d.t_warehouse]

	# Rows the operator priced themselves keep their rate, like core does.
	fixed = [d for d in outputs if d.set_basic_rate_manually or d.allow_zero_valuation_rate]
	allocated = [d for d in outputs if not (d.set_basic_rate_manually or d.allow_zero_valuation_rate)]
	for d in fixed:
		if d.allow_zero_valuation_rate:
			d.basic_rate = 0.0
		d.basic_amount = flt(flt(d.transfer_qty) * flt(d.basic_rate), precision)

	set_output_weights(allocated, repost)
	input_cost = sum(flt(d.basic_amount) for d in inputs)
	if not allocated:
		unallocated = flt(input_cost - sum(flt(d.basic_amount) for d in fixed), precision)
		if unallocated and not repost:
			frappe.throw(
				_(
					"Every output row is priced manually, so {0} of the material consumed is not"
					" allocated. Untick Set Basic Rate Manually on at least one row."
				).format(frappe.format_value(unallocated, {"fieldtype": "Currency"}))
			)
		return

	direct = get_direct_packing_cost(doc, inputs, {d.item_code for d in allocated}, precision)
	common = flt(input_cost - sum(direct.values()) - sum(flt(d.basic_amount) for d in fixed), precision)
	if common < 0 and not repost:
		frappe.throw(
			_(
				"Manually priced output rows ({0}) are worth more than the material consumed on this entry."
			).format(", ".join(sorted({d.item_code for d in fixed})))
		)

	basis = [_weight_basis(d) for d in allocated]
	total_basis = sum(basis)
	amounts = [common * b / total_basis if total_basis else 0 for b in basis]

	for product, cost in direct.items():
		product_idx = [i for i, d in enumerate(allocated) if d.item_code == product]
		product_basis = sum(basis[i] for i in product_idx)
		for i in product_idx:
			amounts[i] += cost * basis[i] / product_basis if product_basis else cost / len(product_idx)

	amounts = [flt(a, precision) for a in amounts]
	heaviest = max(range(len(allocated)), key=lambda i: basis[i])
	amounts[heaviest] = flt(amounts[heaviest] + common + sum(direct.values()) - sum(amounts), precision)

	for d, amount in zip(allocated, amounts):
		d.basic_amount = amount
		# Not rounded, same as core: basic_amount is the exact figure.
		d.basic_rate = amount / flt(d.transfer_qty) if flt(d.transfer_qty) else 0.0


def get_direct_packing_cost(doc, inputs, products, precision):
	"""Cost of each product's own packing: {finished_item: amount}.

	Each auto packing row is costed at the row's own FIFO rate by core; here
	the cost of a material is shared out by the quantities stored in
	custom_packing_allocation, i.e. every product pays the material's average
	rate on this entry regardless of which FIFO layer a row happened to hit.
	"""
	direct = {}
	for packing_item in {d.item_code for d in inputs if d.get("custom_is_auto_packing")}:
		cost = sum(
			flt(d.basic_amount)
			for d in inputs
			if d.get("custom_is_auto_packing") and d.item_code == packing_item
		)
		lines = [
			line
			for line in doc.get("custom_packing_allocation") or []
			if line.packing_material_item == packing_item
			and line.finished_item in products
			and flt(line.qty) > 0
		]
		total_qty = sum(flt(line.qty) for line in lines)
		if not cost or not total_qty:
			# Nothing to attribute: the material stays in the common pool.
			continue

		shares = {}
		for line in lines:
			shares[line.finished_item] = shares.get(line.finished_item, 0) + flt(line.qty)
		split = {product: flt(cost * qty / total_qty, precision) for product, qty in shares.items()}
		largest = max(shares, key=shares.get)
		split[largest] = flt(split[largest] + cost - sum(split.values()), precision)

		for product, amount in split.items():
			direct[product] = direct.get(product, 0) + amount
	return direct


def set_output_weights(rows, repost=False):
	"""Store kg per output row in custom_weight_kg. On a repost the stored
	value is kept; on a normal save a product without a weight is an error."""
	missing = set()
	for d in rows:
		if repost and flt(d.get("custom_weight_kg")) > 0:
			continue
		if not flt(d.transfer_qty):
			# Template rows left at 0 are not produced; they need no weight.
			d.custom_weight_kg = 0
			continue
		kg_per_unit = get_kg_per_stock_unit(d.item_code, d.stock_uom)
		if not kg_per_unit:
			missing.add(d.item_code)
		d.custom_weight_kg = flt(d.transfer_qty) * kg_per_unit

	if missing and not repost:
		frappe.throw(
			_(
				"Set the weight of these items before saving a Production entry"
				" (Item > Weight Per Unit, per {0}):<br>{1}"
			).format(_("stock UOM"), "<br>".join(frappe.bold(i) for i in sorted(missing))),
			title=_("Missing Item Weight"),
		)


def get_kg_per_stock_unit(item_code, stock_uom):
	"""Kg in one stock unit: the UOM itself when it is a weight (wastage kept
	in Kg), else the Item's Weight Per Unit converted to Kg. 0 if unknown."""
	if stock_uom:
		factor = get_uom_conv_factor(stock_uom, KG)
		if factor:
			return flt(factor)

	weight_per_unit, weight_uom = frappe.get_cached_value(
		"Item", item_code, ["weight_per_unit", "weight_uom"]
	)
	if not flt(weight_per_unit):
		return 0.0
	factor = get_uom_conv_factor(weight_uom or KG, KG)
	return flt(weight_per_unit) * flt(factor) if factor else 0.0


def _weight_basis(d):
	# Falls back to qty only on a repost of an entry that has no stored
	# weight; a normal save never gets here without a weight.
	return flt(d.get("custom_weight_kg")) or flt(d.transfer_qty)


def fix_additional_cost_rounding(doc):
	"""Core spreads additional costs by value but leaves the per-row figures
	unrounded; round them and put the remainder on the largest row so the
	rows add up to the Additional Costs table exactly."""
	total = flt(doc.total_additional_costs)
	rows = [d for d in doc.items if d.is_finished_item and d.t_warehouse and flt(d.additional_cost)]
	if not total or not rows:
		return

	precision = rows[0].precision("additional_cost")
	for d in rows:
		d.additional_cost = flt(d.additional_cost, precision)
	largest = max(rows, key=lambda d: flt(d.basic_amount))
	largest.additional_cost = flt(
		largest.additional_cost + total - sum(flt(d.additional_cost) for d in rows), precision
	)


def get_additional_cost_gl_entries(doc):
	"""Same entries core posts for Additional Costs (credit the cost account,
	reverse it on the row's expense account), but each account's lines are
	shared out by the rows' additional_cost and the rounding remainder goes
	to the largest row, so every cost account is credited its exact total."""
	rows = [d for d in doc.items if d.t_warehouse and flt(d.additional_cost)]
	total = sum(flt(d.additional_cost) for d in rows)
	if not rows or not total:
		return []

	precision = doc.get_debit_field_precision()
	by_account = {}  # expense_account -> [amount, base_amount]
	for t in doc.get("additional_costs"):
		totals = by_account.setdefault(t.expense_account, [0.0, 0.0])
		totals[0] += flt(t.amount)
		totals[1] += flt(t.base_amount)

	largest = max(range(len(rows)), key=lambda i: flt(rows[i].additional_cost))
	remarks = doc.get("remarks") or _("Accounting Entry for Stock")
	gl_entries = []
	for account, (amount, base_amount) in by_account.items():
		amounts = [flt(amount * flt(d.additional_cost) / total, precision) for d in rows]
		base_amounts = [flt(base_amount * flt(d.additional_cost) / total, precision) for d in rows]
		amounts[largest] = flt(amounts[largest] + amount - sum(amounts), precision)
		base_amounts[largest] = flt(base_amounts[largest] + base_amount - sum(base_amounts), precision)

		for d, row_amount, row_base_amount in zip(rows, amounts, base_amounts):
			if not row_amount and not row_base_amount:
				continue
			gl_entries.append(
				doc.get_gl_dict(
					{
						"account": account,
						"against": d.expense_account,
						"cost_center": d.cost_center,
						"remarks": remarks,
						"credit_in_account_currency": row_amount,
						"credit": row_base_amount,
					},
					item=d,
				)
			)
			gl_entries.append(
				doc.get_gl_dict(
					{
						"account": d.expense_account,
						"against": account,
						"cost_center": d.cost_center,
						"remarks": remarks,
						# negative credit instead of debit, as core does
						"credit": -1 * row_base_amount,
					},
					item=d,
				)
			)
	return gl_entries


def check_fully_allocated(doc):
	precision = doc.precision("value_difference")
	unallocated = flt(
		flt(doc.total_incoming_value) - flt(doc.total_outgoing_value) - flt(doc.total_additional_costs),
		precision,
	)
	if unallocated:
		frappe.throw(
			_(
				"Production cost allocation is off by {0}: output value must equal material consumed"
				" plus additional costs."
			).format(frappe.format_value(unallocated, {"fieldtype": "Currency"}))
		)
