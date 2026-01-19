# geoapify/geoapify/api/routing.py
#
# - Keeps the existing heavy_truck_distance() interface unchanged
# - Adds heavy_truck_route_geojson() for full route geometry (GeoJSON) suitable for plotting
# - Fixes Frappe behavior where nested args often arrive as JSON strings

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

import frappe
import requests


GEOAPIFY_ROUTING_URL = "https://api.geoapify.com/v1/routing"


def _get_geoapify_key(explicit_key: Optional[str] = None) -> str:
    """Resolve API key from args or site config."""
    if explicit_key:
        return explicit_key.strip()

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
        frappe.throw(f"Invalid {name}: {value!r}. Must be a number.")
        raise exc


def _validate_lat_lon(origin_lat: float, origin_lon: float) -> None:
    if not (-90.0 <= origin_lat <= 90.0):
        frappe.throw("Latitude must be between -90 and 90.")
    if not (-180.0 <= origin_lon <= 180.0):
        frappe.throw("Longitude must be between -180 and 180.")


def _build_waypoints_two_point(
    origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float
) -> str:
    """Geoapify expects 'lat,lon|lat,lon' for waypoints."""
    return f"{origin_lat},{origin_lon}|{dest_lat},{dest_lon}"


def _build_waypoints_multi(waypoints: List[Tuple[float, float]]) -> str:
    """Geoapify expects 'lat,lon|lat,lon|...' for waypoints."""
    return "|".join([f"{lat},{lon}" for lat, lon in waypoints])


def _normalize_waypoints(waypoints: Any) -> List[Tuple[float, float]]:
    """
    Accepts:
      - pipe string: "lat,lon|lat,lon|..."
      - JSON string: '[{"lat":..,"lon":..}, ...]' or '[[lat,lon], ...]'
      - list of dicts: [{"lat":..,"lon":..}, ...]
      - list of lists: [[lat,lon], ...]
    Returns:
      [(lat, lon), ...]
    """
    if waypoints is None:
        return []

    # Frappe often sends nested structures as JSON strings. Handle that first.
    if isinstance(waypoints, str):
        raw = waypoints.strip()
        if not raw:
            return []

        # If it looks like JSON, parse it.
        if raw[0] in ("[", "{"):
            try:
                waypoints = json.loads(raw)
            except Exception:
                frappe.throw("Invalid waypoints JSON string.")

        else:
            # Treat as "lat,lon|lat,lon"
            normalized: List[Tuple[float, float]] = []
            parts = [p.strip() for p in raw.split("|") if p.strip()]
            for part in parts:
                pieces = [x.strip() for x in part.split(",")]
                if len(pieces) != 2:
                    frappe.throw(f"Invalid waypoint '{part}'. Expected 'lat,lon'.")
                lat_str, lon_str = pieces
                lat_val = _parse_float("lat", lat_str)
                lon_val = _parse_float("lon", lon_str)
                _validate_lat_lon(lat_val, lon_val)
                normalized.append((lat_val, lon_val))
            return normalized

    # Now handle list/dict forms
    normalized2: List[Tuple[float, float]] = []

    if isinstance(waypoints, dict):
        # Edge-case: a single dict
        lat_val = _parse_float("lat", waypoints.get("lat"))
        lon_val = _parse_float("lon", waypoints.get("lon"))
        _validate_lat_lon(lat_val, lon_val)
        normalized2.append((lat_val, lon_val))
        return normalized2

    for item in (waypoints or []):
        if isinstance(item, dict):
            lat_val = _parse_float("lat", item.get("lat"))
            lon_val = _parse_float("lon", item.get("lon"))
        else:
            # list/tuple form [lat, lon]
            lat_val = _parse_float("lat", item[0])
            lon_val = _parse_float("lon", item[1])

        _validate_lat_lon(lat_val, lon_val)
        normalized2.append((lat_val, lon_val))

    return normalized2


# -----------------------------------------------------------------------------
# EXISTING METHOD (KEEP UNCHANGED): heavy_truck_distance
# -----------------------------------------------------------------------------

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
    origin_lat_val = _parse_float("origin_lat", origin_lat)
    origin_lon_val = _parse_float("origin_lon", origin_lon)
    dest_lat_val = _parse_float("dest_lat", dest_lat)
    dest_lon_val = _parse_float("dest_lon", dest_lon)

    _validate_lat_lon(origin_lat_val, origin_lon_val)
    _validate_lat_lon(dest_lat_val, dest_lon_val)

    key = _get_geoapify_key(api_key)

    params: Dict[str, Any] = {
        "waypoints": _build_waypoints_two_point(origin_lat_val, origin_lon_val, dest_lat_val, dest_lon_val),
        "mode": "heavy_truck",
        "format": "json",
        "units": units,
        "traffic": traffic,
        "apiKey": key,
    }
    if max_speed is not None:
        params["max_speed"] = int(max_speed)

    try:
        response = requests.get(GEOAPIFY_ROUTING_URL, params=params, timeout=20)
    except requests.RequestException as exc:
        frappe.throw(f"Geoapify routing request failed: {exc}")

    if response.status_code != 200:
        try:
            error_payload = response.json()
        except Exception:
            error_payload = {"message": response.text}
        frappe.throw(f"Geoapify routing error ({response.status_code}): {error_payload}")

    data = response.json()

    results = data.get("results") or []
    if not results:
        frappe.throw("Geoapify returned no routes (results array empty).")

    route0 = results[0]
    return {
        "distance": route0.get("distance"),
        "distance_units": route0.get("distance_units"),
        "time_seconds": route0.get("time"),
        "mode": "heavy_truck",
        "raw": {
            "properties": data.get("properties"),
            "route": {k: route0.get(k) for k in ("distance", "distance_units", "time", "toll", "ferry")},
        },
    }


# -----------------------------------------------------------------------------
# NEW METHOD (ADDITIVE): heavy_truck_route_geojson
# -----------------------------------------------------------------------------

@frappe.whitelist()
def heavy_truck_route_geojson(
    waypoints: Any,
    units: str = "metric",
    api_key: Optional[str] = None,
    traffic: str = "free_flow",
    max_speed: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Return road-following route geometry and metadata in GeoJSON format.

    This is ADDITIVE and does NOT change heavy_truck_distance().

    Inputs:
      - waypoints can be:
          * "lat,lon|lat,lon|..."  (string)
          * JSON string: '[{"lat":..,"lon":..}, ...]' or '[[lat,lon], ...]'
          * list of dicts / list of lists

    Returns:
      {
        "summary": { "distance", "distance_units", "time_seconds", "mode", "units", "toll" },
        "geojson": <GeoJSON FeatureCollection from Geoapify>
      }
    """
    key = _get_geoapify_key(api_key)

    waypoint_list = _normalize_waypoints(waypoints)
    if len(waypoint_list) < 2:
        frappe.throw("At least 2 waypoints are required.")

    params: Dict[str, Any] = {
        "waypoints": _build_waypoints_multi(waypoint_list),
        "mode": "heavy_truck",
        "format": "geojson",  # <--- crucial for geometry + steps in FeatureCollection
        "units": units,
        "traffic": traffic,
        "apiKey": key,
    }
    if max_speed is not None:
        params["max_speed"] = int(max_speed)

    try:
        response = requests.get(GEOAPIFY_ROUTING_URL, params=params, timeout=25)
    except requests.RequestException as exc:
        frappe.throw(f"Geoapify routing request failed: {exc}")

    if response.status_code != 200:
        try:
            error_payload = response.json()
        except Exception:
            error_payload = {"message": response.text}
        frappe.throw(f"Geoapify routing error ({response.status_code}): {error_payload}")

    feature_collection = response.json()
    features = feature_collection.get("features") or []
    if not features:
        frappe.throw("Geoapify returned no features (empty FeatureCollection).")

    properties = features[0].get("properties") or {}
    summary = {
        "distance": properties.get("distance"),
        "distance_units": properties.get("distance_units"),
        "time_seconds": properties.get("time"),
        "mode": properties.get("mode"),
        "units": properties.get("units"),
        "toll": properties.get("toll"),
    }

    return {"summary": summary, "geojson": feature_collection}

