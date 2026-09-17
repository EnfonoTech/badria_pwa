app_name        = "badria_pwa"
app_title       = "Badria PWA"
app_publisher   = "Enfono"
app_description = "Badria Van Sales PWA – Full field sales operations for ERPNext"
app_email       = "nah@enfono.com"
app_license     = "mit"

# required_apps = ["erpnext"]  # Uncomment if ERPNext is installed

website_redirects = [
    {"source": "/badria_pwa",       "target": "/assets/badria_pwa/pwa/index.html"},
    {"source": "/badria_pwa/login", "target": "/assets/badria_pwa/pwa/index.html"},
]

after_install   = "badria_pwa.install.setup.after_install"
after_uninstall = "badria_pwa.install.setup.after_uninstall"

doc_events = {
    "Salary Slip": {
        "validate": "badria_pwa.payroll.salary_slip.enforce_production_no_paid_leave",
    },
    "Stock Entry": {
        # Must run in before_validate, not validate: core Stock Entry's own
        # validate() throws "At least one raw material item must be present"
        # for a Manufacture entry that has no consumption row yet, and a
        # doc_events "validate" hook only runs *after* the controller's own
        # validate() returns - too late to add the packing rows it needs.
        "before_validate": "badria_pwa.manufacturing.packing_automation.apply_packing_rules",
    },
}

# Form scripts. Needs `bench build --app badria_pwa` after install/update.
doctype_js = {
    "Stock Entry": "public/js/stock_entry.js",
}

fixtures = [
	"Client Script",
	{
		"dt": "Custom Field",
		"filters": [
			[
				"name",
				"in",
				[
					"Full and Final Statement-custom_settlement_type",
					"Stock Entry-custom_employee_details_tab",
					"Stock Entry-custom_shift_type",
					"Stock Entry-custom_department",
					"Stock Entry-custom_shift_employees_section",
					"Stock Entry-custom_shift_employees",
					"Stock Entry-custom_production_template",
					"Item-custom_packing_automation_section",
					"Item-custom_enable_packing_automation",
					"Item-custom_pieces_per_carton",
					"Item-custom_packing_materials",
				],
			]
		],
	},
]
