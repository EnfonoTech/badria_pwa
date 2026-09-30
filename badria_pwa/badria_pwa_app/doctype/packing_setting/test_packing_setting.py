# Copyright (c) 2026, Enfono and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from badria_pwa.badria_pwa_app.doctype.packing_setting.packing_setting import (
	get_item_default_warehouse,
)
from badria_pwa.manufacturing.packing_automation import _get_source_warehouse


class TestPackingSetting(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = frappe.db.get_value("Company", {}, "name")
		cls.warehouse = frappe.db.get_value("Warehouse", {"company": cls.company, "is_group": 0}, "name")
		if not (cls.company and cls.warehouse):
			raise cls.skipTest(cls, "Needs a Company with a non-group Warehouse")

		cls.item_code = "_Test Packing Box"
		if not frappe.db.exists("Item", cls.item_code):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": cls.item_code,
					"item_name": cls.item_code,
					"item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
					"stock_uom": frappe.db.get_value("UOM", {}, "name"),
					"is_stock_item": 1,
					"item_defaults": [{"company": cls.company, "default_warehouse": cls.warehouse}],
				}
			).insert(ignore_permissions=True)

	def test_get_item_default_warehouse_for_company(self):
		self.assertEqual(get_item_default_warehouse(self.item_code, self.company), self.warehouse)

	def test_get_item_default_warehouse_falls_back_to_any_company(self):
		self.assertEqual(get_item_default_warehouse(self.item_code, "_No Such Company"), self.warehouse)

	def test_rule_warehouse_wins_over_stock_entry_source(self):
		doc = frappe._dict(company=self.company, from_warehouse="_Other Warehouse")
		self.assertEqual(_get_source_warehouse(doc, self.item_code, self.warehouse), self.warehouse)

	def test_rule_warehouse_of_other_company_is_ignored(self):
		doc = frappe._dict(company="_No Such Company", from_warehouse="_Header Warehouse")
		self.assertEqual(_get_source_warehouse(doc, self.item_code, self.warehouse), "_Header Warehouse")

	def test_falls_back_to_item_default_warehouse(self):
		doc = frappe._dict(company=self.company, from_warehouse=None)
		self.assertEqual(_get_source_warehouse(doc, self.item_code, None), self.warehouse)
