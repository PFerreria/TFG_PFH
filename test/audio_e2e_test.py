"""
IMERS Audio End-to-End Test Suite

Tests the full audio pipeline (Whisper TTS → NLP → Geocoding → Dispatch) using:

Audio files are passed directly to the pipeline's TTS agent — no pre-transcription.
The pipeline itself transcribes via Whisper, then classifies, geolocates, and dispatches.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

_HERE     = Path(__file__).resolve().parent
_CODE_DIR = _HERE.parent
_REC_DIR  = _CODE_DIR / "data" / "recordings"
_OUT_DIR  = _HERE / "e2e_results"
_OUT_DIR.mkdir(exist_ok=True)

if str(_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(_CODE_DIR))

from dotenv import load_dotenv
load_dotenv(_CODE_DIR / ".env")



@dataclass
class AudioScenario:
    id:                 str
    name:               str
    source:             str
    audio_path:         str
    expected_type:      Optional[str] = None
    expected_severity:  Optional[str] = None
    expected_has_location: bool = True
    expected_transcript_hint: Optional[str] = None
    notes:              str = ""
    script:             Optional[str] = None



USER_FILE_SCENARIOS: list[AudioScenario] = [
    AudioScenario(
        id="U01",
        name="User OGG – caida desde ventana",
        source="user_file",
        audio_path=r"C:\Users\epifh\Downloads\sample_audio.ogg",
        expected_type="fall_injury",
        expected_severity="critical",
        expected_has_location=True,
        expected_transcript_hint="Guadalquivir",
        notes="Neighbor fell from 3rd-floor window, Av. del Bajo Guadalquivir #4. OGG Opus 16kHz.",
    ),
    AudioScenario(
        id="U02",
        name="User MP4 – intento de suicidio azotea",
        source="user_file",
        audio_path=r"C:\Users\epifh\Downloads\reg_audio.mp4",
        expected_type="mental_health_crisis",
        expected_severity="critical",
        expected_has_location=True,
        expected_transcript_hint="Carmona",
        notes="Neighbor seen on rooftop about to jump, Calle Cano de Carmona #5. MP4.",
    ),
]



RECORDING_SCENARIOS: list[AudioScenario] = [
    AudioScenario(
        id="R01", name="[MP3] Cardiac Arrest – Calle Feria",
        source="recording",
        audio_path=str(_REC_DIR / "cardiac_arrest_01.mp3"),
        expected_type="cardiac_arrest", expected_severity="critical",
    ),
    AudioScenario(
        id="R02", name="[MP3] Fire – apartment building Resolana",
        source="recording",
        audio_path=str(_REC_DIR / "fire_01.mp3"),
        expected_type="fire", expected_severity="critical",
    ),
    AudioScenario(
        id="R03", name="[MP3] Gas Leak – Av. Menéndez Pelayo",
        source="recording",
        audio_path=str(_REC_DIR / "gas_leak_01.mp3"),
        expected_type="gas_leak", expected_severity="critical",
    ),
    AudioScenario(
        id="R04", name="[MP3] Traffic Accident – highway A-4",
        source="recording",
        audio_path=str(_REC_DIR / "traffic_accident_04.mp3"),
        expected_type="traffic_accident", expected_severity="critical",
        expected_has_location=False,
        notes="Whisper transcribes 'A-4' as 'autovía 4'; km-markers not geocodable via Nominatim.",
    ),
    AudioScenario(
        id="R05", name="[MP3] Stroke – facial droop San Jacinto",
        source="recording",
        audio_path=str(_REC_DIR / "stroke_01.mp3"),
        expected_type="stroke", expected_severity="critical",
    ),
    AudioScenario(
        id="R06", name="[MP3] Stroke – sudden speech loss Salvador",
        source="recording",
        audio_path=str(_REC_DIR / "stroke_02.mp3"),
        expected_type="stroke", expected_severity="critical",
        notes="Previously failing BUG-004: lay-language symptoms",
    ),
    AudioScenario(
        id="R07", name="[MP3] Drowning – river Guadalquivir",
        source="recording",
        audio_path=str(_REC_DIR / "drowning_01.mp3"),
        expected_type="drowning", expected_severity="critical",
    ),
    AudioScenario(
        id="R08", name="[MP3] Mental Health – suicidal Alameda",
        source="recording",
        audio_path=str(_REC_DIR / "mental_health_01.mp3"),
        expected_type="mental_health_crisis", expected_severity="critical",
    ),
    AudioScenario(
        id="R09", name="[MP3] Gas Leak – fear of explosion",
        source="recording",
        audio_path=str(_REC_DIR / "gas_leak_01.mp3"),
        expected_type="gas_leak", expected_severity="critical",
        notes="gas_leak must win over explosion when caller smells gas (higher-weight rule)",
    ),
    AudioScenario(
        id="R10", name="[MP3] Explosion confirmed",
        source="recording",
        audio_path=str(_REC_DIR / "explosion_01.mp3"),
        expected_type="explosion", expected_severity="critical",
    ),
]



SYNTH_SCRIPTS: list[dict] = [
    {
        "id": "S01",
        "name": "[SYNTH] Parada cardíaca – Plaza de Cuba",
        "expected_type": "cardiac_arrest",
        "expected_severity": "critical",
        "expected_transcript_hint": "Plaza de Cuba",
        "notes": "Clear cardiac arrest call with known Seville location",
        "script": (
            "Llamo al ciento doce. Mi marido acaba de desplomarse en la Plaza de Cuba, "
            "número tres. No respira y no tiene pulso. Hay una persona intentando hacerle "
            "el masaje cardíaco pero no sabe bien cómo. Por favor manden una ambulancia "
            "urgente, es una emergencia."
        ),
    },
    {
        "id": "S02",
        "name": "[SYNTH] Incendio con número de calle en palabras (BUG-001)",
        "expected_type": "fire",
        "expected_severity": "critical",
        "expected_transcript_hint": "Feria",
        "notes": "Tests BUG-001 fix: street number spoken as word ('número dieciséis')",
        "script": (
            "Hay un incendio en el edificio de la Calle Feria número dieciséis. "
            "Hay llamas saliendo por las ventanas del segundo piso. "
            "Hay personas atrapadas dentro, al menos dos familias. "
            "Manden los bomberos ya, por favor, es urgente."
        ),
    },
    {
        "id": "S03",
        "name": "[SYNTH] Fuga de gas con miedo a explosión (BUG-002)",
        "expected_type": "gas_leak",
        "expected_severity": "critical",
        "expected_transcript_hint": "gas",
        "notes": "Tests BUG-002 fix: gas_leak wins when caller smells gas + fears explosion",
        "script": (
            "Llamo porque huele muchísimo a gas en todo mi portal, en la Avenida "
            "de la Borbolla número ocho. El olor es insoportable y tengo miedo "
            "de que haya una explosión de un momento a otro. Hay vecinos mayores "
            "en el edificio. Por favor vengan rápido."
        ),
    },
    {
        "id": "S04",
        "name": "[SYNTH] Ictus con síntomas coloquiales (BUG-004)",
        "expected_type": "stroke",
        "expected_severity": "critical",
        "expected_transcript_hint": "cara",
        "notes": "Tests BUG-004 fix: lay-language stroke ('cara torcida', 'palabras sin sentido')",
        "script": (
            "Hola, llamo porque mi padre tiene la cara torcida de repente y dice "
            "palabras sin sentido, no le entendemos nada. Está en la Calle Reyes "
            "Católicos número veintidós, en Sevilla. No puede levantar el brazo "
            "derecho. Por favor es urgente."
        ),
    },
    {
        "id": "S05",
        "name": "[SYNTH] Accidente de tráfico con múltiples heridos",
        "expected_type": "traffic_accident",
        "expected_severity": "critical",
        "expected_transcript_hint": "Constitución",
        "notes": "Multi-vehicle accident, several injured, clear Seville landmark",
        "script": (
            "Ha habido un accidente muy grave en la Avenida de la Constitución "
            "con la Calle Sierpes. Un autobús ha chocado contra varios coches. "
            "Hay al menos cinco heridos en la calzada, uno parece estar inconsciente. "
            "Necesitamos ambulancias y policía inmediatamente."
        ),
    },
    {
        "id": "S06",
        "name": "[SYNTH] Persona ahogándose en el río",
        "expected_type": "drowning",
        "expected_severity": "critical",
        "expected_transcript_hint": "Guadalquivir",
        "notes": "Drowning at named Seville landmark",
        "script": (
            "Una persona se está ahogando en el río Guadalquivir, a la altura del "
            "Puente de Triana. Ha caído al agua y no sabe nadar, ya lleva un minuto "
            "bajo el agua. Hay gente mirando pero nadie se ha tirado. "
            "Por favor manden los equipos de rescate acuático ya."
        ),
    },
    {
        "id": "S07",
        "name": "[SYNTH] Nino atragantado - llamada muy nerviosa",
        "expected_type": "choking",
        "expected_severity": "critical",
        "expected_has_location": True,
        "notes": "Pediatric choking, very distressed caller",
        "script": (
            "Mi hijo, mi hijo de dos años se ha atragantado, se ha atragantado con "
            "un juguete pequeño y no puede respirar, se está poniendo morado. "
            "Vivimos en la Calle Torneo número cuarenta y cinco, primer piso. "
            "Por favor vengan ya, no sé qué hacer."
        ),
    },
    {
        "id": "S08",
        "name": "[SYNTH] Crisis de salud mental - persona en azotea",
        "expected_type": "mental_health_crisis",
        "expected_severity": "critical",
        "expected_transcript_hint": "azotea",
        "notes": "Mental health crisis, person threatening to jump from rooftop",
        "script": (
            "Llamo porque hay una persona en la azotea del edificio de enfrente "
            "que parece que quiere tirarse. Está llorando y gritando cosas. "
            "El edificio está en la Calle Imagen número tres, en el Casco Antiguo "
            "de Sevilla. Por favor manden a alguien con urgencia antes de que "
            "haga algo."
        ),
    },
    {
        "id": "S09",
        "name": "[SYNTH] Derrumbe parcial de edificio",
        "expected_type": "infrastructure_collapse",
        "expected_severity": "critical",
        "expected_transcript_hint": "derrumbe",
        "notes": "Partial building collapse, multiple trapped. 'Calle Catalanes' not in OSM/Nominatim — using Calle San Marcos instead.",
        "script": (
            "Acaba de producirse un derrumbe parcial en un edificio antiguo en la Calle "
            "San Marcos número doce, en el Casco Antiguo de Sevilla. Se ha hundido parte "
            "del techo y hay al menos tres personas atrapadas bajo los escombros. "
            "Se escuchan voces pidiendo ayuda. Necesitamos bomberos de rescate urgentemente."
        ),
    },
    {
        "id": "S10",
        "name": "[SYNTH] Llamada en inglés – accidente de tráfico",
        "expected_type": "other",
        "expected_severity": None,
        "expected_has_location": False,
        "expected_transcript_hint": "accident",
        "notes": (
            "English-language call — known limitation: Spanish-only classifier returns 'other'. "
            "Operator must handle unknown-language calls manually. Location also not geocoded "
            "since spaCy es_core_news_lg does not parse English NER reliably."
        ),
        "script": (
            "Hello, I need help urgently. There has been a serious car accident "
            "on Avenida de la Borbolla near the Maria Luisa Park in Sevilla. "
            "Two cars have crashed, there is a woman unconscious inside one "
            "of the vehicles and she is bleeding heavily. Please send an ambulance "
            "as quickly as possible."
        ),
    },
    {
        "id": "S11",
        "name": "[SYNTH] Sobredosis – llamada confusa",
        "expected_type": "overdose",
        "expected_severity": "critical",
        "notes": "Drug overdose, confused secondary caller",
        "script": (
            "Hola, llamo porque mi amigo ha tomado demasiadas pastillas, no sé "
            "cuántas, y ahora no le puedo despertar. Respira pero muy despacio. "
            "Estamos en la Calle Amor de Dios número siete, en un piso. "
            "Por favor vengan rápido, no sé si está bien."
        ),
    },
    {
        "id": "S12",
        "name": "[SYNTH] Incendio forestal en zona periurbana",
        "expected_type": "fire",
        "expected_severity": "critical",
        "expected_has_location": True,
        "notes": "Wildfire near city outskirts — tests non-address location",
        "script": (
            "Hay un incendio forestal en el monte que está junto a la carretera "
            "de Castilleja de la Cuesta, kilómetro cuatro. El fuego está avanzando "
            "hacia las casas y hay mucho humo. Ya hay llamas de varios metros de altura. "
            "Manden los bomberos forestales ya."
        ),
    },
]



def synthesize_wav(text: str, voice: str = "Microsoft Helena Desktop",
                   rate: int = -2) -> Optional[str]:
    """Synthesize Spanish text to WAV using Windows SAPI. Returns path or None."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()

    ps_script = f"""
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SelectVoice('{voice}')
$synth.Rate = {rate}
$synth.SetOutputToWaveFile('{tmp.name.replace(chr(92), "/")}')
$synth.Speak('{text.replace("'", "''")}')
$synth.Dispose()
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0 or not Path(tmp.name).exists():
        return None
    return tmp.name



_pipeline = None
_tts = None


def get_pipeline():
    global _pipeline
    if _pipeline is None:
        print("  [init] Loading IMERS pipeline...")
        t0 = time.perf_counter()
        from pipeline.graph import IMERSPipeline
        _pipeline = IMERSPipeline()
        print(f"  [init] Pipeline ready in {time.perf_counter()-t0:.1f}s")
    return _pipeline


def get_tts():
    global _tts
    if _tts is None:
        from agents.tts_agent import TTSAgent
        _tts = TTSAgent()
    return _tts



@dataclass
class AudioResult:
    id:             str
    name:           str
    source:         str
    status:         str
    audio_path:     str

    transcript:         Optional[str] = None
    transcript_hint_ok: bool = False
    tts_ms:         float = 0.0

    pipeline_status:    Optional[str] = None
    incident_type:      Optional[str] = None
    severity:           Optional[str] = None
    victims:            int = 0
    location_address:   Optional[str] = None
    location_lat:       Optional[float] = None
    location_lon:       Optional[float] = None
    location_confidence: Optional[str] = None
    units_dispatched:   int = 0
    routes_count:       int = 0
    protocol_source:    Optional[str] = None
    pipeline_ms:        float = 0.0
    total_ms:           float = 0.0

    type_match:     Optional[bool] = None
    severity_match: Optional[bool] = None
    location_found: bool = False

    incongruences:  list = field(default_factory=list)
    error_message:  Optional[str] = None
    notes:          str = ""



_SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_SEVILLA_BBOX = (37.30, 37.48, -6.10, -5.85)


def _check_location_bounds(lat, lon) -> bool:
    if lat is None or lon is None:
        return True
    lat_min, lat_max, lon_min, lon_max = _SEVILLA_BBOX
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def run_audio_scenario(sc: AudioScenario) -> AudioResult:
    res = AudioResult(
        id=sc.id, name=sc.name, source=sc.source,
        status="fail", audio_path=sc.audio_path,
        notes=sc.notes,
    )

    if not Path(sc.audio_path).exists():
        res.status = "error"
        res.error_message = f"Audio file not found: {sc.audio_path}"
        return res

    try:
        tts = get_tts()

        t0_tts = time.perf_counter()
        tts_result = tts.run(sc.audio_path)
        res.tts_ms = (time.perf_counter() - t0_tts) * 1000

        if tts_result.error:
            res.status = "error"
            res.error_message = f"TTS/Whisper error: {tts_result.error}"
            return res

        res.transcript = tts_result.transcript or ""

        if not res.transcript.strip():
            res.status = "error"
            res.error_message = "Whisper returned empty transcript"
            return res

        if sc.expected_transcript_hint:
            res.transcript_hint_ok = (
                sc.expected_transcript_hint.lower() in res.transcript.lower()
            )
            if not res.transcript_hint_ok:
                res.incongruences.append(
                    f"TRANSCRIPT_HINT_MISSING: '{sc.expected_transcript_hint}' "
                    f"not found in transcript"
                )

        pipeline = get_pipeline()
        t0_pipe = time.perf_counter()
        report = pipeline.run_transcript(res.transcript)
        res.pipeline_ms = (time.perf_counter() - t0_pipe) * 1000
        res.total_ms = res.tts_ms + res.pipeline_ms

        res.pipeline_status = report.get("status")

        if report.get("status") == "aborted":
            res.status = "fail"
            res.error_message = f"Pipeline aborted: {report.get('abort_reason')}"
            return res

        loc      = report.get("location") or {}
        dispatch = report.get("dispatch") or {}
        protocol = report.get("protocol") or {}

        res.incident_type       = report.get("incident_type")
        res.severity            = report.get("severity")
        res.victims             = report.get("victims", 0)
        res.location_address    = loc.get("address")
        res.location_lat        = loc.get("latitude")
        res.location_lon        = loc.get("longitude")
        res.location_confidence = loc.get("confidence")
        res.location_found      = bool(loc.get("latitude"))
        res.units_dispatched    = dispatch.get("total_units", 0)
        res.routes_count        = len(report.get("routes") or [])
        res.protocol_source     = protocol.get("source")

        if sc.expected_type:
            res.type_match = (res.incident_type == sc.expected_type)
            if not res.type_match:
                res.incongruences.append(
                    f"TYPE_MISMATCH: expected '{sc.expected_type}', got '{res.incident_type}'"
                )

        if sc.expected_severity:
            exp_ord = _SEVERITY_ORDER.get(sc.expected_severity, 0)
            got_ord = _SEVERITY_ORDER.get(res.severity or "", 0)
            res.severity_match = (got_ord >= exp_ord - 1)
            if not res.severity_match:
                res.incongruences.append(
                    f"SEVERITY_TOO_LOW: expected>={sc.expected_severity}, got {res.severity}"
                )

        if sc.expected_has_location and not res.location_found:
            res.incongruences.append("LOCATION_MISSING: expected geocoded location but got none")

        if res.location_found and not _check_location_bounds(res.location_lat, res.location_lon):
            res.incongruences.append(
                f"LOCATION_OUT_OF_BOUNDS: ({res.location_lat:.4f}, {res.location_lon:.4f})"
            )

        sev = res.severity or ""
        if sev in ("critical", "high") and res.units_dispatched == 0:
            res.incongruences.append("NO_UNITS: critical/high severity but zero units dispatched")

        if res.units_dispatched > 0 and res.routes_count == 0:
            res.incongruences.append("NO_ROUTES: units dispatched but no routes calculated")

        dispatch_priority = str(dispatch.get("priority", "")).lower()
        if sev == "critical" and dispatch_priority in ("low", "non-urgent", "routine"):
            res.incongruences.append(
                f"PRIORITY_MISMATCH: severity=critical but dispatch priority='{dispatch_priority}'"
            )

        critical_ok = (
            (res.type_match is None or res.type_match) and
            (res.severity_match is None or res.severity_match) and
            (not sc.expected_has_location or res.location_found) and
            len([i for i in res.incongruences if not i.startswith("TRANSCRIPT_HINT")]) == 0
        )
        res.status = "pass" if critical_ok else (
            "warn" if res.incongruences else "fail"
        )

    except Exception as exc:
        import traceback
        res.status = "error"
        res.error_message = f"{exc}\n{traceback.format_exc()}"

    return res



def _status_icon(s: str) -> str:
    return {"pass": "PASS", "fail": "FAIL", "error": "ERR", "warn": "WARN", "skip": "SKIP"}.get(s, s.upper())


def generate_report(results: list[AudioResult], elapsed_total: float) -> str:
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    md_path   = _OUT_DIR / f"{ts}_audio_report.md"
    json_path = _OUT_DIR / f"{ts}_audio_report.json"

    passed  = sum(1 for r in results if r.status == "pass")
    failed  = sum(1 for r in results if r.status == "fail")
    errors  = sum(1 for r in results if r.status == "error")
    warned  = sum(1 for r in results if r.status == "warn")
    total   = len(results)

    avg_tts  = sum(r.tts_ms for r in results if r.tts_ms > 0) / max(1, sum(1 for r in results if r.tts_ms > 0))
    avg_pipe = sum(r.pipeline_ms for r in results if r.pipeline_ms > 0) / max(1, sum(1 for r in results if r.pipeline_ms > 0))
    avg_tot  = sum(r.total_ms for r in results if r.total_ms > 0) / max(1, sum(1 for r in results if r.total_ms > 0))

    lines = [
        "# IMERS Audio E2E Test Report",
        f"\n**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Total run time:** {elapsed_total:.1f}s",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total scenarios | {total} |",
        f"| PASS | {passed} |",
        f"| FAIL | {failed} |",
        f"| WARN | {warned} |",
        f"| ERROR | {errors} |",
        f"| Pass rate | {100*passed/max(1,total):.1f}% |",
        "",
        "## Timing",
        "",
        f"| Stage | Avg (ms) |",
        f"|-------|----------|",
        f"| Whisper transcription | {avg_tts:.0f} |",
        f"| Pipeline (NLP+Geo+Dispatch) | {avg_pipe:.0f} |",
        f"| Total end-to-end | {avg_tot:.0f} |",
        "",
    ]

    for group, label in [
        ("user_file",   "User-Provided Audio Files"),
        ("recording",   "Existing MP3 Recordings"),
        ("synthesized", "SAPI-Synthesized Spanish Calls"),
    ]:
        group_results = [r for r in results if r.source == group]
        if not group_results:
            continue

        lines += [f"## {label}", ""]
        lines += [
            "| ID | Name | Status | Type | Severity | Location | Whisper ms | Total ms | Issues |",
            "|----|------|--------|------|----------|----------|-----------|----------|--------|",
        ]
        for r in group_results:
            loc_str = r.location_address[:30] if r.location_address else "—"
            issues  = "; ".join(r.incongruences[:2]) or "—"
            lines.append(
                f"| {r.id} | {r.name[:45]} | **{_status_icon(r.status)}** "
                f"| {r.incident_type or '?'} | {r.severity or '?'} "
                f"| {loc_str} | {r.tts_ms:.0f} | {r.total_ms:.0f} | {issues} |"
            )
        lines.append("")

    non_pass = [r for r in results if r.status != "pass"]
    if non_pass:
        lines += ["## Non-passing Details", ""]
        for r in non_pass:
            lines += [
                f"### {r.id} — {r.name}",
                f"- **Status:** {r.status}",
                f"- **Source:** {r.source}",
                f"- **Audio:** `{r.audio_path}`",
            ]
            if r.transcript:
                lines.append(f"- **Transcript:** _{r.transcript[:200]}_")
            if r.error_message:
                lines.append(f"- **Error:** `{r.error_message[:300]}`")
            if r.incongruences:
                lines.append(f"- **Incongruences:**")
                for inc in r.incongruences:
                    lines.append(f"  - {inc}")
            lines.append("")

    user_results = [r for r in results if r.source == "user_file"]
    if user_results:
        lines += ["## Full Transcripts (User-Provided Files)", ""]
        for r in user_results:
            lines += [
                f"### {r.id} — {r.name}",
                f"**Pipeline classification:** {r.incident_type} / {r.severity}",
                f"**Location resolved:** {r.location_address or '—'}",
                f"**Whisper transcript:**",
                f"> {r.transcript or '(empty)'}",
                "",
            ]

    md_content = "\n".join(lines)
    md_path.write_text(md_content, encoding="utf-8")

    json_data = {
        "timestamp": datetime.now().isoformat(),
        "summary": {"total": total, "pass": passed, "fail": failed, "warn": warned, "error": errors},
        "timing_avg_ms": {"tts": round(avg_tts), "pipeline": round(avg_pipe), "total": round(avg_tot)},
        "results": [asdict(r) for r in results],
    }
    json_path.write_text(json.dumps(json_data, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n  Report written to:")
    print(f"    {md_path}")
    print(f"    {json_path}")

    return str(md_path)



def main():
    parser = argparse.ArgumentParser(description="IMERS Audio E2E Test Suite")
    parser.add_argument("--no-synth",      action="store_true", help="Skip SAPI-synthesized scenarios")
    parser.add_argument("--no-recordings", action="store_true", help="Skip existing MP3 recordings")
    parser.add_argument("--quick",         action="store_true", help="User files only (U01, U02)")
    args = parser.parse_args()

    print("=" * 65)
    print("  IMERS Audio E2E Test Suite")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 65)

    scenarios: list[AudioScenario] = list(USER_FILE_SCENARIOS)

    if not args.quick:
        if not args.no_recordings:
            scenarios += RECORDING_SCENARIOS

        if not args.no_synth:
            print("\n[synth] Generating SAPI audio with Microsoft Helena (es-ES)...")
            synth_scenarios: list[AudioScenario] = []
            for s in SYNTH_SCRIPTS:
                sys.stdout.write(f"  Synthesizing {s['id']}... ")
                sys.stdout.flush()
                wav_path = synthesize_wav(s["script"])
                if wav_path:
                    print("OK")
                    synth_scenarios.append(AudioScenario(
                        id=s["id"],
                        name=s["name"],
                        source="synthesized",
                        audio_path=wav_path,
                        expected_type=s.get("expected_type"),
                        expected_severity=s.get("expected_severity"),
                        expected_has_location=s.get("expected_has_location", True),
                        expected_transcript_hint=s.get("expected_transcript_hint"),
                        notes=s.get("notes", ""),
                        script=s["script"],
                    ))
                else:
                    print("FAILED — skipping")
            scenarios += synth_scenarios

    print(f"\n[run] {len(scenarios)} audio scenarios to test\n")

    results: list[AudioResult] = []
    t_start = time.perf_counter()

    for i, sc in enumerate(scenarios, 1):
        print(f"[{i:02d}/{len(scenarios)}] {sc.id} — {sc.name}")
        res = run_audio_scenario(sc)
        results.append(res)

        icon  = _status_icon(res.status)
        ttype = res.incident_type or "?"
        sev   = res.severity or "?"
        loc   = (res.location_address or "no location")[:40]
        tms   = f"{res.tts_ms:.0f}ms TTS / {res.pipeline_ms:.0f}ms pipe"
        print(f"       {icon} | {ttype} / {sev} | {loc}")
        if res.transcript:
            print(f"       Transcript: {res.transcript[:100]}...")
        if res.incongruences:
            for inc in res.incongruences[:3]:
                print(f"       ! {inc}")
        if res.error_message:
            print(f"       ERROR: {res.error_message[:120]}")
        print(f"       {tms} | total {res.total_ms:.0f}ms")
        print()

    elapsed = time.perf_counter() - t_start

    passed = sum(1 for r in results if r.status == "pass")
    total  = len(results)
    print("=" * 65)
    print(f"  RESULTS: {passed}/{total} passed  ({100*passed/max(1,total):.1f}%)")
    print(f"  Total run time: {elapsed:.1f}s")
    print("=" * 65)

    generate_report(results, elapsed)

    for sc in scenarios:
        if sc.source == "synthesized" and Path(sc.audio_path).exists():
            try:
                Path(sc.audio_path).unlink()
            except Exception:
                pass

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
