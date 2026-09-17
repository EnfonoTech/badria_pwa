# Copyright (c) 2026, Enfono and Contributors
# See license.txt
#
# NOTE: custom_shift_type is a Custom Field shipped via
# badria_pwa/fixtures/custom_field.json - these tests only pass once that
# fixture has been migrated onto the site (`bench migrate`).

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate

from badria_pwa.manufacturing.stock_entry import get_shift_employees


class IntegrationTestGetShiftEmployees(FrappeTestCase):
    def setUp(self):
        already_assigned = set(frappe.get_all("Shift Assignment", pluck="employee"))
        candidates = [
            e for e in frappe.get_all("Employee", pluck="name", limit=200) if e not in already_assigned
        ]
        if len(candidates) < 2:
            self.skipTest("Not enough Employees without an existing Shift Assignment to test against.")
        self.employee_in_shift = candidates[0]
        self.employee_not_in_shift = candidates[1]

        if not frappe.db.exists("Shift Type", "_Test Shift Type"):
            frappe.get_doc(
                {
                    "doctype": "Shift Type",
                    "name": "_Test Shift Type",
                    "start_time": "09:00:00",
                    "end_time": "17:00:00",
                }
            ).insert(ignore_permissions=True)

        frappe.get_doc(
            {
                "doctype": "Shift Assignment",
                "employee": self.employee_in_shift,
                "shift_type": "_Test Shift Type",
                "status": "Active",
                "start_date": getdate("2030-01-01"),
                "end_date": getdate("2030-01-31"),
            }
        ).insert(ignore_permissions=True).submit()

    def test_returns_employee_when_posting_date_is_within_assignment_range(self):
        results = get_shift_employees("_Test Shift Type", getdate("2030-01-15"))
        result_employees = [r["employee"] for r in results]
        self.assertIn(self.employee_in_shift, result_employees)
        self.assertNotIn(self.employee_not_in_shift, result_employees)

    def test_excludes_employee_when_posting_date_is_outside_assignment_range(self):
        results = get_shift_employees("_Test Shift Type", getdate("2030-03-01"))
        result_employees = [r["employee"] for r in results]
        self.assertNotIn(self.employee_in_shift, result_employees)

    def test_returns_empty_when_no_shift_type_given(self):
        self.assertEqual(get_shift_employees(None, getdate("2030-01-15")), [])

    def test_returns_empty_when_no_posting_date_given(self):
        self.assertEqual(get_shift_employees("_Test Shift Type", None), [])
