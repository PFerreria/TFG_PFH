"""
Tests the full pipeline using a comprehensive set of realistic scenarios and edge cases.

Output:
    test/e2e_results/YYYY-MM-DD_HH-MM_report.md
    test/e2e_results/YYYY-MM-DD_HH-MM_report.json
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

if sys.platform == "win32":
    try:
        sys.stdout.fileno(); sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, io.UnsupportedOperation, ValueError):
        pass
    try:
        sys.stderr.fileno(); sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, io.UnsupportedOperation, ValueError):
        pass

_HERE        = Path(__file__).resolve().parent
_CODE_DIR    = _HERE.parent
_RECORDINGS  = _CODE_DIR / "data" / "recordings" / "ai_audios"

if str(_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(_CODE_DIR))

from dotenv import load_dotenv
load_dotenv(_CODE_DIR / ".env")

_RESULTS_DIR = _HERE / "e2e_results"
_RESULTS_DIR.mkdir(exist_ok=True)


@dataclass
class Scenario:
    id:           str
    name:         str
    category:     str
    transcript:   Optional[str] = None
    audio_file:   Optional[str] = None
    expected_type: Optional[str] = None
    expected_severity: Optional[str] = None
    expected_has_location: bool = True
    expected_status: str = "processed"
    notes: str = ""


TEXT_SCENARIOS = [
    Scenario(
        id="T01", name="Cardiac Arrest – home (Calle Feria)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "cardiac_arrest_01.txt", encoding="utf-8").read().strip(),
        audio_file="cardiac_arrest_01.mp3",
        expected_type="cardiac_arrest", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T02", name="Cardiac Arrest – Plaza Nueva bystander",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "cardiac_arrest_02.txt", encoding="utf-8").read().strip(),
        audio_file="cardiac_arrest_02.mp3",
        expected_type="cardiac_arrest", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T03", name="Cardiac Arrest – industrial zone",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "cardiac_arrest_03.txt", encoding="utf-8").read().strip(),
        audio_file="cardiac_arrest_03.mp3",
        expected_type="cardiac_arrest", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T04", name="Traffic Accident – multi-vehicle Av. Constitución",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "traffic_accident_01.txt", encoding="utf-8").read().strip(),
        audio_file="traffic_accident_01.mp3",
        expected_type="traffic_accident", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T05", name="Traffic Accident – pedestrian run-over Calle Betis",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "traffic_accident_02.txt", encoding="utf-8").read().strip(),
        audio_file="traffic_accident_02.mp3",
        expected_type="traffic_accident", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T06", name="Traffic Accident – motorcycle minor (Alameda)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "traffic_accident_03.txt", encoding="utf-8").read().strip(),
        audio_file="traffic_accident_03.mp3",
        expected_type="traffic_accident", expected_severity="high",
        expected_has_location=True,
    ),
    Scenario(
        id="T07", name="Traffic Accident – highway mass casualty (A-4)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "traffic_accident_04.txt", encoding="utf-8").read().strip(),
        audio_file="traffic_accident_04.mp3",
        expected_type="traffic_accident", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T08", name="Fire – apartment building (Calle Resolana)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "fire_01.txt", encoding="utf-8").read().strip(),
        audio_file="fire_01.mp3",
        expected_type="fire", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T09", name="Fire – car fire in parking lot (Nervión CC)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "fire_02.txt", encoding="utf-8").read().strip(),
        audio_file="fire_02.mp3",
        expected_type="fire", expected_severity="high",
        expected_has_location=True,
    ),
    Scenario(
        id="T10", name="Fire – kitchen smoke (Calle Sierpes restaurant)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "fire_03.txt", encoding="utf-8").read().strip(),
        audio_file="fire_03.mp3",
        expected_type="fire", expected_severity="high",
        expected_has_location=True,
    ),
    Scenario(
        id="T11", name="Gas Leak – residential (Av. Menéndez Pelayo)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "gas_leak_01.txt", encoding="utf-8").read().strip(),
        audio_file="gas_leak_01.mp3",
        expected_type="gas_leak", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T12", name="Gas Leak – street pipeline (Calle Reyes Católicos)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "gas_leak_02.txt", encoding="utf-8").read().strip(),
        audio_file="gas_leak_02.mp3",
        expected_type="gas_leak", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T13", name="Stroke – facial droop (Calle San Jacinto)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "stroke_01.txt", encoding="utf-8").read().strip(),
        audio_file="stroke_01.mp3",
        expected_type="stroke", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T14", name="Stroke – sudden speech loss (Plaza del Salvador)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "stroke_02.txt", encoding="utf-8").read().strip(),
        audio_file="stroke_02.mp3",
        expected_type="stroke", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T15", name="Drowning – child in pool (Av. Eduardo Dato)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "drowning_01.txt", encoding="utf-8").read().strip(),
        audio_file="drowning_01.mp3",
        expected_type="drowning", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T16", name="Drowning – Guadalquivir river (Puente de Triana)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "drowning_02.txt", encoding="utf-8").read().strip(),
        audio_file="drowning_02.mp3",
        expected_type="drowning", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T17", name="Fall Injury – elderly fall (Calle González Cuadrado)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "fall_injury_01.txt", encoding="utf-8").read().strip(),
        audio_file="fall_injury_01.mp3",
        expected_type="fall_injury", expected_severity="high",
        expected_has_location=True,
    ),
    Scenario(
        id="T18", name="Fall Injury – construction fall (Calle Luis Montoto)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "fall_injury_02.txt", encoding="utf-8").read().strip(),
        audio_file="fall_injury_02.mp3",
        expected_type="fall_injury", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T19", name="Mental Health – suicide risk on bridge (Barqueta)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "mental_health_01.txt", encoding="utf-8").read().strip(),
        audio_file="mental_health_01.mp3",
        expected_type="mental_health_crisis", expected_severity="high",
        expected_has_location=True,
    ),
    Scenario(
        id="T20", name="Mental Health – anxiety attack (Calle Canalejas)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "mental_health_02.txt", encoding="utf-8").read().strip(),
        audio_file="mental_health_02.mp3",
        expected_type="mental_health_crisis", expected_severity="medium",
        expected_has_location=True,
    ),
    Scenario(
        id="T21", name="Overdose – heroin at nightclub (Calle Marqués de Contadero)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "overdose_01.txt", encoding="utf-8").read().strip(),
        audio_file="overdose_01.mp3",
        expected_type="overdose", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T22", name="Chemical Spill – hazmat truck (Polígono Calonge)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "chemical_spill_01.txt", encoding="utf-8").read().strip(),
        audio_file="chemical_spill_01.mp3",
        expected_type="chemical_spill", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T23", name="Explosion – bar explosion (Calle Cervantes)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "explosion_01.txt", encoding="utf-8").read().strip(),
        audio_file="explosion_01.mp3",
        expected_type="explosion", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T24", name="Flooding – street inundation (Calle Torneo)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "flooding_01.txt", encoding="utf-8").read().strip(),
        audio_file="flooding_01.mp3",
        expected_type="flooding", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T25", name="Infrastructure Collapse – building collapse (Calle Catalanes)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "infrastructure_collapse_01.txt", encoding="utf-8").read().strip(),
        audio_file="infrastructure_collapse_01.mp3",
        expected_type="infrastructure_collapse", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T26", name="Assault – knife attack (Calle Imagen)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "assault_01.txt", encoding="utf-8").read().strip(),
        audio_file="assault_01.mp3",
        expected_type="assault", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T27", name="Domestic Violence – knife threat (Calle Torneo)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "domestic_violence_01.txt", encoding="utf-8").read().strip(),
        audio_file="domestic_violence_01.mp3",
        expected_type="domestic_violence", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T28", name="Robbery – armed bank robbery (Av. de Andalucía)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "robbery_01.txt", encoding="utf-8").read().strip(),
        audio_file="robbery_01.mp3",
        expected_type="robbery", expected_severity="critical",
        expected_has_location=True,
    ),
    Scenario(
        id="T29", name="Missing Person – child lost (Parque María Luisa)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "missing_person_01.txt", encoding="utf-8").read().strip(),
        audio_file="missing_person_01.mp3",
        expected_type="missing_person", expected_severity="medium",
        expected_has_location=True,
    ),
    Scenario(
        id="T30", name="Missing Person – Alzheimer patient (Calle Amor de Dios)",
        category="text_pipeline",
        transcript=open(_RECORDINGS / "missing_person_02.txt", encoding="utf-8").read().strip(),
        audio_file="missing_person_02.mp3",
        expected_type="missing_person", expected_severity="medium",
        expected_has_location=True,
    ),
]

EDGE_CASES = [
    Scenario(
        id="E01", name="Empty transcript",
        category="edge_case",
        transcript="",
        expected_status="aborted",
        expected_has_location=False,
        notes="Should abort with clear error message",
    ),
    Scenario(
        id="E02", name="Single word ('ayuda')",
        category="edge_case",
        transcript="Ayuda.",
        expected_type="other",
        expected_has_location=False,
        notes="Minimal input, low confidence expected",
    ),
    Scenario(
        id="E03", name="English transcript – injury",
        category="edge_case",
        transcript=(
            "Hello, there was a serious car accident at the junction of "
            "Avenida de la Constitución and Calle Sierpes. One driver is unconscious "
            "and trapped. Please send an ambulance urgently."
        ),
        expected_type="traffic_accident", expected_severity="critical",
        expected_has_location=True,
        notes="English call — pipeline should handle multi-language",
    ),
    Scenario(
        id="E04", name="Vague location – no specific address",
        category="edge_case",
        transcript="Hay un incendio en un edificio de Sevilla. No sé exactamente dónde.",
        expected_type="fire",
        expected_has_location=False,
        notes="Should trigger geo retry and end with low confidence location",
    ),
    Scenario(
        id="E05", name="Multiple incident types – fire + injuries + gas",
        category="edge_case",
        transcript=(
            "Hay una explosión en la fábrica de la Calle Industria 40, Polígono Sur. "
            "Hay fuego y huele a gas. Hay al menos diez heridos en el suelo. "
            "El tejado se ha hundido parcialmente. Necesitamos bomberos, ambulancias "
            "y policía urgentemente."
        ),
        expected_type="explosion",
        expected_severity="critical",
        expected_has_location=True,
        notes="Multiple incident cues — should pick dominant type (explosion)",
    ),
    Scenario(
        id="E06", name="Non-emergency – information request",
        category="edge_case",
        transcript=(
            "Llamo para pedir información sobre cómo hacer una denuncia por robo "
            "de coche. No es una emergencia, fue ayer por la tarde."
        ),
        expected_type="robbery",
        expected_has_location=False,
        notes="Non-emergency call — should classify but with low urgency",
    ),
    Scenario(
        id="E07", name="Stuttered / repeated call",
        category="edge_case",
        transcript=(
            "Hola, sí, llamaba, llamaba porque, porque, mi, mi madre, mi madre "
            "ha tenido, ha tenido un infarto, un infarto. Vivimos, vivimos en la "
            "Calle Feria, en la Calle Feria número cincuenta. Por favor, por favor, "
            "vengan rápido, vengan rápido."
        ),
        expected_type="cardiac_arrest",
        expected_severity="critical",
        expected_has_location=True,
        notes="Stuttered speech — transcription and NLP robustness test",
    ),
    Scenario(
        id="E08", name="Whisper-only test – synthetic silence WAV",
        category="edge_case",
        transcript=None,
        notes="Silent WAV file — TTS should fail or return empty, pipeline aborts",
        expected_status="aborted",
        expected_has_location=False,
    ),
]


@dataclass
class TestResult:
    id:              str
    name:            str
    category:        str
    status:          str
    scenario:        dict

    pipeline_status: Optional[str] = None
    incident_type:   Optional[str] = None
    severity:        Optional[str] = None
    victims:         Optional[int] = None
    location_address: Optional[str] = None
    location_confidence: Optional[str] = None
    location_found:  bool = False
    units_dispatched: int = 0
    protocol_source: Optional[str] = None
    routes_count:    int = 0

    total_ms:        float = 0.0
    node_timings:    dict  = field(default_factory=dict)

    transcription:   Optional[str] = None
    expected_transcript: Optional[str] = None
    transcript_similarity: float = 0.0

    type_match:      bool = False
    severity_match:  bool = False
    location_match:  bool = False
    schema_complete: bool = False
    incongruences:   list = field(default_factory=list)

    error_message:   Optional[str] = None
    raw_report:      Optional[dict] = None



def _text_similarity(a: str, b: str) -> float:
    """Return SequenceMatcher ratio for two strings (0=no match, 1=identical)."""
    if not a or not b:
        return 0.0
    a_norm = re.sub(r"\s+", " ", a.lower().strip())
    b_norm = re.sub(r"\s+", " ", b.lower().strip())
    return SequenceMatcher(None, a_norm, b_norm).ratio()


def _make_silence_wav(duration_sec: float = 2.0) -> str:
    """Write a silent WAV file to a temp path. Returns the path."""
    sr = 16000
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(tmp.name, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        frames = struct.pack("<h", 0) * int(sr * duration_sec)
        wf.writeframes(frames)
    return tmp.name


def _make_tone_wav(freq: float = 440.0, duration_sec: float = 3.0) -> str:
    """Write a sine-wave WAV file to a temp path. Returns the path."""
    sr = 16000
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(tmp.name, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        for i in range(int(sr * duration_sec)):
            val = int(16000 * math.sin(2 * math.pi * freq * i / sr))
            wf.writeframes(struct.pack("<h", val))
    return tmp.name


def _check_required_schema(report: dict) -> tuple[bool, list[str]]:
    """Check that all required keys are present in the final report."""
    required_top = [
        "incident_id", "timestamp", "status", "incident_type",
        "severity", "victims", "location", "protocol", "dispatch",
        "routes", "nearest_hospital", "historical_context", "pipeline",
    ]
    required_dispatch  = ["units", "total_units", "first_arrival_minutes", "priority"]
    required_location  = ["address", "latitude", "longitude", "confidence"]
    required_pipeline  = ["started_at", "completed_at", "node_timings", "geo_retries"]

    missing = []
    for k in required_top:
        if k not in report:
            missing.append(f"top-level key '{k}' missing")

    if "dispatch" in report and isinstance(report["dispatch"], dict):
        for k in required_dispatch:
            if k not in report["dispatch"]:
                missing.append(f"dispatch.{k} missing")

    if "location" in report and isinstance(report["location"], dict):
        for k in required_location:
            if k not in report["location"]:
                missing.append(f"location.{k} missing")

    if "pipeline" in report and isinstance(report["pipeline"], dict):
        for k in required_pipeline:
            if k not in report["pipeline"]:
                missing.append(f"pipeline.{k} missing")

    return len(missing) == 0, missing


_SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}

def _check_incongruences(report: dict, scenario: Scenario) -> list[str]:
    """Detect logical incongruences between pipeline output and scenario expectations."""
    issues = []
    if report.get("status") == "aborted":
        return issues

    incident_type = report.get("incident_type", "")
    severity      = report.get("severity", "")
    victims       = report.get("victims", 0)
    loc           = report.get("location") or {}
    dispatch      = report.get("dispatch") or {}
    protocol      = report.get("protocol") or {}
    routes        = report.get("routes") or []

    if scenario.expected_type and incident_type != scenario.expected_type:
        issues.append(
            f"TYPE_MISMATCH: expected '{scenario.expected_type}', got '{incident_type}'"
        )

    if scenario.expected_severity:
        exp_ord = _SEVERITY_ORDER.get(scenario.expected_severity, 0)
        got_ord = _SEVERITY_ORDER.get(severity, 0)
        if got_ord < exp_ord - 1:
            issues.append(
                f"SEVERITY_TOO_LOW: expected >= '{scenario.expected_severity}', got '{severity}'"
            )

    transcript = scenario.transcript or ""
    victim_words = ["herido", "heridos", "víctima", "inconsciente", "atrapado",
                    "injured", "victim", "trapped", "unconscious"]
    has_victim_mention = any(w in transcript.lower() for w in victim_words)
    if has_victim_mention and victims == 0:
        issues.append("ZERO_VICTIMS: transcript mentions victims but count=0")

    lat = loc.get("latitude")
    lon = loc.get("longitude")
    if lat is not None and lon is not None:
        if not (37.30 <= lat <= 37.48 and -6.10 <= lon <= -5.85):
            issues.append(
                f"LOCATION_OUT_OF_BOUNDS: ({lat:.4f}, {lon:.4f}) is outside Sevilla city area"
            )

    if scenario.expected_has_location and not loc.get("latitude"):
        issues.append("LOCATION_MISSING: expected a location but none resolved")

    total_units = dispatch.get("total_units", 0)
    if severity in ("critical", "high") and total_units == 0:
        issues.append("NO_UNITS: critical/high severity but no units dispatched")

    if total_units > 0 and len(routes) == 0:
        issues.append("NO_ROUTES: units dispatched but route calculation returned nothing")

    common_types = {
        "cardiac_arrest", "fire", "traffic_accident", "gas_leak",
        "stroke", "drowning", "fall_injury",
    }
    if incident_type in common_types:
        proc_src = protocol.get("source", "")
        if "stub" in str(proc_src).lower():
            issues.append(
                f"PROTOCOL_STUB: expected real protocol for '{incident_type}' but got stub source"
            )

    dispatch_priority = str(dispatch.get("priority", "")).lower()
    if severity == "critical" and dispatch_priority in ("low", "non-urgent", "routine"):
        issues.append(
            f"PRIORITY_MISMATCH: severity=critical but dispatch_priority='{dispatch_priority}'"
        )

    schema_ok, missing_keys = _check_required_schema(report)
    if not schema_ok:
        for mk in missing_keys:
            issues.append(f"SCHEMA: {mk}")

    return issues



_pipeline_instance = None
_pipeline_lock     = threading.Lock()


def _get_pipeline():
    global _pipeline_instance
    with _pipeline_lock:
        if _pipeline_instance is None:
            print("  [init] Initialising IMERS pipeline (real agents)…")
            t0 = time.perf_counter()
            from pipeline.graph import IMERSPipeline
            _pipeline_instance = IMERSPipeline()
            elapsed = time.perf_counter() - t0
            print(f"  [init] Pipeline ready in {elapsed:.1f}s")
    return _pipeline_instance



_tts_instance = None


def _get_tts():
    global _tts_instance
    if _tts_instance is None:
        from agents.tts_agent import TTSAgent
        _tts_instance = TTSAgent()
    return _tts_instance



def run_text_test(scenario: Scenario) -> TestResult:
    """Run a single text-pipeline scenario and return a populated TestResult."""
    result = TestResult(
        id=scenario.id,
        name=scenario.name,
        category=scenario.category,
        status="fail",
        scenario={"id": scenario.id, "name": scenario.name, "transcript": scenario.transcript},
    )

    if not scenario.transcript:
        result.status = "skip"
        result.error_message = "No transcript provided for text test"
        return result

    try:
        pipeline = _get_pipeline()
        t0       = time.perf_counter()
        report   = pipeline.run_transcript(scenario.transcript)
        elapsed  = time.perf_counter() - t0

        result.total_ms       = elapsed * 1000
        result.raw_report     = report
        result.pipeline_status = report.get("status")
        result.node_timings   = (report.get("pipeline") or {}).get("node_timings", {})

        if report.get("status") == "aborted":
            result.incident_type = None
            result.location_found = False
            if scenario.expected_status == "aborted":
                result.status = "pass"
            else:
                result.status = "fail"
                result.error_message = f"Unexpected abort: {report.get('abort_reason')}"
            return result

        loc = report.get("location") or {}
        result.incident_type        = report.get("incident_type")
        result.severity             = report.get("severity")
        result.victims              = report.get("victims", 0)
        result.location_address     = loc.get("address")
        result.location_confidence  = loc.get("confidence")
        result.location_found       = bool(loc.get("latitude"))
        result.units_dispatched     = (report.get("dispatch") or {}).get("total_units", 0)
        result.protocol_source      = (report.get("protocol") or {}).get("source")
        result.routes_count         = len(report.get("routes") or [])

        result.type_match     = (result.incident_type == scenario.expected_type)
        exp_sev_ord = _SEVERITY_ORDER.get(scenario.expected_severity or "", 0)
        got_sev_ord = _SEVERITY_ORDER.get(result.severity or "", 0)
        result.severity_match = (got_sev_ord >= exp_sev_ord - 1)
        result.location_match = (result.location_found == scenario.expected_has_location)
        schema_ok, _ = _check_required_schema(report)
        result.schema_complete = schema_ok

        result.incongruences = _check_incongruences(report, scenario)

        critical_checks_ok = (
            result.schema_complete and
            result.type_match and
            result.severity_match and
            result.location_match
        )
        result.status = "pass" if critical_checks_ok else "fail"

    except Exception as exc:
        result.status        = "error"
        result.error_message = str(exc)

    return result


def run_audio_test(scenario: Scenario) -> TestResult:
    """Transcribe an MP3 file then run through the pipeline. Returns TestResult."""
    result = TestResult(
        id=f"A{scenario.id[1:]}",
        name=f"[AUDIO] {scenario.name}",
        category="audio_pipeline",
        status="fail",
        scenario={"id": scenario.id, "name": scenario.name, "audio_file": scenario.audio_file},
    )

    if not scenario.audio_file:
        result.status = "skip"
        result.error_message = "No audio file defined"
        return result

    audio_path = str(_RECORDINGS / scenario.audio_file)
    if not Path(audio_path).exists():
        result.status = "skip"
        result.error_message = f"Audio file not found: {audio_path}"
        return result

    try:
        tts    = _get_tts()
        t0_tts = time.perf_counter()
        tts_res = tts.run(audio_path)
        tts_ms  = (time.perf_counter() - t0_tts) * 1000

        if tts_res.error:
            result.status        = "fail"
            result.error_message = f"TTS error: {tts_res.error}"
            return result

        result.transcription = tts_res.transcript

        if scenario.transcript:
            result.expected_transcript  = scenario.transcript
            result.transcript_similarity = _text_similarity(
                tts_res.transcript, scenario.transcript
            )

        pipeline = _get_pipeline()
        t0_pipe  = time.perf_counter()
        report   = pipeline.run_transcript(tts_res.transcript)
        pipe_ms  = (time.perf_counter() - t0_pipe) * 1000

        result.total_ms       = tts_ms + pipe_ms
        result.raw_report     = report
        result.pipeline_status = report.get("status")
        result.node_timings   = (report.get("pipeline") or {}).get("node_timings", {})

        if report.get("status") == "aborted":
            result.status = "fail" if scenario.expected_status != "aborted" else "pass"
            result.error_message = f"Pipeline aborted: {report.get('abort_reason')}"
            return result

        loc = report.get("location") or {}
        result.incident_type       = report.get("incident_type")
        result.severity            = report.get("severity")
        result.victims             = report.get("victims", 0)
        result.location_address    = loc.get("address")
        result.location_confidence = loc.get("confidence")
        result.location_found      = bool(loc.get("latitude"))
        result.units_dispatched    = (report.get("dispatch") or {}).get("total_units", 0)
        result.protocol_source     = (report.get("protocol") or {}).get("source")
        result.routes_count        = len(report.get("routes") or [])

        result.type_match     = (result.incident_type == scenario.expected_type)
        exp_sev_ord = _SEVERITY_ORDER.get(scenario.expected_severity or "", 0)
        got_sev_ord = _SEVERITY_ORDER.get(result.severity or "", 0)
        result.severity_match = (got_sev_ord >= exp_sev_ord - 1)
        result.location_match = (result.location_found == scenario.expected_has_location)
        schema_ok, _ = _check_required_schema(report)
        result.schema_complete = schema_ok
        result.incongruences   = _check_incongruences(report, scenario)

        critical_checks_ok = (
            result.schema_complete and
            result.type_match and
            result.severity_match and
            result.location_match and
            result.transcript_similarity >= 0.40
        )
        result.status = "pass" if critical_checks_ok else "fail"

        result.node_timings["tts_whisper_ms"] = round(tts_ms, 1)

    except Exception as exc:
        result.status        = "error"
        result.error_message = str(exc)

    return result


def run_silence_test() -> TestResult:
    """Test pipeline with a silent WAV file (edge case E08)."""
    silence_path = _make_silence_wav(2.0)
    scenario     = EDGE_CASES[7]

    result = TestResult(
        id="E08",
        name="[AUDIO] Silent WAV – expected abort",
        category="edge_case_audio",
        status="fail",
        scenario={"id": "E08", "name": scenario.name, "audio_file": silence_path},
    )

    try:
        tts   = _get_tts()
        t0    = time.perf_counter()
        res   = tts.run(silence_path)
        elapsed = (time.perf_counter() - t0) * 1000

        result.total_ms      = elapsed
        result.transcription = res.transcript or ""

        if res.error or not res.transcript or res.transcript.strip() == "":
            result.status = "pass"
            result.error_message = f"TTS returned empty (expected): error={res.error}, transcript='{res.transcript}'"
        else:
            result.status = "fail"
            result.incongruences.append(
                f"WHISPER_HALLUCINATION: got '{res.transcript}' from silent audio"
            )
    except Exception as exc:
        result.status        = "error"
        result.error_message = str(exc)
    finally:
        try:
            Path(silence_path).unlink()
        except Exception:
            pass

    return result



def _api_get(base: str, path: str, **kwargs) -> tuple[int, dict, float]:
    import httpx
    t0 = time.perf_counter()
    try:
        r = httpx.get(f"{base}{path}", timeout=30.0, **kwargs)
        return r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else {}, (time.perf_counter() - t0) * 1000
    except Exception as exc:
        return -1, {"error": str(exc)}, (time.perf_counter() - t0) * 1000


def _api_post(base: str, path: str, payload: dict) -> tuple[int, dict, float]:
    import httpx
    t0 = time.perf_counter()
    try:
        r = httpx.post(f"{base}{path}", json=payload, timeout=120.0)
        return r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else {}, (time.perf_counter() - t0) * 1000
    except Exception as exc:
        return -1, {"error": str(exc)}, (time.perf_counter() - t0) * 1000


def run_api_tests(base_url: str = "http://127.0.0.1:8000") -> list[dict]:
    """Run all API-level tests and return list of result dicts."""
    results = []

    def _r(id_: str, name: str, code: int, expected_code: int, ms: float,
           body: dict, checks: list[str]) -> dict:
        passed = code == expected_code and len(checks) == 0
        return {
            "id": id_, "name": name, "status": "pass" if passed else "fail",
            "http_code": code, "expected_code": expected_code,
            "response_ms": round(ms, 1),
            "failed_checks": checks,
        }

    code, body, ms = _api_get(base_url, "/api/health")
    results.append(_r("API01", "GET /api/health", code, 200, ms, body,
        ["missing 'status' key"] if "status" not in body else []))

    code, body, ms = _api_get(base_url, "/api/incidents/active")
    checks = []
    if not isinstance(body, list):
        checks.append(f"expected list, got {type(body).__name__}")
    results.append(_r("API02", "GET /api/incidents/active", code, 200, ms, body, checks))

    code, body, ms = _api_get(base_url, "/api/incidents/history", params={"days": 7})
    checks = []
    if not isinstance(body, list):
        checks.append(f"expected list, got {type(body).__name__}")
    results.append(_r("API03", "GET /api/incidents/history?days=7", code, 200, ms, body, checks))

    code, body, ms = _api_get(base_url, "/api/dashboard")
    checks = []
    for k in ("kpis", "hotspots", "recent_incidents"):
        if k not in body:
            checks.append(f"missing '{k}'")
    results.append(_r("API04", "GET /api/dashboard", code, 200, ms, body, checks))

    code, body, ms = _api_get(base_url, "/api/kpis")
    checks = []
    if not isinstance(body, dict):
        checks.append(f"expected dict, got {type(body).__name__}")
    results.append(_r("API05", "GET /api/kpis", code, 200, ms, body, checks))

    code, body, ms = _api_get(base_url, "/api/hotspots")
    checks = []
    if not isinstance(body, (list, dict)):
        checks.append(f"unexpected type: {type(body).__name__}")
    results.append(_r("API06", "GET /api/hotspots", code, 200, ms, body, checks))

    code, body, ms = _api_get(base_url, "/api/bases")
    checks = []
    if not isinstance(body, list):
        checks.append(f"expected list, got {type(body).__name__}")
    results.append(_r("API07", "GET /api/bases", code, 200, ms, body, checks))

    code, body, ms = _api_get(base_url, "/api/geocode",
        params={"q": "Avenida de la Constitución, Sevilla"})
    checks = []
    if not body.get("latitude") or not body.get("longitude"):
        checks.append("missing latitude/longitude in geocode response")
    results.append(_r("API08", "GET /api/geocode (Av. Constitución)", code, 200, ms, body, checks))

    code, body, ms = _api_get(base_url, "/api/protocols")
    checks = []
    if not isinstance(body, list):
        checks.append(f"expected list, got {type(body).__name__}")
    results.append(_r("API09", "GET /api/protocols", code, 200, ms, body, checks))

    code, body, ms = _api_get(base_url, "/api/protocols/search",
        params={"q": "paro cardiaco"})
    checks = []
    if not isinstance(body, (list, dict)):
        checks.append(f"unexpected type: {type(body).__name__}")
    results.append(_r("API10", "GET /api/protocols/search?q=paro cardiaco", code, 200, ms, body, checks))

    transcript = "Mi padre tiene un paro cardíaco en Calle Feria 32, Sevilla."
    code, body, ms = _api_post(base_url, "/api/process",
        {"transcript": transcript, "city_hint": "Sevilla, España"})
    checks = []
    if body.get("status") == "aborted":
        checks.append(f"pipeline aborted: {body.get('abort_reason')}")
    if body.get("incident_type") != "cardiac_arrest":
        checks.append(f"type mismatch: expected cardiac_arrest, got {body.get('incident_type')}")
    results.append(_r("API11", "POST /api/process – cardiac arrest", code, 200, ms, body, checks))

    transcript = "Hay un incendio en el edificio de la Calle Resolana 8, Sevilla."
    code, body, ms = _api_post(base_url, "/api/process",
        {"transcript": transcript, "city_hint": "Sevilla, España"})
    checks = []
    if body.get("status") == "aborted":
        checks.append(f"pipeline aborted: {body.get('abort_reason')}")
    if body.get("incident_type") != "fire":
        checks.append(f"type mismatch: expected fire, got {body.get('incident_type')}")
    results.append(_r("API12", "POST /api/process – fire", code, 200, ms, body, checks))

    code, body, ms = _api_post(base_url, "/api/process", {})
    checks = []
    if code not in (400, 422):
        checks.append(f"expected 400/422 for empty body, got {code}")
    results.append(_r("API13", "POST /api/process – empty body (validation error)", code, 422, ms, body, checks))

    long_text = "Hay un accidente. " * 500
    code, body, ms = _api_post(base_url, "/api/process",
        {"transcript": long_text, "city_hint": "Sevilla, España"})
    checks = []
    if code not in (400, 422):
        checks.append(f"expected 400/422 for >8000 char transcript, got {code}")
    results.append(_r("API14", "POST /api/process – oversized transcript (>8000 chars)", code, 422, ms, body, checks))

    code, body, ms = _api_get(base_url, "/api/route",
        params={"origin": "Calle Feria 32, Sevilla",
                "destination": "Hospital Virgen del Rocío",
                "unit_type": "ambulance_sva"})
    checks = []
    if code not in (200, 404):
        checks.append(f"unexpected status: {code}")
    results.append(_r("API15", "GET /api/route", code, 200, ms, body, checks))

    return results



def _sep(char: str = "─", width: int = 70):
    print(char * width)

def _status_icon(status: str) -> str:
    return {"pass": "✓", "fail": "✗", "error": "!", "skip": "○"}.get(status, "?")



def _pct(n: int, total: int) -> str:
    return f"{n}/{total} ({100 * n // max(total, 1)}%)"


def _node_timing_stats(results: list[TestResult]) -> dict[str, dict]:
    """Aggregate per-node timing stats across all results."""
    from collections import defaultdict
    data: dict[str, list[float]] = defaultdict(list)
    for r in results:
        for node, ms in (r.node_timings or {}).items():
            if isinstance(ms, (int, float)):
                data[node].append(float(ms) * 1000 if ms < 100 else float(ms))
    stats = {}
    for node, vals in sorted(data.items()):
        if vals:
            vals_sorted = sorted(vals)
            stats[node] = {
                "count": len(vals),
                "mean_ms":  round(sum(vals) / len(vals), 1),
                "min_ms":   round(min(vals), 1),
                "max_ms":   round(max(vals), 1),
                "p50_ms":   round(vals_sorted[len(vals_sorted) // 2], 1),
                "p95_ms":   round(vals_sorted[int(len(vals_sorted) * 0.95)], 1),
            }
    return stats


def generate_report(
    text_results:  list[TestResult],
    audio_results: list[TestResult],
    edge_results:  list[TestResult],
    api_results:   list[dict],
    run_started:   datetime,
    run_elapsed_s: float,
) -> tuple[str, dict]:
    """Return (markdown_report, json_data)."""

    all_pipeline_results = text_results + edge_results
    all_results          = all_pipeline_results + audio_results

    pass_text  = sum(1 for r in text_results  if r.status == "pass")
    pass_audio = sum(1 for r in audio_results if r.status == "pass")
    pass_edge  = sum(1 for r in edge_results  if r.status == "pass")
    pass_api   = sum(1 for r in api_results   if r.get("status") == "pass")

    total_text  = len(text_results)
    total_audio = len(audio_results)
    total_edge  = len(edge_results)
    total_api   = len(api_results)

    fail_text  = [r for r in text_results  if r.status in ("fail", "error")]
    fail_audio = [r for r in audio_results if r.status in ("fail", "error")]
    fail_edge  = [r for r in edge_results  if r.status in ("fail", "error")]
    fail_api   = [r for r in api_results   if r.get("status") == "fail"]

    all_incongruences = []
    for r in all_results:
        for inc in (r.incongruences or []):
            all_incongruences.append(f"[{r.id}] {r.name}: {inc}")

    timing_stats = _node_timing_stats(all_results)

    pipeline_times = [r.total_ms for r in all_pipeline_results if r.total_ms > 0]
    audio_times    = [r.total_ms for r in audio_results        if r.total_ms > 0]

    def _stats_line(times: list[float]) -> str:
        if not times:
            return "N/A"
        s = sorted(times)
        return (f"mean={sum(s)/len(s):.0f}ms  "
                f"min={min(s):.0f}ms  max={max(s):.0f}ms  "
                f"p50={s[len(s)//2]:.0f}ms  "
                f"p95={s[int(len(s)*0.95)]:.0f}ms")

    ts = run_started.strftime("%Y-%m-%d %H:%M UTC")

    md = f"""# IMERS E2E Test Report
**Run date:** {ts}
**Total duration:** {run_elapsed_s:.1f}s
**Mode:** Real pipeline (IMERS_MOCK_MODE=0), Real LLM via Groq/Fireworks

---

## Executive Summary

| Category | Pass | Total | Rate |
|---|---|---|---|
| Text pipeline (direct transcript) | {pass_text} | {total_text} | {100*pass_text//max(total_text,1)}% |
| Audio pipeline (MP3 → Whisper → pipeline) | {pass_audio} | {total_audio} | {100*pass_audio//max(total_audio,1)}% |
| Edge cases | {pass_edge} | {total_edge} | {100*pass_edge//max(total_edge,1)}% |
| API endpoints | {pass_api} | {total_api} | {100*pass_api//max(total_api,1)}% |
| **TOTAL** | **{pass_text+pass_audio+pass_edge+pass_api}** | **{total_text+total_audio+total_edge+total_api}** | **{100*(pass_text+pass_audio+pass_edge+pass_api)//max(total_text+total_audio+total_edge+total_api,1)}%** |

**Total incongruences detected:** {len(all_incongruences)}

---

## Performance Metrics

### Pipeline wall-clock time (text scenarios, n={len(pipeline_times)})
```
{_stats_line(pipeline_times)}
```

### Audio E2E time (TTS + pipeline, n={len(audio_times)})
```
{_stats_line(audio_times)}
```

### Node timing breakdown (averages across all runs)

| Node | Count | Mean (ms) | Min (ms) | P50 (ms) | P95 (ms) | Max (ms) |
|---|---|---|---|---|---|---|
"""
    for node, s in timing_stats.items():
        md += f"| `{node}` | {s['count']} | {s['mean_ms']} | {s['min_ms']} | {s['p50_ms']} | {s['p95_ms']} | {s['max_ms']} |\n"

    if api_results:
        api_times = [r["response_ms"] for r in api_results if r.get("response_ms", 0) > 0]
        md += f"""
### API endpoint latency (n={len(api_times)})
```
{_stats_line(api_times)}
```
"""

    md += """
---

## Text Pipeline Test Results

| ID | Scenario | Status | Type | Sev | Loc | Units | Total(ms) | Incongruences |
|---|---|---|---|---|---|---|---|---|
"""
    for r in text_results:
        icon = _status_icon(r.status)
        incs = len(r.incongruences or [])
        md += (
            f"| {r.id} | {r.name[:45]} | {icon} {r.status} "
            f"| {r.incident_type or '-'} "
            f"| {r.severity or '-'} "
            f"| {'✓' if r.location_found else '✗'} "
            f"| {r.units_dispatched} "
            f"| {r.total_ms:.0f} "
            f"| {incs} |\n"
        )

    if audio_results:
        md += """
---

## Audio Pipeline Test Results

| ID | Scenario | Status | Transcript Sim | Type | Sev | Loc | TTS+Pipe(ms) | Incongruences |
|---|---|---|---|---|---|---|---|---|
"""
        for r in audio_results:
            icon = _status_icon(r.status)
            sim  = f"{r.transcript_similarity:.2f}" if r.transcript_similarity else "-"
            incs = len(r.incongruences or [])
            md += (
                f"| {r.id} | {r.name[:40]} | {icon} {r.status} "
                f"| {sim} "
                f"| {r.incident_type or '-'} "
                f"| {r.severity or '-'} "
                f"| {'✓' if r.location_found else '✗'} "
                f"| {r.total_ms:.0f} "
                f"| {incs} |\n"
            )

    md += """
---

## Edge Case Results

| ID | Scenario | Status | Notes |
|---|---|---|---|
"""
    for r in edge_results:
        icon  = _status_icon(r.status)
        notes = r.error_message or ""
        md += f"| {r.id} | {r.name[:50]} | {icon} {r.status} | {notes[:80]} |\n"

    if api_results:
        md += """
---

## API Endpoint Results

| ID | Endpoint | Status | HTTP | Response(ms) | Issues |
|---|---|---|---|---|---|
"""
        for r in api_results:
            icon   = _status_icon(r.get("status", "?"))
            issues = "; ".join(r.get("failed_checks", []))[:80]
            md += (
                f"| {r['id']} | {r['name'][:45]} | {icon} {r.get('status')} "
                f"| {r.get('http_code')} "
                f"| {r.get('response_ms')} "
                f"| {issues} |\n"
            )

    if all_incongruences:
        md += """
---

## Incongruences Detected

"""
        for inc in all_incongruences:
            md += f"- {inc}\n"

    if fail_text or fail_audio or fail_edge:
        md += """
---

## Failed Test Details

"""
        for r in fail_text + fail_audio + fail_edge:
            md += f"### [{r.id}] {r.name}\n"
            md += f"- **Status:** {r.status}\n"
            md += f"- **Pipeline status:** {r.pipeline_status}\n"
            md += f"- **Classified as:** `{r.incident_type}` / severity `{r.severity}`\n"
            md += f"- **Location found:** {r.location_found} ({r.location_address})\n"
            md += f"- **Units dispatched:** {r.units_dispatched}\n"
            if r.error_message:
                md += f"- **Error:** {r.error_message}\n"
            if r.incongruences:
                md += "- **Incongruences:**\n"
                for inc in r.incongruences:
                    md += f"  - {inc}\n"
            if r.transcription:
                md += f"- **Transcript:** `{r.transcription[:200]}`\n"
            if r.transcript_similarity:
                md += f"- **Transcript similarity:** {r.transcript_similarity:.2f}\n"
            md += "\n"

    md += """
---

## Improvement Recommendations

"""
    recs = []

    type_mismatches = [r for r in all_results
                       if "TYPE_MISMATCH" in " ".join(r.incongruences or [])]
    if type_mismatches:
        types_failed = set()
        for r in type_mismatches:
            for inc in r.incongruences:
                if "TYPE_MISMATCH" in inc:
                    types_failed.add(inc.split("expected '")[1].split("'")[0])
        recs.append(f"**Classification accuracy:** {len(type_mismatches)} scenarios misclassified. "
                    f"Affected expected types: {', '.join(sorted(types_failed))}. "
                    "Consider expanding NLP rule set or keyword weights in `classify_incident.py`.")

    location_misses = [r for r in all_results
                       if "LOCATION_MISSING" in " ".join(r.incongruences or [])]
    if location_misses:
        recs.append(f"**Geo extraction:** {len(location_misses)} scenarios where location was expected "
                    "but not found. Review `extract_location.py` address patterns and consider adding "
                    "Seville-specific landmark aliases.")

    oob_locations = [r for r in all_results
                     if "LOCATION_OUT_OF_BOUNDS" in " ".join(r.incongruences or [])]
    if oob_locations:
        recs.append(f"**Out-of-bounds locations:** {len(oob_locations)} cases where geocoding resolved "
                    "to coordinates outside Sevilla. The API-level city-centre fallback handles this, "
                    "but the root cause (NER extracting wrong entity) should be investigated.")

    stub_protocols = [r for r in all_results
                      if "PROTOCOL_STUB" in " ".join(r.incongruences or [])]
    if stub_protocols:
        recs.append(f"**Protocol coverage:** {len(stub_protocols)} common incident types returned "
                    "'stub' protocol source. Run `python tools/protocol_indexer.py ingest` to "
                    "populate the vector store and cache.")

    no_routes = [r for r in all_results
                 if "NO_ROUTES" in " ".join(r.incongruences or [])]
    if no_routes:
        recs.append(f"**Route calculation:** {len(no_routes)} cases where units were dispatched "
                    "but no routes returned. Check ORS API key validity and OSMnx fallback configuration.")

    hallucinations = [r for r in all_results
                      if "WHISPER_HALLUCINATION" in " ".join(r.incongruences or [])]
    if hallucinations:
        recs.append("**Whisper hallucination:** Silent/noise-only audio produced non-empty transcription. "
                    "Consider adding a minimum confidence threshold or audio energy check before "
                    "passing to the pipeline.")

    if audio_results:
        low_sim = [r for r in audio_results if 0 < r.transcript_similarity < 0.60]
        if low_sim:
            avg_sim = sum(r.transcript_similarity for r in audio_results if r.transcript_similarity) / max(
                len([r for r in audio_results if r.transcript_similarity]), 1)
            recs.append(f"**Transcription accuracy:** {len(low_sim)} audio files with similarity < 0.60. "
                        f"Average similarity: {avg_sim:.2f}. "
                        f"Consider using `whisper-large-v3` model (set WHISPER_MODEL=large-v3 in .env) "
                        "for better accuracy at the cost of speed.")

    if pipeline_times:
        avg_ms = sum(pipeline_times) / len(pipeline_times)
        if avg_ms > 20000:
            recs.append(f"**Pipeline latency:** Average {avg_ms:.0f}ms (>{20000}ms threshold). "
                        "Consider setting IMERS_FAST_MODE=1 for non-critical use cases, or "
                        "reducing IMERS_AGENT_TIMEOUT_SECS to fail faster on slow LLM calls.")

    if not recs:
        recs.append("No significant issues detected. Pipeline operating within expected parameters.")

    for rec in recs:
        md += f"- {rec}\n"

    md += f"\n---\n*Generated by e2e_comprehensive.py at {ts}*\n"

    json_data = {
        "run_date": ts,
        "duration_seconds": round(run_elapsed_s, 1),
        "summary": {
            "text_pass": pass_text, "text_total": total_text,
            "audio_pass": pass_audio, "audio_total": total_audio,
            "edge_pass": pass_edge, "edge_total": total_edge,
            "api_pass": pass_api, "api_total": total_api,
            "incongruences": len(all_incongruences),
        },
        "performance": {
            "pipeline_times_ms": {"stats": _stats_line(pipeline_times)},
            "audio_times_ms":    {"stats": _stats_line(audio_times)},
            "node_timings":      timing_stats,
        },
        "incongruences": all_incongruences,
        "recommendations": recs,
        "text_results": [
            {k: v for k, v in asdict(r).items() if k not in ("raw_report",)}
            for r in text_results
        ],
        "audio_results": [
            {k: v for k, v in asdict(r).items() if k not in ("raw_report",)}
            for r in audio_results
        ],
        "edge_results": [
            {k: v for k, v in asdict(r).items() if k not in ("raw_report",)}
            for r in edge_results
        ],
        "api_results": api_results,
    }

    return md, json_data



def main():
    parser = argparse.ArgumentParser(description="IMERS E2E Comprehensive Test Suite")
    parser.add_argument("--quick",       action="store_true",
                        help="Run text pipeline + edge cases only (skip audio)")
    parser.add_argument("--api",         action="store_true",
                        help="Also run HTTP API tests (requires server on port 8000)")
    parser.add_argument("--audio-only",  action="store_true",
                        help="Run only audio tests (skip text pipeline)")
    parser.add_argument("--api-url",     default="http://127.0.0.1:8000",
                        help="Base URL of IMERS server (default: http://127.0.0.1:8000)")
    args = parser.parse_args()

    run_started = datetime.now(timezone.utc)
    t_global    = time.perf_counter()

    ts_file = run_started.strftime("%Y-%m-%d_%H-%M")
    md_path  = _RESULTS_DIR / f"{ts_file}_report.md"
    json_path= _RESULTS_DIR / f"{ts_file}_report.json"

    _sep("═")
    print("  IMERS E2E Comprehensive Test Suite")
    print(f"  {run_started.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Output: {md_path}")
    _sep("═")

    text_results:  list[TestResult] = []
    audio_results: list[TestResult] = []
    edge_results:  list[TestResult] = []
    api_results:   list[dict]       = []

    if not args.audio_only:
        print(f"\n[1/4] Text Pipeline Tests ({len(TEXT_SCENARIOS)} scenarios)")
        _sep()
        for scenario in TEXT_SCENARIOS:
            print(f"  Running {scenario.id}: {scenario.name[:55]}", end=" ", flush=True)
            t0 = time.perf_counter()
            result = run_text_test(scenario)
            elapsed = time.perf_counter() - t0
            icon = _status_icon(result.status)
            print(f"{icon} {result.status.upper()}  ({elapsed:.1f}s)  "
                  f"type={result.incident_type or '-'}  sev={result.severity or '-'}")
            if result.incongruences:
                for inc in result.incongruences:
                    print(f"         ⚠  {inc}")
            text_results.append(result)
            time.sleep(0.5)

        pass_t = sum(1 for r in text_results if r.status == "pass")
        print(f"\n  Text pipeline: {_pct(pass_t, len(text_results))} passed")

    if not args.audio_only:
        print(f"\n[2/4] Edge Case Tests ({len(EDGE_CASES)} scenarios)")
        _sep()
        for i, scenario in enumerate(EDGE_CASES):
            if scenario.id == "E08":
                print(f"  Running E08: Silent WAV file", end=" ", flush=True)
                t0 = time.perf_counter()
                result = run_silence_test()
                elapsed = time.perf_counter() - t0
                icon = _status_icon(result.status)
                print(f"{icon} {result.status.upper()}  ({elapsed:.1f}s)")
                if result.error_message:
                    print(f"         → {result.error_message[:100]}")
            else:
                print(f"  Running {scenario.id}: {scenario.name[:55]}", end=" ", flush=True)
                t0 = time.perf_counter()
                result = run_text_test(scenario)
                elapsed = time.perf_counter() - t0
                icon = _status_icon(result.status)
                print(f"{icon} {result.status.upper()}  ({elapsed:.1f}s)  "
                      f"type={result.incident_type or '-'}")
                if result.incongruences:
                    for inc in result.incongruences:
                        print(f"         ⚠  {inc}")
            edge_results.append(result)
            time.sleep(0.3)

        pass_e = sum(1 for r in edge_results if r.status == "pass")
        print(f"\n  Edge cases: {_pct(pass_e, len(edge_results))} passed")

    if not args.quick:
        audio_scenarios = [s for s in TEXT_SCENARIOS if s.audio_file]
        print(f"\n[3/4] Audio Pipeline Tests ({len(audio_scenarios)} MP3 files)")
        _sep()
        for scenario in audio_scenarios:
            print(f"  Running A{scenario.id[1:]}: {scenario.name[:50]}", end=" ", flush=True)
            t0 = time.perf_counter()
            result = run_audio_test(scenario)
            elapsed = time.perf_counter() - t0
            icon = _status_icon(result.status)
            sim_str = f"sim={result.transcript_similarity:.2f}  " if result.transcript_similarity else ""
            print(f"{icon} {result.status.upper()}  ({elapsed:.1f}s)  "
                  f"{sim_str}type={result.incident_type or '-'}")
            if result.incongruences:
                for inc in result.incongruences:
                    print(f"         ⚠  {inc}")
            audio_results.append(result)
            time.sleep(1.0)

        pass_a = sum(1 for r in audio_results if r.status == "pass")
        print(f"\n  Audio pipeline: {_pct(pass_a, len(audio_results))} passed")
    else:
        print("\n[3/4] Audio Pipeline Tests  [SKIPPED — use without --quick to enable]")

    if args.api:
        import httpx as _httpx
        try:
            _resp = _httpx.get(f"{args.api_url}/api/health", timeout=5.0)
            server_up = _resp.status_code == 200
        except Exception:
            server_up = False

        if not server_up:
            print(f"\n[4/4] API Tests  [SKIPPED — server not reachable at {args.api_url}]")
            print(f"      Start with: python -m uvicorn dashboard.api:app --host 0.0.0.0 --port 8000")
        else:
            print(f"\n[4/4] API Endpoint Tests (server: {args.api_url})")
            _sep()
            api_results = run_api_tests(args.api_url)
            for r in api_results:
                icon = _status_icon(r.get("status", "?"))
                issues = "; ".join(r.get("failed_checks", []))
                print(f"  {r['id']}: {r['name'][:50]}  {icon}  {r.get('response_ms'):.0f}ms"
                      + (f"  — {issues}" if issues else ""))
            pass_api = sum(1 for r in api_results if r.get("status") == "pass")
            print(f"\n  API: {_pct(pass_api, len(api_results))} passed")
    else:
        print("\n[4/4] API Tests  [SKIPPED — use --api flag to enable]")

    run_elapsed = time.perf_counter() - t_global
    _sep("═")
    print("\nGenerating report…")

    md, json_data = generate_report(
        text_results, audio_results, edge_results, api_results,
        run_started, run_elapsed,
    )

    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(json.dumps(json_data, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n  Markdown: {md_path}")
    print(f"  JSON:     {json_path}")
    _sep("═")
    print(f"\nTotal runtime: {run_elapsed:.1f}s")

    t_pass = sum(1 for r in text_results  if r.status == "pass")
    a_pass = sum(1 for r in audio_results if r.status == "pass")
    e_pass = sum(1 for r in edge_results  if r.status == "pass")
    p_pass = sum(1 for r in api_results   if r.get("status") == "pass")

    t_tot = len(text_results)
    a_tot = len(audio_results)
    e_tot = len(edge_results)
    p_tot = len(api_results)

    grand_pass  = t_pass + a_pass + e_pass + p_pass
    grand_total = t_tot + a_tot + e_tot + p_tot

    print(f"\n  Results: {grand_pass}/{grand_total} passed  "
          f"(text={t_pass}/{t_tot}  audio={a_pass}/{a_tot}  "
          f"edge={e_pass}/{e_tot}  api={p_pass}/{p_tot})")
    all_incs = sum(len(r.incongruences or []) for r in text_results + audio_results + edge_results)
    print(f"  Incongruences: {all_incs}")
    print()


if __name__ == "__main__":
    main()
