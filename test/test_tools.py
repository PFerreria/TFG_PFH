import json
import sys
import os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

SCENARIO = (
    "Hola, ha habido un accidente de tráfico muy grave en la Avenida de la "
    "Constitución esquina con Calle Sierpes en Sevilla. Hay tres vehículos "
    "implicados, uno de los conductores está inconsciente y atrapado en el "
    "coche, otro tiene heridas visibles en la cabeza, y hay humo saliendo "
    "del motor del tercer vehículo."
)

def section(title: str):
    print(f"\n{'═' * 60}")
    print(f"  {title}")
    print('═' * 60)

def ok(label: str, value):
    print(f"  ✓ {label}: {value}")

def fail(label: str, error):
    print(f"  ✗ {label}: {error}", file=sys.stderr)

section("Tool 1 — classify_incident")
from tools.classify_incident import classify_incident

raw = classify_incident(SCENARIO)
classification = json.loads(raw)

priority_to_severity = {
    "P-1 (Emergency)": "critical",
    "P-2 (Urgent)": "high",
    "P-3 (Non-Urgent)": "medium",
    "P-4 (Information)": "low",
}
severity = priority_to_severity.get(classification.get("priority"), "medium")

ok("incident_type", classification["incident_type"])
ok("priority",      classification["priority"])
ok("victims",       classification["victims"])
ok("confidence",    classification["confidence"])

assert classification["incident_type"] == "traffic_accident", \
    f"Expected traffic_accident, got {classification['incident_type']}"
assert severity in ("critical", "high"), \
    f"Expected critical or high, got {severity}"

section("Tool 2 — extract_location")
from tools.extract_location import extract_location

raw = extract_location(SCENARIO, city_hint="Sevilla, España")
location = json.loads(raw)

ok("found",      location["found"])
ok("address",    location.get("address", "—"))
ok("lat/lon",    f"{location.get('latitude')}, {location.get('longitude')}")
ok("confidence", location["confidence"])
ok("candidates", location["candidates"])

if not location["found"]:
    print("  ⚠  Geocoding failed (no ORS key or Nominatim rate-limit). "
          "Continuing with candidates for downstream steps.")

section("Tool 3 — fetch_protocol")
from tools.protocol_indexer import query_protocol_index as fetch_protocol

raw = fetch_protocol(
    incident_type=classification["incident_type"],
    severity=severity,
    extra_context=f"{classification['victims']} victims, one trapped",
)
protocol = json.loads(raw)

ok("source",           protocol["source"])
ok("retrieval_tier",   protocol["retrieval_tier"])
print(f"\n  Protocol steps (first):\n  {protocol['steps'][0][:150]}…")

section("Tool 4 — recommend_units")
from tools.recommend_units import recommend_units

incident_address = location.get("address") or (
    location["candidates"][0] if location["candidates"] else "Sevilla, España"
)

raw = recommend_units(
    incident_type=classification["incident_type"],
    severity=severity,
    location=incident_address,
    victims=classification["victims"],
)
dispatch = json.loads(raw)

ok("total_units",             dispatch["total_units"])
ok("estimated_first_arrival", f"{dispatch['estimated_first_arrival']} min")

for unit in dispatch["dispatched"]:
    print(f"    -> {unit['id']:15s}  ({unit['type']:18s})  ETA {unit['eta_minutes']} min")

for w in dispatch["warnings"]:
    print(f"  ⚠  {w}")

section("Tool 5 — get_route")
from tools.get_route import get_route

if dispatch["dispatched"]:
    first_unit = dispatch["dispatched"][0]
    raw = get_route(
        origin_address=first_unit["base_location"],
        destination_address=incident_address,
        destination_lat=location.get("latitude") or 0.0,
        destination_lon=location.get("longitude") or 0.0,
    )
    route = json.loads(raw)

    ok("backend",          route["backend"])
    ok("distance_km",      f"{route['distance_km']} km")
    ok("duration_minutes", f"{route['duration_minutes']} min")
    ok("origin_coords",    route["origin_coords"])
    ok("destination",      route["destination_coords"])

    if route.get("error"):
        print(f"  ⚠  {route['error']}")

    if route["instructions"]:
        first_step = route["instructions"][0]
        instr = first_step.get("instruction", str(first_step))
        print(f"\n  First turn: {instr}")
else:
    print("  No units dispatched — skipping route calculation.")

section("Pipeline summary")
print(f"  Incident     : {classification['incident_type']} / {severity}")
print(f"  Victims      : {classification['victims']}")
print(f"  Location     : {location.get('address') or location['candidates']}")
print(f"  Protocol src : {protocol['source']}")
print(f"  Units sent   : {dispatch['total_units']}  (first ETA {dispatch['estimated_first_arrival']} min)")
print(f"  Route via    : {route['backend'] if dispatch['dispatched'] else 'n/a'}")
print(f"\n  All tools passed ✓\n")
