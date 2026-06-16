from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass, asdict
from typing import Optional, List

from smolagents import tool

_preview_ctx = threading.local()


def _in_preview_mode() -> bool:
    """Return True when the calling thread has set the preview-mode flag."""
    return getattr(_preview_ctx, "active", False)


_SPEED_KMH: dict[str, float] = {
    "ambulance_sva": 70.0,
    "ambulance_svb": 70.0,
    "police":        80.0,
    "fire":          60.0,
    "rescue":        60.0,
}
_SPEED_SUBTYPE_KMH: dict[str, float] = {
    "VIR":  85.0,
    "MOTO": 90.0,
    "AEA":  55.0,
}

_BASE_COORDS: dict[str, tuple[float, float]] = {
    "Hospital Virgen del Rocío":      (37.3582, -5.9794),   
    "Hospital Virgen Macarena":       (37.4093, -5.9877),   
    "Base 061 Cartuja":               (37.4102, -6.0049),   
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


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in kilometres between two WGS84 coordinates."""
    R = 6371.0
    f1, f2 = math.radians(lat1), math.radians(lat2)
    df = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(df / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


_SEVILLA_CENTER: tuple[float, float] = (37.3886, -5.9823)


def _compute_eta(speed_kmh: float, base_location: str,
                 incident_lat: float, incident_lon: float) -> float:
    """Return ETA in minutes: road distance (haversine × 1.3) ÷ emergency speed."""
    coords = _BASE_COORDS.get(base_location)
    if not coords:
        return 8.0  
    dist_km = _haversine_km(coords[0], coords[1], incident_lat, incident_lon)
    road_km  = dist_km * 1.3  
    minutes  = (road_km / speed_kmh) * 60.0
    return max(1.0, round(minutes, 1))

@dataclass
class Unit:
    id: str
    type: str            
    subtype: str        
    status: str
    base_location: str
    speed_kmh: float                     
    eta_minutes: Optional[float] = None  

_UNIT_REGISTRY: list[Unit] = [
    Unit("SVA-01", "ambulance_sva", "SVA",  "available", "Base 061 Cartuja",            70.0),
    Unit("SVA-02", "ambulance_sva", "SVA",  "available", "Base 061 Cartuja",            70.0),
    Unit("VIR-01", "ambulance_sva", "VIR",  "available", "Base 061 Cartuja",            85.0),

    Unit("SVA-03", "ambulance_sva", "SVA",  "available", "Hospital Virgen del Rocío",   70.0),
    Unit("SVA-04", "ambulance_sva", "SVA",  "en_route",  "Hospital Virgen del Rocío",   70.0),
    Unit("SVB-01", "ambulance_svb", "SVB",  "available", "Hospital Virgen del Rocío",   70.0),
    Unit("SVB-02", "ambulance_svb", "SVB",  "available", "Hospital Virgen del Rocío",   70.0),
    Unit("SVB-03", "ambulance_svb", "SVB",  "busy",      "Hospital Virgen del Rocío",   70.0),

    Unit("SVA-05", "ambulance_sva", "SVA",  "available", "Hospital Virgen Macarena",    70.0),
    Unit("SVA-06", "ambulance_sva", "SVA",  "available", "Hospital Virgen Macarena",    70.0),
    Unit("SVB-04", "ambulance_svb", "SVB",  "available", "Hospital Virgen Macarena",    70.0),
    Unit("SVB-05", "ambulance_svb", "SVB",  "available", "Hospital Virgen Macarena",    70.0),
    Unit("SVB-06", "ambulance_svb", "SVB",  "busy",      "Hospital Virgen Macarena",    70.0),

    Unit("SVA-07", "ambulance_sva", "SVA",  "available", "Hospital de Valme",           70.0),
    Unit("SVB-07", "ambulance_svb", "SVB",  "available", "Hospital de Valme",           70.0),
    Unit("SVB-08", "ambulance_svb", "SVB",  "available", "Hospital de Valme",           70.0),

    Unit("ZETA-01", "police", "ZETA", "available", "Jefatura Policía Local",            80.0),
    Unit("ZETA-02", "police", "ZETA", "available", "Jefatura Policía Local",            80.0),
    Unit("MOTO-01", "police", "MOTO", "available", "Jefatura Policía Local",            90.0),
    Unit("UME-01",  "police", "UME",  "available", "Jefatura Policía Local",            70.0),
    Unit("UME-02",  "police", "UME",  "busy",      "Jefatura Policía Local",            70.0),

    Unit("ZETA-03", "police", "ZETA", "available", "Distrito Sur",                      80.0),
    Unit("ZETA-04", "police", "ZETA", "available", "Distrito Sur",                      80.0),
    Unit("MOTO-02", "police", "MOTO", "available", "Distrito Sur",                      90.0),

    Unit("ZETA-05", "police", "ZETA", "busy",      "Distrito Macarena",                 80.0),
    Unit("ZETA-06", "police", "ZETA", "available", "Distrito Macarena",                 80.0),
    Unit("MOTO-03", "police", "MOTO", "available", "Distrito Macarena",                 90.0),

    Unit("ZETA-07", "police", "ZETA", "available", "Distrito Triana",                   80.0),
    Unit("MOTO-04", "police", "MOTO", "available", "Distrito Triana",                   90.0),

    Unit("ZETA-08", "police", "ZETA", "available", "Distrito Este",                     80.0),
    Unit("MOTO-05", "police", "MOTO", "available", "Distrito Este",                     90.0),

    Unit("BUL-01",  "fire",    "BUL",  "available", "Parque Central San Bernardo",      60.0),
    Unit("BUP-01",  "fire",    "BUP",  "available", "Parque Central San Bernardo",      60.0),
    Unit("AEA-01",  "fire",    "AEA",  "available", "Parque Central San Bernardo",      55.0),
    Unit("FSV-01",  "rescue",  "FSV",  "available", "Parque Central San Bernardo",      60.0),
    Unit("UMES-01", "fire",    "UMES", "available", "Parque Central San Bernardo",      60.0),

    Unit("BUP-02",  "fire",    "BUP",  "available", "Parque Carretera de Carmona",      60.0),
    Unit("BUP-03",  "fire",    "BUP",  "busy",      "Parque Carretera de Carmona",      60.0),
    Unit("AEA-02",  "fire",    "AEA",  "available", "Parque Carretera de Carmona",      55.0),

    Unit("BUL-02",  "fire",    "BUL",  "available", "Parque Triana Los Remedios",       60.0),
    Unit("BUP-04",  "fire",    "BUP",  "available", "Parque Triana Los Remedios",       60.0),
    Unit("FSV-02",  "rescue",  "FSV",  "available", "Parque Triana Los Remedios",       60.0),

    Unit("BUP-05",  "fire",    "BUP",  "available", "Parque Polígono Sur",              60.0),
    Unit("BUP-06",  "fire",    "BUP",  "available", "Parque Polígono Sur",              60.0),
    Unit("FSV-03",  "rescue",  "FSV",  "busy",      "Parque Polígono Sur",              60.0),
    Unit("BUL-03",  "fire",    "BUL",  "available", "Parque Polígono Sur",              60.0),
]

_dispatch_lock = threading.Lock()
_dispatched: dict[str, float] = {} 


def _is_dispatched(unit_id: str) -> bool:
    """Return True if unit is currently dispatched and its TTL hasn't expired."""
    with _dispatch_lock:
        expiry = _dispatched.get(unit_id)
        if expiry is None:
            return False
        if time.monotonic() > expiry:
            del _dispatched[unit_id]
            return False
        return True


def _mark_dispatched(unit_id: str, eta_minutes: float) -> None:
    """Mark a unit as en_route for ETA * 3 minutes."""
    ttl = eta_minutes * 3 * 60
    with _dispatch_lock:
        _dispatched[unit_id] = time.monotonic() + ttl


def release_units(unit_ids: list[str]) -> None:
    """Immediately return units to available status (call when incident resolves)."""
    with _dispatch_lock:
        for uid in unit_ids:
            _dispatched.pop(uid, None)


def _available_units(unit_type: str,
                     incident_lat: float = 0.0,
                     incident_lon: float = 0.0) -> list[Unit]:
    """Returns non-busy, non-dispatched units of unit_type sorted by ETA; eta_minutes=None if no coordinates given."""
    location_known = incident_lat != 0.0 or incident_lon != 0.0
    result = []
    for u in _UNIT_REGISTRY:
        if u.type != unit_type:
            continue
        if u.status != "available":
            continue
        if _is_dispatched(u.id):
            continue
        eta = (
            _compute_eta(u.speed_kmh, u.base_location, incident_lat, incident_lon)
            if location_known else None
        )
        result.append(Unit(u.id, u.type, u.subtype, u.status, u.base_location, u.speed_kmh, eta))
    return sorted(result, key=lambda u: (u.eta_minutes is None, u.eta_minutes or 0.0))

DispatchRule = tuple[str, int, bool]

_DISPATCH_RULES: dict[tuple[str, str], list[DispatchRule]] = {
    ("cardiac_arrest", "critical"): [
        ("ambulance_sva", 1, True),
        ("ambulance_svb", 1, True),
        ("police",        1, False),
    ],
    ("cardiac_arrest", "high"): [
        ("ambulance_sva", 1, True),
        ("ambulance_svb", 1, False),
    ],
    ("cardiac_arrest", "medium"): [
        ("ambulance_svb", 1, True),
    ],
    ("traffic_accident", "critical"): [
        ("ambulance_sva", 1, True),
        ("ambulance_svb", 2, True),
        ("police",        2, True),
        ("fire",          1, False),
        ("rescue",        1, False),
    ],
    ("traffic_accident", "high"): [
        ("ambulance_sva", 1, True),
        ("ambulance_svb", 1, False),
        ("police",        1, True),
    ],
    ("traffic_accident", "medium"): [
        ("ambulance_svb", 1, True),
        ("police",        1, True),
    ],
    ("traffic_accident", "low"): [
        ("police",        1, True),
    ],
    ("fire", "critical"): [
        ("fire",          2, True),
        ("rescue",        1, True),
        ("ambulance_svb", 2, True),
        ("police",        2, True),
    ],
    ("fire", "high"): [
        ("fire",          2, True),
        ("ambulance_svb", 1, True),
        ("police",        1, False),
    ],
    ("fire", "medium"): [
        ("fire",          1, True),
        ("ambulance_svb", 1, False),
    ],
    ("stroke", "critical"):        [("ambulance_sva", 1, True), ("ambulance_svb", 1, False)],
    ("stroke", "high"):            [("ambulance_sva", 1, True)],
    ("drowning", "critical"):      [("ambulance_sva", 1, True), ("ambulance_svb", 1, True), ("police", 1, True)],
    ("gas_leak", "high"):          [("fire", 1, True), ("police", 1, True), ("ambulance_svb", 1, False)],
    ("gas_leak", "critical"):      [("fire", 2, True), ("police", 2, True), ("ambulance_sva", 1, True)],
    ("explosion", "critical"):     [("fire", 2, True), ("rescue", 1, True), ("ambulance_sva", 1, True), ("ambulance_svb", 2, True), ("police", 2, True)],
    ("assault", "high"):           [("police", 2, True), ("ambulance_svb", 1, False)],
    ("assault", "medium"):         [("police", 1, True)],
    ("robbery", "high"):           [("police", 2, True)],
    ("robbery", "medium"):         [("police", 1, True)],
    ("fall_injury", "high"):       [("ambulance_svb", 1, True), ("police", 1, False)],
    ("fall_injury", "medium"):     [("ambulance_svb", 1, True)],
    ("mental_health_crisis", "high"):   [("police", 1, True), ("ambulance_svb", 1, False)],
    ("mental_health_crisis", "medium"): [("police", 1, True)],
    ("missing_person", "medium"):  [("police", 2, True)],
    ("flooding", "critical"):      [("fire", 2, True), ("police", 2, True), ("rescue", 1, False)],
    ("flooding", "high"):          [("fire", 1, True), ("police", 1, True)],
    ("flooding", "medium"):        [("police", 1, True)],
    ("flooding", "low"):           [("police", 1, True)],
    ("infrastructure_collapse", "critical"): [("fire", 3, True), ("rescue", 2, True), ("ambulance_sva", 1, True), ("ambulance_svb", 2, True), ("police", 3, True)],
    ("infrastructure_collapse", "high"):     [("fire", 2, True), ("rescue", 1, True), ("ambulance_svb", 1, True), ("police", 2, True)],
    ("chemical_spill", "critical"):          [("fire", 2, True), ("rescue", 1, True), ("police", 2, True), ("ambulance_sva", 1, True)],
    ("chemical_spill", "high"):              [("fire", 1, True), ("police", 1, True), ("ambulance_svb", 1, False)],
    ("other_medical", "critical"): [("ambulance_sva", 1, True), ("ambulance_svb", 1, False)],
    ("other_medical", "high"):     [("ambulance_svb", 1, True)],
    ("other_medical", "medium"):   [("ambulance_svb", 1, True)],
    ("other_police",  "high"):     [("police", 2, True)],
    ("other_police",  "medium"):   [("police", 1, True)],
    ("other", "critical"):         [("police", 1, True), ("ambulance_svb", 1, False)],
    ("other", "high"):             [("police", 1, True)],
    ("other", "medium"):           [("police", 1, True)],
    ("other", "low"):              [("police", 1, True)],
    ("traffic_disruption", "high"):   [("police", 2, True)],
    ("traffic_disruption", "medium"): [("police", 1, True)],
    ("traffic_disruption", "low"):    [("police", 1, True)],
    ("utility_failure", "high"):   [("fire", 1, True), ("police", 1, False)],
    ("utility_failure", "medium"): [("police", 1, True)],
    ("utility_failure", "low"):    [("police", 1, True)],
}

_DEFAULT_RULE: list[DispatchRule] = [("ambulance_svb", 1, True), ("police", 1, False)]

def _get_rules(incident_type: str, severity: str) -> list[DispatchRule]:
    """Return dispatch rules, falling back through severity levels if no exact match."""
    key = (incident_type, severity)
    if key in _DISPATCH_RULES:
        return _DISPATCH_RULES[key]
    for fallback_sev in ("critical", "high", "medium", "low"):
        fallback_key = (incident_type, fallback_sev)
        if fallback_key in _DISPATCH_RULES:
            return _DISPATCH_RULES[fallback_key]
    return _DEFAULT_RULE

@tool
def recommend_units(
    incident_type: str,
    severity: str,
    location: str,
    victims: int = 0,
    incident_lat: float = 0.0,
    incident_lon: float = 0.0,
) -> str:
    """Recommends emergency units based on incident type, severity and victim count.

    Args:
        incident_type: Type of emergency — e.g. 'traffic_accident', 'cardiac_arrest', 'fire'.
        severity: One of 'critical', 'high', 'medium', 'low'.
        location: Incident address (context only, not used for routing here).
        victims: Estimated victim count; >3 scales up unit counts.
        incident_lat: Latitude for ETA calculation.
        incident_lon: Longitude for ETA calculation.

    Returns:
        JSON string with keys: dispatched, unavailable, warnings, total_units, estimated_first_arrival.
    """
    rules = _get_rules(incident_type, severity)

    if victims > 3:
        rules = [(utype, max(count, 2), mandatory) for utype, count, mandatory in rules]
    if victims > 6:
        rules = [(utype, max(count, 3), mandatory) for utype, count, mandatory in rules]

    dispatched: list[dict] = []
    unavailable: list[str] = []
    warnings: list[str] = []

    for unit_type, count_needed, mandatory in rules:
        available = _available_units(unit_type, incident_lat, incident_lon)
        allocated = available[:count_needed]

        if len(allocated) < count_needed:
            shortage = count_needed - len(allocated)
            if mandatory and not allocated:
                warnings.append(
                    f"No available {unit_type} units — MANDATORY unit type missing. "
                    "Consider requesting mutual aid."
                )
                unavailable.append(unit_type)
            elif shortage > 0:
                warnings.append(
                    f"Only {len(allocated)}/{count_needed} {unit_type} unit(s) available."
                )

        for unit in allocated:
            dispatched.append({
                "id": unit.id,
                "type": unit.type,
                "subtype": unit.subtype,
                "eta_minutes": unit.eta_minutes,
                "base_location": unit.base_location,
                "destination": location,
            })

            if not _in_preview_mode():
                _mark_dispatched(unit.id, unit.eta_minutes or 30.0)

    known_etas = [u["eta_minutes"] for u in dispatched if u["eta_minutes"] is not None]
    first_arrival = min(known_etas) if known_etas else None

    if severity == "critical" and first_arrival and first_arrival > 10:
        warnings.append(
            f"First unit ETA is {first_arrival} min — exceeds 10-min target for critical incidents."
        )

    return json.dumps({
        "dispatched": dispatched,
        "unavailable": unavailable,
        "warnings": warnings,
        "total_units": len(dispatched),
        "estimated_first_arrival": first_arrival,
    }, ensure_ascii=False)

if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding='utf-8')

    tests = [
        ("cardiac_arrest", "critical", "Calle Betis 22, Sevilla", 1, 37.3800, -5.9980),
        ("traffic_accident", "critical", "Avda. Constitución, Sevilla", 4, 37.3860, -5.9960),
        ("fire", "high", "Plaza Nueva 8, Sevilla", 0, 37.3886, -5.9953),
        ("assault", "medium", "Barrio Triana, Sevilla", 2, 37.3840, -6.0020),
        ("infrastructure_collapse", "critical", "Edificio Centro, Sevilla", 12, 37.3886, -5.9953),
        ("flooding", "high", "Calle Feria 30, Sevilla", 0, 37.3952, -5.9870),
        ("chemical_spill", "critical", "Polígono Industrial Sur, Sevilla", 2, 37.3350, -5.9650),
    ]
    print("recommend_units — test results\n" + "=" * 50)
    for itype, sev, loc, vic, lat, lon in tests:
        result = json.loads(recommend_units(itype, sev, loc, vic, lat, lon))
        print(f"\n[{itype} / {sev}] victims={vic}")
        print(f"  Total units : {result['total_units']}")
        print(f"  First ETA   : {result['estimated_first_arrival']} min")
        for u in result["dispatched"]:
            print(f"  -> {u['id']:15s} ({u['type']:18s}) ETA {u['eta_minutes']} min")
        for w in result["warnings"]:
            print(f"  ⚠ {w}")
