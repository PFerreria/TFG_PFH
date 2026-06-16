"""Geocodes the incident location and computes parallel routes for all dispatched units."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from smolagents import ToolCallingAgent, Model

from agents._base import _extract_agent_json
from tools.extract_location import extract_location
from tools.get_route        import get_route

logger = logging.getLogger(__name__)



GEO_SYSTEM_PROMPT = """
You are an Emergency Geographic Specialist. Your only job is to resolve the incident location.
Unit routes are computed automatically after you return — do NOT call get_route.

Steps:
1. Call extract_location(transcript=<transcript>, city_hint=<city>) to geocode the location.
2. If found=false, retry ONCE with the best candidate string from the candidates list.
3. Whether or not the location was found, call final_answer immediately after step 1 or 2.
   Do NOT call extract_location more than twice total.

   final_answer(answer='{"location_resolved": true, "incident_address": "Calle Sierpes 12, Sevilla", "incident_lat": 37.3886, "incident_lon": -5.9953, "location_confidence": "high", "routes": [], "map_url": null, "warnings": []}')

   If both calls returned found=false, call final_answer with location_resolved=false:
   final_answer(answer='{"location_resolved": false, "incident_address": "<best candidate from candidates list>", "incident_lat": null, "incident_lon": null, "location_confidence": "low", "routes": [], "map_url": null, "warnings": ["Location not resolved — operator must clarify address"]}')

   The full schema for the answer string:
   {
     "location_resolved": true | false,
     "incident_address": "<resolved address or best candidate>",
     "incident_lat": <float | null>,
     "incident_lon": <float | null>,
     "location_confidence": "high" | "medium" | "low",
     "routes": [],
     "map_url": null,
     "warnings": ["<warning if any>"]
   }

CRITICAL:
  - Always use final_answer(answer='...json...') — with answer= as a named argument.
  - NEVER call final_answer({"key": value}) — passing a dict directly causes a 400 error.
  - Call final_answer exactly once. Do not call get_route.
  - Call extract_location at most twice — then call final_answer regardless of result.
""".strip()



class GeoAgent:
    """Wraps extract_location and get_route; skips LLM geocoding when coordinates are pre-resolved."""

    def __init__(self, model: Model, max_steps: int = 5):
        self.model = model
        self._agent = ToolCallingAgent(
            tools=[extract_location, get_route],
            model=model,
            name="geo_agent",
            description=(
                "Geocodes an incident location from a call transcript and "
                "computes the fastest route for each dispatched unit. "
                "Returns coordinates, address confidence, per-unit ETAs and a map link."
            ),
            max_steps=max_steps,
            verbosity_level=1,
        )
        logger.info("[GeoAgent] Initialised")


    def run(
        self,
        transcript: str,
        units: list[dict],
        city_hint: str = "Sevilla, España",
        known_lat: float | None = None,
        known_lon: float | None = None,
        known_address: str | None = None,
        known_confidence: str | None = None,
        known_is_midpoint: bool = False,
    ) -> dict:
        """
        Resolve location and compute routes for all units.

        Args:
            transcript:        Raw emergency call transcript (used for location
                               extraction when coordinates are not yet known).
            units:             List of unit dicts, each with 'id' and 'base_location'.
            city_hint:         City used to disambiguate partial addresses.
            known_lat:         Latitude already resolved by nlp_node (optional).
            known_lon:         Longitude already resolved by nlp_node (optional).
            known_address:     Address string already resolved by nlp_node (optional).
            known_confidence:  Confidence level of the pre-resolved location.

        Returns:
            Parsed dict matching the geo output schema.
        """
        if known_lat is not None and known_lon is not None:
            logger.info(
                f"[GeoAgent] Location pre-resolved ({known_confidence}) at "
                f"{known_lat},{known_lon} — skipping LLM geocoding"
            )
            warnings = []
            if known_is_midpoint:
                warnings.append(
                    f"No house number provided — ETA calculated to street midpoint of '{known_address}'. "
                    "Operator should confirm exact address."
                )
            result = {
                "location_resolved":   True,
                "incident_address":    known_address or "",
                "incident_lat":        known_lat,
                "incident_lon":        known_lon,
                "location_confidence": known_confidence or "medium",
                "routes":              [],
                "map_url":             None,
                "warnings":            warnings,
            }
        else:
            prompt = self._build_prompt(transcript, units, city_hint)
            try:
                raw = self._agent.run(prompt)
                result = self._parse_output(raw)
            except Exception as exc:
                logger.error(f"[GeoAgent] Agent run failed: {exc}")
                result = self._fallback(transcript, units, city_hint, str(exc))

        if not result.get("routes"):
            result["routes"] = self._compute_routes_parallel(
                units=units,
                destination_address=result.get("incident_address", ""),
                dest_lat=result.get("incident_lat") or 0.0,
                dest_lon=result.get("incident_lon") or 0.0,
            )

        if result.get("incident_lat") and result.get("incident_lon"):
            result["map_url"] = (
                f"https://www.google.com/maps?q="
                f"{result['incident_lat']},{result['incident_lon']}"
            )

        return result

    def run_to_json(self, transcript: str, units: list[dict],
                    city_hint: str = "Sevilla, España") -> str:
        return json.dumps(self.run(transcript, units, city_hint), ensure_ascii=False)


    def _compute_routes_parallel(
        self,
        units: list[dict],
        destination_address: str,
        dest_lat: float = 0.0,
        dest_lon: float = 0.0,
    ) -> list[dict]:
        """Routes all units in parallel via ThreadPoolExecutor to keep latency at max(single_route_time)."""
        if not units:
            return []

        def _route_one(unit: dict) -> dict:
            base = unit.get("base_location", "")
            try:
                raw = get_route(
                    origin_address=base,
                    destination_address=destination_address,
                    destination_lat=dest_lat,
                    destination_lon=dest_lon,
                )
                r = json.loads(raw)
                first_instr = ""
                if r.get("instructions"):
                    first_instr = r["instructions"][0].get("instruction", "")
                return {
                    "unit_id":          unit.get("id", "unknown"),
                    "unit_base":        base,
                    "distance_km":      r.get("distance_km"),
                    "eta_minutes":      r.get("duration_minutes"),
                    "backend":          r.get("backend"),
                    "first_instruction": first_instr,
                    "error":            r.get("error"),
                }
            except Exception as exc:
                return {
                    "unit_id":   unit.get("id", "unknown"),
                    "unit_base": base,
                    "distance_km": None, "eta_minutes": None,
                    "backend": "error", "first_instruction": "",
                    "error": str(exc),
                }

        results: list[dict] = [None] * len(units)
        with ThreadPoolExecutor(max_workers=min(len(units), 8)) as pool:
            future_to_idx = {pool.submit(_route_one, u): i for i, u in enumerate(units)}
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                results[idx] = future.result()

        return results


    def _build_prompt(self, transcript: str, units: list[dict], city_hint: str) -> str:
        """Build the agent prompt from transcript, unit list and city hint."""
        unit_lines = "\n".join(
            f"  - id={u.get('id')}  base={u.get('base_location', 'unknown')}"
            for u in units
        )
        return "\n".join([
            GEO_SYSTEM_PROMPT,
            "",
            "=== INPUT ===",
            f"city_hint : {city_hint}",
            "",
            "Transcript:",
            f"\"\"\"\n{transcript[:600]}\n\"\"\"",
            "",
            "Units to route:",
            unit_lines or "  (none)",
            "",
            f"Begin: call extract_location(transcript=<above transcript>, city_hint='{city_hint}')",
        ])

    def _parse_output(self, raw) -> dict:
        result = _extract_agent_json(raw)
        if result is not None:
            return result
        logger.warning("[GeoAgent] Could not parse JSON from agent output")
        return {}

    def _fallback(
        self, transcript: str, units: list[dict], city_hint: str, error: str
    ) -> dict:
        """Resolve location directly when the agent fails."""
        try:
            loc = json.loads(extract_location(transcript, city_hint))
        except Exception:
            loc = {"found": False, "address": None, "latitude": None,
                   "longitude": None, "confidence": "low", "is_midpoint": False}

        warnings = [f"Agent failed, used direct tool fallback: {error}"]
        if loc.get("is_midpoint"):
            warnings.append(
                f"No house number provided — ETA calculated to street midpoint of "
                f"'{loc.get('address')}'. Operator should confirm exact address."
            )

        return {
            "location_resolved": loc.get("found", False),
            "incident_address":  loc.get("address"),
            "incident_lat":      loc.get("latitude"),
            "incident_lon":      loc.get("longitude"),
            "location_confidence": loc.get("confidence", "low"),
            "routes":   [],
            "map_url":  None,
            "warnings": warnings,
        }



if __name__ == "__main__":
    print("GeoAgent — fallback/routing test (no LLM)\n" + "-" * 50)

    TRANSCRIPT = (
        "Ha habido un accidente muy grave en la Avenida de la Constitución "
        "esquina con Calle Sierpes en Sevilla. Hay tres heridos."
    )
    UNITS = [
        {"id": "AMB-SVA-01", "base_location": "Hospital Virgen del Rocío, Sevilla"},
        {"id": "POL-01",     "base_location": "Comisaría Central, Sevilla"},
        {"id": "BOM-01",     "base_location": "Parque Bomberos Sur, Sevilla"},
    ]

    class _Direct(GeoAgent):
        def run(self, transcript, units, city_hint="Sevilla, España"):
            result = self._fallback(transcript, units, city_hint, "mock")
            result["routes"] = self._compute_routes_parallel(
                units=units,
                destination_address=result.get("incident_address") or "Sevilla, España",
                dest_lat=result.get("incident_lat") or 0.0,
                dest_lon=result.get("incident_lon") or 0.0,
            )
            if result.get("incident_lat"):
                result["map_url"] = (
                    f"https://www.google.com/maps?q="
                    f"{result['incident_lat']},{result['incident_lon']}"
                )
            return result

    agent = _Direct.__new__(_Direct)
    result = agent.run(TRANSCRIPT, UNITS)

    print(f"Location resolved : {result['location_resolved']}")
    print(f"Address          : {result['incident_address']}")
    print(f"Coordinates      : {result['incident_lat']}, {result['incident_lon']}")
    print(f"Confidence       : {result['location_confidence']}")
    print(f"Map URL          : {result.get('map_url')}")
    print(f"\nRoutes ({len(result['routes'])}):")
    for r in result["routes"]:
        print(f"  {r['unit_id']:15s} | {r.get('distance_km')} km | "
              f"{r.get('eta_minutes')} min | via {r.get('backend')}")
        if r.get("error"):
            print(f"    [WARN] {r['error']}")