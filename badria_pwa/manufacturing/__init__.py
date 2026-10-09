# Stock Entry Type used for daily production (purpose Repack, many finished goods).
PRODUCTION_STOCK_ENTRY_TYPE = "Production"


def is_production_entry(doc):
	return doc.get("stock_entry_type") == PRODUCTION_STOCK_ENTRY_TYPE and doc.get("purpose") == "Repack"
