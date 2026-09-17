# Copyright (c) 2026, Enfono and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import today

from badria_pwa.api.production_entry import create_from_attendance_tool, get_production_entries


class IntegrationTestProductionEntry(FrappeTestCase):
    def setUp(self):
        self.employee = frappe.db.get_value("Employee", {"status": "Active"}, "name")
        if not self.employee:
            self.skipTest("No active Employee record available in this site to test against.")

    def tearDown(self):
        frappe.db.rollback()

    def test_duplicate_entry_for_same_employee_and_day_is_blocked(self):
        first = frappe.get_doc(
            {
                "doctype": "Production Entry",
                "employee": self.employee,
                "date": today(),
                "production_quantity": 10,
            }
        ).insert()
        first.submit()

        duplicate = frappe.get_doc(
            {
                "doctype": "Production Entry",
                "employee": self.employee,
                "date": today(),
                "production_quantity": 5,
            }
        )
        self.assertRaises(frappe.ValidationError, duplicate.insert)

    def test_zero_quantity_is_rejected(self):
        doc = frappe.get_doc(
            {
                "doctype": "Production Entry",
                "employee": self.employee,
                "date": today(),
                "production_quantity": 0,
            }
        )
        self.assertRaises(frappe.ValidationError, doc.insert)

    def test_create_from_attendance_tool_creates_entries_and_reports_row_errors(self):
        result = create_from_attendance_tool(
            date=today(),
            entries=[
                {"employee": self.employee, "quantity": 10},
                {"employee": self.employee, "quantity": 0},
            ],
        )

        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(result["created"][0]["employee"], self.employee)
        self.assertEqual(len(result["errors"]), 1)
        self.assertTrue(
            frappe.db.exists("Production Entry", {"employee": self.employee, "date": today()})
        )

    def test_create_from_attendance_tool_skips_duplicate_with_error_message(self):
        create_from_attendance_tool(
            date=today(),
            entries=[{"employee": self.employee, "quantity": 10}],
        )

        result = create_from_attendance_tool(
            date=today(),
            entries=[{"employee": self.employee, "quantity": 5}],
        )

        self.assertEqual(len(result["created"]), 0)
        self.assertEqual(len(result["errors"]), 1)

    def test_get_production_entries_returns_rows_for_the_date(self):
        create_from_attendance_tool(
            date=today(),
            entries=[{"employee": self.employee, "quantity": 10}],
        )

        rows = get_production_entries(date=today())

        self.assertTrue(any(row.employee == self.employee for row in rows))
