from __future__ import annotations

import json
import logging
import math
import os
from typing import Optional

import requests
from smolagents import tool

logger = logging.getLogger(__name__)

try:
    import polyline as _polyline_lib  
    _POLYLINE_AVAILABLE = True
except ImportError:
    _polyline_lib = None
    _POLYLINE_AVAILABLE = False

ORS_API_KEY  = os.getenv("ORS_API_KEY", "")
ORS_BASE_URL = "https://api.openrouteservice.org/v2/directions/driving-car"

SEVILLA_BOUNDS = {
    "lat_min": 37.25,
    "lat_max": 37.52,
    "lon_min": -6.12,
    "lon_max": -5.82,
}

def _in_sevilla_bounds(lat: float, lon: float) -> bool:
    """Return True if (lat, lon) falls within the Sevilla operational bounds."""
    return (
        SEVILLA_BOUNDS["lat_min"] <= lat <= SEVILLA_BOUNDS["lat_max"]
        and SEVILLA_BOUNDS["lon_min"] <= lon <= SEVILLA_BOUNDS["lon_max"]
    )

VEHICLE_SPEEDS_KMH = {
    "police": 80,
    "ambulance_sva": 70,
    "ambulance_svb": 70,
    "fire": 60,
    "rescue": 60,
    "default": 60
}

UNIT_BASES = {
    "ambulance_svb": ["Hospital Virgen del Rocío", "Hospital Virgen Macarena", "Hospital de Valme"],
    "ambulance_sva": ["Base 061 Cartuja", "Hospital Virgen del Rocío", "Hospital Virgen Macarena", "Hospital de Valme"],
    "police": ["Jefatura Policía Local", "Distrito Sur", "Distrito Macarena", "Distrito Triana", "Distrito Este"],
    "fire":   ["Parque Central San Bernardo", "Parque Carretera de Carmona", "Parque Triana Los Remedios", "Parque Polígono Sur"],
    "rescue": ["Parque Central San Bernardo", "Parque Triana Los Remedios", "Parque Polígono Sur"],
}

_BASE_COORDS_CACHE: dict[str, tuple[float, float]] = {
    "Base 061 Cartuja":               (37.4102, -6.0049),
    "Hospital Virgen del Rocío":      (37.3582, -5.9794),
    "Hospital Virgen Macarena":       (37.4093, -5.9877),
    "Hospital de Valme":              (37.3236, -5.9664),
    "Jefatura Policía Local":         (37.3828, -5.9625),
    "Distrito Sur":                   (37.3681, -5.9862),
    "Distrito Macarena":              (37.4115, -5.9815),
    "Distrito Triana":                (37.3855, -6.0121),
    "Distrito Este":                  (37.4042, -5.9221),
    "Parque Central San Bernardo":    (37.3842, -5.9819),
    "Parque Carretera de Carmona":    (37.3995, -5.9688),
    "Parque Triana Los Remedios":     (37.3718, -6.0054),
    "Parque Polígono Sur":            (37.3804, -5.9492),
}

def _geocode_address(address: str) -> Optional[tuple[float, float]]:
    """Returns (lat, lon) for an address string. Bases are resolved from the
    hardcoded cache instantly; other addresses fall back to Nominatim."""
    if address in _BASE_COORDS_CACHE:
        return _BASE_COORDS_CACHE[address]

    import time
    from geopy.geocoders import Nominatim
    from geopy.exc import GeocoderTimedOut

    geolocator = Nominatim(user_agent="imers_emergency_dispatch/1.0")
    try:
        time.sleep(1)
        loc = geolocator.geocode(f"{address}, Sevilla, España", timeout=10)
        if loc:
            result = (loc.latitude, loc.longitude)
            _BASE_COORDS_CACHE[address] = result
            return result
    except GeocoderTimedOut:
        pass
    return None

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance in km between two WGS84 points."""
    R = 6371.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a = math.sin(dφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def _encode_polyline(coords: list[tuple[float, float]], precision: int = 6) -> str:
    """Encode a list of (lat, lon) tuples to Google's polyline algorithm format."""
    factor = 10 ** precision
    output = []
    
    prev_lat, prev_lon = 0, 0
    
    for lat, lon in coords:
        dlat = int(round((lat - prev_lat) * factor))
        dlng = int(round((lon - prev_lon) * factor))
        
        for delta in [dlat, dlng]:
            shifted = ~(delta << 1) if delta < 0 else (delta << 1)
            chunks = []
            while shifted >= 0x20:
                chunks.append((0x20 | (shifted & 0x1f)) + 63)
                shifted >>= 5
            chunks.append(shifted + 63)
            output.extend(chr(chunk) for chunk in chunks)
        
        prev_lat, prev_lon = lat, lon
    
    return ''.join(output)

def _route_via_ors(
    origin_lat: float, origin_lon: float,
    dest_lat: float,   dest_lon: float,
    speed_kmh: float = 60.0,
) -> Optional[dict]:
    """Call ORS Directions API. Returns route dict or None on failure."""
    if not ORS_API_KEY:
        return None

    headers = {
        "Authorization": ORS_API_KEY,
        "Content-Type":  "application/json",
    }
    body = {
        "coordinates": [
            [origin_lon, origin_lat],
            [dest_lon,   dest_lat],
        ],
        "instructions": True,
        "language": "es",
        "units": "km",
        "preference": "fastest",
    }

    try:
        resp = requests.post(ORS_BASE_URL, headers=headers, json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if not data.get("routes"):
            logger.warning("[get_route] ORS returned no routes")
            return None
        route = data["routes"][0]
        summary = route.get("summary", {})
        segments = route.get("segments") or []
        steps = segments[0]["steps"] if segments else []

        distance_km = summary["distance"]
        duration_minutes = max(1.0, round((distance_km / speed_kmh) * 60, 1)) if speed_kmh > 0 else 0

        instructions = [
            {
                "step": i + 1,
                "instruction": s["instruction"],
                "distance_km": round(s["distance"], 2),
                "duration_min": round((s["distance"] / speed_kmh) * 60, 1) if speed_kmh > 0 else 0,
            }
            for i, s in enumerate(steps)
        ]
        polyline_coords = None
        raw_geometry = route.get("geometry")
        if raw_geometry and _POLYLINE_AVAILABLE:
            try:
                polyline_coords = _polyline_lib.decode(raw_geometry, precision=5)
            except Exception as e:
                logger.warning("[get_route] ORS polyline decode failed: %s", e)
        elif raw_geometry and not _POLYLINE_AVAILABLE:
            logger.warning("[get_route] polyline package not installed — run: pip install polyline")
        return {
            "backend": "openrouteservice",
            "distance_km": round(distance_km, 2),
            "duration_minutes": duration_minutes,
            "instructions": instructions,
            "polyline": raw_geometry,
            "polyline_coords": polyline_coords,
        }

    except Exception as e:
        logger.warning("[get_route] ORS failed: %s", e)
        return None

def _route_via_osmnx(
    origin_lat: float, origin_lon: float,
    dest_lat: float,   dest_lon: float,
    speed_kmh: float = 60.0,
    city: str = "Seville, Spain",
) -> Optional[dict]:
    """Route using OSMnx + NetworkX. Slower but works offline."""
    try:
        import osmnx as ox
        import networkx as nx

        G = ox.graph_from_place(city, network_type="drive")

        orig_node = ox.nearest_nodes(G, origin_lon, origin_lat)
        dest_node = ox.nearest_nodes(G, dest_lon,   dest_lat)

        route_nodes = nx.shortest_path(G, orig_node, dest_node, weight="travel_time")

        if hasattr(ox, 'routing') and hasattr(ox.routing, 'route_to_gdf'):
            edge_data = ox.routing.route_to_gdf(G, route_nodes)
        else:
            edge_data = ox.utils_graph.route_to_gdf(G, route_nodes)
        total_km  = round(edge_data["length"].sum() / 1000, 2)
        total_min = round((total_km / speed_kmh) * 60, 1) if speed_kmh > 0 else 0

        route_coords = []
        for _, row in edge_data.iterrows():
            geom = row.geometry
            if hasattr(geom, 'coords'):
                route_coords.extend([(lat, lon) for lon, lat in geom.coords])
        
        polyline = _encode_polyline(route_coords) if route_coords else None

        return {
            "backend": "osmnx",
            "distance_km": total_km,
            "duration_minutes": total_min,
            "instructions": [{"note": "Turn-by-turn not available in OSMnx backend"}],
            "polyline": polyline,
        }

    except ImportError:
        logger.warning("[get_route] osmnx not installed. Run: pip install osmnx networkx")
        return None
    except Exception as e:
        logger.warning("[get_route] OSMnx failed: %s", e)
        return None

def _route_stub(
    origin_lat: float, origin_lon: float,
    dest_lat: float,   dest_lon: float,
    speed_kmh: float = 60.0,
) -> dict:
    """Fallback: straight-line distance + emergency speed estimate."""
    dist_km = _haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
    road_km = round(dist_km * 1.3, 2)
    eta_min = max(1.0, round((road_km / speed_kmh) * 60, 1)) if speed_kmh > 0 else 0

    straight_line_coords = [(origin_lat, origin_lon), (dest_lat, dest_lon)]
    polyline = _encode_polyline(straight_line_coords)

    return {
        "backend": "stub_straight_line",
        "distance_km": road_km,
        "duration_minutes": eta_min,
        "instructions": [
            {
                "step": 1,
                "instruction": f"Proceed to {dest_lat:.4f}, {dest_lon:.4f}",
                "distance_km": road_km,
                "duration_min": eta_min,
            }
        ],
        "polyline": polyline,
        "note": "Straight-line estimate × 1.3 road factor. Set ORS_API_KEY for real routing.",
    }

@tool
def get_route(
    destination_address: str,
    origin_address: str = "",
    unit_type: str = "",
    is_return_trip: bool = False,
    origin_lat: float = 0.0,
    origin_lon: float = 0.0,
    destination_lat: float = 0.0,
    destination_lon: float = 0.0,
) -> str:
    """Routes from origin to incident via ORS → OSMnx → haversine×1.3 fallback; accepts addresses or pre-resolved coords.

    Args:
        destination_address: Incident address; ignored if destination_lat/lon are non-zero.
        origin_address: Unit base address; ignored if origin_lat/lon are non-zero.
        unit_type: Unit type for speed selection ('ambulance_sva', 'police', 'fire', 'rescue').
        is_return_trip: Reduces speed when returning with a patient.
        origin_lat: Origin latitude (skips geocoding if provided).
        origin_lon: Origin longitude (skips geocoding if provided).
        destination_lat: Destination latitude (skips geocoding if provided).
        destination_lon: Destination longitude (skips geocoding if provided).

    Returns:
        JSON string with keys: backend, distance_km, duration_minutes, instructions, polyline,
        origin_coords, destination_coords, error.
    """
    error_msg = None

    if is_return_trip:
        if unit_type in ["fire", "rescue"]:
            speed_kmh = 45.0
        elif unit_type == "police":
            speed_kmh = 60.0
        else:
            speed_kmh = 50.0
    else:
        speed_kmh = VEHICLE_SPEEDS_KMH.get(unit_type, VEHICLE_SPEEDS_KMH["default"])

    if destination_lat == 0.0 and destination_lon == 0.0:
        coords = _geocode_address(destination_address)
        if coords:
            destination_lat, destination_lon = coords
        else:
            return json.dumps({
                "backend": "error",
                "distance_km": None,
                "duration_minutes": None,
                "instructions": [],
                "polyline": None,
                "origin_coords": [origin_lat, origin_lon],
                "destination_coords": None,
                "error": f"Could not geocode destination '{destination_address}'. Cannot route.",
            }, ensure_ascii=False)

    if not _in_sevilla_bounds(destination_lat, destination_lon):
        return json.dumps({
            "backend": "error",
            "distance_km": None,
            "duration_minutes": None,
            "instructions": [],
            "polyline": None,
            "origin_coords": [origin_lat, origin_lon],
            "destination_coords": [destination_lat, destination_lon],
            "error": (
                f"Destination coordinates ({destination_lat:.4f}, {destination_lon:.4f}) "
                f"are outside Sevilla's operational area. "
                f"This system only handles incidents within the city of Sevilla."
            ),
        }, ensure_ascii=False)

    if origin_lat == 0.0 and origin_lon == 0.0:
        if unit_type and unit_type in UNIT_BASES and not origin_address:
            best_dist = float('inf')
            best_base = None
            best_coords = None

            for base_name in UNIT_BASES[unit_type]:
                curr_coords = _geocode_address(base_name)
                if curr_coords:
                    dist = _haversine_km(curr_coords[0], curr_coords[1], destination_lat, destination_lon)
                    if dist < best_dist:
                        best_dist = dist
                        best_base = base_name
                        best_coords = curr_coords

            if best_coords:
                origin_lat, origin_lon = best_coords
            else:
                origin_lat, origin_lon = 37.3886, -5.9823
                error_msg = f"Could not determine nearest base for '{unit_type}' — using city centre."

        elif origin_address:
            coords = _geocode_address(origin_address)
            if coords:
                origin_lat, origin_lon = coords
            else:
                origin_lat, origin_lon = 37.3886, -5.9823
                error_msg = f"Could not geocode origin '{origin_address}' — using city centre approximation."
        else:
            origin_lat, origin_lon = 37.3886, -5.9823
            error_msg = "No origin_address or unit_type provided — using city centre approximation."

    route = (
        _route_via_ors(origin_lat, origin_lon, destination_lat, destination_lon, speed_kmh)
        or _route_via_osmnx(origin_lat, origin_lon, destination_lat, destination_lon, speed_kmh)

        or _route_stub(origin_lat, origin_lon, destination_lat, destination_lon, speed_kmh)
    )

    route["origin_coords"]      = [origin_lat, origin_lon]
    route["destination_coords"] = [destination_lat, destination_lon]
    route["error"]              = error_msg

    return json.dumps(route, ensure_ascii=False)

if __name__ == "__main__":
    tests = [
        ("Base Sur, Sevilla", "Calle Sierpes 14, Sevilla", "ambulance_svb", False),
        ("", "Plaza Nueva 8, Sevilla", "ambulance_sva", False),
        ("", "Plaza Nueva 8, Sevilla", "ambulance_sva", True),
        ("Parque Bomberos Norte", "Avenida de la Constitución 5, Sevilla", "fire", False),
    ]
    print("get_route test results\n" + "-" * 50)
    for origin, dest, utype, is_ret in tests:
        result = json.loads(get_route(
            origin_address=origin,
            destination_address=dest,
            unit_type=utype,
            is_return_trip=is_ret
        ))
        label = origin if origin else f"Nearest '{utype}'"
        return_str = " (RETURN)" if is_ret else ""
        print(f"\n{label}{return_str}  ->  {dest}")
        print(f"  Backend  : {result['backend']}")
        print(f"  Distance : {result['distance_km']} km")
        print(f"  ETA      : {result['duration_minutes']} min")
        if result.get("error"):
            print(f"  Warning  : {result['error']}")
        if result["instructions"]:
            first = result["instructions"][0]
            print(f"  Step 1   : {first.get('instruction', first)}")
