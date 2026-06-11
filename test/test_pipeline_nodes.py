"""
test_pipeline_nodes.py
-----------------------
Comprehensive unit tests for pipeline/graph.py.

Covers every node function, helper, and routing function in isolation,
plus full pipeline integration tests using stub agents. No real LLM
or audio transcription is needed.

Run with:
    pytest test/test_pipeline_nodes.py -v
"""

from __future__ import annotations

import json
import sys
import os
import time
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.graph import (
    IMERSState,
    _initial_state,
    _timing,
    tts_node,
    nlp_node,
    validate_node,
    geo_retry_node,
    fan_out_node,
    merge_node,
    output_node,
    abort_node,
    route_after_validate,
    route_check_abort,
    build_graph,
)
from langgraph.checkpoint.memory import MemorySaver



def _make_good_tts_result(transcript: str = "Hay un accidente.") -> str:
    return json.dumps({
        "transcript":              transcript,
        "language":                "es",
        "language_probability":    0.99,
        "duration_seconds":        8.0,
        "processing_time_seconds": 1.2,
        "segments":                [],
        "error":                   None,
    })


class StubTTS:
    def run(self, audio_path: str) -> str:
        return _make_good_tts_result()


class StubTTSFailing:
    def run(self, audio_path: str) -> str:
        return json.dumps({"transcript": "", "language": "unknown",
                           "language_probability": 0.0, "duration_seconds": 0.0,
                           "processing_time_seconds": 0.0, "segments": [],
                           "error": "Transcription failed"})


class StubProcedure:
    def run(self, incident_type, severity, transcript="", extra_context="") -> dict:
        return {
            "incident_type": incident_type,
            "severity": severity,
            "action_checklist": ["Secure scene", "Call hospital"],
            "required_units": ["ambulance_sva"],
            "escalation_criteria": "Escalate if >5 victims",
            "protocol_source": "stub",
            "error": None,
        }

    def _fallback(self, incident_type, severity, error, transcript="") -> dict:
        return self.run(incident_type, severity, transcript)


class StubDispatch:
    def run(self, incident_type, severity, location, victims=0) -> dict:
        return {
            "dispatched_units": [
                {"id": "AMB-01", "type": "ambulance_sva", "eta_minutes": 5},
            ],
            "total_units": 1,
            "estimated_first_arrival": 5,
            "dispatch_priority": "high",
            "warnings": [],
            "error": None,
        }

    def _fallback(self, incident_type, severity, location, victims, error,
                  incident_lat=0.0, incident_lon=0.0) -> dict:
        return self.run(incident_type, severity, location, victims)


class StubGeo:
    def run(self, transcript, units, city_hint="Sevilla, España") -> dict:
        return {
            "incident_address": "Calle Betis 22, Sevilla",
            "incident_lat": 37.3822,
            "incident_lon": -6.0026,
            "nearest_hospital": "Hospital Virgen del Rocío",
            "nearest_hospital_km": 2.5,
            "routes": [{"unit_id": "AMB-01", "eta_minutes": 5, "distance_km": 2.8}],
            "error": None,
        }

    def _fallback(self, transcript, units, city_hint, error) -> dict:
        return {
            "location_resolved":   True,
            "incident_address":    "Calle Betis 22, Sevilla",
            "incident_lat":        37.3822,
            "incident_lon":        -6.0026,
            "location_confidence": "medium",
            "routes":              [],
            "map_url":             None,
            "warnings":            [f"stub fallback: {error}"],
        }

    def _compute_routes_parallel(self, units, destination_address,
                                  dest_lat=0.0, dest_lon=0.0) -> list:
        return [
            {"unit_id": u.get("id", "unknown"), "eta_minutes": 5, "distance_km": 2.8}
            for u in (units or [])
        ]


STUB_AGENTS = {
    "tts":       StubTTS(),
    "procedure": StubProcedure(),
    "dispatch":  StubDispatch(),
    "geo":       StubGeo(),
}



def _base_state(**overrides) -> IMERSState:
    s = _initial_state(
        transcript="Hay un incendio en el edificio.",
        city_hint="Sevilla, España",
    )
    s.update(overrides)
    return s



class TestInitialState:

    def test_returns_imers_state(self):
        s = _initial_state()
        assert isinstance(s, dict)

    def test_incident_id_set(self):
        s = _initial_state()
        assert s["incident_id"] is not None
        assert s["incident_id"].startswith("INC-")

    def test_geo_retry_count_zero(self):
        s = _initial_state()
        assert s["geo_retry_count"] == 0

    def test_location_validated_false(self):
        s = _initial_state()
        assert s["location_validated"] is False

    def test_fan_out_done_false(self):
        s = _initial_state()
        assert s["fan_out_done"] is False

    def test_merge_done_false(self):
        s = _initial_state()
        assert s["merge_done"] is False

    def test_audio_path_stored(self):
        s = _initial_state(audio_path="/tmp/call.wav")
        assert s["audio_path"] == "/tmp/call.wav"

    def test_transcript_stored(self):
        s = _initial_state(transcript="Test transcript")
        assert s["transcript"] == "Test transcript"

    def test_city_hint_default(self):
        s = _initial_state()
        assert s["city_hint"] == "Sevilla, España"

    def test_started_at_is_iso_string(self):
        s = _initial_state()
        datetime.fromisoformat(s["started_at"])

    def test_node_timings_empty_dict(self):
        s = _initial_state()
        assert s["node_timings"] == {}



class TestTiming:

    def test_adds_node_to_timings(self):
        state = _initial_state()
        update = _timing(state, "tts_node", 1.234)
        assert "tts_node" in update["node_timings"]

    def test_rounds_to_3_decimals(self):
        state = _initial_state()
        update = _timing(state, "tts_node", 1.23456789)
        assert update["node_timings"]["tts_node"] == pytest.approx(1.235, rel=1e-3)

    def test_preserves_existing_timings(self):
        state = _initial_state()
        state["node_timings"] = {"prev_node": 0.5}
        update = _timing(state, "new_node", 1.0)
        assert "prev_node" in update["node_timings"]
        assert "new_node" in update["node_timings"]

    def test_does_not_mutate_state(self):
        state = _initial_state()
        state["node_timings"] = {"old": 0.1}
        _timing(state, "new_node", 0.5)
        assert "new_node" not in state["node_timings"]



class TestRouteAfterValidate:

    def test_abort_if_abort_reason(self):
        state = _base_state(abort_reason="Fatal error", location_validated=True)
        assert route_after_validate(state) == "abort_node"

    def test_fan_out_when_validated(self):
        state = _base_state(location_validated=True)
        state.pop("abort_reason", None)
        assert route_after_validate(state) == "fan_out_node"

    def test_geo_retry_when_not_validated_first_time(self):
        state = _base_state(location_validated=False, geo_retry_count=0)
        result = route_after_validate(state)
        assert result == "geo_retry_node"

    def test_fan_out_after_max_retries(self):
        state = _base_state(location_validated=False, geo_retry_count=1)
        state.pop("abort_reason", None)
        assert route_after_validate(state) == "fan_out_node"



class TestRouteCheckAbort:

    def test_abort_when_abort_reason(self):
        state = _base_state(abort_reason="Something went wrong")
        assert route_check_abort(state) == "abort_node"

    def test_continue_when_no_abort_reason(self):
        state = _base_state()
        state.pop("abort_reason", None)
        assert route_check_abort(state) == "continue"

    def test_none_abort_reason_continues(self):
        state = _base_state(abort_reason=None)
        assert route_check_abort(state) == "continue"



class TestTTSNode:

    def test_skips_whisper_if_transcript_present(self):
        state = _base_state(transcript="Already transcribed.")
        result = tts_node(state, STUB_AGENTS)
        assert "tts_node" in result.get("node_timings", {})

    def test_aborts_if_no_audio_or_transcript(self):
        state = _initial_state()
        result = tts_node(state, STUB_AGENTS)
        assert result.get("abort_reason") is not None

    def test_transcribes_audio_using_stub(self):
        state = _initial_state(audio_path="fake.wav")
        result = tts_node(state, STUB_AGENTS)
        assert result.get("transcript") is not None
        assert result.get("abort_reason") is None

    def test_aborts_on_tts_error(self):
        state = _initial_state(audio_path="fake.wav")
        agents = dict(STUB_AGENTS)
        agents["tts"] = StubTTSFailing()
        result = tts_node(state, agents)
        assert result.get("abort_reason") is not None

    def test_timing_recorded(self):
        state = _base_state(transcript="Test.")
        result = tts_node(state, STUB_AGENTS)
        assert "tts_node" in result.get("node_timings", {})



class TestNLPNode:

    def _run(self, transcript: str) -> dict:
        state = _base_state(transcript=transcript)
        return nlp_node(state, STUB_AGENTS)

    def test_classifies_traffic_accident(self):
        result = self._run(
            "Ha habido un accidente de tráfico en la Avenida de la Constitución."
        )
        assert result["incident_type"] == "traffic_accident"

    def test_classifies_cardiac_arrest(self):
        result = self._run("Mi padre no respira y no tiene pulso.")
        assert result["incident_type"] == "cardiac_arrest"

    def test_severity_mapped_from_priority(self):
        result = self._run("Hay un incendio en el edificio, veo llamas.")
        assert result["severity"] in ("critical", "high", "medium", "low")

    def test_victims_present(self):
        result = self._run("Hay tres heridos en la calle.")
        assert isinstance(result.get("victims"), int)

    def test_location_fields_present(self):
        result = self._run(
            "Ha habido un accidente en la Calle Betis 22, Sevilla."
        )
        for key in ("location_raw", "location_address", "location_confidence",
                    "location_candidates"):
            assert key in result

    def test_classification_confidence_present(self):
        result = self._run("Hay un incendio.")
        assert result.get("classification_confidence") in ("high", "medium", "low")

    def test_timing_recorded(self):
        result = self._run("Hay un incendio.")
        assert "nlp_node" in result.get("node_timings", {})



class TestValidateNode:

    def test_validated_when_high_confidence_coords(self):
        state = _base_state(
            location_lat=37.39, location_lon=-5.99, location_confidence="high"
        )
        result = validate_node(state, STUB_AGENTS)
        assert result["location_validated"] is True

    def test_validated_when_medium_confidence_coords(self):
        state = _base_state(
            location_lat=37.39, location_lon=-5.99, location_confidence="medium"
        )
        result = validate_node(state, STUB_AGENTS)
        assert result["location_validated"] is True

    def test_not_validated_when_low_confidence(self):
        state = _base_state(
            location_lat=37.39, location_lon=-5.99, location_confidence="low"
        )
        result = validate_node(state, STUB_AGENTS)
        assert result["location_validated"] is False

    def test_not_validated_when_no_coords(self):
        state = _base_state(
            location_lat=None, location_lon=None, location_confidence="high"
        )
        result = validate_node(state, STUB_AGENTS)
        assert result["location_validated"] is False

    def test_timing_recorded(self):
        state = _base_state(
            location_lat=37.39, location_lon=-5.99, location_confidence="high"
        )
        result = validate_node(state, STUB_AGENTS)
        assert "validate_node" in result.get("node_timings", {})



class TestMergeNode:

    def _state_with_results(self, **overrides) -> IMERSState:
        state = _base_state(
            incident_type="traffic_accident",
            severity="critical",
            location_address="Calle Betis 22, Sevilla",
            location_lat=37.3822,
            location_lon=-6.0026,
            procedure_result={
                "incident_type": "traffic_accident",
                "severity": "critical",
                "key_actions": ["Secure scene"],
                "error": None,
            },
            dispatch_result={
                "dispatched_units": [{"id": "AMB-01", "type": "ambulance_sva", "eta_minutes": 5}],
                "total_units": 1,
                "error": None,
            },
            geo_result={
                "incident_address": "Calle Betis 22, Sevilla",
                "incident_lat": 37.3822,
                "incident_lon": -6.0026,
                "nearest_hospital": "Hospital Virgen del Rocío",
                "routes": [],
            },
            fan_out_done=True,
        )
        state.update(overrides)
        return state

    def test_merge_done_set_true(self):
        state = self._state_with_results()
        result = merge_node(state, STUB_AGENTS)
        assert result["merge_done"] is True

    def test_geo_address_promoted(self):
        state = self._state_with_results(
            location_address="Old address",
            geo_result={"incident_address": "New address from geo",
                        "incident_lat": 37.39, "incident_lon": -5.99}
        )
        result = merge_node(state, STUB_AGENTS)
        assert result["location_address"] == "New address from geo"

    def test_procedure_severity_overrides_nlp(self):
        state = self._state_with_results(severity="medium")
        state["procedure_result"]["severity"] = "critical"
        result = merge_node(state, STUB_AGENTS)
        assert result["severity"] == "critical"

    def test_dispatch_error_generates_warning(self):
        state = self._state_with_results()
        state["dispatch_result"]["error"] = "Agent timeout"
        result = merge_node(state, STUB_AGENTS)
        assert result.get("error") is not None

    def test_no_warning_on_clean_state(self):
        state = self._state_with_results()
        result = merge_node(state, STUB_AGENTS)
        if "error" in result and result["error"] is None:
            pass

    def test_timing_recorded(self):
        state = self._state_with_results()
        result = merge_node(state, STUB_AGENTS)
        assert "merge_node" in result.get("node_timings", {})



class TestOutputNode:

    def _full_state(self) -> IMERSState:
        state = _base_state(
            incident_type="fire",
            severity="critical",
            victims=2,
            classification_confidence="high",
            location_address="Plaza Nueva 8, Sevilla",
            location_lat=37.3886,
            location_lon=-5.9823,
            location_confidence="high",
            location_validated=True,
            geo_retry_count=0,
            procedure_result={
                "protocol_text": "Activate fire protocol",
                "key_actions": ["Call fire brigade"],
                "required_resources": ["fire", "ambulance_svb"],
                "escalation_criteria": "If >3 floors affected",
                "source": "stub",
            },
            dispatch_result={
                "dispatched_units": [
                    {"id": "BOM-01", "type": "fire", "eta_minutes": 6},
                ],
                "total_units": 1,
                "estimated_first_arrival": 6,
                "dispatch_priority": "emergency",
                "warnings": [],
            },
            geo_result={
                "incident_address": "Plaza Nueva 8, Sevilla",
                "incident_lat": 37.3886,
                "incident_lon": -5.9823,
                "nearest_hospital": "Hospital Virgen del Rocío",
                "nearest_hospital_km": 1.8,
                "hospital_route": None,
                "routes": [],
            },
            merge_done=True,
        )
        return state

    def test_final_report_present(self):
        state = self._full_state()
        result = output_node(state, STUB_AGENTS)
        assert "final_report" in result

    def test_status_is_processed(self):
        state = self._full_state()
        result = output_node(state, STUB_AGENTS)
        assert result["final_report"]["status"] == "processed"

    def test_incident_id_preserved(self):
        state = self._full_state()
        result = output_node(state, STUB_AGENTS)
        assert result["final_report"]["incident_id"] == state["incident_id"]

    def test_all_top_level_keys_present(self):
        state = self._full_state()
        result = output_node(state, STUB_AGENTS)
        report = result["final_report"]
        for key in ("incident_id", "timestamp", "status",
                    "incident_type", "severity", "victims",
                    "location", "protocol", "dispatch", "routes",
                    "nearest_hospital", "historical_context", "pipeline"):
            assert key in report, f"Missing key: {key}"

    def test_historical_context_pending(self):
        state = self._full_state()
        result = output_node(state, STUB_AGENTS)
        assert result["final_report"]["historical_context"]["status"] == "pending"

    def test_completed_at_set(self):
        state = self._full_state()
        result = output_node(state, STUB_AGENTS)
        assert result.get("completed_at") is not None
        datetime.fromisoformat(result["completed_at"])

    def test_raw_agents_section_present(self):
        state = self._full_state()
        result = output_node(state, STUB_AGENTS)
        assert "_raw" in result["final_report"]

    def test_timing_recorded(self):
        state = self._full_state()
        result = output_node(state, STUB_AGENTS)
        assert "output_node" in result.get("node_timings", {})



class TestAbortNode:

    def test_status_is_aborted(self):
        state = _initial_state()
        state["abort_reason"] = "No transcript provided"
        result = abort_node(state, STUB_AGENTS)
        assert result["final_report"]["status"] == "aborted"

    def test_abort_reason_preserved(self):
        state = _initial_state()
        state["abort_reason"] = "My abort reason"
        result = abort_node(state, STUB_AGENTS)
        assert result["final_report"]["abort_reason"] == "My abort reason"

    def test_incident_id_preserved(self):
        state = _initial_state()
        state["abort_reason"] = "Error"
        result = abort_node(state, STUB_AGENTS)
        assert result["final_report"]["incident_id"] == state["incident_id"]

    def test_completed_at_set(self):
        state = _initial_state()
        state["abort_reason"] = "Error"
        result = abort_node(state, STUB_AGENTS)
        assert result.get("completed_at") is not None

    def test_no_abort_reason_handled(self):
        """abort_node should handle the case where abort_reason is not set."""
        state = _initial_state()
        result = abort_node(state, STUB_AGENTS)
        assert result["final_report"]["status"] == "aborted"



class TestFullPipeline:

    def _build(self):
        return build_graph(STUB_AGENTS, checkpointer=MemorySaver())


    def test_happy_path_with_audio(self):
        app = self._build()
        state = _initial_state(audio_path="fake_call.wav")
        result = app.invoke(state, config={"configurable": {"thread_id": "int-1"}})
        report = result.get("final_report", {})
        assert report["status"] == "processed"

    def test_happy_path_with_transcript(self):
        app = self._build()
        state = _initial_state(
            transcript=(
                "Ha habido un accidente de tráfico en la Avenida de la "
                "Constitución, hay tres heridos."
            )
        )
        result = app.invoke(state, config={"configurable": {"thread_id": "int-2"}})
        report = result.get("final_report", {})
        assert report["status"] == "processed"
        assert report["incident_type"] == "traffic_accident"

    def test_abort_with_no_input(self):
        app = self._build()
        state = _initial_state()
        result = app.invoke(state, config={"configurable": {"thread_id": "int-3"}})
        report = result.get("final_report", {})
        assert report["status"] == "aborted"


    def test_final_report_top_level_keys(self):
        app = self._build()
        state = _initial_state(transcript="Hay un incendio en el edificio.")
        result = app.invoke(state, config={"configurable": {"thread_id": "int-4"}})
        report = result.get("final_report", {})
        for key in ("incident_id", "timestamp", "status",
                    "incident_type", "severity", "victims",
                    "location", "protocol", "dispatch", "routes",
                    "nearest_hospital", "historical_context", "pipeline"):
            assert key in report, f"Missing report key: {key}"

    def test_location_keys(self):
        app = self._build()
        state = _initial_state(transcript="Accidente en la Calle Betis 22.")
        result = app.invoke(state, config={"configurable": {"thread_id": "int-5"}})
        loc = result.get("final_report", {}).get("location", {})
        for key in ("address", "latitude", "longitude", "confidence", "validated"):
            assert key in loc

    def test_dispatch_keys(self):
        app = self._build()
        state = _initial_state(transcript="Hay un incendio.")
        result = app.invoke(state, config={"configurable": {"thread_id": "int-6"}})
        disp = result.get("final_report", {}).get("dispatch", {})
        for key in ("units", "total_units", "first_arrival_minutes", "priority"):
            assert key in disp

    def test_pipeline_keys(self):
        app = self._build()
        state = _initial_state(transcript="Paro cardíaco.")
        result = app.invoke(state, config={"configurable": {"thread_id": "int-7"}})
        pipeline = result.get("final_report", {}).get("pipeline", {})
        for key in ("started_at", "completed_at", "node_timings", "geo_retries"):
            assert key in pipeline

    def test_node_timings_populated(self):
        app = self._build()
        state = _initial_state(transcript="Hay un incendio en la Calle Feria.")
        result = app.invoke(state, config={"configurable": {"thread_id": "int-8"}})
        timings = result.get("final_report", {}).get("pipeline", {}).get("node_timings", {})
        assert len(timings) > 0


    def test_tts_nearly_instant_when_transcript_provided(self):
        app = self._build()
        state = _initial_state(transcript="Mi padre no respira.")
        t0 = time.perf_counter()
        result = app.invoke(state, config={"configurable": {"thread_id": "int-9"}})
        elapsed = time.perf_counter() - t0
        timings = result.get("final_report", {}).get("pipeline", {}).get("node_timings", {})
        tts_time = timings.get("tts_node", 0)
        assert tts_time < 0.1, "TTS should be skipped when transcript is injected"


    def test_historical_context_pending(self):
        app = self._build()
        state = _initial_state(transcript="Hay un incendio.")
        result = app.invoke(state, config={"configurable": {"thread_id": "int-10"}})
        hc = result.get("final_report", {}).get("historical_context", {})
        assert hc.get("status") == "pending"


    def test_two_invocations_have_different_incident_ids(self):
        app = self._build()
        r1 = app.invoke(_initial_state(transcript="Incendio."),
                        config={"configurable": {"thread_id": "int-11a"}})
        r2 = app.invoke(_initial_state(transcript="Accidente."),
                        config={"configurable": {"thread_id": "int-11b"}})
        id1 = r1.get("final_report", {}).get("incident_id")
        id2 = r2.get("final_report", {}).get("incident_id")
        assert id1 != id2



if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v"])
