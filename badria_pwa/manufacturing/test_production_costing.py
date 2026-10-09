# Copyright (c) 2026, Enfono and Contributors
# See license.txt
#
# Needs the "Production" Stock Entry Type and the Stock Entry / Stock Entry
# Detail custom fields from badria_pwa/fixtures (`bench migrate` first).

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from badria_pwa.manufacturing import PRODUCTION_STOCK_ENTRY_TYPE

FLOUR = "_BPT Flour"
OIL = "_BPT Oil"
POUCH = "_BPT Pouch"
BOX = "_BPT Box"
FG_A = "_BPT Samoosa"  # 0.6 kg a piece, 8 a carton, has packing rules
FG_B = "_BPT Markook"  # 0.3 kg a piece, no packing rules
WASTAGE = "_BPT Wastage"
NO_WEIGHT = "_BPT No Weight"

POSTING_DATE = "2026-01-10"


class TestProductionCosting(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = frappe.db.get_value("Company", {}, "name")
		# New Items get this as their default warehouse, which is where the
		# packing automation consumes from.
		cls.warehouse = frappe.db.get_single_value("Stock Settings", "default_warehouse") or frappe.db.get_value(
			"Warehouse", {"company": cls.company, "is_group": 0}, "name"
		)
		if not frappe.db.exists("Stock Entry Type", PRODUCTION_STOCK_ENTRY_TYPE):
			raise cls.skipTest(cls, "Production Stock Entry Type missing - run bench migrate")

		group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
		_make_item(FLOUR, "Kg", group)
		_make_item(OIL, "Kg", group)
		_make_item(POUCH, "Nos", group)
		_make_item(BOX, "Nos", group)
		_make_item(FG_A, "Nos", group, weight=0.6, carton=8)
		_make_item(FG_B, "Nos", group, weight=0.3, carton=10)
		_make_item(WASTAGE, "Kg", group)
		_make_item(NO_WEIGHT, "Nos", group)

		frappe.delete_doc_if_exists("Packing Setting", FG_A)
		frappe.get_doc(
			{
				"doctype": "Packing Setting",
				"finished_item": FG_A,
				"pieces_per_carton": 8,
				"packing_materials": [
					{"packing_material_item": POUCH, "rule_type": "Per Piece", "qty_per_unit": 1, "uom": "Nos"},
					{"packing_material_item": BOX, "rule_type": "Per Carton", "qty_per_unit": 1, "uom": "Nos"},
				],
			}
		).insert(ignore_permissions=True)

		cls.receive({FLOUR: (100, 2.0), OIL: (20, 5.0), POUCH: (1000, 0.1), BOX: (200, 1.0)}, "2026-01-01")

	def setUp(self):
		# Every test starts from the same stock: flour 100 @ 2.00, oil 20 @ 5.00.
		frappe.db.savepoint("bpt_production_costing")

	def tearDown(self):
		frappe.db.rollback(save_point="bpt_production_costing")

	@classmethod
	def receive(cls, items, posting_date):
		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = "Material Receipt"
		se.company = cls.company
		se.set_posting_time = 1
		se.posting_date = posting_date
		se.posting_time = "08:00:00"
		for item_code, (qty, rate) in items.items():
			se.append(
				"items",
				{"item_code": item_code, "qty": qty, "basic_rate": rate, "t_warehouse": cls.warehouse},
			)
		se.insert(ignore_permissions=True)
		se.submit()
		return se

	def make_entry(self, outputs, inputs=None, additional_cost=0, submit=False):
		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = PRODUCTION_STOCK_ENTRY_TYPE
		se.company = self.company
		se.set_posting_time = 1
		se.posting_date = POSTING_DATE
		se.posting_time = "10:00:00"
		for item_code, qty in inputs or [(FLOUR, 50), (OIL, 10)]:
			se.append("items", {"item_code": item_code, "qty": qty, "s_warehouse": self.warehouse})
		for item_code, qty, uom in outputs:
			se.append("items", {"item_code": item_code, "qty": qty, "uom": uom, "t_warehouse": self.warehouse})
		if additional_cost:
			se.append(
				"additional_costs",
				{
					"expense_account": frappe.db.get_value(
						"Account", {"company": self.company, "account_type": "Expenses Included In Valuation"}
					)
					or frappe.get_cached_value("Company", self.company, "default_expense_account"),
					"description": "Labour",
					"amount": additional_cost,
				},
			)
		se.insert(ignore_permissions=True)
		if submit:
			se.submit()
		return se

	def standard_entry(self, **kwargs):
		# 5 Carton (40 pcs, 24 kg) + 4 loose (2.4 kg) of A, 60 pcs B (18 kg),
		# 3 kg wastage = 47.4 kg out of 50 kg flour (100) + 10 kg oil (50).
		return self.make_entry(
			[(FG_A, 5, "Carton"), (FG_A, 4, "Nos"), (FG_B, 60, "Nos"), (WASTAGE, 3, "Kg")], **kwargs
		)

	def test_cost_is_split_by_weight_and_packing_goes_to_its_product(self):
		se = self.standard_entry(additional_cost=30)
		rows = _rows_by_item(se)

		weights = {key: flt(r.custom_weight_kg, 3) for key, r in rows.items() if r.t_warehouse}
		self.assertEqual(weights, {(FG_A, "Carton"): 24.0, (FG_A, "Nos"): 2.4, (FG_B, "Nos"): 18.0, (WASTAGE, "Kg"): 3.0})

		# Packing: 44 pouches (one per piece), 5 boxes (only the full cartons).
		self.assertEqual(flt(rows[(POUCH, "Nos")].qty), 44)
		self.assertEqual(flt(rows[(BOX, "Nos")].qty), 5)
		self.assertTrue(rows[(BOX, "Nos")].custom_is_auto_packing)
		self.assertEqual(
			{(a.finished_item, a.packing_material_item): flt(a.qty) for a in se.custom_packing_allocation},
			{(FG_A, POUCH): 44, (FG_A, BOX): 5},
		)

		# Common pool 150 over 47.4 kg; product A also carries 4.40 + 5.00 packing.
		per_kg = 150 / 47.4
		self.assertAlmostEqual(flt(rows[(FG_B, "Nos")].basic_amount), flt(18 * per_kg, 2), delta=0.011)
		self.assertAlmostEqual(flt(rows[(WASTAGE, "Kg")].basic_amount), flt(3 * per_kg, 2), delta=0.011)
		fg_a = flt(rows[(FG_A, "Carton")].basic_amount) + flt(rows[(FG_A, "Nos")].basic_amount)
		self.assertAlmostEqual(fg_a, 26.4 * per_kg + 9.40, delta=0.02)

		# Everything allocated: outputs = inputs + additional costs, to the cent.
		outputs = [r for r in se.items if r.t_warehouse]
		self.assertEqual(flt(sum(flt(r.basic_amount) for r in outputs), 2), 159.40)
		self.assertEqual(flt(sum(flt(r.additional_cost) for r in outputs), 2), 30.0)
		self.assertEqual(flt(se.total_incoming_value - se.total_outgoing_value, 2), 30.0)
		self.assertEqual(flt(se.value_difference, 2), flt(se.total_additional_costs, 2))

		se.submit()
		self.assertEqual(_stock_adjustment_balance(se), 0)

	def test_packing_rows_follow_finished_qty(self):
		se = self.standard_entry()
		# Operator adds 2 torn boxes by hand - a manual row, not an auto row.
		se.append("items", {"item_code": BOX, "qty": 2, "s_warehouse": self.warehouse})
		# ...and the cartons are cut to 0 by removing the carton row.
		se.items = [r for r in se.items if not (r.item_code == FG_A and r.uom == "Carton")]
		se.save()

		rows = [r for r in se.items if r.item_code == BOX]
		self.assertEqual([(flt(r.qty), r.custom_is_auto_packing) for r in rows], [(2, 0)])
		self.assertEqual(
			{(a.finished_item, a.packing_material_item): flt(a.qty) for a in se.custom_packing_allocation},
			{(FG_A, POUCH): 4},
		)
		# Manual box (2.00) is pooled; A carries only its 4 pouches (0.40).
		pool = 150 + 2.0
		total_kg = 2.4 + 18 + 3
		rows = _rows_by_item(se)
		self.assertAlmostEqual(
			flt(rows[(FG_A, "Nos")].basic_amount), flt(2.4 * pool / total_kg + 0.40, 2), delta=0.011
		)
		self.assertEqual(flt(se.total_incoming_value - se.total_outgoing_value, 2), 0)

	def test_additional_cost_gl_has_no_rounding_residue(self):
		# Core's own GL split of Additional Costs rounds per row and could
		# credit e.g. 45.01 for a 45.00 table, leaving 0.01 on Stock Adjustment.
		for amount in (20, 35, 45, 50, 70, 75):
			se = self.make_entry(
				[(FG_A, 1, "Carton"), (FG_A, 3, "Nos"), (FG_B, 7, "Nos"), (WASTAGE, 0.7, "Kg")],
				inputs=[(FLOUR, 7), (OIL, 1.3)],
				additional_cost=amount,
				submit=True,
			)
			cost_account = se.additional_costs[0].expense_account
			credited = frappe.db.sql(
				"""select sum(credit - debit) from `tabGL Entry`
				where voucher_no = %s and account = %s and is_cancelled = 0""",
				(se.name, cost_account),
			)[0][0]
			self.assertEqual(flt(credited, 2), amount, f"additional cost {amount}")
			self.assertEqual(_stock_adjustment_balance(se), 0, f"additional cost {amount}")

	def test_loose_pieces_get_pouch_but_no_box(self):
		# 8 loose pieces are a full carton's worth, but loose stock is not boxed.
		se = self.make_entry([(FG_A, 2, "Carton"), (FG_A, 8, "Nos")])
		self.assertEqual(
			{(a.finished_item, a.packing_material_item): flt(a.qty) for a in se.custom_packing_allocation},
			{(FG_A, POUCH): 24, (FG_A, BOX): 2},
		)

	def test_missing_weight_blocks_save(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_entry([(FG_B, 10, "Nos"), (NO_WEIGHT, 5, "Nos")])

	def test_backdated_receipt_reposts_production_cost(self):
		se = self.standard_entry(additional_cost=30, submit=True)
		before = _rows_by_item(se)[(FG_B, "Nos")]

		# A dearer flour layer dated BEFORE the original receipt: FIFO now
		# consumes 50 kg of it at 4.00 instead of 2.00 => +100 into the pool.
		self.receive({FLOUR: (50, 4.0)}, "2025-12-31")

		se.reload()
		after = _rows_by_item(se)[(FG_B, "Nos")]
		self.assertAlmostEqual(flt(after.basic_amount), flt(18 * 250 / 47.4, 2), delta=0.011)
		self.assertGreater(flt(after.valuation_rate), flt(before.valuation_rate))
		self.assertEqual(flt(se.total_incoming_value - se.total_outgoing_value, 2), 30.0)

		sle_rate = frappe.db.get_value(
			"Stock Ledger Entry",
			{"voucher_no": se.name, "voucher_detail_no": after.name, "is_cancelled": 0},
			"incoming_rate",
		)
		self.assertAlmostEqual(flt(sle_rate), flt(after.valuation_rate), places=4)
		self.assertEqual(_stock_adjustment_balance(se), 0)


def _make_item(item_code, stock_uom, item_group, weight=0, carton=0):
	if frappe.db.exists("Item", item_code):
		return
	item = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": item_code,
			"item_name": item_code,
			"item_group": item_group,
			"stock_uom": stock_uom,
			"is_stock_item": 1,
			"valuation_method": "FIFO",
			"weight_per_unit": weight,
			"weight_uom": "Kg" if weight else None,
		}
	)
	if carton:
		item.append("uoms", {"uom": "Carton", "conversion_factor": carton})
	item.insert(ignore_permissions=True)


def _rows_by_item(se):
	return {(r.item_code, r.uom): r for r in se.items}


def _stock_adjustment_balance(se):
	account = frappe.get_cached_value("Company", se.company, "stock_adjustment_account")
	balance = frappe.db.sql(
		"""select sum(debit - credit) from `tabGL Entry`
		where voucher_type = 'Stock Entry' and voucher_no = %s and account = %s and is_cancelled = 0""",
		(se.name, account),
	)[0][0]
	return flt(balance, 2)
