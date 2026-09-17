# Copyright (c) 2026, Enfono and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


class FinalSettlement(Document):
    def validate(self):
        if getdate(self.from_date) > getdate(self.to_date):
            frappe.throw(_("From Date cannot be after To Date."))
        self.validate_below_minimum_salary_component()
        self.validate_overlapping_settlement_period()

    def validate_below_minimum_salary_component(self):
        if not self.below_minimum_salary_component:
            return
        component_type = frappe.db.get_value(
            "Salary Component", self.below_minimum_salary_component, "type"
        )
        if component_type != "Deduction":
            frappe.throw(
                _("Below Minimum Salary Component {0} must be a Deduction-type Salary Component.").format(
                    self.below_minimum_salary_component
                )
            )

    def validate_overlapping_settlement_period(self):
        FinalSettlement = frappe.qb.DocType("Final Settlement")
        existing = (
            frappe.qb.from_(FinalSettlement)
            .select(FinalSettlement.name)
            .where(FinalSettlement.employee == self.employee)
            .where(FinalSettlement.docstatus == 1)
            .where(FinalSettlement.name != self.name)
            .where(FinalSettlement.from_date <= self.to_date)
            .where(FinalSettlement.to_date >= self.from_date)
        ).run(as_dict=True)
        if existing:
            frappe.throw(
                _(
                    "A Final Settlement {0} is already submitted for Employee {1} covering an overlapping period."
                ).format(frappe.bold(existing[0].name), self.employee)
            )

    def on_submit(self):
        if self.settlement_status == "Draft":
            frappe.throw(_("Please Calculate Settlement before submitting."))

    def on_cancel(self):
        payment_entry = self.get("payment_entry")
        if payment_entry:
            frappe.throw(
                _(
                    "Cannot cancel {0}: Payment Entry {1} is already linked. Cancel the Payment Entry first."
                ).format(self.name, payment_entry)
            )
        self.cancel_linked_incentives()

    def cancel_linked_incentives(self):
        for fieldname in (
            "employee_incentive",
            "performance_employee_incentive",
            "below_minimum_employee_incentive",
            "other_employee_incentive",
        ):
            incentive = self.get(fieldname)
            if incentive:
                self._cancel_employee_incentive(incentive)

        if self.below_minimum_deduction:
            self._cancel_if_submitted("Additional Salary", self.below_minimum_deduction)

    def _cancel_employee_incentive(self, name):
        # Employee Incentive creates its own Additional Salary on submit but has
        # no on_cancel to reverse it, so both must be cancelled explicitly here.
        additional_salary = frappe.db.get_value(
            "Additional Salary",
            {"ref_doctype": "Employee Incentive", "ref_docname": name, "docstatus": 1},
        )
        if additional_salary:
            self._cancel_if_submitted("Additional Salary", additional_salary)
        self._cancel_if_submitted("Employee Incentive", name)

    def _cancel_if_submitted(self, doctype, name):
        doc = frappe.get_doc(doctype, name)
        if doc.docstatus == 1:
            doc.cancel()
