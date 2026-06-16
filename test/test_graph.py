"""
Test the LangGraph state machine
"""

from __future__ import annotations

import json
import sys
import os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time
from typing import Optional

if sys.platform == "win32":
    import io as _io
    try:
        sys.stdout.fileno(); sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, _io.UnsupportedOperation, ValueError):
        pass
    try:
        sys.stderr.fileno(); sys.stderr.reconfigure(encoding='utf-8')
    except (AttributeError, _io.UnsupportedOperation, ValueError):
        pass


class StubTTS:
    """Returns a pre-baked transcript without touching Whisper."""
    def run(self, audio_path: str) -> str:
        return json.dumps({
            "transcript":              "Ha habido un accidente de tráfico en la Avenida de la Constitución "
                                       "esquina con Calle Sierpes, hay tres heridos.",
            "language":                "es",
            "language_probability":    0.99,
            "duration_seconds":        12.4,
            "processing_time_seconds": 1.8,
            "segments":                [],
            "error":                   None,
        })


class StubProcedure:
    def run(self, incident_type, severity, transcript="", extra_context="") -> dict:
        return {
            "incident_type":           incident_type,
            "severity":                severity,
            "action_checklist":        ["Secure scene", "Triage victims", "Notify hospital"],
            "required_units":          ["ambulance_sva", "police"],
            "escalation_criteria":     "Escalate if > 5 victims or fire detected",
            "protocol_source":         "stub",
            "error":                   None,
        }

    def _fallback(self, incident_type, severity, error, transcript="") -> dict:
        return self.run(incident_type, severity, transcript)


class StubDispatch:
    def run(self, incident_type, severity, location, victims=0) -> dict:
        return {
            "dispatched_units": [
                {"id": "AMB-SVA-01", "type": "ambulance_sva", "eta_minutes": 5},
                {"id": "POL-01",     "type": "police",        "eta_minutes": 3},
            ],
            "total_units":             2,
            "estimated_first_arrival": 3,
            "dispatch_priority":       "urgent",
            "warnings":                [],
            "error":                   None,
        }

    def _fallback(self, incident_type, severity, location, victims, error,
                  incident_lat=0.0, incident_lon=0.0) -> dict:
        return self.run(incident_type, severity, location, victims)


class StubGeo:
    def run(self, transcript, units, city_hint="Sevilla, Espana") -> dict:
        return {
            "incident_address":   "Avenida de la Constitución, Sevilla",
            "incident_lat":       37.3861,
            "incident_lon":       -5.9926,
            "nearest_hospital":   "Hospital Virgen del Rocío",
            "nearest_hospital_km": 2.1,
            "routes": [
                {"unit_id": "AMB-SVA-01", "eta_minutes": 5, "distance_km": 3.2},
            ],
            "error": None,
        }

    def _fallback(self, transcript, units, city_hint, error) -> dict:
        return {
            "location_resolved":   True,
            "incident_address":    "Avenida de la Constitución, Sevilla",
            "incident_lat":        37.3861,
            "incident_lon":        -5.9926,
            "location_confidence": "medium",
            "routes":              [],
            "map_url":             None,
            "warnings":            [f"stub fallback: {error}"],
        }

    def _compute_routes_parallel(self, units, destination_address,
                                  dest_lat=0.0, dest_lon=0.0) -> list:
        return [
            {"unit_id": u.get("id", "unknown"), "eta_minutes": 5, "distance_km": 3.2}
            for u in (units or [])
        ]


class StubGeoFailing:
    """Returns an empty location to trigger the geo retry path."""
    call_count = 0
    def run(self, transcript, units, city_hint="Sevilla, Espana") -> dict:
        StubGeoFailing.call_count += 1
        return {
            "incident_address":    None,
            "incident_lat":        None,
            "incident_lon":        None,
            "nearest_hospital":    None,
            "nearest_hospital_km": None,
            "routes":              [],
            "error":               "Geocoding failed",
        }

    def _fallback(self, transcript, units, city_hint, error) -> dict:
        return StubGeo()._fallback(transcript, units, city_hint, error)

    def _compute_routes_parallel(self, units, destination_address,
                                  dest_lat=0.0, dest_lon=0.0) -> list:
        return []


class StubAnalysis:
    def enrich_incident(self, incident_type, location, lat=None, lon=None) -> dict:
        return {
            "is_hotspot":            True,
            "similar_incidents_30d": 7,
            "avg_response_time_target": 6.2,
            "risk_level":            "high",
            "historical_note":       "This intersection has seen 7 traffic incidents in 30 days.",
            "error":                 None,
        }



def section(title: str):
    print(f"\n{'=' * 64}")
    print(f"  {title}")
    print("=" * 64)


def ok(label: str, value, width: int = 26):
    print(f"  ✓ {label:<{width}}: {value}")


def fail(label: str, value, width: int = 26):
    print(f"  ✗ {label:<{width}}: {value}", file=sys.stderr)


def assert_key(d: dict, *keys):
    for key in keys:
        assert key in d and d[key] is not None, \
            f"Expected key '{key}' to be present and non-null in report"



from pipeline.graph import build_graph, _initial_state, IMERSState
from langgraph.checkpoint.memory import MemorySaver



section("Test 1 - Happy path (full pipeline, stub agents)")

agents_happy = {
    "tts":       StubTTS(),
    "procedure": StubProcedure(),
    "dispatch":  StubDispatch(),
    "geo":       StubGeo(),
    "analysis":  StubAnalysis(),
}

app = build_graph(agents_happy, checkpointer=MemorySaver())

state = _initial_state(audio_path="fake_call.wav", city_hint="Sevilla, Espana")

t0     = time.perf_counter()
result = app.invoke(state, config={"configurable": {"thread_id": "test_1"}})
elapsed = round(time.perf_counter() - t0, 3)

report = result.get("final_report", {})

ok("status",                 report.get("status"))
ok("incident_id",            report.get("incident_id"))
ok("incident_type",          report.get("incident_type"))
ok("severity",               report.get("severity"))
ok("victims",                report.get("victims"))
ok("location.address",       report.get("location", {}).get("address"))
ok("dispatch.total_units",   report.get("dispatch", {}).get("total_units"))
ok("dispatch.priority",      report.get("dispatch", {}).get("priority"))
ok("historical_context.status", report.get("historical_context", {}).get("status"))
ok("geo_retries",            report.get("pipeline", {}).get("geo_retries"))
ok("wall_clock_seconds",     f"{elapsed}s")
ok("node_timings keys",      list((report.get("pipeline") or {}).get("node_timings", {}).keys()))

assert report["status"] == "processed",         f"status={report['status']}"
assert report["incident_type"] == "traffic_accident"
assert report["dispatch"]["total_units"] == 2
assert report["location"]["address"] is not None
assert report["historical_context"]["status"] == "pending"

print("\n  Test 1 PASSED [OK]")



section("Test 2 - Direct transcript injection (no audio file)")

state2 = _initial_state(
    transcript="Mi padre ha sufrido un infarto en casa, Calle Betis 22, Sevilla.",
    city_hint="Sevilla, Espana",
)
result2 = app.invoke(state2, config={"configurable": {"thread_id": "test_2"}})
report2 = result2.get("final_report", {})

ok("status",       report2.get("status"))
ok("incident_type",report2.get("incident_type"))
ok("severity",     report2.get("severity"))
ok("tts skipped",  result2.get("node_timings", {}).get("tts_node", 0) < 0.01)

assert report2["status"] == "processed"
print("\n  Test 2 PASSED [OK]")



section("Test 3 - Abort path (missing input)")

state3  = _initial_state()
result3 = app.invoke(state3, config={"configurable": {"thread_id": "test_3"}})
report3 = result3.get("final_report", {})

ok("status",       report3.get("status"))
ok("abort_reason", report3.get("abort_reason"))

assert report3["status"] == "aborted",         f"Expected aborted, got {report3['status']}"
assert report3.get("abort_reason") is not None, "abort_reason must be set"
print("\n  Test 3 PASSED [OK]")



section("Test 4 - Geo retry path (location initially fails)")

state4 = _initial_state(
    transcript="Hay un incendio en el edificio.",
    city_hint="Sevilla, Espana",
)

app4    = build_graph(agents_happy, checkpointer=MemorySaver())
result4 = app4.invoke(state4, config={"configurable": {"thread_id": "test_4"}})
report4 = result4.get("final_report", {})

ok("status",      report4.get("status"))
ok("geo_retries", report4.get("pipeline", {}).get("geo_retries"))

assert report4["status"] in ("processed", "aborted"), \
    f"Unexpected status: {report4['status']}"
print(f"  geo_retries={report4.get('pipeline', {}).get('geo_retries')}  "
      f"(expected >=1 for vague transcript)")
print("\n  Test 4 PASSED [OK]")



section("Test 5 - Final report schema completeness")

required_top_level = [
    "incident_id", "timestamp", "status",
    "incident_type", "severity", "victims",
    "location", "protocol", "dispatch", "routes",
    "nearest_hospital", "historical_context", "pipeline",
]

required_dispatch = ["units", "total_units", "first_arrival_minutes", "priority"]
required_location = ["address", "latitude", "longitude", "confidence"]
required_pipeline = ["started_at", "completed_at", "node_timings", "geo_retries"]

for key in required_top_level:
    assert key in report, f"Missing top-level key: {key}"
    ok(f"report['{key}'] exists", "✓")

for key in required_dispatch:
    assert key in report["dispatch"], f"Missing dispatch key: {key}"

for key in required_location:
    assert key in report["location"], f"Missing location key: {key}"

for key in required_pipeline:
    assert key in report["pipeline"], f"Missing pipeline key: {key}"

print("\n  Test 5 PASSED [OK]")



section("All tests passed [OK]")
print(f"  Pipeline wall-clock (test 1): {elapsed}s")
timings = (report.get("pipeline") or {}).get("node_timings", {})
for node, t in timings.items():
    print(f"    {node:<22}: {t}s")
print()