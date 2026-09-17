// Copyright (c) 2026, Enfono and contributors
// For license information, please see license.txt

frappe.ui.form.on("Packing Setting", {
	refresh(frm) {
		frm.set_query("uom", "packing_materials", function (doc, cdt, cdn) {
			let row = locals[cdt][cdn];
			return {
				query: "badria_pwa.badria_pwa_app.doctype.packing_setting.packing_setting.get_packing_item_uoms",
				filters: {
					item_code: row.packing_material_item,
				},
			};
		});
	},
});

frappe.ui.form.on("Packing Material Rule", {
	packing_material_item(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (row.packing_material_item) {
			frappe.db.get_value("Item", row.packing_material_item, "stock_uom", (r) => {
				frappe.model.set_value(cdt, cdn, "uom", r.stock_uom);
			});
		} else {
			frappe.model.set_value(cdt, cdn, "uom", "");
		}
	},
});
