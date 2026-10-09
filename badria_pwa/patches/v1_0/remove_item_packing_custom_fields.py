import frappe

# Packing automation moved from fields on Item to the standalone "Packing
# Setting" DocType; these Item fields were left behind in the fixtures but
# nothing reads them. Dropping them from the fixtures does not delete the
# already-synced Custom Fields, so remove exactly these, on Item only.

OLD_ITEM_FIELDS = (
    "custom_packing_automation_section",
    "custom_enable_packing_automation",
    "custom_pieces_per_carton",
    "custom_packing_materials",
)


def execute():
    for fieldname in OLD_ITEM_FIELDS:
        name = frappe.db.get_value("Custom Field", {"dt": "Item", "fieldname": fieldname})
        if name:
            frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
