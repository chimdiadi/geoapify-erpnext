# geoapify_integration/api/geoapify.py
import frappe
import requests

@frappe.whitelist()
def autocomplete(text):
    if not text or len(text) < 3:
        return []

    api_key = frappe.db.get_single_value(
        "Geoapify Settings", "geoapify_key"
    )

    if not api_key:
        frappe.throw("Geoapify API key not configured")

    r = requests.get(
        "https://api.geoapify.com/v1/geocode/autocomplete",
        params={"text": text, "apiKey": api_key},
        timeout=8,
    )
    r.raise_for_status()

    features = r.json().get("features", [])
    return [
        {
            "label": f["properties"].get("formatted"),
            "lat": f["properties"].get("lat"),
            "lon": f["properties"].get("lon"),
            "place_id": f["properties"].get("place_id"),
        }
        for f in features[:10]
    ]

