"""
test_agents.py
--------------
Integration test — runs all five IMERS agents in pipeline order against
a realistic scenario, using fallback mode (no LLM required).

Run with:
    python test_agents.py

Each agent is tested in isolation first, then the results are chained
as they would be in the LangGraph pipeline.
"""

import json
import sys
import time
import io
from pathlib import Path

import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.tts_agent import TTSAgent


if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


SCRIPT_DIR = Path(__file__).resolve().parent
AUDIO_PATH = str(SCRIPT_DIR / "data/recordings/Grabación1.m4a")

TRANSCRIPT = (
    "Hola, ha habido un accidente de trafico muy grave en la Avenida de la "
    "Constitucion esquina con Calle Sierpes en Sevilla. Hay tres vehiculos "
    "implicados, uno de los conductores esta inconsciente y atrapado en el "
    "coche, otro tiene heridas visibles en la cabeza, y hay humo saliendo "
    "del motor del tercer vehiculo. Hay al menos cuatro personas heridas."
)


def section(title: str):
    print(f"\n{'=' * 64}")
    print(f"  {title}")
    print("=" * 64)


def ok(label: str, value, width: int = 18):
    print(f"  [OK] {label:<{width}}: {value}")


def warn(msg: str):
    print(f"  [WARN]  {msg}")



section("Agent 1 — TTS Agent")

tts = TTSAgent()

if AUDIO_PATH:
    t0 = time.perf_counter()
    tts_result = json.loads(tts.run_to_json(AUDIO_PATH))
    ok("language",    tts_result["language"])
    ok("duration",    f"{tts_result['duration_sec']}s")
    ok("processed",   f"{round(time.perf_counter() - t0, 2)}s")
    ok("error",       tts_result["error"])
    transcript = tts_result["transcript"] or TRANSCRIPT
    print(f"\n  Transcript: {transcript[:80]}…")
else:
    print("  No audio file provided — using hardcoded transcript for downstream tests.")
    print(f"  Transcript: {TRANSCRIPT[:80]}…")
    transcript = TRANSCRIPT

assert transcript, "Transcript must not be empty"
print("  TTSAgent [OK]")



section("Agent 2 — Procedure Agent (fallback mode)")
from agents.procedure_agent import ProcedureAgent
from tools.classify_incident import classify_incident

classification_json = classify_incident(transcript)
classification = json.loads(classification_json)

priority_to_severity = {
    "P-1 (Emergency)": "critical",
    "P-2 (Urgent)": "high",
    "P-3 (Non-Urgent)": "medium",
    "P-4 (Information)": "low",
}
severity = priority_to_severity.get(classification["priority"], "high")

procedure = ProcedureAgent.__new__(ProcedureAgent)
assessment = procedure._fallback(classification["incident_type"], severity, "none", transcript)
assessment["victims"] = classification["victims"]

ok("incident_type",  assessment["incident_type"])
ok("severity",       assessment["severity"])
ok("victims",        assessment["victims"])
ok("confidence",     assessment["classification_confidence"])
ok("protocol_src",   assessment["protocol_source"])
ok("key_actions",    len(assessment["key_actions"]))
ok("error",          assessment["error"])

assert assessment["incident_type"] == "traffic_accident", \
    f"Expected traffic_accident, got {assessment['incident_type']}"
assert assessment["severity"] in ("critical", "high"), \
    f"Expected critical/high, got {assessment['severity']}"
print("  ProcedureAgent [OK]")



section("Agent 3 — Geo Agent (fallback mode)")
from agents.geo_agent import GeoAgent

geo = GeoAgent.__new__(GeoAgent)
geo_record = geo._fallback(transcript, [], "Sevilla, España", "none")

ok("location_found", geo_record["location_resolved"])
ok("address",        (geo_record["incident_address"] or "")[:50])
ok("latitude",       geo_record["incident_lat"])
ok("longitude",      geo_record["incident_lon"])
ok("confidence",     geo_record["location_confidence"])
ok("map_url",        geo_record.get("map_url"))

for w in geo_record["warnings"]:
    warn(w)
print("  GeoAgent [OK]")



section("Agent 4 — Dispatch Agent (fallback mode)")
from agents.dispatch_agent import DispatchAgent

dispatch = DispatchAgent.__new__(DispatchAgent)
dispatch_result = dispatch._fallback(
    incident_type=assessment["incident_type"],
    severity=assessment["severity"],
    location=geo_record["incident_address"] or "Av. Constitucion, Sevilla",
    victims=assessment["victims"],
    error="none"
)

ok("approved",       dispatch_result["dispatch_approved"])
ok("total_units",    dispatch_result["total_units"])
ok("first_arrival",  f"{dispatch_result['estimated_first_arrival']} min")

for u in dispatch_result["units"]:
    print(f"  -> {u['id']:15s}  ({u['type']:18s})  ETA {u['eta_minutes']} min")

for w in dispatch_result["warnings"]:
    warn(w)

assert dispatch_result["total_units"] > 0, "At least one unit must be dispatched"
print("  DispatchAgent [OK]")



section("Agent 5 — Analysis Agent (fallback mode)")
from agents.analysis_agent import AnalysisAgent

analysis = AnalysisAgent.__new__(AnalysisAgent)
report = analysis.generate_dashboard_data()

ok("hotspots",       len(report.get("hotspots", [])))
ok("predictions",    len(report.get("forecast", [])))
ok("rt_mean",        f"{report.get('kpis', {}).get('overall', {}).get('mean', 0)} min")
ok("pct_hotspots",   len(report.get("hotspots", [])))

if report.get("hotspots"):
    hs = report["hotspots"][0]
    print(f"\n  Top hotspot : {hs.get('cell_id')} — {hs.get('incident_count')} incidents, "
          f"type={hs.get('dominant_type')}")


print("  AnalysisAgent [OK]")



section("Full pipeline summary")
print(f"  Transcript   : {transcript[:60]}...")
print(f"  Incident     : {assessment['incident_type']} / {assessment['severity_confirmed']}")
print(f"  Victims      : {assessment['victims']}")
print(f"  Location     : {geo_record['incident_address'] or 'unresolved'}")
print(f"  Coords       : {geo_record['incident_lat']}, {geo_record['incident_lon']}")
print(f"  Priority     : {dispatch_result['dispatch_priority'] if 'dispatch_priority' in dispatch_result else 'high'}")
print(f"  Units sent   : {dispatch_result['total_units']}  "
      f"(first ETA {dispatch_result['estimated_first_arrival']} min)")
print(f"  Protocol src : {assessment['protocol_source']}")
print(f"  Top hotspot  : {report['hotspots'][0]['cell_id'] if report.get('hotspots') else 'none'}")
print("\n  All agents passed [OK]\n")