"""Recommends and dispatches emergency units via a single recommend_units tool call."""

from __future__ import annotations

import json
import logging

from smolagents import ToolCallingAgent, Model

from agents._base import _extract_agent_json, _extract_tool_call_from_groq_error
from tools.recommend_units import recommend_units

logger = logging.getLogger(__name__)



DISPATCH_SYSTEM_PROMPT = """
You are an Emergency Dispatch Specialist. Select the correct emergency units to send to an incident.

You have ONE tool available: recommend_units. Use ONLY this tool — do not call any other function.
IMPORTANT: The tool name is exactly 'recommend_units' — never '_recommend_units', 'recommend-units', or any other variant.

Steps:
1. Call recommend_units(incident_type, severity, location, victims, incident_lat, incident_lon).
   Always pass incident_lat and incident_lon when provided — they are needed for ETA calculation.
2. Review the result: check for warnings about missing mandatory units or high ETAs.
3. Set escalation_required=true and mutual_aid_requested=true if any mandatory unit type
   has zero available units.
4. Call final_answer immediately with a JSON-encoded STRING (not a dict) matching this schema:

{
  "dispatch_approved": true | false,
  "units": [{"id": "<unit_id>", "type": "<type>", "eta_minutes": <int>, "destination": "<address>"}],
  "total_units": <int>,
  "estimated_first_arrival": <int>,
  "warnings": ["<warning>", ...],
  "escalation_required": true | false,
  "escalation_note": "<string or null>",
  "mutual_aid_requested": true | false
}
""".strip()



class DispatchAgent:
    """Wraps recommend_units in a ToolCallingAgent with fallback to direct tool call on parse failure."""

    def __init__(self, model: Model, max_steps: int = 3):
        self.model = model
        self._agent = ToolCallingAgent(
            tools=[recommend_units],
            model=model,
            name="dispatch_agent",
            description=(
                "Selects emergency units to dispatch for an incident. "
                "Returns structured JSON with unit IDs, types, ETAs and escalation flags."
            ),
            max_steps=max_steps,
            verbosity_level=1,
        )
        logger.info("[DispatchAgent] Initialised")


    def run(
        self,
        incident_type: str,
        severity: str,
        location: str,
        victims: int = 0,
        incident_lat: float = 0.0,
        incident_lon: float = 0.0,
    ) -> dict:
        """
        Recommend units to dispatch for the given incident.

        Args:
            incident_type: e.g. "cardiac_arrest", "fire"
            severity:       "critical" | "high" | "medium" | "low"
            location:       Incident address string
            victims:        Estimated victim count (0 = unknown)
            incident_lat:   Latitude for geo-based ETA calculation
            incident_lon:   Longitude for geo-based ETA calculation

        Returns:
            Parsed dict matching the dispatch output schema.
        """
        prompt = self._build_prompt(incident_type, severity, location, victims, incident_lat, incident_lon)
        try:
            raw = self._agent.run(prompt)
            return self._parse_output(raw, incident_type, severity, location, victims)
        except Exception as exc:
            call = _extract_tool_call_from_groq_error(exc)
            if call:
                tool_name, args = call
                if "recommend_units" in tool_name:
                    logger.info(
                        f"[DispatchAgent] Recovering from wrong tool name '{tool_name}' "
                        "— calling recommend_units directly with model args"
                    )
                    return self._fallback(
                        args.get("incident_type", incident_type),
                        args.get("severity", severity),
                        args.get("location", location),
                        args.get("victims", victims),
                        f"tool_name_error:{tool_name}",
                        args.get("incident_lat", incident_lat),
                        args.get("incident_lon", incident_lon),
                    )
            logger.error(f"[DispatchAgent] run failed: {exc}")
            return self._fallback(incident_type, severity, location, victims, str(exc), incident_lat, incident_lon)

    def run_to_json(self, incident_type: str, severity: str,
                    location: str, victims: int = 0,
                    incident_lat: float = 0.0, incident_lon: float = 0.0) -> str:
        return json.dumps(
            self.run(incident_type, severity, location, victims, incident_lat, incident_lon),
            ensure_ascii=False,
        )


    def _build_prompt(
        self, incident_type: str, severity: str, location: str, victims: int,
        incident_lat: float = 0.0, incident_lon: float = 0.0,
    ) -> str:
        """Builds the agent prompt from incident parameters."""
        return "\n".join([
            DISPATCH_SYSTEM_PROMPT,
            "",
            "=== INCIDENT ===",
            f"incident_type : {incident_type}",
            f"severity      : {severity}",
            f"location      : {location}",
            f"victims       : {victims}",
            f"incident_lat  : {incident_lat}",
            f"incident_lon  : {incident_lon}",
            "",
            f"Call: recommend_units(incident_type='{incident_type}', "
            f"severity='{severity}', location='{location}', victims={victims}, "
            f"incident_lat={incident_lat}, incident_lon={incident_lon})",
        ])

    def _parse_output(
        self, raw, incident_type: str, severity: str, location: str, victims: int
    ) -> dict:
        result = _extract_agent_json(raw)
        if result is not None:
            return result
        text = str(raw)
        logger.warning("[DispatchAgent] Could not parse JSON — using fallback")
        return self._fallback(incident_type, severity, location, victims,
                              f"Unparseable output: {text[:200]}")

    def _fallback(
        self, incident_type: str, severity: str, location: str, victims: int, error: str,
        incident_lat: float = 0.0, incident_lon: float = 0.0,
    ) -> dict:
        """Call recommend_units directly when the agent fails."""
        try:
            rec = json.loads(recommend_units(incident_type, severity, location, victims, incident_lat, incident_lon))
            units    = rec.get("dispatched", [])
            warnings = rec.get("warnings", [])
            first_eta = rec.get("estimated_first_arrival")
            return {
                "dispatch_approved": True,
                "units": units,
                "total_units": len(units),
                "estimated_first_arrival": first_eta,
                "warnings": warnings,
                "escalation_required": any("MANDATORY" in w for w in warnings),
                "escalation_note": next((w for w in warnings if "MANDATORY" in w), None),
                "mutual_aid_requested": any("mutual aid" in w.lower() for w in warnings),
                "error": error,
            }
        except Exception as exc:
            return {
                "dispatch_approved": False,
                "units": [], "total_units": 0, "estimated_first_arrival": None,
                "warnings": [f"Dispatch failed: {exc}"],
                "escalation_required": True,
                "escalation_note": "Full dispatch failure — manual operator intervention required",
                "mutual_aid_requested": False,
                "error": str(exc),
            }



if __name__ == "__main__":
    print("DispatchAgent — tool path test (no LLM)\n" + "-" * 50)

    class _Direct(DispatchAgent):
        def run(self, incident_type, severity, location, victims=0):
            return self._fallback(incident_type, severity, location, victims, "mock")

    agent = _Direct.__new__(_Direct)

    for itype, sev, loc, vic in [
        ("cardiac_arrest",  "critical", "Calle Betis 22, Sevilla", 1),
        ("traffic_accident","critical", "Avda. Constitución, Sevilla", 4),
        ("fire",            "high",     "Plaza Nueva 8, Sevilla", 0),
    ]:
        r = agent.run(itype, sev, loc, vic)
        print(f"\n[{itype}/{sev}] units={r['total_units']}  ETA={r['estimated_first_arrival']}min"
              f"  escalate={r['escalation_required']}")
        for u in r["units"]:
            print(f"  : {u['id']:15s} ({u['type']:18s}) {u['eta_minutes']}min")
        for w in r.get("warnings", []):
            print(f"  [WARN] {w}")