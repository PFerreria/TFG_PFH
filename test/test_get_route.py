"""
Unit tests for tools/get_route.py.
"""

from __future__ import annotations

import json
import math
import sys
import os
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.get_route import (
    SEVILLA_BOUNDS,
    VEHICLE_SPEEDS_KMH,
    UNIT_BASES,
    _BASE_COORDS_CACHE,
    _in_sevilla_bounds,
    _haversine_km,
    _encode_polyline,
    _route_stub,
    _geocode_address,
    get_route,
)



class TestInSevillaBounds:

    def test_city_centre_is_inside(self):
        assert _in_sevilla_bounds(37.3886, -5.9823) is True

    def test_triana_inside(self):
        assert _in_sevilla_bounds(37.3822, -6.0026) is True

    def test_out_of_bounds_north(self):
        assert _in_sevilla_bounds(38.0, -5.98) is False

    def test_out_of_bounds_south(self):
        assert _in_sevilla_bounds(37.0, -5.98) is False

    def test_out_of_bounds_east(self):
        assert _in_sevilla_bounds(37.39, -5.5) is False

    def test_out_of_bounds_west(self):
        assert _in_sevilla_bounds(37.39, -6.5) is False

    def test_madrid_is_outside(self):
        assert _in_sevilla_bounds(40.4168, -3.7038) is False

    def test_boundary_lat_min(self):
        assert _in_sevilla_bounds(SEVILLA_BOUNDS["lat_min"], -5.98) is True

    def test_boundary_lat_max(self):
        assert _in_sevilla_bounds(SEVILLA_BOUNDS["lat_max"], -5.98) is True

    def test_boundary_lon_min(self):
        assert _in_sevilla_bounds(37.39, SEVILLA_BOUNDS["lon_min"]) is True

    def test_boundary_lon_max(self):
        assert _in_sevilla_bounds(37.39, SEVILLA_BOUNDS["lon_max"]) is True

    def test_just_outside_lat_min(self):
        assert _in_sevilla_bounds(SEVILLA_BOUNDS["lat_min"] - 0.001, -5.98) is False

    def test_just_outside_lon_max(self):
        assert _in_sevilla_bounds(37.39, SEVILLA_BOUNDS["lon_max"] + 0.001) is False



class TestHaversineKm:

    def test_same_point_is_zero(self):
        assert _haversine_km(37.39, -5.99, 37.39, -5.99) == pytest.approx(0.0, abs=1e-6)

    def test_known_distance_sevilla_to_madrid(self):
        dist = _haversine_km(37.39, -5.99, 40.42, -3.70)
        assert 380 < dist < 420

    def test_symmetry(self):
        d1 = _haversine_km(37.39, -5.99, 37.40, -5.98)
        d2 = _haversine_km(37.40, -5.98, 37.39, -5.99)
        assert d1 == pytest.approx(d2, rel=1e-6)

    def test_short_distance_within_city(self):
        dist = _haversine_km(37.3886, -5.9823, 37.3922, -5.9909)
        assert 0.5 < dist < 2.0

    def test_positive_result(self):
        dist = _haversine_km(37.39, -5.99, 37.40, -5.98)
        assert dist > 0

    def test_return_type_is_float(self):
        result = _haversine_km(37.39, -5.99, 37.40, -5.98)
        assert isinstance(result, float)



class TestEncodePolyline:

    def test_empty_list_returns_empty_string(self):
        assert _encode_polyline([]) == ""

    def test_single_point_is_encodable(self):
        result = _encode_polyline([(37.3886, -5.9823)])
        assert isinstance(result, str)
        assert len(result) > 0

    def test_two_points_returns_string(self):
        result = _encode_polyline([(37.3886, -5.9823), (37.39, -5.99)])
        assert isinstance(result, str)
        assert len(result) > 0

    def test_identical_points_encodable(self):
        result = _encode_polyline([(37.39, -5.99), (37.39, -5.99)])
        assert isinstance(result, str)

    def test_output_is_printable_ascii(self):
        coords = [(37.3886, -5.9823), (37.3922, -5.9909), (37.3810, -5.9965)]
        result = _encode_polyline(coords)
        assert all(32 <= ord(c) <= 126 for c in result), \
            "Polyline should only contain printable ASCII"

    def test_precision_parameter(self):
        coords = [(37.38859, -5.98234)]
        r5 = _encode_polyline(coords, precision=5)
        r6 = _encode_polyline(coords, precision=6)
        assert isinstance(r5, str)
        assert isinstance(r6, str)



class TestRouteStub:

    ORIGIN = (37.3886, -5.9823)
    DEST   = (37.3922, -5.9909)

    def test_returns_dict(self):
        result = _route_stub(*self.ORIGIN, *self.DEST, speed_kmh=60.0)
        assert isinstance(result, dict)

    def test_backend_is_stub(self):
        result = _route_stub(*self.ORIGIN, *self.DEST, speed_kmh=60.0)
        assert result["backend"] == "stub_straight_line"

    def test_distance_is_positive(self):
        result = _route_stub(*self.ORIGIN, *self.DEST, speed_kmh=60.0)
        assert result["distance_km"] > 0

    def test_duration_is_positive(self):
        result = _route_stub(*self.ORIGIN, *self.DEST, speed_kmh=60.0)
        assert result["duration_minutes"] > 0

    def test_road_factor_applied(self):
        straight = _haversine_km(*self.ORIGIN, *self.DEST)
        result = _route_stub(*self.ORIGIN, *self.DEST, speed_kmh=60.0)
        expected = round(straight * 1.3, 2)
        assert result["distance_km"] == pytest.approx(expected, rel=0.01)

    def test_duration_formula(self):
        result = _route_stub(*self.ORIGIN, *self.DEST, speed_kmh=60.0)
        expected = round((result["distance_km"] / 60.0) * 60, 1)
        assert result["duration_minutes"] == pytest.approx(expected, rel=0.01)

    def test_instructions_not_empty(self):
        result = _route_stub(*self.ORIGIN, *self.DEST, speed_kmh=60.0)
        assert len(result["instructions"]) >= 1

    def test_polyline_present(self):
        result = _route_stub(*self.ORIGIN, *self.DEST, speed_kmh=60.0)
        assert result.get("polyline") is not None
        assert isinstance(result["polyline"], str)

    def test_zero_speed_no_division_error(self):
        """speed_kmh=0 should not raise ZeroDivisionError."""
        result = _route_stub(*self.ORIGIN, *self.DEST, speed_kmh=0.0)
        assert result["duration_minutes"] == 0

    def test_same_point_distance_near_zero(self):
        result = _route_stub(37.39, -5.99, 37.39, -5.99, speed_kmh=60.0)
        assert result["distance_km"] == pytest.approx(0.0, abs=0.05)



class TestGeocodeAddress:

    def test_known_base_returns_from_cache(self):
        for addr, (lat, lon) in _BASE_COORDS_CACHE.items():
            result = _geocode_address(addr)
            assert result is not None
            assert result == (lat, lon)

    def test_unknown_address_hits_nominatim(self):
        mock_location = MagicMock()
        mock_location.latitude  = 37.3886
        mock_location.longitude = -5.9823

        with patch("geopy.geocoders.Nominatim") as MockNominatim:
            instance = MockNominatim.return_value
            instance.geocode.return_value = mock_location
            result = _geocode_address("Calle Sierpes 14, Sevilla")

        assert result == (37.3886, -5.9823)

    def test_nominatim_timeout_returns_none(self):
        from geopy.exc import GeocoderTimedOut
        with patch("geopy.geocoders.Nominatim") as MockNominatim:
            instance = MockNominatim.return_value
            instance.geocode.side_effect = GeocoderTimedOut
            result = _geocode_address("Some address that requires geocoding")
        assert result is None



class TestGetRoute:

    DEST_LAT = 37.3886
    DEST_LON = -5.9823

    def _parse(self, **kwargs) -> dict:
        return json.loads(get_route(**kwargs))


    def test_coords_provided_uses_stub_or_real(self):
        result = self._parse(
            destination_address="Seville centre",
            origin_lat=37.3621, origin_lon=-5.9818,
            destination_lat=self.DEST_LAT, destination_lon=self.DEST_LON,
        )
        assert result.get("backend") is not None
        assert result.get("distance_km") is not None
        assert result.get("duration_minutes") is not None

    def test_origin_and_destination_coords_in_output(self):
        result = self._parse(
            destination_address="Centro Sevilla",
            origin_lat=37.3621, origin_lon=-5.9818,
            destination_lat=self.DEST_LAT, destination_lon=self.DEST_LON,
        )
        assert result["origin_coords"] == [37.3621, -5.9818]
        assert result["destination_coords"] == [self.DEST_LAT, self.DEST_LON]

    def test_output_has_all_required_keys(self):
        result = self._parse(
            destination_address="X",
            origin_lat=37.3621, origin_lon=-5.9818,
            destination_lat=self.DEST_LAT, destination_lon=self.DEST_LON,
        )
        for key in ("backend", "distance_km", "duration_minutes",
                    "instructions", "polyline", "origin_coords",
                    "destination_coords", "error"):
            assert key in result, f"Missing key: {key}"

    def test_instructions_is_list(self):
        result = self._parse(
            destination_address="X",
            origin_lat=37.3621, origin_lon=-5.9818,
            destination_lat=self.DEST_LAT, destination_lon=self.DEST_LON,
        )
        assert isinstance(result["instructions"], list)


    def test_destination_outside_bounds_returns_error(self):
        result = self._parse(
            destination_address="Madrid",
            origin_lat=37.3886, origin_lon=-5.9823,
            destination_lat=40.4168, destination_lon=-3.7038,
        )
        assert result["backend"] == "error"
        assert result["error"] is not None
        assert "outside" in result["error"].lower() or "operational" in result["error"].lower()


    def test_return_trip_ambulance_slower_than_emergency(self):
        common_kwargs = dict(
            destination_address="X",
            origin_lat=37.3621, origin_lon=-5.9818,
            destination_lat=self.DEST_LAT, destination_lon=self.DEST_LON,
            unit_type="ambulance_svb",
        )
        emergency = self._parse(**common_kwargs, is_return_trip=False)
        returning = self._parse(**common_kwargs, is_return_trip=True)
        assert returning["duration_minutes"] >= emergency["duration_minutes"]

    def test_return_trip_fire_slower(self):
        common_kwargs = dict(
            destination_address="X",
            origin_lat=37.3621, origin_lon=-5.9818,
            destination_lat=self.DEST_LAT, destination_lon=self.DEST_LON,
            unit_type="fire",
        )
        emergency = self._parse(**common_kwargs, is_return_trip=False)
        returning = self._parse(**common_kwargs, is_return_trip=True)
        assert returning["duration_minutes"] >= emergency["duration_minutes"]


    def test_unit_type_picks_nearest_base(self):
        result = self._parse(
            destination_address="Plaza Nueva 8, Sevilla",
            unit_type="ambulance_svb",
            destination_lat=self.DEST_LAT, destination_lon=self.DEST_LON,
        )
        assert result["origin_coords"] != [0.0, 0.0]

    def test_destination_geocoding_failure_returns_error(self):
        with patch("tools.get_route._geocode_address", return_value=None):
            result = self._parse(
                destination_address="Address That Cannot Be Geocoded",
                origin_lat=37.3886, origin_lon=-5.9823,
                destination_lat=0.0, destination_lon=0.0,
            )
        assert result["backend"] == "error"
        assert result["error"] is not None


    def test_vehicle_speeds_all_positive(self):
        for vehicle, speed in VEHICLE_SPEEDS_KMH.items():
            assert speed > 0, f"Speed for {vehicle} must be positive"

    def test_ambulance_sva_speed(self):
        assert VEHICLE_SPEEDS_KMH["ambulance_sva"] == 70

    def test_police_speed(self):
        assert VEHICLE_SPEEDS_KMH["police"] == 80


    def test_no_origin_uses_city_centre(self):
        result = self._parse(
            destination_address="",
            destination_lat=self.DEST_LAT, destination_lon=self.DEST_LON,
        )
        assert result.get("origin_coords") is not None

    def test_origin_address_from_cache(self):
        cache_key = next(iter(_BASE_COORDS_CACHE))
        result = self._parse(
            origin_address=cache_key,
            destination_address="",
            destination_lat=self.DEST_LAT, destination_lon=self.DEST_LON,
        )
        expected = list(_BASE_COORDS_CACHE[cache_key])
        assert result["origin_coords"] == expected


    def test_ors_failure_falls_through_to_stub(self):
        """If ORS and OSMnx both fail, the stub must be used."""
        with patch("tools.get_route._route_via_ors",   return_value=None), \
             patch("tools.get_route._route_via_osmnx", return_value=None):
            result = self._parse(
                destination_address="",
                origin_lat=37.3621, origin_lon=-5.9818,
                destination_lat=self.DEST_LAT, destination_lon=self.DEST_LON,
            )
        assert result["backend"] == "stub_straight_line"



if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v"])
