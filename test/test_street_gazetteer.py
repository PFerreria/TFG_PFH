"""Tests for the fuzzy street gazetteer and its extract_location integration.

All tests run offline: Nominatim is never called (geocoding is monkeypatched),
so they are deterministic and need no API keys. The transcripts come from the
real_audios recordings that previously failed location extraction.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib

el = importlib.import_module("tools.extract_location")
from tools.street_gazetteer import match_street, scan_transcript, street_center


class TestGazetteerData:
    def test_gazetteer_loads_with_coordinates(self):
        from tools.street_gazetteer import _load_index
        index = _load_index()
        assert len(index) > 1000, "gazetteer missing or too small — run tools/build_street_gazetteer.py"

    def test_street_center_within_sevilla_bounds(self):
        from tools.get_route import _in_sevilla_bounds
        center = street_center("Avenida de la Constitución")
        assert center is not None
        assert _in_sevilla_bounds(*center)


class TestMatchStreet:
    """Whisper mishearings from the real_audios recordings."""

    @pytest.mark.parametrize("heard, official", [
        ("calle Antonio Filipos Rojas 6", "Calle Antonio Filpo Rojas"),
        ("calle Caño de Carmona 5",       "Calle Caños de Carmona"),
        ("avenida del Greco 16",          "Avenida El Greco"),
        ("calle es Paco Gandía 2",        "Calle Paco Gandía"),
    ])
    def test_misheard_street_maps_to_official(self, heard, official):
        hit = match_street(heard)
        assert hit is not None, f"no match for {heard!r}"
        assert hit[0] == official

    def test_house_number_is_preserved(self):
        hit = match_street("calle Antonio Filipos Rojas 6")
        assert hit[1] == "6"

    def test_subset_match_finds_contained_street(self):
        hit = match_street("avenida del bajo Guadalquivir 4")
        assert hit is not None
        assert hit[0] == "Calle Guadalquivir"

    def test_nonexistent_street_returns_none(self):
        assert match_street("calle Catalanes 12") is None


class TestScanTranscript:
    def test_street_without_type_word(self):
        out = scan_transcript("Ha habido un accidente en Francisco de Ariño 28. Hay heridos.")
        assert out and out[0] == "Calle Francisco de Ariño 28"

    def test_misheard_avenue_name(self):
        out = scan_transcript(
            "Hola, buenas. Estoy en Gravita Constitución y hay aquí una persona "
            "que se ha desmayado por un golpe de calor."
        )
        assert "Avenida de la Constitución" in out

    def test_no_false_positives_on_generic_text(self):
        out = scan_transcript(
            "Por favor manden una ambulancia urgente, hay un herido grave y "
            "necesito ayuda inmediatamente."
        )
        assert out == []


class TestCandidateExtraction:
    def test_station_landmark(self):
        cands = el._extract_candidates(
            "Acaba de haber un incendio en la estación de tren en Santa Justa. Hay mucho humo."
        )
        assert "Santa Justa" in cands

    def test_calle_es_filler_removed(self):
        cands = el._extract_candidates(
            "Hola, me he dejado las llaves dentro. La calle es Paco Gandía 2, quinto izquierda."
        )
        assert any("calle Paco Gandía" in c for c in cands)

    def test_worded_highway(self):
        cands = el._extract_candidates(
            "Es un accidente muy grave en la autovía 4 kilómetro 120, sentido Cádiz."
        )
        assert "autovía A-4 kilómetro 120, Sevilla" in cands


class TestExtractLocationFuzzyIntegration:
    """extract_location with geocoding mocked: the fuzzy path must engage only
    after exact geocoding fails, and must fall back to gazetteer centers."""

    def test_fuzzy_result_marked_and_medium_confidence(self, monkeypatch):
        def fake_geocode(address, city_hint="Sevilla, España"):
            if "Antonio Filpo Rojas" in address:
                return {"address": "Calle Antonio Filpo Rojas, Sevilla", "latitude": 37.4,
                        "longitude": -5.97, "raw_query": address, "confidence": "high",
                        "is_midpoint": False}
            return None
        monkeypatch.setattr(el, "_geocode", fake_geocode)

        out = json.loads(el.extract_location(
            "Mi abuelo se ha caído por la escalera. Estoy en la calle Antonio Filipos Rojas, número 6."
        ))
        assert out["found"] is True
        assert out["fuzzy_matched"] is True
        assert out["confidence"] == "medium"
        assert "Antonio Filpo Rojas" in out["address"]

    def test_gazetteer_center_when_nominatim_fails(self, monkeypatch):
        monkeypatch.setattr(el, "_geocode", lambda *a, **k: None)
        out = json.loads(el.extract_location(
            "Hola, buenas. Estoy en Gravita Constitución y hay una persona desmayada."
        ))
        assert out["found"] is True
        assert out["fuzzy_matched"] is True
        assert "Avenida de la Constitución" in out["address"]
        assert out["is_midpoint"] is True
        assert out["latitude"] is not None

    def test_exact_match_bypasses_fuzzy(self, monkeypatch):
        def fake_geocode(address, city_hint="Sevilla, España"):
            return {"address": "Calle Feria, Sevilla", "latitude": 37.397,
                    "longitude": -5.991, "raw_query": address, "confidence": "high",
                    "is_midpoint": False}
        monkeypatch.setattr(el, "_geocode", fake_geocode)

        out = json.loads(el.extract_location(
            "Hay un incendio en la calle Feria número 32, Sevilla."
        ))
        assert out["found"] is True
        assert out["fuzzy_matched"] is False
        assert out["confidence"] == "high"

    def test_unresolvable_still_asks_operator(self, monkeypatch):
        monkeypatch.setattr(el, "_geocode", lambda *a, **k: None)
        monkeypatch.setattr(el, "street_center", lambda *a, **k: None)
        out = json.loads(el.extract_location(
            "Hay un incendio en la calle Catalanes número 12, en el centro."
        ))
        assert out["found"] is False
        assert out["address"] == "Dirección no localizada, operador aclare dirección"
        assert out["confidence"] == "low"


class TestGeocodeCache:
    def test_failures_are_not_cached(self, monkeypatch):
        """A transient Nominatim failure must not poison the cache."""
        el._geocode_success_cache.clear()
        calls = {"n": 0}

        class FlakyGeolocator:
            def geocode(self, *a, **k):
                calls["n"] += 1
                from geopy.exc import GeocoderTimedOut
                raise GeocoderTimedOut("transient")

        monkeypatch.setattr(el, "_get_geolocator", lambda: FlakyGeolocator())
        monkeypatch.setattr(el.time, "sleep", lambda *_: None)

        assert el._geocode_cached("Calle Prueba 1", "Sevilla, España") is None
        first_calls = calls["n"]
        assert el._geocode_cached("Calle Prueba 1", "Sevilla, España") is None
        assert calls["n"] > first_calls, "failed lookup was cached — retry never reached Nominatim"
        assert not el._geocode_success_cache
