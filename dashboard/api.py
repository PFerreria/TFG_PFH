"""
FastAPI Backend
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import pathlib
import random
import shutil
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import unquote

from dotenv import load_dotenv
load_dotenv()

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
logger = logging.getLogger(__name__)


app = FastAPI(title="IMERS API", version="2.0.0")

_cors_origins_env = os.getenv("IMERS_CORS_ORIGINS", "")
_cors_origins = (
    [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
    if _cors_origins_env
    else [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:1420",
        "tauri://localhost",
    ]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        """Add no-cache and security headers to all responses."""
        response = await call_next(request)
        path = request.url.path
        if path.startswith(("/css/", "/js/")) or path in ("/", "/eivp"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

app.add_middleware(NoCacheStaticMiddleware)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

MOCK_MODE = os.getenv("IMERS_MOCK_MODE", "1") == "1"


_HERE = pathlib.Path(__file__).parent
_FRONTEND_DIR = _HERE / "frontend"

if not _FRONTEND_DIR.is_dir():
    _FRONTEND_DIR = _HERE

if (_FRONTEND_DIR / "css").is_dir():
    app.mount("/css", StaticFiles(directory=_FRONTEND_DIR / "css"), name="css")

if (_FRONTEND_DIR / "js").is_dir():
    app.mount("/js", StaticFiles(directory=_FRONTEND_DIR / "js"), name="js")

logger.info(f"[API] Serving static assets from {_FRONTEND_DIR}")

@app.get("/")
async def serve_index():
    """Serve the dashboard index page."""
    index = _FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"detail": f"index.html not found in {_FRONTEND_DIR}"}


@app.get("/eivp")
async def serve_eivp():
    """Compatibility route for older frontend entry point links."""
    page = _FRONTEND_DIR / "eivp.html"
    if page.exists():
        return FileResponse(page)
    return await serve_index()


_pipeline         = None
_analysis_runner  = None
_session_manager  = None
_dashboard_cache  = None


_queue_items: list[dict]        = []
_queue_lock:  asyncio.Lock      = None
_queue_event: asyncio.Event     = None
_db_async_lock: asyncio.Lock    = None
_active_session                 = None

_UPLOADS_DIR = pathlib.Path(
    os.environ.get("IMERS_UPLOADS_DIR", "./data/recordings/uploads")
)

_DB_PATH  = pathlib.Path(os.environ.get("IMERS_DB_PATH", "./data/imers.db"))
_db_write_lock = threading.Lock()

ACCEPTED_AUDIO_EXTS = {".wav", ".mp3", ".mp4", ".m4a", ".ogg", ".flac"}
MAX_UPLOAD_BYTES    = 50 * 1024 * 1024
VALID_INCIDENT_TYPES = {
    "all", "traffic_accident", "cardiac_arrest", "fire", "assault",
    "gas_leak", "fall_injury", "stroke", "other_medical",
    "domestic_violence", "robbery", "drowning", "overdose",
    "mental_health_crisis", "flooding", "explosion", "chemical_spill",
    "infrastructure_collapse", "missing_person", "other_police", "other",
}

_ALL_INCIDENT_TYPES: list[str] = [
    "cardiac_arrest", "stroke", "fall_injury", "drowning", "overdose",
    "mental_health_crisis", "other_medical",
    "traffic_accident",
    "fire", "gas_leak", "explosion", "chemical_spill",
    "flooding", "infrastructure_collapse",
    "assault", "domestic_violence", "robbery", "missing_person",
    "other_police", "other",
]

_TYPE_META: dict[str, tuple[str, str, str]] = {
    "cardiac_arrest":          ("PRT-2024-001", "Protocolo PCR — Parada Cardiorrespiratoria",      "SANITARIO"),
    "stroke":                  ("PRT-2024-019", "Protocolo ICTUS — Código Ictus",                  "SANITARIO"),
    "fall_injury":             ("PRT-2024-011", "Protocolo TRA — Traumatismo / Caída",             "SANITARIO"),
    "drowning":                ("PRT-2024-012", "Protocolo AHO — Ahogamiento / Inmersión",         "SANITARIO"),
    "overdose":                ("PRT-2024-013", "Protocolo SOB — Sobredosis / Intoxicación",       "SANITARIO"),
    "mental_health_crisis":    ("PRT-2024-014", "Protocolo SME — Crisis de Salud Mental",          "SANITARIO"),
    "other_medical":           ("PRT-2024-090", "Protocolo MED — Emergencia Médica General",       "SANITARIO"),
    "traffic_accident":        ("PRT-2024-015", "Protocolo ATT — Accidente de Tráfico",            "TRÁFICO"),
    "fire":                    ("PRT-2024-008", "Protocolo INC — Incendio Estructural",             "BOMBEROS"),
    "gas_leak":                ("PRT-2024-022", "Protocolo GAS — Fuga de Gas / GLP",               "RIESGO"),
    "explosion":               ("PRT-2024-021", "Protocolo EXP — Explosión",                       "RIESGO"),
    "chemical_spill":          ("PRT-2024-023", "Protocolo NRBQ — Vertido Químico / NRBQ",        "RIESGO"),
    "flooding":                ("PRT-2024-020", "Protocolo INU — Inundación / Riada",              "RIESGO"),
    "infrastructure_collapse": ("PRT-2024-024", "Protocolo DER — Derrumbe / Colapso",             "RIESGO"),
    "assault":                 ("PRT-2024-031", "Protocolo AGR — Agresión / Violencia",            "RIESGO"),
    "domestic_violence":       ("PRT-2024-032", "Protocolo VG — Violencia de Género",              "RIESGO"),
    "robbery":                 ("PRT-2024-033", "Protocolo ROB — Robo / Hurto",                    "RIESGO"),
    "missing_person":          ("PRT-2024-025", "Protocolo DES — Persona Desaparecida",            "RIESGO"),
    "other_police":            ("PRT-2024-091", "Protocolo POL — Incidente Policial",              "RIESGO"),
    "other":                   ("PRT-2024-099", "Protocolo GEN — Incidente General",               "RIESGO"),
}


class _WSManager:
    def __init__(self):
        self._clients: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        """Accept the WebSocket connection and register the client."""
        await ws.accept()
        self._clients.append(ws)

    def disconnect(self, ws: WebSocket):
        """Remove a client from the active connections list."""
        if ws in self._clients:
            self._clients.remove(ws)

    async def broadcast(self, data: dict):
        """Send *data* to all connected clients, removing any that have disconnected."""
        dead = []
        for ws in self._clients:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

ws_manager = _WSManager()


def _restore_dispatch_state() -> None:
    """Re-mark units from active/en_route incidents as dispatched after a process restart.

    Prevents the unit registry from incorrectly showing all units as available
    immediately after a restart when real incidents are still in progress.
    """
    try:
        from tools.recommend_units import _mark_dispatched
        with _db_conn() as conn:
            rows = conn.execute(
                "SELECT data FROM incidents WHERE status IN ('active','en_route')"
            ).fetchall()
        count = 0
        for row in rows:
            try:
                inc   = json.loads(row["data"])
                units = (inc.get("dispatch") or {}).get("units") or []
                for u in units:
                    if isinstance(u, dict):
                        uid = u.get("id")
                        eta = u.get("eta_minutes", 5)
                        if uid:
                            _mark_dispatched(uid, int(eta))
                            count += 1
            except Exception as _exc:
                logger.debug("[DB] Skipping malformed incident row during dispatch restore: %s", _exc)
        if count:
            logger.info("[DB] Restored %d dispatched unit(s) from %d active incident(s)",
                        count, len(rows))
    except Exception as e:
        logger.warning("[DB] Could not restore dispatch state: %s", e)


async def _wal_checkpoint_loop() -> None:
    """Periodically checkpoint the SQLite WAL file to prevent unbounded growth."""
    while True:
        await asyncio.sleep(300)
        try:
            with _db_conn() as conn:
                conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception as e:
            logger.warning("[DB] WAL checkpoint failed: %s", e)


def _resolve_stale_active_incidents() -> None:
    """Auto-resolve active/en_route incidents older than 4 hours.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    try:
        with _db_write_lock, _db_conn() as conn:
            rows = conn.execute(
                "SELECT id, data FROM incidents WHERE status IN ('active','en_route') "
                "AND timestamp < ?", (cutoff,)
            ).fetchall()
            for row in rows:
                inc = json.loads(row["data"])
                inc["status"] = "resolved"
                conn.execute(
                    "UPDATE incidents SET status='resolved', data=? WHERE id=?",
                    (json.dumps(inc), row["id"]),
                )
        if rows:
            logger.info("[DB] Auto-resolved %d stale active incident(s) (>4 h old).", len(rows))

        with _db_conn() as conn:
            active_count = conn.execute(
                "SELECT COUNT(*) FROM incidents WHERE status IN ('active','en_route')"
            ).fetchone()[0]

        if active_count < 3:
            _reseed_active_incidents()
    except Exception as exc:
        logger.warning("[DB] Could not resolve stale incidents: %s", exc)


def _reseed_active_incidents() -> None:
    """Seed fresh active incidents so the demo always shows live data."""
    try:
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location(
            "seed_incidents",
            pathlib.Path(__file__).resolve().parent.parent / "scripts" / "seed_incidents.py",
        )
        if spec and spec.loader:
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            import random as _rand
            rng = _rand.Random(42)
            with _db_conn() as conn:
                max_row = conn.execute(
                    "SELECT MAX(CAST(SUBSTR(id,5) AS INTEGER)) FROM incidents"
                ).fetchone()[0] or 5000
            with _db_conn() as conn:
                mod.seed_active(conn, rng, start_idx=int(max_row) + 1)
            logger.info("[DB] Re-seeded active incidents.")
    except Exception as exc:
        logger.warning("[DB] Could not re-seed active incidents: %s", exc)


@app.on_event("startup")
async def startup():
    global _pipeline, _analysis_runner, _session_manager, _dashboard_cache
    global _queue_lock, _queue_event, _db_async_lock

    _queue_lock    = asyncio.Lock()
    _queue_event   = asyncio.Event()
    _db_async_lock = asyncio.Lock()
    _UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    _init_db()
    _resolve_stale_active_incidents()
    _restore_dispatch_state()
    asyncio.create_task(_wal_checkpoint_loop())

    if MOCK_MODE:
        logger.warning("[API] MOCK_MODE=1 — real pipeline disabled. Set IMERS_MOCK_MODE=0 to use Qwen.")
        _pipeline         = _MockPipeline()
        _session_manager = _make_session_manager(_pipeline)
        _dashboard_cache = _make_mock_dashboard()
        asyncio.create_task(_queue_processor())
        return

    try:
        from pipeline.graph  import IMERSPipeline
        try:
            from analysis_cycle.analysis_runner import AnalysisRunner
        except ImportError:
            from analysis_runner import AnalysisRunner
        try:
            from realtime.call_session import CallSessionManager
        except ImportError:
            from call_session import CallSessionManager

        hf_token  = os.getenv("HF_TOKEN")
        _pipeline = IMERSPipeline(hf_token=hf_token)

        _analysis_runner = AnalysisRunner(hf_token=hf_token)
        _analysis_runner.attach_to_pipeline(_pipeline)
        _analysis_runner.start_scheduler(interval_minutes=60)

        _session_manager = _make_session_manager(_pipeline)
        logger.info("[API] Real pipeline ready")

    except Exception as e:
        logger.error(f"[API] Startup failed ({e}) — falling back to mock mode")
        _pipeline         = _MockPipeline()
        _session_manager = _make_session_manager(_pipeline)
        _dashboard_cache = _make_mock_dashboard()

    asyncio.create_task(_queue_processor())


@app.on_event("shutdown")
async def shutdown():
    if _analysis_runner:
        _analysis_runner.stop_scheduler()


def _make_session_manager(pipeline):
    """Create a CallSessionManager that persists each report to SQLite and broadcasts it over WebSocket."""
    try:
        try:
            from realtime.call_session import CallSessionManager
        except ImportError:
            from call_session import CallSessionManager
    except ImportError:
        return None

    loop = asyncio.get_running_loop()

    def _on_report(session_id, report_type, report):
        _upsert_db_incident(report)
        asyncio.run_coroutine_threadsafe(
            ws_manager.broadcast({
                "type": "incident_report", "report_type": report_type,
                "session_id": session_id, "data": report,
            }),
            loop
        )

    return CallSessionManager(pipeline=pipeline, on_report=_on_report)


class _MockPipeline:
    _PROTOCOLS: dict = {
        "cardiac_arrest": {
            "text": "Parada cardiorrespiratoria. Activar código parada inmediatamente.",
            "key_actions": [
                "Enviar SVA + SVB de forma inmediata.",
                "Confirmar ausencia de pulso y respiración.",
                "Iniciar RCP: 30 compresiones + 2 ventilaciones.",
                "Desfibrilación si DEA disponible en < 3 min.",
                "Primera unidad en escena < 8 min.",
                "Notificar hospital receptor código 90.",
            ],
            "escalation": "Si > 3 víctimas: activar PEM. Solicitar refuerzo 2 SVB adicionales.",
            "source": "EPES-112-PCR",
        },
        "traffic_accident": {
            "text": "Accidente de tráfico con víctimas. Asegurar escena y triaje START.",
            "key_actions": [
                "Señalización a 100 m en ambas direcciones.",
                "Evaluar vehículos y número de víctimas.",
                "Triaje START: Rojo / Amarillo / Verde / Negro.",
                "Enviar SVA + SVB + unidad policial.",
                "Si atrapados: bomberos con GREA.",
                "No mover con sospecha de lesión medular.",
                "PMA si > 3 víctimas.",
            ],
            "escalation": "Si > 5 víctimas: activar PEM y coordinador sanitario.",
            "source": "EPES-112-ATT",
        },
        "fire": {
            "text": "Incendio estructural. Confirmar dirección y plantas afectadas.",
            "key_actions": [
                "Confirmar dirección exacta y número de plantas afectadas.",
                "Enviar 2 unidades de bomberos + SVA + unidad policial.",
                "Cortar suministro de gas y electricidad.",
                "Evacuación y punto de encuentro a 200 m.",
                "Si atrapados: activar GREA.",
                "Perímetro de seguridad de 100 m.",
            ],
            "escalation": "Si > 3 plantas o explosiones: enviar 2 unidades adicionales.",
            "source": "CBAS-INC",
        },
        "assault": {
            "text": "Agresión activa. Esperar autorización policial antes de entrada sanitaria.",
            "key_actions": [
                "Esperar autorización policial antes de acceder.",
                "Evaluar víctimas y lesiones visibles.",
                "Separar testigos y agresor.",
                "Atención sanitaria con seguridad garantizada.",
                "Preservar indicios judiciales.",
            ],
            "escalation": "Si hay armas: solicitar GEOS. Mantener perímetro.",
            "source": "CECOP-AGR",
        },
        "gas_leak": {
            "text": "Fuga de gas. No activar interruptores eléctricos. Evacuar inmediatamente.",
            "key_actions": [
                "NO activar interruptores eléctricos.",
                "Evacuar inmediatamente el edificio.",
                "Ventilar: abrir puertas y ventanas.",
                "Cortar suministro desde exterior.",
                "Solicitar técnico de la compañía distribuidora.",
                "Inspeccionar con detector antes de reentrar.",
            ],
            "escalation": "Con víctimas por inhalación: activar protocolo de incendio simultáneamente.",
            "source": "BOM-GAS",
        },
        "stroke": {
            "text": "Ictus cerebrovascular. Protocolo código ictus. ETA < 8 min al hospital.",
            "key_actions": [
                "Activar código ictus al hospital receptor.",
                "Enviar SVA de forma urgente.",
                "Evaluar signos FAST: cara, brazo, habla, tiempo.",
                "Mantener constantes y vía aérea permeable.",
                "Trasladar al hospital con unidad de ictus más cercana.",
            ],
            "escalation": "Si deterioro neurológico rápido: avisar neurocirugía de guardia.",
            "source": "EPES-112-ICTUS",
        },
        "fall_injury": {
            "text": "Caída con traumatismo. Inmovilización cervical preventiva si procede.",
            "key_actions": [
                "Evaluar nivel de consciencia y movilidad.",
                "Inmovilización cervical si hay sospecha de lesión medular.",
                "SVB para valoración y traslado.",
                "Registrar mecanismo de caída y altura.",
            ],
            "escalation": "Si hay pérdida de consciencia o fractura abierta: enviar SVA.",
            "source": "EPES-112-TRAUMA",
        },
        "other_medical": {
            "text": "Emergencia médica general. SVB para valoración inicial.",
            "key_actions": [
                "Evaluar estado de consciencia y constantes vitales.",
                "Enviar SVB para valoración.",
                "Si deterioro rápido o sospecha de patología grave: enviar SVA.",
                "Mantener informado al operador sobre la evolución.",
            ],
            "escalation": "Si deterioro o diagnóstico grave: refuerzo SVA.",
            "source": "EPES-112-MED",
        },
    }
    _DEFAULT_PROTOCOL = {
        "text": "Emergencia general. Evaluar la situación y enviar los recursos adecuados.",
        "key_actions": [
            "Confirmar dirección exacta del incidente.",
            "Evaluar número de víctimas.",
            "Enviar recursos según protocolo de emergencias.",
        ],
        "escalation": "Escalar según evaluación en escena.",
        "source": "EPES-112-GEN",
    }

    def run_transcript(self, transcript: str, incident_id: str = None, is_preliminary: bool = False, **kwargs) -> dict:
        import json as _json
        h        = int(hashlib.md5(transcript.encode(), usedforsecurity=False).hexdigest(), 16)
        types    = ["traffic_accident","cardiac_arrest","fire","assault","gas_leak","fall_injury","stroke","other_medical"]
        sevs     = ["critical","high","medium","low"]
        itype    = types[h % len(types)]
        sev      = sevs[h % 4]
        victims  = h % 4
        _loc_idx = h % len(_HOTSPOT_COORDS)
        lat, lon = _HOTSPOT_COORDS[_loc_idx]
        address  = _HOTSPOT_ADDRESSES[_loc_idx]
        time.sleep(0.2)

        dispatch_data: dict = {}
        try:
            from tools.recommend_units import recommend_units as _ru, _preview_ctx as _ru_ctx
            _ru_ctx.active = True
            try:
                dispatch_data = _json.loads(_ru(itype, sev, "Sevilla", victims, lat, lon))
            finally:
                _ru_ctx.active = False
            units         = dispatch_data.get("dispatched", [])
            first_arrival = dispatch_data.get("estimated_first_arrival") or (
                min(u["eta_minutes"] for u in units) if units else 5
            )
        except Exception:
            units         = []
            first_arrival = 5

        protocol = self._PROTOCOLS.get(itype, self._DEFAULT_PROTOCOL)

        return {
            "incident_id":   incident_id or f"INC-{uuid.uuid4().hex[:8].upper()}",
            "status":        "processed",
            "incident_type": itype,
            "severity":      sev,
            "victims":       victims,
            "location": {
                "address":    address,
                "latitude":   lat,
                "longitude":  lon,
                "confidence": "high",
            },
            "dispatch": {
                "units":                  units,
                "total_units":            len(units),
                "first_arrival_minutes":  first_arrival,
                "warnings":               dispatch_data.get("warnings", []),
            },
            "protocol": protocol,
            "transcript_preview": transcript,
        }


_TYPES   = [
    "traffic_accident", "cardiac_arrest", "stroke", "drowning",
    "fall_injury", "overdose", "mental_health_crisis", "other_medical",
    "assault", "domestic_violence", "robbery", "missing_person", "other_police",
    "fire", "gas_leak", "explosion", "chemical_spill",
    "flooding", "infrastructure_collapse", "other",
]
_SEVS    = ["critical","high","medium","low"]
_SEV_W   = [0.08, 0.22, 0.45, 0.25]
_CENTRE  = (37.3886, -5.9823)
_HOTSPOT_COORDS = [
    (37.3886,-5.9823),
    (37.3818,-5.9965),
    (37.3849,-5.9714),
    (37.4023,-5.9856),
    (37.3736,-5.9913),
    (37.4068,-5.9628),
    (37.3783,-5.9432),
    (37.3572,-5.9836),
]
_HOTSPOT_ADDRESSES = [
    "Calle Sierpes 20, Sevilla",
    "Calle Betis 24, Triana, Sevilla",
    "Av. de la Cruz del Campo 18, Sevilla",
    "Ronda de Capuchinos 15, Sevilla",
    "Av. de los Reyes Católicos 30, Sevilla",
    "Calle Felipe II 8, Sevilla",
    "Av. de Jerez 45, Sevilla",
    "Av. de la Palmera 10, Heliópolis, Sevilla",
]

_SEVILLA_LAT_MIN, _SEVILLA_LAT_MAX =  37.25,  37.52
_SEVILLA_LON_MIN, _SEVILLA_LON_MAX = -6.12, -5.82

_MOCK_DISPATCH_SETS: dict = {
    "traffic_accident": [
        [{"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":7},  {"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4},  {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}],
        [{"id":"SVB-04","type":"ambulance_svb","subtype":"SVB","eta_minutes":6},  {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5}],
        [{"id":"SVA-05","type":"ambulance_sva","subtype":"SVA","eta_minutes":9},  {"id":"SVB-02","type":"ambulance_svb","subtype":"SVB","eta_minutes":5},   {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}, {"id":"ZETA-05","type":"police","subtype":"ZETA","eta_minutes":8}],
        [{"id":"SVB-07","type":"ambulance_svb","subtype":"SVB","eta_minutes":5},  {"id":"BUP-05","type":"fire","subtype":"BUP","eta_minutes":6},            {"id":"ZETA-07","type":"police","subtype":"ZETA","eta_minutes":4}],
    ],
    "cardiac_arrest": [
        [{"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":7},  {"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4}],
        [{"id":"SVA-01","type":"ambulance_sva","subtype":"SVA","eta_minutes":8},  {"id":"SVB-04","type":"ambulance_svb","subtype":"SVB","eta_minutes":6}],
        [{"id":"SVA-05","type":"ambulance_sva","subtype":"SVA","eta_minutes":5},  {"id":"SVB-02","type":"ambulance_svb","subtype":"SVB","eta_minutes":4}],
    ],
    "stroke": [
        [{"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":7},  {"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4}],
        [{"id":"SVA-05","type":"ambulance_sva","subtype":"SVA","eta_minutes":9}],
        [{"id":"SVA-07","type":"ambulance_sva","subtype":"SVA","eta_minutes":6}],
    ],
    "drowning": [
        [{"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":8},  {"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":5},   {"id":"FSV-02","type":"rescue","subtype":"FSV","eta_minutes":9}],
        [{"id":"VIR-01","type":"ambulance_sva","subtype":"VIR","eta_minutes":6},  {"id":"FSV-02","type":"rescue","subtype":"FSV","eta_minutes":10}],
    ],
    "fall_injury": [
        [{"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4}],
        [{"id":"SVB-04","type":"ambulance_svb","subtype":"SVB","eta_minutes":6}],
        [{"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":7},  {"id":"SVB-02","type":"ambulance_svb","subtype":"SVB","eta_minutes":5}],
        [{"id":"SVB-07","type":"ambulance_svb","subtype":"SVB","eta_minutes":5}],
    ],
    "overdose": [
        [{"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":7},  {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}],
        [{"id":"SVB-04","type":"ambulance_svb","subtype":"SVB","eta_minutes":6},  {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5}],
    ],
    "mental_health_crisis": [
        [{"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4},  {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}],
        [{"id":"SVA-01","type":"ambulance_sva","subtype":"SVA","eta_minutes":8},  {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5}],
        [{"id":"ZETA-07","type":"police","subtype":"ZETA","eta_minutes":4}],
    ],
    "other_medical": [
        [{"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4}],
        [{"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":7},  {"id":"SVB-04","type":"ambulance_svb","subtype":"SVB","eta_minutes":6}],
        [{"id":"SVB-02","type":"ambulance_svb","subtype":"SVB","eta_minutes":5}],
        [{"id":"SVB-07","type":"ambulance_svb","subtype":"SVB","eta_minutes":6}],
    ],
    "assault": [
        [{"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3},       {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5},         {"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4}],
        [{"id":"ZETA-06","type":"police","subtype":"ZETA","eta_minutes":8}],
        [{"id":"ZETA-07","type":"police","subtype":"ZETA","eta_minutes":4},       {"id":"SVB-02","type":"ambulance_svb","subtype":"SVB","eta_minutes":5}],
        [{"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3},       {"id":"ZETA-07","type":"police","subtype":"ZETA","eta_minutes":4}],
    ],
    "domestic_violence": [
        [{"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3},       {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5}],
        [{"id":"ZETA-07","type":"police","subtype":"ZETA","eta_minutes":4},       {"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4}],
        [{"id":"ZETA-06","type":"police","subtype":"ZETA","eta_minutes":8},       {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}],
    ],
    "robbery": [
        [{"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3},       {"id":"ZETA-07","type":"police","subtype":"ZETA","eta_minutes":4}],
        [{"id":"MOTO-01","type":"police","subtype":"MOTO","eta_minutes":2},       {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5}],
        [{"id":"ZETA-06","type":"police","subtype":"ZETA","eta_minutes":8},       {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5}],
    ],
    "missing_person": [
        [{"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}],
        [{"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5},       {"id":"ZETA-07","type":"police","subtype":"ZETA","eta_minutes":4}],
    ],
    "other_police": [
        [{"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5}],
        [{"id":"ZETA-07","type":"police","subtype":"ZETA","eta_minutes":4}],
    ],
    "fire": [
        [{"id":"BUP-01","type":"fire","subtype":"BUP","eta_minutes":6},           {"id":"BUP-02","type":"fire","subtype":"BUP","eta_minutes":8},             {"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4}, {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}],
        [{"id":"BUL-01","type":"fire","subtype":"BUL","eta_minutes":5},           {"id":"UMES-01","type":"fire","subtype":"UMES","eta_minutes":6},           {"id":"SVB-04","type":"ambulance_svb","subtype":"SVB","eta_minutes":6}],
        [{"id":"BUP-02","type":"fire","subtype":"BUP","eta_minutes":8},           {"id":"BUP-05","type":"fire","subtype":"BUP","eta_minutes":10},            {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5}, {"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":7}],
    ],
    "gas_leak": [
        [{"id":"BUP-01","type":"fire","subtype":"BUP","eta_minutes":6},           {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3},         {"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4}],
        [{"id":"BUP-02","type":"fire","subtype":"BUP","eta_minutes":8},           {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5}],
        [{"id":"BUL-01","type":"fire","subtype":"BUL","eta_minutes":5},           {"id":"BUP-02","type":"fire","subtype":"BUP","eta_minutes":8}],
    ],
    "explosion": [
        [{"id":"BUP-01","type":"fire","subtype":"BUP","eta_minutes":6},           {"id":"BUP-02","type":"fire","subtype":"BUP","eta_minutes":8},             {"id":"BUP-05","type":"fire","subtype":"BUP","eta_minutes":10},
         {"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":7},  {"id":"SVA-01","type":"ambulance_sva","subtype":"SVA","eta_minutes":8},    {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}, {"id":"FSV-01","type":"rescue","subtype":"FSV","eta_minutes":9}],
        [{"id":"BUP-01","type":"fire","subtype":"BUP","eta_minutes":6},           {"id":"BUP-05","type":"fire","subtype":"BUP","eta_minutes":10},
         {"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":7},  {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5}],
    ],
    "chemical_spill": [
        [{"id":"BUP-01","type":"fire","subtype":"BUP","eta_minutes":6},           {"id":"BUP-02","type":"fire","subtype":"BUP","eta_minutes":8},
         {"id":"FSV-01","type":"rescue","subtype":"FSV","eta_minutes":9},         {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}],
        [{"id":"BUP-02","type":"fire","subtype":"BUP","eta_minutes":8},           {"id":"FSV-01","type":"rescue","subtype":"FSV","eta_minutes":9},           {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5}],
    ],
    "flooding": [
        [{"id":"BUP-01","type":"fire","subtype":"BUP","eta_minutes":6},           {"id":"BUP-02","type":"fire","subtype":"BUP","eta_minutes":8},             {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}],
        [{"id":"BUL-02","type":"fire","subtype":"BUL","eta_minutes":7},           {"id":"FSV-02","type":"rescue","subtype":"FSV","eta_minutes":9}],
    ],
    "infrastructure_collapse": [
        [{"id":"BUP-01","type":"fire","subtype":"BUP","eta_minutes":6},           {"id":"BUP-02","type":"fire","subtype":"BUP","eta_minutes":8},             {"id":"BUP-05","type":"fire","subtype":"BUP","eta_minutes":10},
         {"id":"FSV-01","type":"rescue","subtype":"FSV","eta_minutes":9},         {"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":7},    {"id":"SVA-01","type":"ambulance_sva","subtype":"SVA","eta_minutes":8}, {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}],
    ],
    "other": [
        [{"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}],
        [{"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4}],
        [{"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5},       {"id":"SVB-04","type":"ambulance_svb","subtype":"SVB","eta_minutes":6}],
    ],
}
_DEFAULT_DISPATCH_SET = [[{"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4}, {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}]]


_FALLBACK_ADDRESSES = [
    "Avenida de la Constitución 1, Sevilla",
    "Calle Sierpes 45, Sevilla",
    "Calle San Fernando 14, Sevilla",
    "Plaza Nueva 4, Sevilla",
    "Calle Tetuán 21, Sevilla",
    "Calle Betis 23, Triana, Sevilla",
    "Calle San Jacinto 80, Triana, Sevilla",
    "Avenida de Kansas City 4, Sevilla",
    "Calle Luis Montoto 3, Sevilla",
    "Avenida Eduardo Dato 22, Sevilla",
    "Calle Feria 43, Sevilla",
    "Calle Resolana 12, Sevilla",
    "Calle Torneo 15, Sevilla",
    "Calle Asunción 41, Sevilla",
    "Avenida de la Palmera 45, Sevilla",
    "Calle Blas Infante 7, Sevilla",
    "Avenida Reina Mercedes 12, Sevilla",
    "Avenida de Portugal 20, Sevilla",
    "Calle Torreblanca 5, Sevilla",
    "Avenida de la Paz 4, Sevilla",
]


def _clamp_to_sevilla(lat: float, lon: float):
    """Ensure a coordinate pair falls within Sevilla's operational bounds."""
    return (
        max(_SEVILLA_LAT_MIN, min(_SEVILLA_LAT_MAX, lat)),
        max(_SEVILLA_LON_MIN, min(_SEVILLA_LON_MAX, lon)),
    )

def _rand_incident(seed: int, hours_back: int = 120) -> dict:
    """Generate a single deterministic mock incident using *seed* as the random source."""
    rng  = random.Random(seed * 7 + 13)
    if rng.random() < 0.65:
        base = _HOTSPOT_COORDS[seed % 4]
        lat  = base[0] + rng.gauss(0, 0.002)
        lon  = base[1] + rng.gauss(0, 0.003)
    else:
        lat  = _CENTRE[0] + rng.gauss(0, 0.015)
        lon  = _CENTRE[1] + rng.gauss(0, 0.020)
    lat, lon = _clamp_to_sevilla(lat, lon)
    itype  = rng.choice(_TYPES)
    status = rng.choice(["active", "en_route", "resolved", "resolved", "resolved"])
    if status in ("active", "en_route"):
        ts = datetime.now(timezone.utc) - timedelta(minutes=rng.uniform(2, 239))
    else:
        ts = (datetime.now(timezone.utc)
              - timedelta(hours=rng.uniform(0, hours_back), minutes=rng.uniform(0, 60)))
    unit_sets = _MOCK_DISPATCH_SETS.get(itype, _DEFAULT_DISPATCH_SET)
    units = rng.choice(unit_sets)
    first_arrival = min(u["eta_minutes"] for u in units)
    return {
        "id":               f"INC-{seed:05d}",
        "incident_type":    itype,
        "severity":         rng.choices(_SEVS, weights=_SEV_W)[0],
        "latitude":         round(lat, 5),
        "longitude":        round(lon, 5),
        "address":          rng.choice(_FALLBACK_ADDRESSES),
        "timestamp":        ts.isoformat(),
        "status":           status,
        "dispatch": {
            "units":                 units,
            "total_units":           len(units),
            "first_arrival_minutes": first_arrival,
        },
        "units_dispatched": len(units),
        "response_time_min":round(max(2.0, rng.gauss(6.5, 2.0)), 1),
        "confidence_score": rng.randint(75, 99),
    }


def _db_conn() -> sqlite3.Connection:
    """Open a new SQLite connection with WAL mode and Row factory."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db() -> None:
    """Create schema and seed mock incidents on first run."""
    with _db_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                id            TEXT PRIMARY KEY,
                status        TEXT NOT NULL DEFAULT 'active',
                timestamp     TEXT NOT NULL,
                incident_type TEXT,
                severity      TEXT,
                data          TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status    ON incidents (status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON incidents (timestamp DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_type      ON incidents (incident_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_severity  ON incidents (severity)")

    with _db_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    if count == 0 and MOCK_MODE:
        _seed_mock_incidents()


def _seed_mock_incidents() -> None:
    """Seed the database with comprehensive mock incidents covering all 20 types.

    Delegates to scripts/seed_incidents.py when available (the authoritative
    seeder that produces 350+ historical + 12 active incidents across all
    neighbourhoods and incident categories).  Falls back to a lightweight
    in-process seeder if the script cannot be imported.
    """
    try:
        import importlib.util, sys as _sys
        spec = importlib.util.spec_from_file_location(
            "seed_incidents",
            pathlib.Path(__file__).resolve().parent.parent / "scripts" / "seed_incidents.py",
        )
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            import random as _rand
            rng = _rand.Random(42)
            with _db_conn() as conn:
                mod._create_schema(conn, reset=False)
                mod.seed_historical(conn, rng, start_idx=1)
                mod.seed_active(conn, rng, start_idx=4001)
            logger.info("[DB] Comprehensive seeder: 362 incidents loaded.")
            return
    except Exception as _e:
        logger.warning(f"[DB] Comprehensive seeder unavailable ({_e}), using fallback.")

    incidents = [_rand_incident(i, hours_back=2160) for i in range(150)]
    with _db_write_lock, _db_conn() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO incidents (id, status, timestamp, incident_type, severity, data) "
            "VALUES (?,?,?,?,?,?)",
            [
                (
                    inc["id"],
                    inc.get("status", "resolved"),
                    inc.get("timestamp", datetime.now(timezone.utc).isoformat()),
                    inc.get("incident_type", "other"),
                    inc.get("severity", "medium"),
                    json.dumps(inc),
                )
                for inc in incidents
            ],
        )
    logger.info("[DB] Fallback seeder: 150 incidents loaded.")


def _db_get_incidents(active_only: bool = False, history_only: bool = False, days: int = 0) -> list[dict]:
    """Read incidents from SQLite, returning parsed dicts sorted by timestamp desc.

    Args:
        days: When history_only=True and days > 0, restrict to the last *days* days.
    """
    with _db_conn() as conn:
        if active_only:
            rows = conn.execute(
                "SELECT data FROM incidents WHERE status IN ('active','en_route','processed') "
                "ORDER BY timestamp DESC LIMIT 200"
            ).fetchall()
        elif history_only:
            if days > 0:
                rows = conn.execute(
                    "SELECT data FROM incidents "
                    "WHERE status NOT IN ('active','en_route','processed') "
                    "AND timestamp >= datetime('now', ? || ' days') "
                    "ORDER BY timestamp DESC LIMIT 500",
                    (f"-{days}",),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT data FROM incidents "
                    "WHERE status NOT IN ('active','en_route','processed') "
                    "ORDER BY timestamp DESC LIMIT 500"
                ).fetchall()
        else:
            rows = conn.execute(
                "SELECT data FROM incidents ORDER BY timestamp DESC LIMIT 500"
            ).fetchall()
    return [json.loads(r["data"]) for r in rows]


def _upsert_db_incident(report: dict) -> None:
    """Insert or replace an incident record in SQLite, normalising fields from the pipeline report."""
    if not report:
        return
    inc_id = report.get("incident_id") or report.get("id")
    if not inc_id:
        return

    location = report.get("location") or {}
    dispatch = report.get("dispatch") or {}

    status = report.get("status") or "active"
    if status == "processed":
        status = "active"

    raw_lat = location.get("latitude") or report.get("latitude")
    raw_lon = location.get("longitude") or report.get("longitude")

    existing_row = None
    if raw_lat is None or raw_lon is None or status == "active":
        with _db_conn() as conn:
            existing_row = conn.execute(
                "SELECT data FROM incidents WHERE id=?", (inc_id,)
            ).fetchone()

    if (raw_lat is None or raw_lon is None):
        if existing_row:
            old = json.loads(existing_row["data"])
            raw_lat = raw_lat if raw_lat is not None else old.get("latitude")
            raw_lon = raw_lon if raw_lon is not None else old.get("longitude")

    if status == "active" and existing_row:
        existing_status = json.loads(existing_row["data"]).get("status", "active")
        if existing_status in ("en_route", "resolved"):
            status = existing_status

    raw_lat = raw_lat or 37.3886
    raw_lon = raw_lon or -5.9823
    clamped_lat, clamped_lon = _clamp_to_sevilla(float(raw_lat), float(raw_lon))

    dispatch_units = dispatch.get("units") or report.get("units") or []
    total_units    = dispatch.get("total_units") or len(dispatch_units) or report.get("units_dispatched") or 0

    data = {
        "id":               inc_id,
        "incident_id":      inc_id,
        "incident_type":    report.get("incident_type") or "other",
        "severity":         report.get("severity") or "medium",
        "latitude":         clamped_lat,
        "longitude":        clamped_lon,
        "address":          location.get("address") or report.get("address") or "Av. de la Constitución, Sevilla",
        "timestamp":        report.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        "status":           status,
        "dispatch": {
            "units":                 dispatch_units,
            "total_units":           total_units,
            "first_arrival_minutes": dispatch.get("first_arrival_minutes") or dispatch.get("estimated_first_arrival") or 5,
            "decision":              dispatch.get("decision") or "",
        },
        "units_dispatched": total_units,
        "response_time_min":(dispatch.get("first_arrival_minutes")
                            or dispatch.get("estimated_first_arrival")
                            or report.get("response_time_min")
                            or 5.0),
        "confidence_score": report.get("confidence_score") or 90,
        "victims":          report.get("victims") or 0,
        "decision":         dispatch.get("decision") or report.get("decision") or "",
        "note":             report.get("agent_note") or report.get("note") or "",
        "protocol":         report.get("protocol") or "",
    }

    with _db_write_lock, _db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO incidents "
            "(id, status, timestamp, incident_type, severity, data) VALUES (?,?,?,?,?,?)",
            (inc_id, data["status"], data["timestamp"],
             data["incident_type"], data["severity"], json.dumps(data)),
        )

def _make_mock_dashboard() -> dict:
    """Build the static mock dashboard payload used when the analysis runner is unavailable.

    Hotspot counts, dominant types and risk scores are computed from the actual
    incidents stored in the DB so the dashboard always reflects the seeded data.
    Falls back to hardcoded values if the DB is empty.
    """
    now = datetime.now(timezone.utc)

    _AREA_DEFS = [
        ("Centro Histórico",        37.3886, -5.9823),
        ("Triana",                  37.3818, -5.9965),
        ("Nervión",                 37.3849, -5.9714),
        ("Macarena",                37.4023, -5.9856),
        ("Los Remedios",            37.3736, -5.9913),
        ("San Pablo - Santa Justa", 37.4068, -5.9628),
        ("Este - Alcosa",           37.3783, -5.9432),
        ("Sur - Heliópolis",        37.3572, -5.9836),
        ("Palmera - Bellavista",    37.3627, -5.9757),
        ("Torreblanca",             37.3718, -5.8962),
        ("Casco Norte",             37.4213, -5.9782),
        ("Polígono Sur",            37.3480, -5.9810),
    ]
    hotspots = []
    try:
        with _db_conn() as _hconn:
            for area_name, alat, alon in _AREA_DEFS:
                total_row = _hconn.execute(
                    "SELECT COUNT(*) FROM incidents WHERE json_extract(data,'$.area')=?",
                    (area_name,)
                ).fetchone()
                total_count = total_row[0] if total_row else 0

                dom_row = _hconn.execute(
                    "SELECT COUNT(*) as n, incident_type FROM incidents "
                    "WHERE json_extract(data,'$.area')=? "
                    "GROUP BY incident_type ORDER BY n DESC LIMIT 1",
                    (area_name,)
                ).fetchone()
                dom_count = dom_row[0] if dom_row else 0
                dominant  = dom_row[1] if (dom_row and dom_row[1]) else "traffic_accident"

                if dom_count < 2:
                    continue

                risk = min(99, max(10, int(total_count * 2.5)))
                hotspots.append({
                    "area_label":     area_name,
                    "incident_count": total_count,
                    "dominant_count": dom_count,
                    "risk_score":     risk,
                    "dominant_type":  dominant.replace("_", " "),
                    "centre_lat":     alat,
                    "centre_lon":     alon,
                })
    except Exception:
        hotspots = [
            {"area_label":"Centro Histórico","incident_count":73,"risk_score":92,
             "dominant_type":"traffic accident","centre_lat":37.3886,"centre_lon":-5.9823},
            {"area_label":"Triana","incident_count":52,"risk_score":76,
             "dominant_type":"traffic accident","centre_lat":37.3818,"centre_lon":-5.9965},
            {"area_label":"Nervión","incident_count":46,"risk_score":68,
             "dominant_type":"assault","centre_lat":37.3849,"centre_lon":-5.9714},
            {"area_label":"Macarena","incident_count":41,"risk_score":62,
             "dominant_type":"fire","centre_lat":37.4023,"centre_lon":-5.9856},
        ]
    hotspots.sort(key=lambda h: h["risk_score"], reverse=True)

    forecast = []
    day_names = ["LUN","MAR","MIE","JUE","VIE","SAB","DOM"]
    _dow_avg: dict[int, float] = {}
    try:
        with _db_conn() as _fconn:
            rows = _fconn.execute("""
                SELECT CAST(strftime('%w', substr(timestamp,1,19)) AS INTEGER) AS dow,
                       COUNT(*) AS total,
                       COUNT(DISTINCT substr(timestamp,1,10)) AS days
                FROM incidents
                WHERE substr(timestamp,1,10) < date('now')
                  AND substr(timestamp,1,10) >= date('now','-90 days')
                GROUP BY dow
            """).fetchall()
            for dow_int, total, days in rows:
                _dow_avg[dow_int] = total / max(1, days)
    except Exception as _exc:
        logger.debug("[Dashboard] DOW avg calculation failed, using baseline: %s", _exc)

    for i in range(7):
        d         = now + timedelta(days=i)
        dow_label = day_names[d.weekday()]
        sqlite_dow = (d.weekday() + 1) % 7
        if _dow_avg:
            v = max(1, round(_dow_avg.get(sqlite_dow, 15.0)))
        else:
            v = 18 if d.weekday() < 5 else 13
        forecast.append({"date": d.date().isoformat(), "day_of_week": dow_label, "predicted_incidents": v})

    hourly_distribution = [
        {"hour": h, "count": [6,4,3,2,3,5,8,12,15,16,14,13,12,14,15,14,16,18,17,16,14,12,10,8][h]}
        for h in range(24)
    ]
    try:
        from tools.recommend_units import _UNIT_REGISTRY, _is_dispatched as _ru_is_dispatched
        _total_units    = len(_UNIT_REGISTRY)
        _avail_units    = sum(
            1 for u in _UNIT_REGISTRY
            if u.status == "available" and not _ru_is_dispatched(u.id)
        )
    except Exception:
        _total_units = 0
        _avail_units = 0

    kpis_overall = {"mean": 6.4, "median": 5.8, "p90": 10.2, "count": 350, "pct_within_8min": 73}
    kpis_by_sev  = {
        "critical": {"count": 76,  "mean": 4.8, "target": 6,  "pct_meeting_target": 91},
        "high":     {"count": 129, "mean": 6.1, "target": 8,  "pct_meeting_target": 78},
        "medium":   {"count": 121, "mean": 8.4, "target": 10, "pct_meeting_target": 81},
        "low":      {"count": 36,  "mean": 11.7,"target": 15, "pct_meeting_target": 94},
    }
    try:
        with _db_conn() as _kconn:
            rows = _kconn.execute(
                "SELECT json_extract(data,'$.response_time_min') as rt, "
                "       json_extract(data,'$.severity') as sev "
                "FROM incidents WHERE status NOT IN ('active','en_route') "
                "AND json_extract(data,'$.response_time_min') IS NOT NULL"
            ).fetchall()
            if rows:
                import statistics as _stats
                times = [r[0] for r in rows if r[0]]
                if times:
                    kpis_overall["mean"]   = round(_stats.mean(times), 1)
                    kpis_overall["median"] = round(_stats.median(times), 1)
                    kpis_overall["count"]  = len(times)
                    sorted_t = sorted(times)
                    p90_idx  = int(len(sorted_t) * 0.9)
                    kpis_overall["p90"] = round(sorted_t[min(p90_idx, len(sorted_t)-1)], 1)
                    kpis_overall["pct_within_8min"] = round(
                        sum(1 for t in times if t <= 8) / len(times) * 100, 1
                    )
                for sev in ("critical","high","medium","low"):
                    sev_times = [r[0] for r in rows if r[1] == sev and r[0]]
                    if sev_times:
                        kpis_by_sev[sev]["count"] = len(sev_times)
                        kpis_by_sev[sev]["mean"]  = round(_stats.mean(sev_times), 1)
    except Exception as _exc:
        logger.debug("[Dashboard] KPI calculation failed, using defaults: %s", _exc)

    kpis = {
        "overall":         kpis_overall,
        "trend":           "improving",
        "available_units": _avail_units,
        "total_units":     _total_units,
        "by_severity":     kpis_by_sev,
    }
    return {
        "hotspots":             hotspots,
        "forecast":             forecast,
        "hourly_distribution":  hourly_distribution,
        "historical_daily_avg": 16.0,
        "kpis":                 kpis,
        "generated_at":         now.isoformat(),
        "source":               "mock",
    }

_MOCK_PROTOCOLS = [
    {
        "code": "PRT-2024-001", "title": "PROTOCOLO PCR · Parada Cardiorrespiratoria",
        "category": "SANITARIO", "updated": "Feb 2024",
        "source": "EPES 112 Andalucía",
        "excerpt": "Activar código parada. SVA+SVB. RCP: 30+2. Desfibrilación precoz. ETA < 8 min.",
        "tags": ["cardiac","svb","sva","rcp"],
        "steps": [
            "Confirmar inconsciencia y ausencia de pulso / respiración.",
            "Activar código parada: enviar SVA + SVB.",
            "RCP inmediata: 30 compresiones + 2 ventilaciones.",
            "Desfibrilación si DEA disponible en < 3 min.",
            "Primera unidad en escena < 8 min.",
            "Notificar hospital receptor código 90.",
            "Continuar hasta ROSC o decisión médica.",
        ],
        "escalation": "Si > 3 víctimas: activar PEM. Refuerzo 2 SVB adicionales.",
        "notes": "Menores de 1 año: protocolo pediátrico. Ratio 15:2.",
        "related_incident_ids": [],
    },
    {
        "code": "PRT-2024-015", "title": "PROTOCOLO ATT · Accidente de Tráfico con Víctimas",
        "category": "TRÁFICO", "updated": "Mar 2024",
        "source": "EPES 112 Andalucía",
        "excerpt": "Asegurar escena. Señalización 100m. Triaje START. SVA+POL. Si atrapados: GREA.",
        "tags": ["trauma","triaje","start","rescate"],
        "steps": [
            "Señalización a 100m en ambas direcciones.",
            "Evaluar vehículos y víctimas.",
            "Triaje START: Rojo/Amarillo/Verde/Negro.",
            "Enviar SVA + SVB + policial.",
            "Si atrapados: bomberos con GREA.",
            "No mover con sospecha de lesión medular.",
            "PMA si > 3 víctimas.",
        ],
        "escalation": "Si > 5 víctimas: PEM y coordinador sanitario.",
        "notes": "Autopistas: perímetro mínimo 200m.",
        "related_incident_ids": [],
    },
    {
        "code": "PRT-2024-008", "title": "PROTOCOLO INC · Incendio Estructural",
        "category": "BOMBEROS", "updated": "Ene 2024",
        "source": "CBAS · Bomberos Sevilla",
        "excerpt": "Confirmar dirección y plantas. Corte suministros. Evacuación 200m. BOM×2+SVA.",
        "tags": ["fuego","evacuación","bomberos"],
        "steps": [
            "Confirmar dirección exacta y plantas afectadas.",
            "Enviar 2 bomberos + SVA + policial.",
            "Cortar gas y electricidad.",
            "Evacuación. Punto de encuentro 200m.",
            "Si atrapados: activar GREA.",
            "Perímetro de seguridad 100m.",
        ],
        "escalation": "Si > 3 plantas o explosiones: 2 unidades adicionales.",
        "notes": "MATPEL: protocolo específico. Notificar SEPRONA.",
        "related_incident_ids": [],
    },
    {
        "code": "PRC-2024-003", "title": "PROCEDIMIENTO TRIAJE · Sistema START",
        "category": "SANITARIO", "updated": "Ene 2024",
        "source": "EPES 112 Andalucía",
        "excerpt": "Rojo (crítico), Amarillo (urgente), Verde (leve), Negro (no recuperable).",
        "tags": ["triaje","start","masivas"],
        "steps": [
            "NEGRO: sin respiración tras desobstrucción.",
            "ROJO: FR>30, capilar>2s, no obedece.",
            "AMARILLO: camina pero precisa atención <60min.",
            "VERDE: ambulante, lesiones menores.",
            "Máx 30-60s por víctima.",
            "Registrar categorías antes de tratar.",
        ],
        "escalation": ">10 víctimas: Sistema Extendido con equipo de 2.",
        "notes": "Menores: protocolo JumpSTART.",
        "related_incident_ids": [],
    },
    {
        "code": "PRT-2024-022", "title": "PROTOCOLO GAS · Fuga de Gas Natural o GLP",
        "category": "RIESGO", "updated": "Feb 2024",
        "source": "Bomberos de Sevilla",
        "excerpt": "No interruptores. Ventilar. Evacuar. Cortar exterior. Detector antes de reentrada.",
        "tags": ["gas","explosión","evacuación"],
        "steps": [
            "NO activar interruptores eléctricos.",
            "Evacuar inmediatamente.",
            "Ventilar abriendo puertas y ventanas.",
            "Cortar suministro desde exterior.",
            "Solicitar técnico compañía.",
            "Inspeccionar con detector antes de reentrar.",
        ],
        "escalation": "Con víctimas por inhalación: activar INC simultáneamente.",
        "notes": "GLP más pesado que el aire: acumula en zonas bajas.",
        "related_incident_ids": [],
    },
    {
        "code": "PRT-2024-031", "title": "PROTOCOLO AGR · Agresión y Violencia",
        "category": "RIESGO", "updated": "Mar 2024",
        "source": "CECOP 112 Andalucía",
        "excerpt": "Asegurar con policía antes de entrada sanitaria. Testigos y agresor separados.",
        "tags": ["agresión","violencia","policial"],
        "steps": [
            "Esperar autorización policial.",
            "Evaluar víctimas y lesiones.",
            "Separar testigos y agresor.",
            "Atención sanitaria con seguridad garantizada.",
            "Preservar indicios judiciales.",
        ],
        "escalation": "Si hay armas: solicitar GEOS. Mantener perímetro.",
        "notes": "Violencia de género: activar VIOGEN.",
        "related_incident_ids": [],
    },
]

_INCIDENT_HINTS = {
    "cardiac_arrest": ["pcr", "cardio", "parada", "infarto"],
    "traffic_accident": ["trafico", "tráfico", "accidente", "att", "vehiculo", "vehículo"],
    "fire": ["incendio", "fuego"],
    "gas_leak": ["gas", "glp"],
    "assault": ["agresion", "agresión", "violencia", "agr"],
    "fall_injury": ["caida", "caída", "trauma", "fractura"],
    "stroke": ["ictus", "stroke", "derrame"],
}

_CATEGORY_HINTS = {
    "SANITARIO": ["sanitario", "pcr", "ictus", "epes", "profesionales"],
    "TRÁFICO": ["trafico", "tráfico", "vehiculo", "vehículos", "accidentes", "carretera"],
    "BOMBEROS": ["bomberos", "incendio", "fuego"],
    "RIESGO": ["emergencias", "operacion", "operación", "riesgo", "gas"],
}


def _infer_incident_type(text: str) -> str:
    """Return the incident type key that best matches *text*, or 'other' if none match."""
    value = (text or "").lower()
    for incident, hints in _INCIDENT_HINTS.items():
        if any(h in value for h in hints):
            return incident
    return "other"


def _infer_urgency(text: str) -> str:
    """Return urgency level ('critical', 'high', 'medium', or 'low') inferred from *text*."""
    value = (text or "").lower()
    if any(h in value for h in ("critico", "crítico", "critical", "grave", "urgente vital")):
        return "critical"
    if any(h in value for h in ("alto", "high", "urgente")):
        return "high"
    if any(h in value for h in ("bajo", "leve", "low")):
        return "low"
    return "medium"


def _infer_category(text: str) -> str:
    """Return the protocol category ('SANITARIO', 'TRÁFICO', 'BOMBEROS', or 'RIESGO') inferred from *text*."""
    value = (text or "").lower()
    for category, hints in _CATEGORY_HINTS.items():
        if any(h in value for h in hints):
            return category
    return "RIESGO"


def _get_fallback_steps(incident_type: str) -> list:
    """Return standard protocol steps for an incident type from the mock bank."""
    proto = _MockPipeline._PROTOCOLS.get(incident_type) or _MockPipeline._DEFAULT_PROTOCOL
    return list(proto.get("key_actions") or [])


def _get_incident_usage_counts() -> dict[str, int]:
    """Return {incident_type: count} for the last 30 days from the DB."""
    try:
        with _db_conn() as conn:
            rows = conn.execute(
                "SELECT incident_type, COUNT(*) AS n FROM incidents "
                "WHERE timestamp >= datetime('now', '-30 days') "
                "GROUP BY incident_type"
            ).fetchall()
        return {r["incident_type"]: r["n"] for r in rows}
    except Exception as exc:
        logger.warning("[_get_incident_usage_counts] %s", exc)
        return {}


def _normalise_adapted_entry(entry: dict, incident_type: str = "", urgency: str = "") -> dict:
    """Normalise a raw protocol cache entry into the dashboard protocol card schema."""
    text_blob = " ".join([
        str(entry.get("title", "")),
        str(entry.get("excerpt", "")),
        str(entry.get("raw_text", "")),
        " ".join(entry.get("tags", []) if isinstance(entry.get("tags", []), list) else []),
    ])
    detected_incident = incident_type or _infer_incident_type(text_blob)
    detected_urgency = urgency or _infer_urgency(text_blob)

    raw_steps = entry.get("steps") or []
    _generic_markers = ("revisar documento", "documento original")
    is_stub = len(raw_steps) < 2 or (
        len(raw_steps) == 1 and any(m in raw_steps[0].lower() for m in _generic_markers)
    )
    steps = _get_fallback_steps(detected_incident) if is_stub else raw_steps

    return {
        "code": entry.get("code") or "—",
        "title": entry.get("title") or "—",
        "category": entry.get("category") or "—",
        "updated": entry.get("updated") or entry.get("indexed_at", "")[:10] or "—",
        "source": entry.get("source") or entry.get("source_file") or "—",
        "excerpt": entry.get("excerpt") or (steps[0] if steps else ""),
        "tags": entry.get("tags") or [],
        "steps": steps,
        "escalation": entry.get("escalation") or "",
        "notes": entry.get("notes") or "",
        "related_incident_ids": entry.get("related_incident_ids") or [],
        "incident_type": detected_incident,
        "urgency": detected_urgency,
    }


def _get_adapted_protocols() -> tuple[list[dict], str]:
    """Return one protocol card per incident type, annotated with retrieval_tier and usage_count.
    """
    cache: dict = {}
    try:
        from tools.protocol_indexer import load_cache as _pi_load_cache
        cache = _pi_load_cache() or {}
    except Exception as _exc:
        logger.debug("[Protocols] cache unavailable: %s", _exc)

    vector_available = False
    try:
        from tools.protocol_indexer import vector_store_available
        vector_available = vector_store_available()
    except Exception:
        pass

    usage_counts = _get_incident_usage_counts()

    protocols: list[dict] = []
    for incident_type in _ALL_INCIDENT_TYPES:
        meta_code, meta_title, meta_cat = _TYPE_META.get(
            incident_type,
            (f"PRT-{abs(hash(incident_type)) % 90000 + 10000:05d}",
             incident_type.replace("_", " ").title(), "RIESGO"),
        )

        if incident_type in cache:
            tier    = "cache"
            sev_map = cache[incident_type]
            entry   = dict(
                sev_map.get("critical") or sev_map.get("high")
                or next(iter(sev_map.values()), {})
            )
        else:
            tier = "vector" if vector_available else "stub"
            try:
                from tools.protocol_indexer import _STUBS
                entry = dict(_STUBS.get(incident_type) or {})
            except Exception:
                entry = {}
            if not entry:
                entry = {
                    "code":       meta_code,
                    "title":      meta_title,
                    "steps":      _get_fallback_steps(incident_type),
                    "escalation": "",
                    "notes":      "Protocolo disponible vía agente RAG.",
                    "source":     tier,
                }

        card = _normalise_adapted_entry(entry, incident_type=incident_type, urgency="critical")
        if not card.get("code") or card["code"] == "—":
            card["code"] = meta_code
        if not card.get("title") or card["title"] == "—":
            card["title"] = meta_title
        if not card.get("category") or card["category"] == "—":
            card["category"] = meta_cat
        card["retrieval_tier"] = tier
        card["usage_count"]    = usage_counts.get(incident_type, 0)
        card["incident_type"]  = incident_type
        protocols.append(card)

    return protocols, "per_incident_type"

@app.get("/api/health")
async def health():
    return {
        "status":    "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mock_mode": MOCK_MODE,
    }


@app.get("/api/bases")
async def get_bases():
    try:
        from tools.get_route import UNIT_BASES, _BASE_COORDS_CACHE
        bases_map = {}
        for base_type, bases in UNIT_BASES.items():
            for base_name in bases:
                coords = _BASE_COORDS_CACHE.get(base_name)
                if coords:
                    if base_name not in bases_map:
                        bases_map[base_name] = {
                            "name": base_name,
                            "lat": coords[0],
                            "lon": coords[1],
                            "types": [base_type]
                        }
                    else:
                        if base_type not in bases_map[base_name]["types"]:
                            bases_map[base_name]["types"].append(base_type)
        return {"bases": list(bases_map.values())}
    except Exception as e:
        logger.error(f"[API] Error getting bases: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/route")
async def get_api_route(origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float, unit_type: str = "default"):
    try:
        from tools.get_route import _in_sevilla_bounds as _rt_in_bounds
        if not _rt_in_bounds(dest_lat, dest_lon):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Destination ({dest_lat:.4f}, {dest_lon:.4f}) is outside "
                    "Sevilla's operational area. This system only handles "
                    "incidents within the city of Sevilla."
                ),
            )
    except ImportError:
        pass

    try:
        from tools.get_route import get_route
        route_str = get_route(
            destination_address="",
            origin_address="",
            unit_type=unit_type,
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            destination_lat=dest_lat,
            destination_lon=dest_lon
        )
        return json.loads(route_str)
    except Exception as e:
        logger.error(f"[API] Error calculating route: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/geocode")
async def geocode_address(address: str = Query(..., description="Free-text address to geocode")):
    """Geocode a free-text address within Sevilla's operational area.
    Used by the dispatch modal to update map/ETA after the operator corrects the address.
    """
    from tools.extract_location import _geocode
    try:
        result = _geocode(address, city_hint="Sevilla, España")
        if result:
            lat, lon = result["latitude"], result["longitude"]
            map_url = (
                f"https://www.google.com/maps/dir/?api=1"
                f"&destination={lat},{lon}&travelmode=driving"
            )
            return {"found": True, "address": result["address"],
                    "lat": lat, "lon": lon, "map_url": map_url}
        return {"found": False, "address": address, "lat": None, "lon": None, "map_url": None}
    except Exception as e:
        logger.error(f"[API] Geocode error for '{address}': {e}")
        return {"found": False, "address": address, "lat": None, "lon": None, "map_url": None}


@app.get("/api/incidents/active")
async def get_active():
    active = _db_get_incidents(active_only=True)
    return {"incidents": active, "count": len(active)}


@app.get("/api/incidents/history")
async def get_history(days: int = Query(30, ge=1, le=365), incident_type: str = "all"):
    if incident_type not in VALID_INCIDENT_TYPES:
        raise HTTPException(status_code=422, detail="Invalid incident_type")
    incs = _db_get_incidents(history_only=True, days=days)
    if incident_type != "all":
        incs = [i for i in incs if i.get("incident_type") == incident_type]
    return {"incidents": incs, "count": len(incs), "days": days}


def _inject_unit_counts(data: dict) -> dict:
    """Inject live available/total unit counts into a dashboard dict's kpis."""
    try:
        from tools.recommend_units import _UNIT_REGISTRY, _is_dispatched as _ru_is_dispatched
        total  = len(_UNIT_REGISTRY)
        avail  = sum(
            1 for u in _UNIT_REGISTRY
            if u.status == "available" and not _ru_is_dispatched(u.id)
        )
        kpis = data.get("kpis") or {}
        kpis["available_units"] = avail
        kpis["total_units"]     = total
        data["kpis"] = kpis
    except Exception as _exc:
        logger.debug("[Dashboard] Unit count injection failed: %s", _exc)
    return data


@app.get("/api/dashboard")
async def get_dashboard():
    if _analysis_runner:
        return _inject_unit_counts(_analysis_runner.get_dashboard_data())
    if _dashboard_cache:
        return _dashboard_cache
    return _make_mock_dashboard()


@app.get("/api/hotspots")
async def get_hotspots():
    data = await get_dashboard()
    return {"hotspots": data.get("hotspots", [])}


@app.get("/api/forecast")
async def get_forecast():
    data = await get_dashboard()
    return {"forecast": data.get("forecast", []),
            "historical_daily_avg": data.get("historical_daily_avg", 0)}


@app.get("/api/kpis")
async def get_kpis():
    data = await get_dashboard()
    return data.get("kpis", {})


@app.get("/api/protocols")
async def get_protocols():
    try:
        from tools.protocol_indexer import _load_cache
        cache = _load_cache()
        if cache:
            protocols = []
            for itype, sevs in cache.items():
                for sev, entry in sevs.items():
                    protocols.append(entry)
            seen  = set()
            dedup = []
            for p in protocols:
                if p.get("code") not in seen:
                    seen.add(p.get("code"))
                    dedup.append(p)
            return {"protocols": dedup, "count": len(dedup), "source": "cache"}
    except Exception as _exc:
        logger.debug("[Protocols] Cache load failed, falling back to mock: %s", _exc)
    return {"protocols": _MOCK_PROTOCOLS, "count": len(_MOCK_PROTOCOLS), "source": "mock"}


@app.get("/api/protocols/adapted")
async def get_protocols_adapted(
    incident_type: str = Query(""),
    severity:      str = Query(""),
):
    """Return all protocol cards, or a single card when incident_type + severity are given
    (used by the article panel's severity selector to reload steps without a full page refresh)."""
    if incident_type and severity:
        try:
            from tools.protocol_indexer import _from_cache, _from_vector, _from_stub, vector_store_available
            result = _from_cache(incident_type, severity)
            tier   = "cache"
            if result is None:
                if vector_store_available():
                    result = _from_vector(incident_type, severity, "")
                    tier   = "vector"
            if result is None:
                result = _from_stub(incident_type)
                tier   = "stub"
            meta_code, meta_title, meta_cat = _TYPE_META.get(
                incident_type,
                (f"PRT-{abs(hash(incident_type)) % 90000 + 10000:05d}",
                 incident_type.replace("_", " ").title(), "RIESGO"),
            )
            card = _normalise_adapted_entry(result, incident_type=incident_type, urgency=severity)
            if not card.get("code") or card["code"] == "—":
                card["code"] = meta_code
            if not card.get("title") or card["title"] == "—":
                card["title"] = meta_title
            if not card.get("category") or card["category"] == "—":
                card["category"] = meta_cat
            card["retrieval_tier"] = tier
            card["incident_type"]  = incident_type
            card["urgency"]        = severity
            return {"protocol": card, "source": tier}
        except Exception as exc:
            logger.error("[API] get_protocols_adapted single lookup failed: %s", exc)
            raise HTTPException(status_code=500, detail="Protocol lookup failed")

    protocols, source = _get_adapted_protocols()
    return {"protocols": protocols, "count": len(protocols), "source": source}


@app.get("/api/protocols/raw")
async def get_protocols_raw():
    docs_dir = pathlib.Path("./data/protocol_index")
    if not docs_dir.exists():
        return {"documents": [], "count": 0, "source": str(docs_dir)}

    documents = []
    for path in docs_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".pdf", ".txt", ".md", ".docx"}:
            continue
        stat = path.stat()
        documents.append({
            "filename": path.name,
            "extension": path.suffix.lower().lstrip("."),
            "size_bytes": stat.st_size,
            "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "download_url": f"/api/protocols/raw/file/{path.name}",
        })
    documents.sort(key=lambda d: d["filename"].lower())
    return {"documents": documents, "count": len(documents), "source": str(docs_dir)}


@app.get("/api/protocols/raw/file/{filename:path}")
async def get_protocol_raw_file(filename: str):
    docs_dir = pathlib.Path("./data/protocol_index").resolve()
    requested = unquote(filename)
    target = (docs_dir / requested).resolve()
    if docs_dir not in target.parents and target != docs_dir:
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {requested}")
    return FileResponse(target)


@app.get("/api/protocols/search")
async def search_protocols(
    q: str = Query(""),
    k: int = Query(6, ge=1, le=15),
):
    """Semantic search over the protocol vector store — same ChromaDB the agent queries.
    Falls back to text-matching the structured cache when the vector store is unavailable."""
    q = q.strip()
    if len(q) < 3:
        return {"results": [], "query": q, "source": "none", "count": 0}

    try:
        from tools.protocol_indexer import search_chunks
        chunks = search_chunks(q, k=k)
        if chunks:
            return {"results": chunks, "query": q, "source": "vector", "count": len(chunks)}
    except Exception as exc:
        logger.warning("[search_protocols] Vector search failed: %s", exc)

    fallback: list[dict] = []
    try:
        from tools.protocol_indexer import load_cache as _pi_load_cache
        cache = _pi_load_cache() or {}
        q_lower = q.lower()
        seen: set[str] = set()
        for itype, sev_map in cache.items():
            for _sev, entry in sev_map.items():
                text_blob = " ".join([
                    str(entry.get("title", "")),
                    " ".join(entry.get("steps", [])),
                    str(entry.get("notes", "")),
                ])
                if q_lower in text_blob.lower():
                    title = str(entry.get("title", ""))
                    if title not in seen:
                        seen.add(title)
                        steps = entry.get("steps") or [""]
                        fallback.append({
                            "text":   (title + ". " + steps[0])[:400],
                            "score":  1.0,
                            "source": entry.get("source", "cache"),
                        })
    except Exception:
        pass

    return {"results": fallback[:k], "query": q, "source": "fallback", "count": len(fallback[:k])}


@app.get("/api/protocols/coverage")
async def get_protocols_coverage():
    """Return knowledge-base health stats: doc/chunk counts, per-type retrieval tier, usage."""
    summary_path = pathlib.Path("./data/protocol_ingest_summary.json")
    indexed_at   = ""
    node_count   = 0
    if summary_path.exists():
        try:
            with open(summary_path, encoding="utf-8") as fh:
                s        = json.load(fh)
                indexed_at = s.get("indexed_at", "")
                node_count = s.get("node_count", 0)
        except Exception:
            pass

    docs_dir  = pathlib.Path("./data/protocol_index")
    doc_count = 0
    if docs_dir.exists():
        doc_count = sum(
            1 for f in docs_dir.iterdir()
            if f.is_file() and f.suffix.lower() in {".pdf", ".txt", ".md"}
        )

    vector_count     = 0
    vector_available = False
    try:
        import chromadb
        from tools.protocol_indexer import CHROMA_DIR
        if CHROMA_DIR.exists():
            client           = chromadb.PersistentClient(path=str(CHROMA_DIR))
            col              = client.get_or_create_collection("imers_protocols")
            vector_count     = col.count()
            vector_available = vector_count > 0
    except Exception:
        pass

    cache: dict = {}
    try:
        from tools.protocol_indexer import load_cache as _pi_load_cache
        cache = _pi_load_cache() or {}
    except Exception:
        pass

    usage_counts  = _get_incident_usage_counts()
    type_coverage: list[dict] = []
    cache_types = vector_types = stub_types = 0

    for itype in _ALL_INCIDENT_TYPES:
        if itype in cache:
            tier = "cache";  cache_types  += 1
        elif vector_available:
            tier = "vector"; vector_types += 1
        else:
            tier = "stub";   stub_types   += 1
        type_coverage.append({
            "incident_type": itype,
            "tier":          tier,
            "usage_count":   usage_counts.get(itype, 0),
        })

    return {
        "doc_count":        doc_count,
        "chunk_count":      node_count or vector_count,
        "vector_count":     vector_count,
        "indexed_at":       indexed_at,
        "vector_available": vector_available,
        "cache_types":      cache_types,
        "vector_types":     vector_types,
        "stub_types":       stub_types,
        "type_coverage":    type_coverage,
    }


class ProcessRequest(BaseModel):
    transcript: str = Field(..., max_length=8000)
    city_hint:  str = Field("Sevilla, España", max_length=200)


@app.post("/api/process")
@limiter.limit("10/minute")
async def process_call(request: Request, req: ProcessRequest):
    if not _pipeline:
        raise HTTPException(status_code=503, detail="Pipeline not initialised")
    req.city_hint = "Sevilla, España"
    loop   = asyncio.get_running_loop()
    report = await loop.run_in_executor(None, _pipeline.run_transcript, req.transcript)

    try:
        from tools.get_route import _in_sevilla_bounds as _rt_in_bounds
        loc = report.get("location") or {}
        lat = loc.get("latitude")
        lon = loc.get("longitude")
        if lat is not None and lon is not None and not _rt_in_bounds(lat, lon):
            logger.warning(
                f"[API] Resolved location ({lat}, {lon}) is outside Sevilla — "
                "overriding with city-centre fallback."
            )
            report.setdefault("location", {})
            report["location"]["latitude"]  = 37.3886
            report["location"]["longitude"] = -5.9823
            report["location"]["address"]   = "Sevilla (ubicación fuera del área operativa — fallback centro)"
            report["location"]["confidence"] = "low"
            report.setdefault("pipeline", {})
            existing_warn = report["pipeline"].get("warnings") or ""
            report["pipeline"]["warnings"] = (
                (existing_warn + "; " if existing_warn else "") +
                "Ubicación fuera del área operativa de Sevilla. Se ha usado el centro de la ciudad como fallback."
            )
    except ImportError:
        pass

    await loop.run_in_executor(None, _upsert_db_incident, report)
    await ws_manager.broadcast({"type": "new_incident", "data": report})
    return report


class PatchIncidentRequest(BaseModel):
    incident_type:    Optional[str] = None
    severity:         Optional[str] = None
    address:          Optional[str] = None
    units_dispatched: Optional[str] = None
    response_time_min: Optional[float] = None
    protocol:         Optional[str] = None
    status:           Optional[str] = None
    note:             Optional[str] = None


@app.patch("/api/incidents/{incident_id}")
async def patch_incident(incident_id: str, req: PatchIncidentRequest):
    """Operator override: update editable fields of an existing incident."""
    _updates = req.model_dump() if hasattr(req, "model_dump") else req.dict()

    def _do_patch():
        with _db_write_lock, _db_conn() as conn:
            row = conn.execute(
                "SELECT data FROM incidents WHERE id=?", (incident_id,)
            ).fetchone()
            if not row:
                return None
            inc = json.loads(row["data"])

            if _updates.get("incident_type")     is not None: inc["incident_type"]     = _updates["incident_type"]
            if _updates.get("severity")          is not None: inc["severity"]          = _updates["severity"]
            if _updates.get("address")           is not None: inc["address"]           = _updates["address"]
            if _updates.get("units_dispatched")  is not None: inc["units_dispatched"]  = _updates["units_dispatched"]
            if _updates.get("response_time_min") is not None: inc["response_time_min"] = _updates["response_time_min"]
            if _updates.get("protocol")          is not None: inc["protocol"]          = _updates["protocol"]
            if _updates.get("note")              is not None: inc["note"]              = _updates["note"]
            if _updates.get("status")            is not None: inc["status"]            = _updates["status"]

            conn.execute(
                "UPDATE incidents SET status=?, incident_type=?, severity=?, data=? WHERE id=?",
                (inc.get("status", "active"), inc.get("incident_type", "other"),
                 inc.get("severity", "medium"), json.dumps(inc), incident_id),
            )
            return inc

    loop = asyncio.get_running_loop()
    inc  = await loop.run_in_executor(None, _do_patch)
    if inc is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    await ws_manager.broadcast({
        "type": "incident_report",
        "report_type": "updated",
        "data": inc,
    })
    return {"status": "success", "incident_id": incident_id, "incident": inc}


@app.post("/api/incidents/{incident_id}/resolve")
async def resolve_incident(incident_id: str):
    def _do_resolve():
        with _db_write_lock, _db_conn() as conn:
            row = conn.execute(
                "SELECT data FROM incidents WHERE id=?", (incident_id,)
            ).fetchone()
            if not row:
                return None
            inc = json.loads(row["data"])
            inc["status"] = "resolved"
            conn.execute(
                "UPDATE incidents SET status='resolved', data=? WHERE id=?",
                (json.dumps(inc), incident_id),
            )
            return inc

    loop = asyncio.get_running_loop()
    inc  = await loop.run_in_executor(None, _do_resolve)
    if inc is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    try:
        from tools.recommend_units import release_units as _release
        dispatch_units = inc.get("dispatch", {}).get("units") or []
        unit_ids = [
            u["id"] if isinstance(u, dict) else str(u)
            for u in dispatch_units
        ]
        if unit_ids:
            _release(unit_ids)
    except Exception as _exc:
        logger.debug("[Resolve] Unit release failed (auto-expire will handle it): %s", _exc)

    await ws_manager.broadcast({
        "type": "incident_report",
        "report_type": "final",
        "data": inc,
    })
    return {"status": "success", "incident_id": incident_id}


@app.delete("/api/incidents/{incident_id}")
async def discard_incident(incident_id: str):
    """Permanently discard an incident (fake call / error). Removes from DB and broadcasts removal."""
    def _do_delete():
        with _db_write_lock, _db_conn() as conn:
            row = conn.execute("SELECT data FROM incidents WHERE id=?", (incident_id,)).fetchone()
            if not row:
                return None
            inc = json.loads(row["data"])
            conn.execute("DELETE FROM incidents WHERE id=?", (incident_id,))
            return inc

    loop = asyncio.get_running_loop()
    inc  = await loop.run_in_executor(None, _do_delete)
    if inc is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    try:
        from tools.recommend_units import release_units as _release
        dispatch_units = inc.get("dispatch", {}).get("units") or []
        unit_ids = [u["id"] if isinstance(u, dict) else str(u) for u in dispatch_units]
        if unit_ids:
            _release(unit_ids)
    except Exception as _exc:
        logger.debug("[Discard] Unit release failed: %s", _exc)

    await ws_manager.broadcast({"type": "incident_discarded", "incident_id": incident_id})
    return {"status": "discarded", "incident_id": incident_id}



def _make_queue_item(
    source: str,
    label: str,
    file_path: Optional[str] = None,
) -> dict:
    """Create a new queue item dict with a unique ID and the given source, label and optional file path."""
    item_id = f"Q-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
    return {
        "item_id":   item_id,
        "source":    source,
        "label":     label,
        "file_path": file_path,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "position":  0,
    }


def _recalc_positions() -> None:
    """Rewrite the position field in each queue item to reflect its 1-based index."""
    for i, item in enumerate(_queue_items):
        item["position"] = i + 1


async def _broadcast_queue() -> None:
    """Recalculate queue positions and broadcast the current snapshot to all WebSocket clients."""
    _recalc_positions()
    snapshot = list(_queue_items)
    await ws_manager.broadcast({
        "type":  "queue_update",
        "queue": snapshot,
        "count": len(snapshot),
    })


async def _queue_processor() -> None:
    """
    Recording items are auto-started; mic items wait for the operator to answer.
    """
    global _active_session
    logger.info("[Queue] Processor started")

    while True:
        if _queue_event:
            await _queue_event.wait()

        async with _queue_lock:
            if _active_session is not None:
                if _queue_event:
                    _queue_event.clear()
                continue
            if not _queue_items:
                if _queue_event:
                    _queue_event.clear()
                continue
            if _queue_event:
                _queue_event.clear()
            item = _queue_items.pop(0)
            _recalc_positions()

        await ws_manager.broadcast({
            "type":    "queue_update",
            "queue":   list(_queue_items),
            "count":   len(_queue_items),
            "started": item,
        })

        if item["source"] == "recording" and item.get("file_path"):
            if not _session_manager:
                logger.warning("[Queue] Session manager not available — skipping item")
                continue
            try:
                session = _session_manager.create(
                    source="file",
                    file_path=item["file_path"],
                    realtime=False,
                )

                async def _wait_and_clear(s=session, fpath=item.get("file_path")):
                    global _active_session
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, _wait_session_complete, s)
                    async with _queue_lock:
                        _active_session = None
                    if fpath:
                        logger.info("[Queue] Recording retained on disk: %s", fpath)
                    if _queue_event:
                        _queue_event.set()
                    await ws_manager.broadcast({"type": "session_ended", "session_id": s.session_id})

                async with _queue_lock:
                    _active_session = session

                session.start()
                asyncio.create_task(_wait_and_clear())
                logger.info(f"[Queue] Started recording session {session.session_id} for '{item['label']}'")
                await ws_manager.broadcast({
                    "type":       "recording_started",
                    "session_id": session.session_id,
                    "label":      item.get("label", ""),
                })

            except Exception as e:
                logger.error(f"[Queue] Failed to start recording session: {e}")
                async with _queue_lock:
                    _active_session = None
                if _queue_event:
                    _queue_event.set()
        else:
            logger.info(f"[Queue] Mic item {item['item_id']} ready — waiting for operator")
            await ws_manager.broadcast({"type": "mic_ready", "item": item})


def _wait_session_complete(session, timeout_s: float = 300.0) -> None:
    """Block until session state is COMPLETE (called in executor thread).
    """
    import time as _time
    from realtime.call_session import SessionState
    deadline = _time.monotonic() + timeout_s
    while session.state not in (SessionState.COMPLETE,):
        if _time.monotonic() >= deadline:
            logger.warning(
                "[Queue] _wait_session_complete timed out after %.0fs for session %s",
                timeout_s, getattr(session, "session_id", "?"),
            )
            break
        _time.sleep(0.25)



class EnqueueRequest(BaseModel):
    source: str = "mic"
    label:  str = "Llamada en vivo"
    file_path: Optional[str] = None


def _validate_upload_path(path: Optional[str]) -> Optional[str]:
    """Resolve *path* and verify it is inside _UPLOADS_DIR.
    Returns the resolved absolute path string, or raises HTTPException(400).
    """
    if path is None:
        return None
    try:
        resolved = pathlib.Path(path).resolve()
        uploads  = _UPLOADS_DIR.resolve()
        try:
            resolved.relative_to(uploads)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"file_path must be inside the uploads directory ({uploads})",
            )
        return str(resolved)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid file_path: {e}")


@app.post("/api/call/enqueue")
async def enqueue_call(req: EnqueueRequest):
    """Add a call or recording to the processing queue."""
    safe_path = _validate_upload_path(req.file_path)
    item = _make_queue_item(
        source=req.source,
        label=req.label,
        file_path=safe_path,
    )
    async with _queue_lock:
        _queue_items.append(item)
        _recalc_positions()
    await _broadcast_queue()
    if _queue_event:
        _queue_event.set()
    return {"item": item, "queue_size": len(_queue_items)}


@app.get("/api/call/queue")
async def get_queue():
    """Return the current call queue."""
    _recalc_positions()
    return {"queue": list(_queue_items), "count": len(_queue_items)}


@app.delete("/api/call/queue/{item_id}")
async def remove_from_queue(item_id: str):
    """Remove an item from the queue by ID."""
    async with _queue_lock:
        before = len(_queue_items)
        _queue_items[:] = [i for i in _queue_items if i["item_id"] != item_id]
        removed = before - len(_queue_items)
        _recalc_positions()
    if removed == 0:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
    await _broadcast_queue()
    return {"removed": item_id, "queue_size": len(_queue_items)}


@app.post("/api/call/queue/{item_id}/start")
async def start_queue_item(item_id: str):
    """Immediately start processing a specific queue item (if no active session)."""
    global _active_session

    async with _queue_lock:
        if _active_session is not None:
            raise HTTPException(status_code=409, detail="Ya hay una sesión activa")
        item = next((i for i in _queue_items if i["item_id"] == item_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail=f"Item {item_id} not found in queue")
        _queue_items.remove(item)
        _recalc_positions()

    await _broadcast_queue()

    if item["source"] == "recording" and item.get("file_path"):
        if not _session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")

        session = _session_manager.create(
            source="file",
            file_path=item["file_path"],
            realtime=False,
        )

        async def _wait_and_clear_manual(s=session, fpath=item.get("file_path")):
            global _active_session
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _wait_session_complete, s)
            async with _queue_lock:
                _active_session = None
            if fpath:
                logger.info("[Queue] Recording retained on disk: %s", fpath)
            if _queue_event:
                _queue_event.set()
            await ws_manager.broadcast({"type": "session_ended", "session_id": s.session_id})

        async with _queue_lock:
            _active_session = session

        session.start()
        asyncio.create_task(_wait_and_clear_manual())
        logger.info("[Queue] Manual start: recording session %s for '%s'", session.session_id, item.get("label", ""))

        await ws_manager.broadcast({
            "type":       "recording_started",
            "session_id": session.session_id,
            "label":      item.get("label", ""),
        })
        return {"started": item, "session_id": session.session_id}

    else:
        logger.info("[Queue] Manual start: mic item %s", item["item_id"])
        await ws_manager.broadcast({"type": "mic_ready", "item": item})
        return {"started": item}


@app.post("/api/recording/upload")
@limiter.limit("5/minute")
async def upload_recording(request: Request, file: UploadFile = File(...)):
    """
    Upload an audio file (WAV, MP3, M4A, OGG, FLAC) and enqueue it.
    """
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 50 MB)")
    ext = pathlib.Path(file.filename or "").suffix.lower()
    if ext not in ACCEPTED_AUDIO_EXTS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{ext}'. Accepted: {', '.join(sorted(ACCEPTED_AUDIO_EXTS))}"
        )

    safe_stem  = pathlib.Path(file.filename).stem[:60].replace(" ", "_")
    unique_name = f"{safe_stem}_{uuid.uuid4().hex[:8]}{ext}"
    dest = _UPLOADS_DIR / unique_name

    try:
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f)
    finally:
        file.file.close()

    logger.info(f"[Upload] Saved recording: {dest} ({dest.stat().st_size} bytes)")

    item = _make_queue_item(
        source="recording",
        label=file.filename or unique_name,
        file_path=str(dest.resolve()),
    )
    async with _queue_lock:
        _queue_items.append(item)
        _recalc_positions()
    await _broadcast_queue()
    if _queue_event:
        _queue_event.set()

    return {
        "filename":   unique_name,
        "path":       str(dest),
        "size_bytes": dest.stat().st_size,
        "item":       item,
        "queue_size": len(_queue_items),
    }


class StartCallRequest(BaseModel):
    source:    str           = "mic"
    file_path: Optional[str] = None


@app.post("/api/call/start")
async def start_call(req: StartCallRequest):
    if not _session_manager:
        raise HTTPException(status_code=503, detail="Session manager not available")
    kwargs = {}
    if req.source == "file" and req.file_path:
        kwargs["file_path"] = _validate_upload_path(req.file_path)
    elif req.source == "socket":
        kwargs.update(host="127.0.0.1", port=9999)
    session = _session_manager.create(source=req.source, **kwargs)
    session.start()
    return {"session_id": session.session_id, "state": session.state.value}


@app.post("/api/call/{session_id}/stop")
async def stop_call(session_id: str):
    if not _session_manager:
        raise HTTPException(status_code=503)
    s = _session_manager.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    s.stop()
    return {"session_id": session_id, "state": "stopped"}


@app.get("/api/call/sessions")
async def list_sessions():
    if not _session_manager:
        raise HTTPException(status_code=503, detail="Session manager not available")
    return {"sessions": _session_manager.active_sessions()}


@app.get("/api/call/{session_id}")
async def get_session(session_id: str):
    if not _session_manager:
        raise HTTPException(status_code=503)
    s = _session_manager.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return s.to_dict()


@app.websocket("/ws/incidents")
async def ws_incidents(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            msg = await ws.receive_text()
            if msg.strip() == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


@app.websocket("/ws/call/audio")
async def ws_audio(ws: WebSocket):
    await ws.accept()
    if not _session_manager:
        await ws.send_json({"type": "error", "message": "Session manager not ready"})
        await ws.close()
        return

    session = _session_manager.create(source="websocket")

    async def _push_report(sid, rtype, report):
        try:
            _upsert_db_incident(report)
        except Exception as e:
            logger.warning(f"[WS] DB upsert error for {sid}: {e}")

        try:
            await ws.send_json({
                "type":        "pipeline_report",
                "report_type": rtype,
                "session_id":  sid,
                "data":        report,
            })
        except Exception as _exc:
            logger.debug("[WS] Direct audio-WS send failed (WS closed, broadcast covers it): %s", _exc)

        await ws_manager.broadcast({
            "type":        "incident_report",
            "report_type": rtype,
            "session_id":  sid,
            "data":        report,
        })

    loop = asyncio.get_running_loop()

    def _safe_push_report(sid, rt, r):
        """Push the pipeline report to the WebSocket, but only if the event loop is still running."""
        if loop.is_closed():
            logger.warning(
                f"[WS] Dropping report for {sid} ({rt}) — event loop already closed"
            )
            return
        try:
            asyncio.run_coroutine_threadsafe(_push_report(sid, rt, r), loop)
        except RuntimeError:
            pass

    session.on_report = _safe_push_report
    session.start()

    await ws.send_json({"type": "session_started", "session_id": session.session_id})
    logger.info(f"[WS] Audio session {session.session_id} started")

    try:
        while True:
            msg = await ws.receive()
            if "bytes" in msg:
                session.push_audio(msg["bytes"])
                if session._transcriber:
                    partial = session._transcriber.current_transcript
                    if partial:
                        await ws.send_json({"type": "transcript_partial", "text": partial})
            elif "text" in msg:
                try:
                    ctrl = json.loads(msg["text"])
                except Exception:
                    ctrl = {"type": msg["text"]}
                if ctrl.get("type") == "hangup":
                    session.hangup()
                    break
                elif ctrl.get("type") == "simulate_text":
                    text = ctrl.get("text", "")
                    if session._transcriber:
                        session._transcriber._transcript = text
                        session._transcriber._check_early_trigger()
                        await ws.send_json({"type": "transcript_partial", "text": text})
                elif ctrl.get("type") == "ping":
                    await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        session.hangup()
    except Exception as e:
        logger.error(f"[WS] Audio error for {session.session_id}: {e}")
        session.hangup()
    finally:
        _session_manager.close(session.session_id)