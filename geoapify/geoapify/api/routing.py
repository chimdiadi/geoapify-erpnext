# geoapify_integration/api/routing.py

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import frappe
import requests


GEOAPIFY_ROUTING_URL = "https://api.geoapify.com/v1/routing"


def _get_geoapify_key(explicit_key: Optional[str] = None) -> str:
    """Resolve API key from args or site config."""
    if explicit_key:
        return explicit_key.strip()

    # Option A: keep it in site_config.json as: "geoapify_api_key": "..."
    key = (frappe.conf.get("geoapify_api_key") or "").strip()
    if not key:
        frappe.throw(
            "Geoapify API key missing. Set frappe.conf.geoapify_api_key in site_config.json "
            "or pass api_key explicitly."
        )
    return key


def _parse_float(name: str, value: Any) -> float:
    try:
        return float(value)
    except Exception as exc:
        frappe.throw(f"Invalid {name}: {value!r}. Must be a number.")  # raises
        raise exc


def _build_waypoints(
    origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float
) -> str:
    # Geoapify expects "lat,lon|lat,lon" for waypoints. :contentReference[oaicite:3]{index=3}
    return f"{origin_lat},{origin_lon}|{dest_lat},{dest_lon}"


@frappe.whitelist()
def heavy_truck_distance(
    origin_lat: Any,
    origin_lon: Any,
    dest_lat: Any,
    dest_lon: Any,
    units: str = "metric",
    api_key: Optional[str] = None,
    traffic: str = "free_flow",
    max_speed: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Calculate route distance using Geoapify Routing API for heavy trucks.

    Returns:
      {
        "distance": <number>,
        "distance_units": "Meters" | "Miles",
        "time_seconds": <number>,
        "mode": "heavy_truck",
        "raw": <optional trimmed response>
      }
    """
    o_lat = _parse_float("origin_lat", origin_lat)
    o_lon = _parse_float("origin_lon", origin_lon)
    d_lat = _parse_float("dest_lat", dest_lat)
    d_lon = _parse_float("dest_lon", dest_lon)

    # Basic sanity checks (optional but helpful)
    if not (-90 <= o_lat <= 90 and -90 <= d_lat <= 90):
        frappe.throw("Latitude must be between -90 and 90.")
    if not (-180 <= o_lon <= 180 and -180 <= d_lon <= 180):
        frappe.throw("Longitude must be between -180 and 180.")

    key = _get_geoapify_key(api_key)

    params: Dict[str, Any] = {
        "waypoints": _build_waypoints(o_lat, o_lon, d_lat, d_lon),
        "mode": "heavy_truck",          # supported travel mode :contentReference[oaicite:4]{index=4}
        "format": "json",               # JSON response structure :contentReference[oaicite:5]{index=5}
        "units": units,                 # metric | imperial :contentReference[oaicite:6]{index=6}
        "traffic": traffic,             # free_flow | approximated :contentReference[oaicite:7]{index=7}
        "apiKey": key,
    }
    if max_speed is not None:
        params["max_speed"] = int(max_speed)  # allowed for truck modes :contentReference[oaicite:8]{index=8}

    try:
        resp = requests.get(GEOAPIFY_ROUTING_URL, params=params, timeout=20)
    except requests.RequestException as exc:
        frappe.throw(f"Geoapify routing request failed: {exc}")

    if resp.status_code != 200:
        # Geoapify returns structured error fields in many cases; keep it readable.
        try:
            err = resp.json()
        except Exception:
            err = {"message": resp.text}
        frappe.throw(f"Geoapify routing error ({resp.status_code}): {err}")

    data = resp.json()

    # In JSON format, route results are in `results[]`. :contentReference[oaicite:9]{index=9}
    results = data.get("results") or []
    if not results:
        frappe.throw("Geoapify returned no routes (results array empty).")

    route0 = results[0]
    # Route object includes distance, distance_units, time. :contentReference[oaicite:10]{index=10}
    return {
        "distance": route0.get("distance"),
        "distance_units": route0.get("distance_units"),
        "time_seconds": route0.get("time"),
        "mode": "heavy_truck",
        # optional: return a trimmed raw object for debugging
        "raw": {
            "properties": data.get("properties"),
            "route": {k: route0.get(k) for k in ("distance", "distance_units", "time", "toll", "ferry")},
        },
    }



@frappe.whitelist()
def heavy_truck_route_geojson(
    waypoints: Any,
    units: str = "metric",
    api_key: Optional[str] = None,
    traffic: str = "free_flow",
    max_speed: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Road-following route geometry for 2+ waypoints using Geoapify Routing API (geojson format).
    Additive API: does not change heavy_truck_distance().
    """
    key = _get_geoapify_key(api_key)

    # Normalize waypoints into [(lat, lon), ...]
    wp_list: List[Tuple[float, float]] = []
    if isinstance(waypoints, str):
        parts = [p.strip() for p in waypoints.split("|") if p.strip()]
        for p in parts:
            lat_s, lon_s = [x.strip() for x in p.split(",")]
            wp_list.append((_parse_float("lat", lat_s), _parse_float("lon", lon_s)))
    else:
        for item in (waypoints or []):
            if isinstance(item, dict):
                wp_list.append((_parse_float("lat", item.get("lat")), _parse_float("lon", item.get("lon"))))
            else:
                wp_list.append((_parse_float("lat", item[0]), _parse_float("lon", item[1])))

    if len(wp_list) < 2:
        frappe.throw("At least 2 waypoints are required.")

    params: Dict[str, Any] = {
        "waypoints": "|".join([f"{lat},{lon}" for lat, lon in wp_list]),
        "mode": "heavy_truck",
        "format": "geojson",   # <-- NEW: geometry + steps in GeoJSON FeatureCollection
        "units": units,
        "traffic": traffic,
        "apiKey": key,
    }
    if max_speed is not None:
        params["max_speed"] = int(max_speed)

    try:
        resp = requests.get(GEOAPIFY_ROUTING_URL, params=params, timeout=25)
    except requests.RequestException as exc:
        frappe.throw(f"Geoapify routing request failed: {exc}")

    if resp.status_code != 200:
        try:
            err = resp.json()
        except Exception:
            err = {"message": resp.text}
        frappe.throw(f"Geoapify routing error ({resp.status_code}): {err}")

    fc = resp.json()
    features = fc.get("features") or []
    if not features:
        frappe.throw("Geoapify returned no features (empty FeatureCollection).")

    props = (features[0].get("properties") or {})
    summary = {
        "distance": props.get("distance"),
        "distance_units": props.get("distance_units"),
        "time_seconds": props.get("time"),
        "mode": props.get("mode"),
        "units": props.get("units"),
        "toll": props.get("toll"),
    }

    return {"summary": summary, "geojson": fc}
