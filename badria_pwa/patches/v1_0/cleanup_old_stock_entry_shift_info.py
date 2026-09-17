import frappe

# The Stock Entry Shift Info tab/table approach (Custom Field
# "custom_shift_info_tab" + child doctype "Stock Entry Active Shift") was
# replaced by simple Shift Type/Employee Link fields on an "Employee Details"
# tab instead, per the client's actual written spec. Removing the old fixture
# files/hooks doesn't delete already-synced DB records, so clean those up
# explicitly. The child doctype itself and its Table custom field are already
# gone (dropped automatically since the doctype's local files were removed).

OLD_CUSTOM_FIELD = "Stock Entry-custom_shift_info_tab"
OLD_REPORT = "Employee Wise Stock Entry Production"


def execute():
    if frappe.db.exists("Custom Field", OLD_CUSTOM_FIELD):
        frappe.delete_doc("Custom Field", OLD_CUSTOM_FIELD, ignore_permissions=True, force=True)

    if frappe.db.exists("Report", OLD_REPORT):
        frappe.delete_doc("Report", OLD_REPORT, ignore_permissions=True, force=True)
