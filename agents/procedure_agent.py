"""Retrieves emergency protocols via RAG (cache → vector → stub) and produces an action checklist."""

from __future__ import annotations

import json
import logging
import os

from smolagents import ToolCallingAgent, tool, Model

from agents._base import _extract_agent_json, _extract_from_groq_error
from tools.protocol_indexer import query_protocol_index as _query_protocol_index

logger = logging.getLogger(__name__)


@tool
def fetch_protocol(incident_type: str, severity: str, extra_context: str = "") -> str:
    """Retrieves the emergency response protocol for a given incident type and severity.
    Checks a structured JSON cache first (fastest), then semantic vector search, then built-in stubs.
    Always returns a usable result even when no documents have been indexed.

    Args:
        incident_type: Emergency type — e.g. "cardiac_arrest", "traffic_accident", "fire",
                       "stroke", "gas_leak", "assault", "fall_injury", "flooding",
                       "explosion", "drowning", "overdose", "mental_health_crisis".
        severity: One of "critical", "high", "medium", "low".
        extra_context: Optional free-text to refine the search, e.g. "victim is a child"
                       or "incident inside a tunnel".

    Returns:
        JSON string with keys: code (str), title (str), steps (list of action strings),
        escalation (str — escalation criteria), notes (str — special considerations),
        protocol_text (str — full raw text), source ("cache"|"vector"|"stub").
    """
    return _query_protocol_index(incident_type, severity, extra_context)



PROCEDURE_SYSTEM_PROMPT = """
You are an Emergency Procedure Specialist. Retrieve the correct protocol and produce a prioritised action checklist.

You have ONE tool available: fetch_protocol. Use ONLY this tool — do not call any other function.

Steps:
1. Call fetch_protocol(incident_type=<type>, severity=<severity>[, extra_context=<context>]).
   fetch_protocol returns everything you need in a single call. Do NOT call it again.
2. Extract concrete, numbered action steps from the retrieved protocol.
3. Identify escalation criteria — set escalate_severity=true if severity should be upgraded.
4. Set hospital_prenotification=true for critical/high severity or when the protocol requires it.
5. Call final_answer immediately after step 1 — do not wait or retry.

   final_answer(answer='{"protocol_source": "rag", "severity_confirmed": "critical", "escalate_severity": false, "escalation_reason": null, "action_checklist": [{"step": 1, "action": "...", "priority": "immediate"}], "special_resources_needed": [], "hospital_prenotification": true}')

   The full schema for the answer string:
   {
     "protocol_source": "rag" | "stub",
     "severity_confirmed": "critical" | "high" | "medium" | "low",
     "escalate_severity": true | false,
     "escalation_reason": "<string or null>",
     "action_checklist": [
       {"step": 1, "action": "<imperative sentence>", "priority": "immediate" | "urgent" | "routine"}
     ],
     "special_resources_needed": ["<resource>", ...],
     "hospital_prenotification": true | false
   }

CRITICAL:
  - Always use final_answer(answer='...json...') — with answer= as a named argument.
  - NEVER call final_answer({"key": value}) — passing a dict directly causes a 400 error.
  - Do NOT include a "protocol_text" field — it causes JSON parse failures.
  - Call final_answer exactly once — do not output prose.
  - Call fetch_protocol exactly once — do not retry.
""".strip()



class ProcedureAgent:
    """Wraps fetch_protocol in a ToolCallingAgent; falls back to direct cache lookup on parse failure."""

    def __init__(self, model: Model, max_steps: int = 5):
        self.model = model
        self._agent = ToolCallingAgent(
            tools=[fetch_protocol],
            model=model,
            name="procedure_agent",
            description=(
                "Retrieves the emergency response protocol for an incident type and severity, "
                "then produces a prioritised action checklist with escalation flags, "
                "special resource requirements and hospital pre-notification status."
            ),
            max_steps=max_steps,
            verbosity_level=1,
        )
        logger.info("[ProcedureAgent] Initialised")


    def run(
        self,
        incident_type: str,
        severity: str,
        transcript: str = "",
        extra_context: str = "",
    ) -> dict:
        """
        Retrieve and interpret the protocol for the given incident.

        Args:
            incident_type: e.g. "traffic_accident", "cardiac_arrest"
            severity:       "critical" | "high" | "medium" | "low"
            transcript:     Original call transcript (used for LLM context only)
            extra_context:  Any additional details, e.g. "victim is a child"

        Returns:
            Parsed dict matching the output schema in PROCEDURE_SYSTEM_PROMPT.
            Falls back to a raw-protocol dict if Qwen output cannot be parsed.
        """
        prompt = self._build_prompt(incident_type, severity, transcript, extra_context)

        try:
            raw = self._agent.run(prompt)
            return self._parse_output(raw, incident_type, severity)
        except Exception as exc:
            recovered = _extract_from_groq_error(exc)
            if recovered:
                logger.info("[ProcedureAgent] Recovered answer from Groq failed_generation")
                return recovered
            logger.error(f"[ProcedureAgent] Agent run failed: {exc}")
            return self._fallback(incident_type, severity, str(exc))

    def run_to_json(self, incident_type: str, severity: str,
                    transcript: str = "", extra_context: str = "") -> str:
        return json.dumps(
            self.run(incident_type, severity, transcript, extra_context),
            ensure_ascii=False,
        )


    def _build_prompt(
        self,
        incident_type: str,
        severity: str,
        transcript: str,
        extra_context: str,
    ) -> str:
        """Build the agent prompt including incident details and optional transcript context."""
        parts = [
            PROCEDURE_SYSTEM_PROMPT,
            "",
            "=== CURRENT INCIDENT ===",
            f"incident_type : {incident_type}",
            f"severity      : {severity}",
        ]
        if transcript:
            parts += ["", f"Call transcript:\n\"\"\"\n{transcript[:1500]}\n\"\"\""]
        if extra_context:
            parts += ["", f"Additional context: {extra_context}"]
        parts += [
            "",
            f"Begin: call fetch_protocol(incident_type='{incident_type}', severity='{severity}'"
            + (f", extra_context='{extra_context}')" if extra_context else ")"),
        ]
        return "\n".join(parts)

    def _parse_output(self, raw: str, incident_type: str, severity: str) -> dict:
        result = _extract_agent_json(raw)
        if result is not None:
            return result
        text = str(raw)
        logger.warning("[ProcedureAgent] Could not parse JSON from agent output")
        return self._fallback(incident_type, severity, f"Unparseable output: {text[:200]}")

    @staticmethod
    def _is_clean_step(s: object) -> bool:
        """Filters out ChromaDB context strings and binary/OCR garbage from the steps list."""
        if not isinstance(s, str) or not s.strip():
            return False
        if s.lstrip().startswith("Context information is below"):
            return False
        ctrl_count = sum(
            1 for c in s
            if ord(c) < 0x20 and c not in "\t\n\r"
        )
        return ctrl_count <= 3

    @staticmethod
    def _sanitize_text(s: str) -> str:
        """Strip non-printable control chars from a protocol text string."""
        if not isinstance(s, str):
            return ""
        return "".join(
            c for c in s
            if ord(c) >= 0x20 or c in "\t\n\r"
        ).strip()

    def _fallback(self, incident_type: str, severity: str, error: str, transcript: str = "") -> dict:
        """Directly queries the protocol cache, skipping the LLM, when agent parsing fails."""
        try:
            protocol_raw = json.loads(fetch_protocol(incident_type, severity))
            title      = protocol_raw.get("title", "")
            raw_steps  = protocol_raw.get("steps", [])
            escalation = protocol_raw.get("escalation", "")
            notes      = protocol_raw.get("notes", "")
            raw_text   = protocol_raw.get("protocol_text", "") or title or ""

            steps = [s for s in raw_steps if self._is_clean_step(s)]

            text = self._sanitize_text(raw_text) or title or "Protocol unavailable"
        except Exception:
            title = steps = escalation = notes = ""
            text  = "Protocol unavailable — fetch failed"

        if steps:
            checklist = [
                {
                    "step":     i + 1,
                    "action":   s if isinstance(s, str) else s.get("action", str(s)),
                    "priority": "immediate" if i < 3 else "urgent",
                }
                for i, s in enumerate(steps)
            ]
        else:
            checklist = [
                {"step": 1,
                 "action": f"Activar protocolo {incident_type.replace('_', ' ')} — severidad {severity}",
                 "priority": "immediate"}
            ]

        res = {
            "incident_type":             incident_type,
            "severity":                  severity,
            "protocol_source":           "cache_fallback",
            "severity_confirmed":        severity,
            "escalate_severity":         False,
            "escalation_reason":         escalation or None,
            "action_checklist":          checklist,
            "key_actions":               [c["action"] for c in checklist],
            "escalation_criteria":       escalation,
            "special_resources_needed":  [],
            "hospital_prenotification":  severity in ("critical", "high"),
            "protocol_text":             text,
            "notes":                     notes,
            "error":                     error,
        }
        return res



if __name__ == "__main__":
    import sys

    print("ProcedureAgent — fallback path test (no LLM needed)\n" + "-" * 50)

    class _MockModel:
        pass

    class _ProcedureAgentDirect(ProcedureAgent):
        """Runs only the fetch_protocol tool directly, skipping CodeAgent."""
        def run(self, incident_type, severity, transcript="", extra_context=""):
            return self._fallback(incident_type, severity, "mock — no LLM")

    agent = _ProcedureAgentDirect.__new__(_ProcedureAgentDirect)
    agent.model = None

    tests = [
        ("cardiac_arrest", "critical", "Mi padre no respira y no tiene pulso."),
        ("traffic_accident", "high", "Accidente en Avenida de la Constitución, heridos leves."),
        ("fire", "critical", "Incendio en edificio, personas atrapadas."),
    ]

    for itype, sev, transcript in tests:
        result = agent.run(itype, sev, transcript)
        print(f"\n[{itype} / {sev}]")
        print(f"  Protocol source : {result['protocol_source']}")
        print(f"  Escalate        : {result['escalate_severity']}")
        print(f"  Hospital pre-notify: {result['hospital_prenotification']}")
        steps = result.get("action_checklist", [])
        for step in steps[:3]:
            print(f"  Step {step['step']}: {step['action']}")
        print(f"  Protocol (100c) : {result['protocol_text'][:100]}…")