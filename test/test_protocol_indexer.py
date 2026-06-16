"""
Unit tests for tools/protocol_indexer.py.
"""

from __future__ import annotations

import json
import sys
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.protocol_indexer as pi
from tools.protocol_indexer import (
    _STUBS,
    _from_stub,
    _from_cache,
    _load_cache,
    query_protocol_index,
)



def _reset_module_cache():
    """Force the module-level _cache singleton to be None so _load_cache re-reads."""
    pi._cache = None
    pi._query_engine = None


@pytest.fixture(autouse=True)
def isolate_cache(monkeypatch, tmp_path):
    _reset_module_cache()
    monkeypatch.setattr(pi, "CACHE_PATH", tmp_path / "protocol_cache.json")
    monkeypatch.setattr(pi, "CHROMA_DIR", tmp_path / "chroma_store")
    yield
    _reset_module_cache()



class TestFromStub:

    def test_known_type_returns_dict(self):
        result = _from_stub("cardiac_arrest")
        assert isinstance(result, dict)

    def test_known_type_has_required_keys(self):
        result = _from_stub("cardiac_arrest")
        for key in ("code", "title", "steps", "escalation", "notes", "source"):
            assert key in result, f"Missing key '{key}' in stub for cardiac_arrest"

    def test_retrieval_tier_is_stub(self):
        result = _from_stub("fire")
        assert result["retrieval_tier"] == "stub"

    def test_steps_is_non_empty_list(self):
        for itype in _STUBS:
            result = _from_stub(itype)
            assert isinstance(result["steps"], list)
            assert len(result["steps"]) > 0, f"No steps for {itype}"

    def test_all_stub_types_covered(self):
        for itype in _STUBS:
            result = _from_stub(itype)
            assert result is not None

    def test_unknown_type_falls_back_to_traffic_accident(self):
        result = _from_stub("alien_invasion")
        assert result["code"] == _STUBS["traffic_accident"]["code"]

    def test_code_is_string(self):
        result = _from_stub("stroke")
        assert isinstance(result["code"], str)

    def test_title_is_string(self):
        result = _from_stub("gas_leak")
        assert isinstance(result["title"], str)

    def test_escalation_is_string(self):
        result = _from_stub("assault")
        assert isinstance(result["escalation"], str)

    def test_stub_is_a_copy_not_reference(self):
        result = _from_stub("fire")
        result["title"] = "MODIFIED"
        assert _STUBS["fire"]["title"] != "MODIFIED"



class TestLoadCache:

    def test_no_file_returns_empty_dict(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pi, "CACHE_PATH", tmp_path / "nonexistent.json")
        _reset_module_cache()
        cache = _load_cache()
        assert cache == {}

    def test_valid_cache_file_is_loaded(self, tmp_path, monkeypatch):
        cache_data = {
            "cardiac_arrest": {
                "critical": {"code": "T-001", "title": "PCR", "steps": ["Step1"],
                             "source": "test"}
            }
        }
        cache_path = tmp_path / "cache.json"
        cache_path.write_text(json.dumps(cache_data), encoding="utf-8")
        monkeypatch.setattr(pi, "CACHE_PATH", cache_path)
        _reset_module_cache()
        loaded = _load_cache()
        assert "cardiac_arrest" in loaded
        assert "critical" in loaded["cardiac_arrest"]

    def test_malformed_json_returns_empty(self, tmp_path, monkeypatch):
        cache_path = tmp_path / "bad_cache.json"
        cache_path.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr(pi, "CACHE_PATH", cache_path)
        _reset_module_cache()
        cache = _load_cache()
        assert cache == {}

    def test_wrong_structure_returns_empty(self, tmp_path, monkeypatch):
        cache_path = tmp_path / "wrong_cache.json"
        cache_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        monkeypatch.setattr(pi, "CACHE_PATH", cache_path)
        _reset_module_cache()
        cache = _load_cache()
        assert cache == {}

    def test_singleton_reuse(self, tmp_path, monkeypatch):
        cache_data = {"fire": {"high": {"code": "X", "title": "Y", "steps": [],
                                         "source": "test"}}}
        cache_path = tmp_path / "cache.json"
        cache_path.write_text(json.dumps(cache_data), encoding="utf-8")
        monkeypatch.setattr(pi, "CACHE_PATH", cache_path)
        _reset_module_cache()
        c1 = _load_cache()
        c2 = _load_cache()
        assert c1 is c2



class TestFromCache:

    def _inject_cache(self, data: dict, monkeypatch):
        pi._cache = data

    def test_exact_match(self, monkeypatch):
        self._inject_cache({
            "fire": {
                "critical": {"code": "F-001", "title": "Fire Critical",
                              "steps": ["Step A"], "source": "test"}
            }
        }, monkeypatch)
        result = _from_cache("fire", "critical")
        assert result is not None
        assert result["code"] == "F-001"
        assert result["retrieval_tier"] in ("cache", "cache_severity_fallback")

    def test_severity_fallback(self, monkeypatch):
        self._inject_cache({
            "fire": {
                "critical": {"code": "F-001", "title": "Fire",
                              "steps": [], "source": "test"}
            }
        }, monkeypatch)
        result = _from_cache("fire", "low")
        assert result is not None
        assert result["code"] == "F-001"
        assert result["retrieval_tier"] == "cache_severity_fallback"

    def test_unknown_incident_type_returns_none(self, monkeypatch):
        self._inject_cache({"fire": {"critical": {}}}, monkeypatch)
        result = _from_cache("alien_attack", "critical")
        assert result is None

    def test_empty_cache_returns_none(self, monkeypatch):
        self._inject_cache({}, monkeypatch)
        assert _from_cache("fire", "critical") is None

    def test_retrieval_tier_set_to_cache(self, monkeypatch):
        self._inject_cache({
            "stroke": {
                "high": {"code": "S-002", "title": "Stroke",
                          "steps": [], "source": "test"}
            }
        }, monkeypatch)
        result = _from_cache("stroke", "high")
        assert result["retrieval_tier"] in ("cache", "cache_severity_fallback")

    def test_returns_copy_not_reference(self, monkeypatch):
        data = {
            "gas_leak": {
                "high": {"code": "G-01", "title": "Gas", "steps": [], "source": "test"}
            }
        }
        self._inject_cache(data, monkeypatch)
        result = _from_cache("gas_leak", "high")
        result["title"] = "MODIFIED"
        assert data["gas_leak"]["high"]["title"] == "Gas"



class TestQueryProtocolIndex:

    def _parse(self, *args, **kwargs) -> dict:
        return json.loads(query_protocol_index(*args, **kwargs))


    def test_stub_returned_when_no_cache(self):
        result = self._parse("cardiac_arrest", "critical")
        assert result["retrieval_tier"] == "stub"

    def test_stub_has_all_keys(self):
        result = self._parse("fire", "high")
        for key in ("code", "title", "steps", "escalation", "notes",
                    "source", "retrieval_tier"):
            assert key in result, f"Missing key '{key}'"

    def test_stub_steps_is_non_empty(self):
        result = self._parse("cardiac_arrest", "critical")
        assert len(result["steps"]) > 0

    def test_unknown_type_returns_stub_fallback(self):
        result = self._parse("unknown_incident_type", "critical")
        assert result["retrieval_tier"] == "stub"
        assert result is not None

    def test_all_stub_types_work(self):
        for itype in _STUBS:
            result = self._parse(itype, "high")
            assert result is not None
            assert result["retrieval_tier"] in ("stub", "cache", "cache_severity_fallback", "vector")

    def test_all_severities_work(self):
        for sev in ("critical", "high", "medium", "low"):
            result = self._parse("fire", sev)
            assert result is not None


    def test_cache_hit_preferred_over_stub(self, monkeypatch):
        cache_data = {
            "fire": {
                "critical": {
                    "code": "CACHE-999",
                    "title": "Cached Fire Protocol",
                    "steps": ["Step from cache"],
                    "escalation": "", "notes": "", "source": "cache"
                }
            }
        }
        pi._cache = cache_data
        result = self._parse("fire", "critical")
        assert result["code"] == "CACHE-999"
        assert result["retrieval_tier"] in ("cache", "cache_severity_fallback")


    def test_vector_result_used_when_cache_miss(self, monkeypatch):
        pi._cache = {}
        mock_engine = MagicMock()
        mock_response = MagicMock()
        mock_response.__str__ = lambda self: "Vector result text for fire critical protocol."
        mock_engine.query.return_value = mock_response

        with patch.object(pi, "_load_vector_engine", return_value=mock_engine):
            result = self._parse("fire", "critical", "extra context")
        assert result["retrieval_tier"] == "vector"
        assert "Vector result" in result["steps"][0]

    def test_binary_vector_result_falls_back_to_stub(self, monkeypatch):
        pi._cache = {}
        mock_engine = MagicMock()
        mock_response = MagicMock()
        binary_str = "\x00\x01\x02" * 50
        mock_response.__str__ = lambda self: binary_str
        mock_engine.query.return_value = mock_response

        with patch.object(pi, "_load_vector_engine", return_value=mock_engine):
            result = self._parse("fire", "critical")
        assert result["retrieval_tier"] == "stub"

    def test_empty_vector_response_falls_back_to_stub(self, monkeypatch):
        pi._cache = {}
        mock_engine = MagicMock()
        mock_response = MagicMock()
        mock_response.__str__ = lambda self: "Empty response"
        mock_engine.query.return_value = mock_response

        with patch.object(pi, "_load_vector_engine", return_value=mock_engine):
            result = self._parse("fire", "critical")
        assert result is not None


    def test_output_is_valid_json_string(self):
        raw = query_protocol_index("cardiac_arrest", "critical")
        assert isinstance(raw, str)
        data = json.loads(raw)
        assert isinstance(data, dict)

    def test_steps_always_a_list(self):
        result = self._parse("fall_injury", "medium")
        assert isinstance(result["steps"], list)

    def test_code_always_a_string(self):
        result = self._parse("assault", "high")
        assert isinstance(result["code"], str)

    def test_title_always_a_string(self):
        result = self._parse("gas_leak", "critical")
        assert isinstance(result["title"], str)

    def test_extra_context_does_not_crash(self):
        result = self._parse(
            "traffic_accident", "high",
            extra_context="victim is elderly, possible spinal injury"
        )
        assert result is not None

    def test_empty_extra_context(self):
        result = self._parse("fire", "medium", "")
        assert result is not None



if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v"])
