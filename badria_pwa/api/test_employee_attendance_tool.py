# Copyright (c) 2026, Enfono and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate

from badria_pwa.api.employee_attendance_tool import get_shift_employees_for_date


class IntegrationTestGetShiftEmployeesForDate(FrappeTestCase):
    def setUp(self):
        already_assigned = set(frappe.get_all("Shift Assignment", pluck="employee"))
        candidates = [
            e for e in frappe.get_all("Employee", pluck="name", limit=200) if e not in already_assigned
        ]
        if len(candidates) < 2:
            self.skipTest("Not enough Employees without an existing Shift Assignment to test against.")
        self.employee_on_shift = candidates[0]
        self.employee_not_on_shift = candidates[1]

        if not frappe.db.exists("Shift Type", "_Test EAT Shift Type"):
            frappe.get_doc(
                {
                    "doctype": "Shift Type",
                    "name": "_Test EAT Shift Type",
                    "start_time": "09:00:00",
                    "end_time": "17:00:00",
                }
            ).insert(ignore_permissions=True)

        frappe.get_doc(
            {
                "doctype": "Shift Assignment",
                "employee": self.employee_on_shift,
                "shift_type": "_Test EAT Shift Type",
                "status": "Active",
                "start_date": getdate("2030-01-01"),
                "end_date": getdate("2030-01-31"),
            }
        ).insert(ignore_permissions=True).submit()

    def test_returns_only_employees_on_the_given_shift_and_date(self):
        result = get_shift_employees_for_date("_Test EAT Shift Type", getdate("2030-01-15"))
        self.assertIn(self.employee_on_shift, result)
        self.assertNotIn(self.employee_not_on_shift, result)

    def test_excludes_employee_outside_the_assignment_date_range(self):
        result = get_shift_employees_for_date("_Test EAT Shift Type", getdate("2030-03-01"))
        self.assertNotIn(self.employee_on_shift, result)
