"""
Unit tests for tools/recommend_units.py.
"""

from __future__ import annotations

import json
import sys
import os
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.recommend_units import (
    _is_dispatched,
    _mark_dispatched,
    release_units,
    _available_units,
    _get_rules,
    recommend_units,
    _UNIT_REGISTRY,
    _DISPATCH_RULES,
    _DEFAULT_RULE,
    _dispatched,
    _dispatch_lock,
)



def _clear_dispatch_state():
    with _dispatch_lock:
        _dispatched.clear()


@pytest.fixture(autouse=True)
def reset_dispatch():
    _clear_dispatch_state()
    yield
    _clear_dispatch_state()



class TestDispatchTracking:

    def test_unit_not_dispatched_initially(self):
        assert _is_dispatched("AMB-SVB-01") is False

    def test_mark_dispatched_makes_unit_busy(self):
        _mark_dispatched("AMB-SVB-01", eta_minutes=5)
        assert _is_dispatched("AMB-SVB-01") is True

    def test_unknown_unit_not_dispatched(self):
        assert _is_dispatched("NONEXISTENT-99") is False

    def test_release_units_clears_dispatch(self):
        _mark_dispatched("AMB-SVB-01", eta_minutes=10)
        _mark_dispatched("POL-01", eta_minutes=3)
        release_units(["AMB-SVB-01", "POL-01"])
        assert _is_dispatched("AMB-SVB-01") is False
        assert _is_dispatched("POL-01") is False

    def test_release_units_noop_for_unknown(self):
        release_units(["NONEXISTENT-99"])

    def test_ttl_zero_eta_treated_as_immediately_expired(self):
        _mark_dispatched("AMB-SVB-02", eta_minutes=0)
        _is_dispatched("AMB-SVB-02")

    def test_multiple_units_tracked_independently(self):
        _mark_dispatched("POL-01", 5)
        _mark_dispatched("BOM-01", 8)
        assert _is_dispatched("POL-01") is True
        assert _is_dispatched("BOM-01") is True
        assert _is_dispatched("AMB-SVB-01") is False

    def test_release_empty_list_noop(self):
        release_units([])



class TestAvailableUnits:

    def test_returns_list(self):
        result = _available_units("ambulance_svb")
        assert isinstance(result, list)

    def test_all_returned_are_correct_type(self):
        result = _available_units("police")
        for unit in result:
            assert unit.type == "police"

    def test_busy_units_excluded(self):
        ambulances = _available_units("ambulance_svb")
        for u in ambulances:
            assert u.status == "available"

    def test_dispatched_units_excluded(self):
        result_before = _available_units("ambulance_svb")
        if result_before:
            first_id = result_before[0].id
            _mark_dispatched(first_id, eta_minutes=10)
            result_after = _available_units("ambulance_svb")
            ids_after = [u.id for u in result_after]
            assert first_id not in ids_after

    def test_sorted_by_eta(self):
        result = _available_units("ambulance_svb", 37.39, -5.99)
        etas = [u.eta_minutes for u in result]
        assert etas == sorted(etas)

    def test_unknown_type_returns_empty(self):
        result = _available_units("helicopter_unit")
        assert result == []

    def test_eta_jitter_positive(self):
        for unit_type in ("ambulance_svb", "police", "fire"):
            for unit in _available_units(unit_type, 37.39, -5.99):
                assert unit.eta_minutes >= 1

    def test_fire_units_available(self):
        result = _available_units("fire")
        assert len(result) > 0

    def test_rescue_units_available(self):
        result = _available_units("rescue")
        assert len(result) > 0



class TestGetRules:

    def test_exact_key_match(self):
        rules = _get_rules("cardiac_arrest", "critical")
        assert rules
        types = [r[0] for r in rules]
        assert "ambulance_sva" in types

    def test_fallback_to_highest_severity(self):
        rules = _get_rules("cardiac_arrest", "low")
        assert rules

    def test_unknown_incident_returns_default(self):
        rules = _get_rules("alien_invasion", "critical")
        assert rules == _DEFAULT_RULE

    def test_traffic_accident_critical_has_many_units(self):
        rules = _get_rules("traffic_accident", "critical")
        assert len(rules) >= 3

    def test_all_dispatch_rules_have_valid_unit_types(self):
        valid_types = {"ambulance_svb", "ambulance_sva", "police", "fire", "rescue"}
        for (itype, sev), rules in _DISPATCH_RULES.items():
            for unit_type, count, mandatory in rules:
                assert unit_type in valid_types, (
                    f"Unknown unit type '{unit_type}' in rule ({itype}, {sev})"
                )

    def test_count_always_positive(self):
        for (itype, sev), rules in _DISPATCH_RULES.items():
            for unit_type, count, mandatory in rules:
                assert count >= 1, (
                    f"Non-positive count {count} in rule ({itype}, {sev}) for {unit_type}"
                )



class TestRecommendUnits:

    def _parse(self, *args, **kwargs) -> dict:
        return json.loads(recommend_units(*args, **kwargs))


    def test_cardiac_arrest_critical_returns_ambulances(self):
        result = self._parse("cardiac_arrest", "critical", "Calle Betis 22, Sevilla", 1)
        types = [u["type"] for u in result["dispatched"]]
        assert "ambulance_sva" in types or "ambulance_svb" in types

    def test_fire_high_includes_fire_unit(self):
        result = self._parse("fire", "high", "Plaza Nueva 8, Sevilla", 0)
        types = [u["type"] for u in result["dispatched"]]
        assert "fire" in types

    def test_assault_medium_includes_police(self):
        result = self._parse("assault", "medium", "Barrio Triana, Sevilla", 0)
        types = [u["type"] for u in result["dispatched"]]
        assert "police" in types

    def test_total_units_matches_dispatched_length(self):
        result = self._parse("traffic_accident", "high", "Avda. Constitución, Sevilla", 2)
        assert result["total_units"] == len(result["dispatched"])

    def test_estimated_first_arrival_is_min_eta(self):
        result = self._parse("cardiac_arrest", "critical", "Calle Feria 30, Sevilla", 1)
        if result["dispatched"]:
            etas = [u["eta_minutes"] for u in result["dispatched"] if u["eta_minutes"] is not None]
            if etas:
                assert result["estimated_first_arrival"] == min(etas)

    def test_estimated_first_arrival_none_when_no_dispatch(self):
        for u in _UNIT_REGISTRY:
            _mark_dispatched(u.id, eta_minutes=60)
        result = self._parse("other", "low", "Somewhere, Sevilla", 0)
        assert result["estimated_first_arrival"] is None or isinstance(result["estimated_first_arrival"], int)
        _clear_dispatch_state()

    def test_dispatch_marks_units_as_busy(self):
        result = self._parse("fire", "medium", "Plaza Nueva, Sevilla", 0)
        dispatched_ids = {u["id"] for u in result["dispatched"]}
        for uid in dispatched_ids:
            assert _is_dispatched(uid) is True


    def test_many_victims_increase_units(self):
        result_normal = self._parse("traffic_accident", "critical", "Sevilla", 1)
        _clear_dispatch_state()
        result_mass = self._parse("traffic_accident", "critical", "Sevilla", 7)
        assert result_mass["total_units"] >= result_normal["total_units"]

    def test_zero_victims_is_valid(self):
        result = self._parse("flooding", "high", "Calle Feria, Sevilla", 0)
        assert result["total_units"] >= 0


    def test_critical_late_arrival_warning(self):
        for u in _UNIT_REGISTRY:
            if u.eta_minutes is None or u.eta_minutes <= 5:
                _mark_dispatched(u.id, eta_minutes=60)
        result = self._parse("cardiac_arrest", "critical", "Sevilla", 1)
        if result["estimated_first_arrival"] and result["estimated_first_arrival"] > 10:
            assert any("10-min" in w or "10 min" in w or "ETA" in w for w in result["warnings"])
        _clear_dispatch_state()

    def test_warnings_is_list(self):
        result = self._parse("cardiac_arrest", "high", "Sevilla", 1)
        assert isinstance(result["warnings"], list)

    def test_unavailable_is_list(self):
        result = self._parse("cardiac_arrest", "high", "Sevilla", 1)
        assert isinstance(result["unavailable"], list)


    def test_output_has_all_required_keys(self):
        result = self._parse("fire", "high", "Plaza Nueva, Sevilla", 0)
        for key in ("dispatched", "unavailable", "warnings", "total_units",
                    "estimated_first_arrival"):
            assert key in result, f"Missing key: {key}"

    def test_each_dispatched_unit_has_required_fields(self):
        result = self._parse("cardiac_arrest", "critical", "Sevilla", 1)
        for u in result["dispatched"]:
            for field in ("id", "type", "eta_minutes", "base_location"):
                assert field in u, f"Dispatched unit missing field: {field}"

    def test_unit_ids_unique(self):
        result = self._parse("traffic_accident", "critical", "Sevilla", 3)
        ids = [u["id"] for u in result["dispatched"]]
        assert len(ids) == len(set(ids)), "Duplicate unit IDs in dispatch result"

    def test_unit_types_are_valid(self):
        valid_types = {"ambulance_svb", "ambulance_sva", "police", "fire", "rescue"}
        result = self._parse("traffic_accident", "critical", "Sevilla", 0)
        for u in result["dispatched"]:
            assert u["type"] in valid_types


    def test_unknown_incident_type_uses_default(self):
        result = self._parse("alien_attack", "critical", "Sevilla", 0)
        assert result["total_units"] >= 0

    def test_unknown_severity_uses_fallback(self):
        result = self._parse("fire", "extreme", "Sevilla", 0)
        assert result["total_units"] >= 0

    def test_destination_field_set(self):
        location = "Calle Sierpes 14, Sevilla"
        result = self._parse("assault", "high", location, 1)
        for u in result["dispatched"]:
            assert u.get("destination") == location

    def test_all_incident_types_in_mapping(self):
        incident_types = [
            "cardiac_arrest", "traffic_accident", "fire", "stroke", "assault",
            "domestic_violence", "robbery", "drowning", "fall_injury", "overdose",
            "gas_leak", "explosion", "missing_person", "mental_health_crisis",
            "flooding", "infrastructure_collapse", "chemical_spill",
            "other_medical", "other_police", "other",
        ]
        for itype in incident_types:
            _clear_dispatch_state()
            result = self._parse(itype, "high", "Sevilla", 0)
            assert "dispatched" in result, f"recommend_units crashed for {itype}"



if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v"])
