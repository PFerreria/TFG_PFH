"""LangGraph state machine: TTS → NLP → validate → [geo_retry] → fan_out(procedure|dispatch|geo) → merge → output."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.memory import MemorySaver

logger = logging.getLogger(__name__)


class IMERSState(TypedDict, total=False):
    """Shared pipeline state; all fields optional so nodes only return what they change."""

    audio_path:     Optional[str]
    transcript:     Optional[str]

    incident_type:               Optional[str]
    severity:                    Optional[str]
    victims:                     Optional[int]
    classification_confidence:   Optional[str]
    classification_raw:          Optional[dict]

    location_raw:           Optional[str]
    location_address:       Optional[str]
    location_lat:           Optional[float]
    location_lon:           Optional[float]
    location_confidence:    Optional[str]
    location_validated:     bool
    location_candidates:    Optional[list]
    location_is_midpoint:   Optional[bool]
    geo_retry_count:        int

    procedure_result:   Optional[dict]
    dispatch_result:    Optional[dict]
    geo_result:         Optional[dict]

    fan_out_done:   bool
    merge_done:     bool

    final_report:   Optional[dict]

    error:          Optional[str]
    abort_reason:   Optional[str]

    incident_id:    Optional[str]
    started_at:     Optional[str]
    completed_at:   Optional[str]
    city_hint:      Optional[str]
    node_timings:   Optional[dict]

    is_preliminary: Optional[bool]



def _initial_state(
    audio_path:     Optional[str] = None,
    transcript:     Optional[str] = None,
    city_hint:      str           = "Sevilla, España",
    incident_id:    Optional[str] = None,
    is_preliminary: bool          = False,
) -> IMERSState:
    return IMERSState(
        audio_path=audio_path,
        transcript=transcript,
        city_hint=city_hint,
        incident_id=incident_id or f"INC-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')[:20]}",
        started_at=datetime.now(timezone.utc).isoformat(),
        geo_retry_count=0,
        location_validated=False,
        fan_out_done=False,
        merge_done=False,
        node_timings={},
        is_preliminary=is_preliminary,
    )


def _timing(state: IMERSState, node: str, elapsed: float) -> dict:
    """Return a partial state update that merges the elapsed time of the node into node_timings."""
    existing = dict(state.get("node_timings") or {})
    existing[node] = round(elapsed, 3)
    return {"node_timings": existing}



def tts_node(state: IMERSState, agents: dict) -> IMERSState:
    """Runs Whisper on audio_path; skips if transcript is already present in state."""
    t0 = time.perf_counter()

    if state.get("transcript"):
        logger.info("[tts_node] Transcript already present — skipping Whisper")
        return _timing(state, "tts_node", time.perf_counter() - t0)

    audio_path = state.get("audio_path")
    if not audio_path:
        return {
            **_timing(state, "tts_node", time.perf_counter() - t0),
            "abort_reason": "tts_node: neither audio_path nor transcript provided",
        }

    tts_agent = agents["tts"]
    try:
        result = json.loads(tts_agent.run(audio_path))
    except Exception as exc:
        return {
            **_timing(state, "tts_node", time.perf_counter() - t0),
            "abort_reason": f"tts_node: transcription raised {type(exc).__name__}: {exc}",
        }

    if result.get("error") or not result.get("transcript"):
        return {
            **_timing(state, "tts_node", time.perf_counter() - t0),
            "abort_reason": f"tts_node: transcription failed — {result.get('error')}",
        }

    logger.info(f"[tts_node] Transcribed {result['duration_seconds']}s audio "
                f"in {result['processing_time_seconds']}s "
                f"(lang={result['language']}, p={result['language_probability']})")

    return {
        **_timing(state, "tts_node", time.perf_counter() - t0),
        "transcript": result["transcript"],
    }



def nlp_node(state: IMERSState, agents: dict) -> IMERSState:
    """Runs classify_incident and extract_location concurrently via ThreadPoolExecutor."""
    t0         = time.perf_counter()
    transcript = state.get("transcript", "")

    from tools.classify_incident import classify_incident
    from tools.extract_location  import extract_location

    city = state.get("city_hint", "Sevilla, España")
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f_classify = pool.submit(classify_incident, transcript)
            f_location = pool.submit(extract_location, transcript, city)
            classify_raw = f_classify.result(timeout=30)
            location_raw = f_location.result(timeout=30)
    except Exception as e:
        classify_raw = classify_incident(transcript)
        location_raw = extract_location(transcript, city)

    classification = json.loads(classify_raw)
    location       = json.loads(location_raw)

    priority_to_severity = {
        "P-1 (Emergency)": "critical",
        "P-2 (Urgent)": "high",
        "P-3 (Non-Urgent)": "medium",
        "P-4 (Information)": "low",
    }
    severity = priority_to_severity.get(classification.get("priority"), "medium")

    logger.info(
        f"[nlp_node] classified={classification['incident_type']}/"
        f"{severity} victims={classification['victims']} "
        f"location_found={location.get('found', False)} confidence={location.get('confidence', 'low')}"
    )

    return {
        **_timing(state, "nlp_node", time.perf_counter() - t0),
        "incident_type":             classification["incident_type"],
        "severity":                  severity,
        "victims":                   classification["victims"],
        "classification_confidence": classification["confidence"],
        "classification_raw":        classification,
        "location_raw":        location["candidates"][0] if (location.get("candidates") and len(location["candidates"]) > 0) else None,
        "location_address":    location.get("address"),
        "location_lat":          location.get("latitude"),
        "location_lon":          location.get("longitude"),
        "location_confidence":   location["confidence"],
        "location_candidates":   location.get("candidates", []),
        "location_is_midpoint":  location.get("is_midpoint", False),
    }



def validate_node(state: IMERSState, agents: dict) -> IMERSState:
    t0 = time.perf_counter()

    lat = state.get("location_lat")
    lon = state.get("location_lon")

    has_coords  = lat is not None and lon is not None
    confidence  = state.get("location_confidence", "low")
    validated   = has_coords and confidence in ("high", "medium")

    logger.info(f"[validate_node] location_validated={validated} "
                f"(has_coords={has_coords}, confidence={confidence})")

    return {
        **_timing(state, "validate_node", time.perf_counter() - t0),
        "location_validated": validated,
    }



def route_after_validate(state: IMERSState) -> str:
    if state.get("abort_reason"):
        return "abort_node"
    if state.get("location_validated"):
        return "fan_out_node"
    if (state.get("geo_retry_count") or 0) >= 1:
        logger.warning("[route_after_validate] Max geo retries reached — proceeding without confirmed location")
        return "fan_out_node"
    return "geo_retry_node"



def geo_retry_node(state: IMERSState, agents: dict) -> IMERSState:
    t0 = time.perf_counter()
    from tools.extract_location import extract_location

    transcript = state.get("transcript", "")
    city       = state.get("city_hint", "Sevilla, España")
    candidates = state.get("location_candidates") or []

    retry_text = " ".join(candidates) if candidates else transcript
    result = json.loads(extract_location(retry_text, city))

    if not result.get("found"):
        logger.warning("[geo_retry_node] Geocoding retry failed — operator must clarify address")
        result = {
            "found":      False,
            "address":    "Dirección no localizada, operador aclare dirección",
            "latitude":   None,
            "longitude":  None,
            "confidence": "low",
            "candidates": candidates,
        }

    retry_count = (state.get("geo_retry_count") or 0) + 1
    logger.info(f"[geo_retry_node] retry={retry_count} found={result['found']} "
                f"address={result.get('address')}")

    return {
        **_timing(state, "geo_retry_node", time.perf_counter() - t0),
        "location_address":    result.get("address"),
        "location_lat":        result.get("latitude"),
        "location_lon":        result.get("longitude"),
        "location_confidence": result.get("confidence", "low"),
        "location_validated":  result.get("found", False),
        "geo_retry_count":     retry_count,
    }


def fan_out_node(state: IMERSState, agents: dict) -> IMERSState:
    """Runs procedure, dispatch and geo agents in parallel."""
    t0 = time.perf_counter()

    transcript    = state.get("transcript", "")
    incident_type = state.get("incident_type", "other")
    severity      = state.get("severity", "medium")
    victims       = state.get("victims", 0)
    address       = state.get("location_address") or state.get("location_raw") or "Unknown"
    lat           = state.get("location_lat") or 0.0
    lon           = state.get("location_lon") or 0.0
    city          = state.get("city_hint", "Sevilla, España")

    procedure_agent = agents["procedure"]
    dispatch_agent  = agents["dispatch"]
    geo_agent       = agents["geo"]

    from tools.recommend_units import recommend_units as _recommend_units, _preview_ctx
    _preview_ctx.active = True
    try:
        _pre_units = json.loads(
            _recommend_units(incident_type, severity, address, victims, lat, lon)
        ).get("dispatched", [])
    finally:
        _preview_ctx.active = False

    def _geo_fallback(reason: str) -> dict:
        """Return a geo result using direct tool calls."""
        res = geo_agent._fallback(transcript, _pre_units, city, reason)
        if not res.get("routes"):
            res["routes"] = geo_agent._compute_routes_parallel(
                units=_pre_units,
                destination_address=res.get("incident_address") or address,
                dest_lat=res.get("incident_lat") or lat,
                dest_lon=res.get("incident_lon") or lon,
            )
        return res

    if state.get("is_preliminary"):
        logger.info("[fan_out_node] preliminary run — skipping LLM agents, using direct fallbacks")
        procedure_res = procedure_agent._fallback(incident_type, severity, "preliminary")
        dispatch_res  = dispatch_agent._fallback(incident_type, severity, address, victims, "preliminary", lat, lon)
        geo_res       = _geo_fallback("preliminary")
        elapsed = time.perf_counter() - t0
        logger.info(f"[fan_out_node] Completed in {elapsed:.2f}s (preliminary)")
        return {
            **_timing(state, "fan_out_node", elapsed),
            "procedure_result": procedure_res,
            "dispatch_result":  dispatch_res,
            "geo_result":       geo_res,
            "fan_out_done":     True,
        }

    fast_mode = os.environ.get("IMERS_FAST_MODE", "0") == "1"

    if not fast_mode and os.environ.get("IMERS_FAST_MODE") is None:
        try:
            from llm_clients import (
                _ollama_available_models, GROQ_API_KEY,
                FIREWORKS_API_KEY, HF_TOKEN,
            )
            _CAPABLE_PREFIXES = {
                "qwen2.5", "qwen3", "llama3.3", "llama3.1:70b",
                "mistral", "phi3", "gemma3",
            }
            _available = _ollama_available_models()
            _has_capable_local = any(
                any(m.startswith(p) for p in _CAPABLE_PREFIXES)
                for m in _available
            )
            _has_cloud = bool(GROQ_API_KEY or FIREWORKS_API_KEY or HF_TOKEN)
            if _available and not _has_capable_local and not _has_cloud:
                fast_mode = True
                logger.warning(
                    "[fan_out_node] Auto-enabling fast_mode: only weak local models "
                    "detected (no cloud API keys). Set IMERS_FAST_MODE=1 in .env to "
                    "suppress this check, or pull a capable model: "
                    "ollama pull qwen2.5:7b"
                )
        except Exception as _e:
            logger.debug("[fan_out_node] fast_mode auto-detect skipped: %s", _e)

    if fast_mode:
        logger.info("[fan_out_node] fast_mode — bypassing LLM, using direct fallback")
        procedure_res = procedure_agent._fallback(incident_type, severity, "fast_mode")
        dispatch_res  = dispatch_agent._fallback(incident_type, severity, address, victims, "fast_mode", lat, lon)
        geo_res       = _geo_fallback("fast_mode")

    else:
        agent_timeout = float(os.environ.get("IMERS_AGENT_TIMEOUT_SECS", "120"))
        logger.info(
            f"[fan_out_node] Starting 3 agents "
            f"(timeout={agent_timeout}s per agent)"
        )

        def _run_procedure():
            return procedure_agent.run(
                incident_type=incident_type,
                severity=severity,
                transcript=transcript,
                extra_context=f"{victims} victims, location: {address}",
            )

        def _run_dispatch():
            return dispatch_agent.run(
                incident_type=incident_type,
                severity=severity,
                location=address,
                victims=victims,
                incident_lat=lat,
                incident_lon=lon,
            )

        def _run_geo():
            loc_conf = state.get("location_confidence", "low")
            pre_lat  = lat  if (loc_conf in ("high", "medium") and lat)  else None
            pre_lon  = lon  if (loc_conf in ("high", "medium") and lon)  else None
            pre_addr = address if address != "Unknown" else None
            return geo_agent.run(
                transcript=transcript,
                units=_pre_units,
                city_hint=city,
                known_lat=pre_lat,
                known_lon=pre_lon,
                known_address=pre_addr,
                known_confidence=loc_conf,
                known_is_midpoint=state.get("location_is_midpoint", False),
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            f_proc = pool.submit(_run_procedure)
            f_disp = pool.submit(_run_dispatch)
            f_geo  = pool.submit(_run_geo)

            def _collect(future, name: str, fallback_fn):
                try:
                    return future.result(timeout=agent_timeout)
                except concurrent.futures.TimeoutError:
                    logger.warning(
                        f"[fan_out_node] {name} exceeded {agent_timeout}s — using fallback"
                    )
                    future.cancel()
                    return fallback_fn()
                except Exception as exc:
                    logger.error(f"[fan_out_node] {name} raised: {exc}")
                    return fallback_fn()

            procedure_res = _collect(
                f_proc, "procedure_agent",
                lambda: procedure_agent._fallback(incident_type, severity, "timeout"),
            )
            dispatch_res = _collect(
                f_disp, "dispatch_agent",
                lambda: dispatch_agent._fallback(incident_type, severity, address, victims, "timeout", lat, lon),
            )
            geo_res = _collect(
                f_geo, "geo_agent",
                lambda: _geo_fallback("timeout"),
            )

    elapsed = time.perf_counter() - t0
    logger.info(
        f"[fan_out_node] Completed in {elapsed:.2f}s "
        f"({'fast_mode' if fast_mode else 'llm_mode'})"
    )

    return {
        **_timing(state, "fan_out_node", elapsed),
        "procedure_result": procedure_res,
        "dispatch_result":  dispatch_res,
        "geo_result":       geo_res,
        "fan_out_done":     True,
    }



def merge_node(state: IMERSState, agents: dict) -> IMERSState:
    """Matches procedure, dispatch and geo results."""
    t0       = time.perf_counter()
    warnings = []

    geo  = state.get("geo_result") or {}
    addr = geo.get("incident_address") or state.get("location_address")
    lat  = geo.get("incident_lat") if geo.get("incident_lat") is not None else state.get("location_lat")
    lon  = geo.get("incident_lon") if geo.get("incident_lon") is not None else state.get("location_lon")

    if not addr:
        warnings.append("Location could not be resolved — address is unknown")

    dispatch = state.get("dispatch_result") or {}
    if dispatch.get("error"):
        warnings.append(f"Dispatch agent error: {dispatch['error']}")
    if not dispatch.get("dispatched_units") and not dispatch.get("units"):
        warnings.append("No units were dispatched — manual intervention required")

    procedure = state.get("procedure_result") or {}
    if procedure.get("error"):
        warnings.append(f"Procedure agent error: {procedure['error']}")

    proc_severity = procedure.get("severity")
    nlp_severity  = state.get("severity")
    if proc_severity and proc_severity != nlp_severity:
        warnings.append(
            f"Severity mismatch: NLP classified '{nlp_severity}', "
            f"Procedure agent assessed '{proc_severity}'. "
            f"Using Procedure agent assessment."
        )
        nlp_severity = proc_severity

    logger.info(f"[merge_node] merge complete, {len(warnings)} warning(s)")

    return {
        **_timing(state, "merge_node", time.perf_counter() - t0),
        "location_address": addr,
        "location_lat":     lat,
        "location_lon":     lon,
        "severity":         nlp_severity,
        "error": "; ".join(warnings) if warnings else None,
        "merge_done": True,
    }



def output_node(state: IMERSState, agents: dict) -> IMERSState:
    """Assembles the final_report dict from all state fields for the dashboard and pipeline caller."""
    t0       = time.perf_counter()
    now      = datetime.now(timezone.utc).isoformat()
    dispatch = state.get("dispatch_result") or {}
    geo      = state.get("geo_result")      or {}
    proc     = state.get("procedure_result") or {}

    units = (
        dispatch.get("dispatched_units")
        or dispatch.get("units")
        or dispatch.get("dispatch_plan")
        or []
    )

    _raw_actions = proc.get("key_actions") or proc.get("action_checklist") or []
    _key_actions = [
        a["action"] if isinstance(a, dict) else str(a)
        for a in _raw_actions
        if a
    ]

    report = {
        "incident_id":    state.get("incident_id"),
        "timestamp":      now,
        "status":         "aborted" if state.get("abort_reason") else "processed",

        "incident_type":             state.get("incident_type"),
        "severity":                  state.get("severity"),
        "victims":                   state.get("victims"),
        "classification_confidence": state.get("classification_confidence"),

        "location": {
            "address":    state.get("location_address"),
            "latitude":   state.get("location_lat"),
            "longitude":  state.get("location_lon"),
            "confidence": state.get("location_confidence"),
            "validated":  state.get("location_validated"),
        },

        "protocol": {
            "text":           proc.get("protocol_text") or (
                _key_actions[0] if _key_actions else None
            ),
            "key_actions":    _key_actions,
            "required_units": proc.get("required_resources") or proc.get("required_units"),
            "escalation":     proc.get("escalation_criteria") or proc.get("escalation_reason"),
            "source":         proc.get("protocol_source") or proc.get("source"),
        },

        "dispatch": {
            "units":                   units,
            "total_units":             dispatch.get("total_units") or len(units),
            "first_arrival_minutes":   dispatch.get("estimated_first_arrival")
                                       or dispatch.get("first_arrival_minutes"),
            "priority":                dispatch.get("dispatch_priority")
                                       or dispatch.get("priority"),
            "warnings":                dispatch.get("warnings", []),
        },

        "routes": geo.get("routes", []),

        "nearest_hospital": {
            "name":         geo.get("nearest_hospital"),
            "distance_km":  geo.get("nearest_hospital_km"),
            "route":        geo.get("hospital_route"),
        },

        "historical_context": {
            "status":  "pending",
            "note":    "Background annotation in progress — check dashboard for results.",
        },

        "pipeline": {
            "started_at":    state.get("started_at"),
            "completed_at":  now,
            "node_timings":  state.get("node_timings"),
            "geo_retries":   state.get("geo_retry_count", 0),
            "warnings":      state.get("error"),
            "abort_reason":  state.get("abort_reason"),
        },

        "_raw": {
            "procedure": proc,
            "dispatch":  dispatch,
            "geo":       geo,
        },

        "transcript_preview": state.get("transcript") or "",
    }

    logger.info(
        f"[output_node] Report ready: {state.get('incident_id')} "
        f"type={state.get('incident_type')} sev={state.get('severity')} "
        f"units={report['dispatch']['total_units']}"
    )

    return {
        **_timing(state, "output_node", time.perf_counter() - t0),
        "final_report": report,
        "completed_at": now,
    }



def abort_node(state: IMERSState, agents: dict) -> IMERSState:
    """Emits a minimal final_report with abort_reason so callers always get a response."""
    reason = state.get("abort_reason", "Unknown error")
    logger.error(f"[abort_node] Pipeline aborted: {reason}")

    report = {
        "incident_id":   state.get("incident_id"),
        "timestamp":     datetime.now(timezone.utc).isoformat(),
        "status":        "aborted",
        "abort_reason":  reason,
        "pipeline": {
            "started_at":   state.get("started_at"),
            "node_timings": state.get("node_timings"),
        },
    }

    return {
        "final_report": report,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }



def route_check_abort(state: IMERSState) -> str:
    return "abort_node" if state.get("abort_reason") else "continue"


def build_graph(agents: dict, checkpointer=None) -> Any:
    """
    Build and compile the IMERS LangGraph state machine.

    Args:
        agents: Dict with exactly these keys: 'tts', 'procedure', 'dispatch', 'geo'.
                The AnalysisAgent is NOT in this dict — it runs independently.
        checkpointer: Optional LangGraph checkpointer (pass MemorySaver() for
                      in-memory persistence / resumability).

    Returns:
        Compiled LangGraph app (call with app.invoke(initial_state)).
    """


    def _tts(state):       return tts_node(state, agents)
    def _nlp(state):       return nlp_node(state, agents)
    def _validate(state):  return validate_node(state, agents)
    def _geo_retry(state): return geo_retry_node(state, agents)
    def _fan_out(state):   return fan_out_node(state, agents)
    def _merge(state):     return merge_node(state, agents)
    def _output(state):    return output_node(state, agents)
    def _abort(state):     return abort_node(state, agents)

    builder = StateGraph(IMERSState)

    builder.add_node("tts_node",      _tts)
    builder.add_node("nlp_node",      _nlp)
    builder.add_node("validate_node", _validate)
    builder.add_node("geo_retry_node",_geo_retry)
    builder.add_node("fan_out_node",  _fan_out)
    builder.add_node("merge_node",    _merge)
    builder.add_node("output_node",   _output)
    builder.add_node("abort_node",    _abort)


    builder.add_edge(START,        "tts_node")
    builder.add_edge("tts_node",   "nlp_node")
    builder.add_edge("nlp_node",   "validate_node")

    builder.add_conditional_edges(
        "validate_node",
        route_after_validate,
        {
            "fan_out_node":  "fan_out_node",
            "geo_retry_node":"geo_retry_node",
            "abort_node":    "abort_node",
        },
    )

    builder.add_edge("geo_retry_node", "validate_node")

    builder.add_edge("fan_out_node", "merge_node")
    builder.add_edge("merge_node",   "output_node")
    builder.add_edge("output_node",  END)

    builder.add_edge("abort_node", END)

    kwargs: dict = {}
    if checkpointer:
        kwargs["checkpointer"] = checkpointer

    app = builder.compile(**kwargs)
    logger.info("[build_graph] IMERS LangGraph compiled successfully")
    return app


class IMERSPipeline:
    """Wrapper that initialises agents, builds the graph and fires post-dispatch hooks."""

    def __init__(
        self,
        hf_token:    Optional[str] = None,
        city_hint:   str           = "Sevilla, España",
        checkpoint:  bool          = True,
    ):
        import os
        import sys
        from agents import TTSAgent, ProcedureAgent, DispatchAgent, GeoAgent

        parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
        from llm_clients import OllamaModel, log_provider_status

        log_provider_status()
        model_procedure = OllamaModel("qwen/qwen3-32b")
        model_dispatch  = OllamaModel("llama-3.3-70b-versatile")
        model_geo       = OllamaModel("llama-3.3-70b-versatile")

        self.agents = {
            "tts":       TTSAgent(),
            "procedure": ProcedureAgent(model_procedure),
            "dispatch":  DispatchAgent(model_dispatch),
            "geo":       GeoAgent(model_geo),
        }
        self.city_hint   = city_hint
        self.last_state: Optional[IMERSState] = None

        self._post_dispatch_hooks: list = []

        checkpointer = MemorySaver() if checkpoint else None
        self.app     = build_graph(self.agents, checkpointer=checkpointer)
        logger.info("[IMERSPipeline] Ready")

    def add_post_dispatch_hook(self, callback) -> None:
        """Registers a callback invoked with final_report after every successful pipeline run.

        Args:
            callback: Callable[[dict], None] — receives the final_report dict.
        """
        self._post_dispatch_hooks.append(callback)
        logger.info(f"[IMERSPipeline] Post-dispatch hook registered ({len(self._post_dispatch_hooks)} total)")

    def run_audio(self, audio_path: str, incident_id: Optional[str] = None) -> dict:
        state = _initial_state(audio_path=audio_path, city_hint=self.city_hint, incident_id=incident_id)
        return self._invoke(state)

    def run_transcript(
        self,
        transcript:     str,
        incident_id:    Optional[str] = None,
        is_preliminary: bool          = False,
    ) -> dict:
        state = _initial_state(
            transcript=transcript,
            city_hint=self.city_hint,
            incident_id=incident_id,
            is_preliminary=is_preliminary,
        )
        return self._invoke(state)

    def _invoke(self, state: IMERSState) -> dict:
        t0 = time.perf_counter()
        try:
            thread_id = state.get('incident_id') or 'default_thread'
            final_state = self.app.invoke(state, config={"thread_id": thread_id})
            self.last_state  = final_state
            elapsed          = time.perf_counter() - t0
            report           = final_state.get("final_report", {})
            report.setdefault("pipeline", {})["wall_clock_seconds"] = round(elapsed, 3)

            logger.info(f"[IMERSPipeline] Completed in {elapsed:.2f}s — "
                        f"status={report.get('status')}")

            if report.get("status") == "processed" and self._post_dispatch_hooks:
                import threading
                for hook in self._post_dispatch_hooks:
                    threading.Thread(
                        target=self._run_hook,
                        args=(hook, report),
                        daemon=True,
                    ).start()

            return report
        except Exception as e:
            logger.error(f"[IMERSPipeline] Unhandled error: {e}", exc_info=True)
            return {
                "status":       "error",
                "error":        str(e),
                "incident_id":  state.get("incident_id"),
            }

    @staticmethod
    def _run_hook(hook, report: dict) -> None:
        try:
            hook(report)
        except Exception as e:
            logger.error(f"[IMERSPipeline] Post-dispatch hook failed: {e}")