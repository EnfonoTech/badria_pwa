# badria_pwa.api.sales_collection - Single source of truth for sales/collection
# data. Used by both the Sales Collection Report and the Sales Final Settlement,
# so the two are always guaranteed to show identical totals.
#
# Ownership model: each Warehouse has an assigned user via a "User Permission"
# record (Allow = Warehouse). That warehouse-to-user assignment is what
# determines ownership of a transaction here - NOT the Sales Person on the
# Sales Team child table (Sales Person is still shown as an informational
# column, but is never used to scope or attribute a row to someone).
#
# Date scope: from_date/to_date filter by when payment was actually RECEIVED
# (Payment Entry posting_date), not by the Sales Invoice's own posting_date.
# An invoice raised outside the range still appears if it was paid inside the
# range, and only the portion paid inside the range counts as collected -
# this is what makes the numbers mean "cash collected this period" for
# commission/incentive purposes. Standalone returns (Part 2 below) have no
# equivalent payment event, so they remain scoped by their own posting_date.

import frappe
from frappe.utils import flt


def get_permitted_warehouses_for_user(user):
	"""Warehouse(s) a user is assigned to own, via User Permission (Allow=Warehouse)."""
	if not user:
		return []
	return frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": "Warehouse"},
		pluck="for_value",
	)


def get_sales_collection_data(
	from_date, to_date, warehouse=None, user=None, employee=None, sales_person=None, company=None,
	customer=None,
):
	"""Return per-invoice rows plus aggregate totals for the given scope.

	`from_date`/`to_date` scope by when payment was RECEIVED (Payment Entry
	posting_date), not by the Sales Invoice's own posting_date - an invoice
	raised outside the range still appears if paid inside it, and only the
	portion paid inside the range is counted as collected.

	Ownership/ scope is warehouse-based: pass `warehouse` directly, or `user`
	(resolved to every warehouse assigned to that user via User Permission), or
	`employee` (resolved to their linked user, then the same way). `sales_person`
	may additionally be passed as a secondary filter for convenience, but is
	never used on its own to determine ownership. `company` is an optional
	additional restriction. `customer` is an optional additional restriction to
	a single customer. Raises if no scoping dimension resolves to anything,
	since an unscoped org-wide pull is never the intent here.
	"""
	if employee and not user:
		user = frappe.db.get_value("Employee", employee, "user_id")

	if user and not warehouse:
		warehouse = get_permitted_warehouses_for_user(user)

	if not warehouse and not sales_person:
		frappe.throw(
			"get_sales_collection_data requires a warehouse, or a user/employee with "
			"an assigned warehouse (via User Permission), or a sales_person to scope by."
		)

	query, params = _build_query(from_date, to_date, warehouse, sales_person, company, customer)
	rows = frappe.db.sql(query, params, as_dict=True)

	total_sales_amount = flt(sum(flt(r.invoice_grand_total) for r in rows), 2)
	total_payment_collected = flt(sum(flt(r.collected_grand_total) for r in rows), 2)
	total_outstanding_amount = flt(sum(flt(r.outstanding_amount) for r in rows), 2)
	collection_percentage = (
		flt(total_payment_collected / total_sales_amount * 100, 2) if total_sales_amount else 0.0
	)

	return {
		"rows": rows,
		"totals": {
			"total_sales_amount": total_sales_amount,
			"total_payment_collected": total_payment_collected,
			"total_outstanding_amount": total_outstanding_amount,
			"collection_percentage": collection_percentage,
		},
	}


def _scope_join(invoice_alias, warehouse, sales_person, param_prefix):
	"""Build INNER JOIN clause(s) constraining `invoice_alias`.name to the
	given warehouse (single value or list) / sales_person scope (AND semantics
	when both given). Returns (sql_fragment, params)."""
	joins = []
	params = {}

	if warehouse:
		alias = f"{param_prefix}_wh"
		operator = "IN" if isinstance(warehouse, (list, tuple, set)) else "="
		# Source Warehouse determination (never Item Warehouse): when the invoice
		# updates stock itself, its own warehouse is authoritative; otherwise stock
		# moved via a separate Delivery Note, so that DN's warehouse is the source.
		# Matching on Sales Invoice Item.warehouse directly misses every invoice
		# delivered this second way, which is how a user's collected payments went
		# missing from the Sales Collection Report / Final Settlement.
		joins.append(f"""
    INNER JOIN (
        SELECT DISTINCT inv.name AS invoice_name
        FROM `tabSales Invoice` inv
        LEFT JOIN (
            SELECT combined.invoice AS invoice, combined.source_warehouse AS source_warehouse
            FROM (
                SELECT sii.parent AS invoice, dn.set_warehouse AS source_warehouse
                FROM `tabSales Invoice Item` sii
                INNER JOIN `tabDelivery Note` dn ON dn.name = sii.delivery_note
                WHERE sii.delivery_note IS NOT NULL AND sii.delivery_note != ''

                UNION

                SELECT dni.against_sales_invoice AS invoice, dn2.set_warehouse AS source_warehouse
                FROM `tabDelivery Note Item` dni
                INNER JOIN `tabDelivery Note` dn2 ON dn2.name = dni.parent AND dn2.docstatus = 1
                WHERE dni.against_sales_invoice IS NOT NULL AND dni.against_sales_invoice != ''
            ) combined
        ) dn_wh ON dn_wh.invoice = inv.name
        WHERE
            (inv.update_stock = 1 AND inv.set_warehouse {operator} %({param_prefix}_warehouse)s)
            OR (inv.update_stock = 0 AND dn_wh.source_warehouse {operator} %({param_prefix}_warehouse)s)
    ) {alias} ON {alias}.invoice_name = {invoice_alias}.name""")
		params[f"{param_prefix}_warehouse"] = list(warehouse) if operator == "IN" else warehouse

	if sales_person:
		alias = f"{param_prefix}_sp"
		joins.append(f"""
    INNER JOIN (
        SELECT DISTINCT parent AS invoice_name
        FROM `tabSales Team`
        WHERE sales_person = %({param_prefix}_sales_person)s
    ) {alias} ON {alias}.invoice_name = {invoice_alias}.name""")
		params[f"{param_prefix}_sales_person"] = sales_person

	return "\n".join(joins), params


def _build_query(from_date, to_date, warehouse, sales_person, company, customer=None):
	params = {"from_date": from_date, "to_date": to_date}

	si_scope_sql, si_scope_params = _scope_join("si", warehouse, sales_person, "si_scope")
	ret_scope_sql, ret_scope_params = _scope_join("ret_si", warehouse, sales_person, "ret_scope")
	sr_scope_sql, sr_scope_params = _scope_join("sr", warehouse, sales_person, "sr_scope")
	params.update(si_scope_params)
	params.update(ret_scope_params)
	params.update(sr_scope_params)

	company_clause_si = ""
	company_clause_sr = ""
	if company:
		params["company"] = company
		company_clause_si = "AND si.company = %(company)s"
		company_clause_sr = "AND sr.company = %(company)s"

	customer_clause_si = ""
	customer_clause_sr = ""
	if customer:
		params["customer"] = customer
		customer_clause_si = "AND si.customer = %(customer)s"
		customer_clause_sr = "AND sr.customer = %(customer)s"

	query = f"""
SELECT * FROM (

    /* ============================================================
       PART 1 — Normal Sales Invoices
       Includes linked returns (direct + JE reconciliation)
       and payment entries per invoice
    ============================================================ */
    SELECT
        si.posting_date                                                     AS posting_date,
        si.customer                                                         AS customer,
        c.customer_name                                                     AS customer_name,
        COALESCE(st_data.sales_person, '')                                  AS sales_person,
        COALESCE(au_data.assigned_user, '')                                 AS assigned_user,
        si.name                                                             AS sales_invoice,

        COALESCE(ret_data.sales_return_names, '')                           AS sales_return,
        COALESCE(ret_data.return_dates, '')                                 AS return_dates,
        COALESCE(ret_data.return_grand_total, 0)                            AS return_grand_total,
        COALESCE(ret_data.return_net_amount,  0)                            AS return_net_amount,

        si.grand_total                                                      AS invoice_grand_total,
        si.net_total                                                        AS invoice_net_amount,

        COALESCE(si.apply_discount_on, '')                                  AS apply_discount_on,
        COALESCE(si.discount_amount, 0)                                     AS discount_amount,

        COALESCE(pe_data.payment_entry, '')                                 AS payment_entry,
        COALESCE(pe_data.allocated_amount, 0)                               AS collected_grand_total,
        COALESCE(ROUND(pe_data.allocated_amount / 1.15, 2), 0)              AS collected_net_amount,

        si.outstanding_amount                                               AS outstanding_amount,

        0                                                                   AS is_standalone_return,
        0                                                                   AS is_partial_return

    FROM `tabSales Invoice` si
    INNER JOIN `tabCustomer` c ON c.name = si.customer
{si_scope_sql}

    LEFT JOIN (
        SELECT
            st.parent                                                       AS invoice,
            GROUP_CONCAT(DISTINCT st.sales_person
                ORDER BY st.sales_person SEPARATOR ', ')                    AS sales_person
        FROM `tabSales Team` st
        GROUP BY st.parent
    ) st_data ON st_data.invoice = si.name

    LEFT JOIN (
        SELECT
            sii.parent                                                      AS invoice,
            GROUP_CONCAT(DISTINCT up.user
                ORDER BY up.user SEPARATOR ', ')                            AS assigned_user
        FROM `tabSales Invoice Item` sii
        INNER JOIN `tabUser Permission` up
            ON up.allow = 'Warehouse' AND up.for_value = sii.warehouse
        GROUP BY sii.parent
    ) au_data ON au_data.invoice = si.name

    LEFT JOIN (
        SELECT
            base.original_invoice                                           AS invoice,
            GROUP_CONCAT(DISTINCT base.return_name
                ORDER BY base.return_name SEPARATOR ', ')                   AS sales_return_names,
            GROUP_CONCAT(DISTINCT base.return_date
                ORDER BY base.return_date SEPARATOR ', ')                   AS return_dates,
            SUM(base.return_grand_total)                                    AS return_grand_total,
            SUM(base.return_net_amount)                                     AS return_net_amount
        FROM (
            /* Mechanism 1 — direct return_against */
            SELECT
                sr_direct.return_against                                    AS original_invoice,
                sr_direct.name                                              AS return_name,
                DATE_FORMAT(sr_direct.posting_date, '%%d-%%m-%%Y')          AS return_date,
                ABS(sr_direct.grand_total)                                  AS return_grand_total,
                ABS(sr_direct.net_total)                                    AS return_net_amount
            FROM `tabSales Invoice` sr_direct
            WHERE sr_direct.is_return = 1 AND sr_direct.docstatus = 1 AND sr_direct.return_against IS NOT NULL

            UNION ALL

            /* Mechanism 2 — JE reconciliation via Payment Reconciliation tool */
            SELECT
                jea_orig.reference_name                                     AS original_invoice,
                ret_si.name                                                 AS return_name,
                DATE_FORMAT(ret_si.posting_date, '%%d-%%m-%%Y')             AS return_date,
                GREATEST(
                    ABS(jea_orig.debit_in_account_currency),
                    ABS(jea_orig.credit_in_account_currency)
                )                                                           AS return_grand_total,
                ROUND(GREATEST(
                    ABS(jea_orig.debit_in_account_currency),
                    ABS(jea_orig.credit_in_account_currency)
                ) / 1.15, 2)                                                AS return_net_amount
            FROM `tabJournal Entry Account` jea_ret
            INNER JOIN `tabSales Invoice` ret_si
                ON ret_si.name = jea_ret.reference_name
                AND ret_si.is_return = 1
                AND ret_si.docstatus = 1
{ret_scope_sql}
            INNER JOIN `tabJournal Entry Account` jea_orig
                ON jea_orig.parent = jea_ret.parent
                AND jea_orig.reference_type = 'Sales Invoice'
                AND jea_orig.reference_name != jea_ret.reference_name
                AND jea_orig.party = jea_ret.party
            INNER JOIN `tabSales Invoice` orig_si
                ON orig_si.name = jea_orig.reference_name
                AND orig_si.is_return = 0
                AND orig_si.docstatus = 1
            INNER JOIN `tabJournal Entry` je
                ON je.name = jea_ret.parent
                AND je.docstatus = 1
            WHERE jea_ret.reference_type = 'Sales Invoice'
            AND NOT EXISTS (
                SELECT 1 FROM `tabSales Invoice` sr_check
                WHERE sr_check.name = jea_ret.reference_name
                AND sr_check.return_against = jea_orig.reference_name
            )
        ) base
        GROUP BY base.original_invoice
    ) ret_data ON ret_data.invoice = si.name

    INNER JOIN (
        SELECT
            per.reference_name                                              AS invoice,
            GROUP_CONCAT(DISTINCT pe.name
                ORDER BY pe.posting_date SEPARATOR ', ')                    AS payment_entry,
            SUM(per.allocated_amount)                                       AS allocated_amount
        FROM `tabPayment Entry Reference` per
        INNER JOIN `tabPayment Entry` pe
            ON pe.name = per.parent
            AND pe.docstatus = 1
            AND pe.payment_type = 'Receive'
            AND pe.posting_date BETWEEN %(from_date)s AND %(to_date)s
        WHERE per.reference_doctype = 'Sales Invoice'
        GROUP BY per.reference_name
    ) pe_data ON pe_data.invoice = si.name

    WHERE
        si.docstatus     = 1
        AND si.is_return = 0
        {company_clause_si}
        {customer_clause_si}

    /* ============================================================
       PART 2 — Standalone Returns
       CAT A: no return_against at all
       CAT B: has outstanding_amount != 0 (unreconciled credit)
       Both shown as separate rows so salesman can see pending credits
    ============================================================ */
    UNION ALL

    SELECT
        sr.posting_date                                                     AS posting_date,
        sr.customer                                                         AS customer,
        c2.customer_name                                                    AS customer_name,
        COALESCE(st_data2.sales_person, '')                                 AS sales_person,
        COALESCE(au_data2.assigned_user, '')                                AS assigned_user,
        ''                                                                  AS sales_invoice,

        sr.name                                                             AS sales_return,
        DATE_FORMAT(sr.posting_date, '%%d-%%m-%%Y')                        AS return_dates,
        ABS(sr.outstanding_amount)                                          AS return_grand_total,
        ROUND(ABS(sr.outstanding_amount) / 1.15, 2)                        AS return_net_amount,

        0                                                                   AS invoice_grand_total,
        0                                                                   AS invoice_net_amount,
        ''                                                                  AS apply_discount_on,
        0                                                                   AS discount_amount,
        ''                                                                  AS payment_entry,
        0                                                                   AS collected_grand_total,
        0                                                                   AS collected_net_amount,

        sr.outstanding_amount                                               AS outstanding_amount,

        1                                                                   AS is_standalone_return,

        /* is_partial_return = 1 when return has return_against (was linked)
           but still has outstanding — meaning partial allocation only      */
        CASE
            WHEN (sr.return_against IS NOT NULL AND sr.return_against != '')
            AND sr.outstanding_amount != 0
            THEN 1
            ELSE 0
        END                                                                 AS is_partial_return

    FROM `tabSales Invoice` sr
    INNER JOIN `tabCustomer` c2 ON c2.name = sr.customer
{sr_scope_sql}

    LEFT JOIN (
        SELECT
            st.parent                                                       AS invoice,
            GROUP_CONCAT(DISTINCT st.sales_person
                ORDER BY st.sales_person SEPARATOR ', ')                    AS sales_person
        FROM `tabSales Team` st
        GROUP BY st.parent
    ) st_data2 ON st_data2.invoice = sr.name

    LEFT JOIN (
        SELECT
            sii2.parent                                                     AS invoice,
            GROUP_CONCAT(DISTINCT up2.user
                ORDER BY up2.user SEPARATOR ', ')                           AS assigned_user
        FROM `tabSales Invoice Item` sii2
        INNER JOIN `tabUser Permission` up2
            ON up2.allow = 'Warehouse' AND up2.for_value = sii2.warehouse
        GROUP BY sii2.parent
    ) au_data2 ON au_data2.invoice = sr.name

    WHERE
        sr.docstatus     = 1
        AND sr.is_return = 1
        AND sr.posting_date BETWEEN %(from_date)s AND %(to_date)s
        AND ABS(sr.outstanding_amount) > 0.009
        {company_clause_sr}
        {customer_clause_sr}

) AS combined_result
ORDER BY posting_date ASC, sales_invoice ASC
"""
	return query, params
