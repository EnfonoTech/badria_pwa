# badria_pwa.api.final_settlement - Settlement calculation and Payment Entry automation

import frappe
from frappe import _
from frappe.query_builder import Order
from frappe.query_builder.functions import Count, Sum
from frappe.utils import cint, flt, getdate


@frappe.whitelist()
def calculate_settlement(name):
    doc = frappe.get_doc("Final Settlement", name)

    if not frappe.has_permission(doc.doctype, "write", doc):
        frappe.throw(_("You are not permitted to calculate this settlement."))

    if doc.docstatus != 0:
        frappe.throw(_("Settlement can only be (re)calculated while in Draft."))

    if not (doc.employee and doc.from_date and doc.to_date):
        frappe.throw(_("Employee, From Date and To Date are required before calculating."))

    if getdate(doc.from_date) > getdate(doc.to_date):
        frappe.throw(_("From Date cannot be after To Date."))

    values = _compute_settlement(doc)
    frappe.db.set_value("Final Settlement", doc.name, values, update_modified=True)

    return values


def _compute_settlement(doc):
    from_date = getdate(doc.from_date)
    to_date = getdate(doc.to_date)
    employee = doc.employee
    settlement_type = doc.settlement_type or "Production"

    if not frappe.db.exists("Employee", employee):
        frappe.throw(_("Employee {0} not found.").format(employee))

    salary_structure, basic_salary = _get_salary_structure_assignment(employee, to_date)

    working_days = _get_attendance(employee, from_date, to_date, settlement_type)

    if settlement_type == "Sales":
        type_fields = {**_production_field_defaults(), **_compute_sales_incentive(doc, from_date, to_date)}
        incentive_for_gross = type_fields["total_incentive"]
        below_minimum_incentive_amount = 0.0
    else:
        minimum_production_per_day = cint(doc.minimum_production_per_day) or 20
        below_minimum_days, below_minimum_quantity = _get_below_minimum_production(
            employee, from_date, to_date, minimum_production_per_day
        )
        # Below-minimum days do NOT reduce Working Days or Total Basic Salary -
        # only actual Attendance absences do that. Below-minimum instead comes
        # off Gross Settlement directly as its own deduction line (per the
        # client's confirmed formula), not folded into the incentive or the
        # basic salary figure.
        above_minimum_days = max(0.0, working_days - below_minimum_days)

        type_fields = {
            **_sales_field_defaults(),
            **_compute_production_incentive(
                doc,
                employee,
                from_date,
                to_date,
                working_days,
                minimum_production_per_day,
                below_minimum_days,
                below_minimum_quantity,
                above_minimum_days,
            ),
        }
        incentive_for_gross = type_fields["incentive_amount"]
        below_minimum_incentive_amount = flt(doc.below_minimum_incentive_amount)

    total_days_in_period = (to_date - from_date).days + 1
    non_working_days = max(0.0, total_days_in_period - working_days)

    total_basic_salary = flt(basic_salary) * (working_days / 30.0)
    other_allowances = flt(doc.other_allowances)
    other_additions = flt(doc.other_additions)
    other_deductions = flt(doc.other_deductions)

    gross_settlement = (
        total_basic_salary
        + incentive_for_gross
        + other_allowances
        + other_additions
        - other_deductions
        - below_minimum_incentive_amount
    )

    total_advance_paid = _get_total_advance_paid(employee, from_date, to_date)
    total_payment_entry_paid = _get_total_payment_entry_paid(employee, from_date, to_date)

    balance_payable = gross_settlement - total_advance_paid - total_payment_entry_paid

    return {
        "salary_structure": salary_structure,
        "basic_salary": basic_salary,
        "working_days": working_days,
        "non_working_days": non_working_days,
        **type_fields,
        "total_basic_salary": total_basic_salary,
        "other_allowances": other_allowances,
        "other_additions": other_additions,
        "other_deductions": other_deductions,
        "gross_settlement": gross_settlement,
        "total_advance_paid": total_advance_paid,
        "total_payment_entry_paid": total_payment_entry_paid,
        "balance_payable": balance_payable,
        "settlement_status": "Calculated",
    }


def _compute_production_incentive(
    doc,
    employee,
    from_date,
    to_date,
    working_days,
    minimum_production_per_day,
    below_minimum_days,
    below_minimum_quantity,
    above_minimum_days,
):
    incentive_rate = flt(doc.incentive_rate) or 4

    total_production = _get_total_production(employee, from_date, to_date)
    minimum_required_production = working_days * minimum_production_per_day
    additional_production = max(0.0, total_production - minimum_required_production)
    incentive_amount = additional_production * incentive_rate

    return {
        "total_production": total_production,
        "minimum_production_per_day": minimum_production_per_day,
        "minimum_required_production": minimum_required_production,
        "additional_production": additional_production,
        "incentive_rate": incentive_rate,
        "incentive_amount": incentive_amount,
        "above_minimum_days": above_minimum_days,
        "below_minimum_days": below_minimum_days,
        "below_minimum_quantity": below_minimum_quantity,
    }


def _production_field_defaults():
    return {
        "total_production": 0.0,
        "minimum_production_per_day": 0,
        "minimum_required_production": 0.0,
        "additional_production": 0.0,
        "incentive_rate": 0.0,
        "incentive_amount": 0.0,
        "above_minimum_days": 0.0,
        "below_minimum_days": 0.0,
        "below_minimum_quantity": 0.0,
    }


def _compute_sales_incentive(doc, from_date, to_date):
    collection_totals = _get_sales_collection_totals(doc.employee, doc.company, from_date, to_date)
    if collection_totals is None:
        # No linked Sales Person for this employee - nothing to derive from the
        # Sales Collection Report, fall back to the manually entered field.
        total_cash_collected = flt(doc.total_cash_collected)
        total_sales_amount = 0.0
        total_outstanding_amount = 0.0
        collection_percentage = 0.0
    else:
        total_cash_collected = collection_totals["total_payment_collected"]
        total_sales_amount = collection_totals["total_sales_amount"]
        total_outstanding_amount = collection_totals["total_outstanding_amount"]
        collection_percentage = collection_totals["collection_percentage"]

    vat_percentage = flt(doc.vat_percentage) or 15

    # Total Sales Amount (Grand Total) is VAT-inclusive: back out VAT the same way.
    total_sales_amount_excl_vat = flt(total_sales_amount / (1 + vat_percentage / 100), 2)

    # Total Cash Collected is VAT-inclusive: Net Sales = Cash Collected / (1 + VAT%).
    net_sales = flt(total_cash_collected / (1 + vat_percentage / 100), 2)
    vat_amount = total_cash_collected - net_sales

    fixed_incentive_percentage = flt(doc.fixed_incentive_percentage) or 4
    fixed_incentive_amount = net_sales * fixed_incentive_percentage / 100

    performance_incentive_percentage = flt(doc.performance_incentive_percentage)
    if performance_incentive_percentage > 1:
        frappe.throw(_("Performance Incentive % cannot exceed 1%."))
    performance_incentive_amount = net_sales * performance_incentive_percentage / 100

    total_incentive = fixed_incentive_amount + performance_incentive_amount

    return {
        "total_sales_amount": total_sales_amount,
        "total_sales_amount_excl_vat": total_sales_amount_excl_vat,
        "total_cash_collected": total_cash_collected,
        "total_outstanding_amount": total_outstanding_amount,
        "collection_percentage": collection_percentage,
        "vat_percentage": vat_percentage,
        "vat_amount": vat_amount,
        "net_sales": net_sales,
        "fixed_incentive_percentage": fixed_incentive_percentage,
        "fixed_incentive_amount": fixed_incentive_amount,
        "performance_incentive_percentage": performance_incentive_percentage,
        "performance_incentive_amount": performance_incentive_amount,
        "total_incentive": total_incentive,
    }


def _sales_field_defaults():
    return {
        "total_sales_amount": 0.0,
        "total_sales_amount_excl_vat": 0.0,
        "total_cash_collected": 0.0,
        "total_outstanding_amount": 0.0,
        "collection_percentage": 0.0,
        "vat_percentage": 0.0,
        "vat_amount": 0.0,
        "net_sales": 0.0,
        "fixed_incentive_percentage": 0.0,
        "fixed_incentive_amount": 0.0,
        "performance_incentive_percentage": 0.0,
        "performance_incentive_amount": 0.0,
        "total_incentive": 0.0,
    }


def _get_salary_structure_assignment(employee, as_of_date):
    SSA = frappe.qb.DocType("Salary Structure Assignment")
    rows = (
        frappe.qb.from_(SSA)
        .select(SSA.salary_structure, SSA.base)
        .where(SSA.employee == employee)
        .where(SSA.docstatus == 1)
        .where(SSA.from_date <= as_of_date)
        .orderby(SSA.from_date, order=Order.desc)
        .limit(1)
    ).run(as_dict=True)
    if not rows:
        return None, 0
    return rows[0].salary_structure, flt(rows[0].base)


def _get_attendance(employee, from_date, to_date, settlement_type="Production"):
    # Production has no paid-leave concept - only "Present"/"Half Day" attendance
    # count towards payment days, so "On Leave" rows are ignored entirely (same
    # as Absent) for that settlement type. Sales settlements are eligible for
    # paid leave, so payment days there also include "On Leave" days whose
    # Leave Type is not Leave Without Pay (full pay), or Partially Paid Leave
    # (fractional pay via Leave Type.fraction_of_daily_salary_per_leave).
    Attendance = frappe.qb.DocType("Attendance")
    rows = (
        frappe.qb.from_(Attendance)
        .select(Attendance.status, Attendance.leave_type, Count(Attendance.name).as_("count"))
        .where(Attendance.employee == employee)
        .where(Attendance.docstatus == 1)
        .where(Attendance.attendance_date.between(from_date, to_date))
        .groupby(Attendance.status, Attendance.leave_type)
    ).run(as_dict=True)

    present_days = 0.0
    half_days = 0.0
    leave_counts = {}
    for row in rows:
        if row.status == "Present":
            present_days += flt(row["count"])
        elif row.status == "Half Day":
            half_days += flt(row["count"])
        elif row.status == "On Leave" and settlement_type == "Sales":
            leave_counts[row.leave_type] = leave_counts.get(row.leave_type, 0.0) + flt(row["count"])

    payment_days = present_days + half_days * 0.5

    if leave_counts:
        leave_type_info = {
            lt.name: lt
            for lt in frappe.get_all(
                "Leave Type",
                filters={"name": ["in", list(leave_counts.keys())]},
                fields=["name", "is_lwp", "is_ppl", "fraction_of_daily_salary_per_leave"],
            )
        }
        for leave_type, count in leave_counts.items():
            lt = leave_type_info.get(leave_type)
            if lt and lt.is_lwp:
                continue  # Leave Without Pay - unpaid, do not add to payment days.
            elif lt and lt.is_ppl:
                payment_days += count * flt(lt.fraction_of_daily_salary_per_leave)
            else:
                payment_days += count  # fully paid leave

    return payment_days


def _get_total_production(employee, from_date, to_date):
    ProductionEntry = frappe.qb.DocType("Production Entry")
    result = (
        frappe.qb.from_(ProductionEntry)
        .select(Sum(ProductionEntry.production_quantity).as_("total"))
        .where(ProductionEntry.employee == employee)
        .where(ProductionEntry.docstatus == 1)
        .where(ProductionEntry.date.between(from_date, to_date))
    ).run(as_dict=True)
    return flt(result[0].total) if result and result[0].total else 0.0


def _get_daily_production(employee, from_date, to_date):
    ProductionEntry = frappe.qb.DocType("Production Entry")
    rows = (
        frappe.qb.from_(ProductionEntry)
        .select(ProductionEntry.date, Sum(ProductionEntry.production_quantity).as_("total"))
        .where(ProductionEntry.employee == employee)
        .where(ProductionEntry.docstatus == 1)
        .where(ProductionEntry.date.between(from_date, to_date))
        .groupby(ProductionEntry.date)
    ).run(as_dict=True)
    return {getdate(row.date): flt(row.total) for row in rows}


def _get_below_minimum_production(employee, from_date, to_date, minimum_production_per_day):
    # Only counts days that actually HAVE a Production Entry recorded below the
    # minimum - a day with no entry at all is NOT treated as "0 produced" here
    # (that caused a serious bug earlier: it reclassified almost every day with
    # no logged entry as below-minimum).
    daily_production = _get_daily_production(employee, from_date, to_date)
    below_minimum_days = 0.0
    below_minimum_quantity = 0.0
    for qty in daily_production.values():
        if flt(qty) < minimum_production_per_day:
            below_minimum_days += 1
            below_minimum_quantity += flt(qty)
    return below_minimum_days, below_minimum_quantity


def _get_total_advance_paid(employee, from_date, to_date):
    # paid_amount reflects what was actually disbursed against the advance (via Journal
    # Entry), regardless of its Draft/Paid/Claimed/Returned status label.
    EmployeeAdvance = frappe.qb.DocType("Employee Advance")
    result = (
        frappe.qb.from_(EmployeeAdvance)
        .select(Sum(EmployeeAdvance.paid_amount).as_("total"))
        .where(EmployeeAdvance.employee == employee)
        .where(EmployeeAdvance.docstatus == 1)
        .where(EmployeeAdvance.posting_date.between(from_date, to_date))
    ).run(as_dict=True)
    return flt(result[0].total) if result and result[0].total else 0.0


def _get_sales_collection_totals(employee, company, from_date, to_date):
    # Single source of truth: derives sales/collection totals from the same
    # shared function the Sales Collection Report itself uses. Ownership is
    # warehouse-based - this employee's linked user must have a Warehouse User
    # Permission assigned to them. Returns None (fall back to manual entry)
    # when the employee has no linked user, or that user has no warehouse
    # assigned yet. Sales Person is NOT used to determine ownership here.
    from badria_pwa.api.sales_collection import get_permitted_warehouses_for_user, get_sales_collection_data

    user = frappe.db.get_value("Employee", employee, "user_id")
    if not user:
        return None

    warehouses = get_permitted_warehouses_for_user(user)
    if not warehouses:
        return None

    result = get_sales_collection_data(
        from_date=from_date,
        to_date=to_date,
        warehouse=warehouses,
        company=company,
    )
    return result["totals"]


def _get_total_payment_entry_paid(employee, from_date, to_date):
    # Standalone Payment Entries made directly to the employee (party_type=Employee),
    # separate from Employee Advance - e.g. ad-hoc payments not routed as an advance.
    PaymentEntry = frappe.qb.DocType("Payment Entry")
    result = (
        frappe.qb.from_(PaymentEntry)
        .select(Sum(PaymentEntry.paid_amount).as_("total"))
        .where(PaymentEntry.party_type == "Employee")
        .where(PaymentEntry.party == employee)
        .where(PaymentEntry.payment_type == "Pay")
        .where(PaymentEntry.docstatus == 1)
        .where(PaymentEntry.posting_date.between(from_date, to_date))
    ).run(as_dict=True)
    return flt(result[0].total) if result and result[0].total else 0.0


@frappe.whitelist()
def create_employee_incentive(name):
    doc = frappe.get_doc("Final Settlement", name)

    if not frappe.has_permission(doc.doctype, "submit", doc):
        frappe.throw(_("You are not permitted to create an incentive record for this document."))

    if doc.docstatus != 1:
        frappe.throw(_("Final Settlement must be submitted before creating an Employee Incentive."))

    # Posting Date defaults to "Today" and is almost never blank, so using it here
    # would date these records by whenever HR happened to process the settlement
    # rather than the period the settlement actually covers - they must land in
    # the settlement's own payroll period (to_date) to reach the right Salary Slip.
    payroll_date = doc.to_date

    if doc.settlement_type == "Sales":
        main_incentive_amount = flt(doc.fixed_incentive_amount)
        main_salary_component = doc.sales_incentive_salary_component or "Basic"
    else:
        main_incentive_amount = flt(doc.incentive_amount)
        main_salary_component = doc.incentive_salary_component or "Basic"
    other_incentive_amount = flt(doc.other_allowances) + flt(doc.other_additions)

    created = {}

    created["employee_incentive"] = _create_one_employee_incentive(
        doc,
        link_field="employee_incentive",
        amount=main_incentive_amount,
        salary_component=main_salary_component,
        payroll_date=payroll_date,
    )
    if doc.settlement_type == "Sales":
        created["performance_employee_incentive"] = _create_one_employee_incentive(
            doc,
            link_field="performance_employee_incentive",
            amount=flt(doc.performance_incentive_amount),
            salary_component=doc.performance_incentive_salary_component or "Basic",
            payroll_date=payroll_date,
        )
    created["other_employee_incentive"] = _create_one_employee_incentive(
        doc,
        link_field="other_employee_incentive",
        amount=other_incentive_amount,
        salary_component=doc.other_salary_component or "Basic",
        payroll_date=payroll_date,
    )
    created["below_minimum_deduction"] = _create_below_minimum_deduction(doc, payroll_date)

    if not any(created.values()):
        frappe.throw(_("There is no incentive/allowance amount greater than zero to create."))

    return created


def _create_below_minimum_deduction(doc, payroll_date):
    if doc.settlement_type == "Sales":
        return None

    below_minimum_amount = flt(doc.below_minimum_incentive_amount)
    if below_minimum_amount <= 0:
        return None

    if doc.below_minimum_deduction:
        # Already created - skip silently rather than block the other incentives.
        return doc.below_minimum_deduction

    if not doc.below_minimum_salary_component:
        frappe.throw(_("Below Minimum Salary Component is required to create its deduction."))

    from hrms.payroll.doctype.salary_structure_assignment.salary_structure_assignment import (
        get_employee_currency,
    )

    additional_salary = frappe.new_doc("Additional Salary")
    additional_salary.employee = doc.employee
    additional_salary.company = doc.company
    additional_salary.currency = get_employee_currency(doc.employee)
    additional_salary.salary_component = doc.below_minimum_salary_component
    additional_salary.overwrite_salary_structure_amount = 0
    additional_salary.amount = below_minimum_amount
    additional_salary.payroll_date = payroll_date
    additional_salary.ref_doctype = doc.doctype
    additional_salary.ref_docname = doc.name
    additional_salary.insert()

    frappe.db.set_value(
        "Final Settlement", doc.name, "below_minimum_deduction", additional_salary.name, update_modified=False
    )

    return additional_salary.name


def _create_one_employee_incentive(doc, link_field, amount, salary_component, payroll_date):
    if amount <= 0:
        return None

    if doc.get(link_field):
        # Already created for this line item - skip silently rather than block
        # the other two from being created.
        return doc.get(link_field)

    from hrms.payroll.doctype.salary_structure_assignment.salary_structure_assignment import (
        get_employee_currency,
    )

    ei = frappe.new_doc("Employee Incentive")
    ei.employee = doc.employee
    ei.company = doc.company
    ei.currency = get_employee_currency(doc.employee)
    ei.salary_component = salary_component
    ei.payroll_date = payroll_date
    ei.incentive_amount = amount
    ei.insert()

    frappe.db.set_value("Final Settlement", doc.name, link_field, ei.name, update_modified=False)

    return ei.name
