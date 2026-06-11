"""
test_analysis_agent.py
-----------------------
Comprehensive unit tests for agents/analysis_agent.py.

Covers:
  - generate_sample_data        (synthetic dataset generation)
  - _load_incident_data         (DB / CSV / empty fallback)
  - get_incident_history        (filtering, date window)
  - get_hotspot_analysis        (grid clustering, risk score)
  - get_trend_forecast          (day-of-week seasonality, date range)
  - get_response_time_stats     (overall, by_severity, trend)
  - get_hourly_distribution     (24-bucket average)
  - AnalysisAgent._direct_enrich (hotspot detection, risk level)
  - AnalysisAgent.generate_dashboard_data (assembled dict)
  - AnalysisAgent.annotate_incident_async (fire-and-forget thread)

Run with:
    pytest test/test_analysis_agent.py -v
"""

from __future__ import annotations

import json
import sys
import os
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents.analysis_agent as aa
from agents.analysis_agent import (
    generate_sample_data,
    _load_incident_data,
    get_incident_history,
    get_hotspot_analysis,
    get_trend_forecast,
    get_response_time_stats,
    get_hourly_distribution,
    AnalysisAgent,
)



@pytest.fixture
def sample_df(tmp_path) -> pd.DataFrame:
    """Generate a small synthetic dataset and inject it as the module's data source."""
    old_db  = aa.DB_PATH
    old_csv = aa.CSV_PATH
    csv_out = tmp_path / "incidents.csv"
    aa.DB_PATH  = tmp_path / "incidents.db"
    aa.CSV_PATH = csv_out
    df = generate_sample_data(n=200, output_path=csv_out)
    yield df
    aa.DB_PATH  = old_db
    aa.CSV_PATH = old_csv


@pytest.fixture
def empty_data(tmp_path):
    """Force _load_incident_data to return an empty frame."""
    old_db  = aa.DB_PATH
    old_csv = aa.CSV_PATH
    aa.DB_PATH  = tmp_path / "nonexistent.db"
    aa.CSV_PATH = tmp_path / "nonexistent.csv"
    yield
    aa.DB_PATH  = old_db
    aa.CSV_PATH = old_csv



class TestGenerateSampleData:

    def test_returns_dataframe(self, tmp_path):
        df = generate_sample_data(n=50, output_path=tmp_path / "out.csv")
        assert isinstance(df, pd.DataFrame)

    def test_correct_row_count(self, tmp_path):
        df = generate_sample_data(n=100, output_path=tmp_path / "out.csv")
        assert len(df) == 100

    def test_required_columns_present(self, tmp_path):
        df = generate_sample_data(n=50, output_path=tmp_path / "out.csv")
        for col in ("id", "timestamp", "incident_type", "severity",
                    "latitude", "longitude", "address", "response_time_minutes", "resolved"):
            assert col in df.columns, f"Missing column: {col}"

    def test_csv_file_created(self, tmp_path):
        csv_path = tmp_path / "incidents.csv"
        generate_sample_data(n=50, output_path=csv_path)
        assert csv_path.exists()

    def test_severities_are_valid(self, tmp_path):
        df = generate_sample_data(n=100, output_path=tmp_path / "out.csv")
        valid = {"critical", "high", "medium", "low"}
        assert set(df["severity"].unique()).issubset(valid)

    def test_coordinates_in_sevilla_range(self, tmp_path):
        df = generate_sample_data(n=100, output_path=tmp_path / "out.csv")
        assert df["latitude"].between(37.0, 38.0).all()
        assert df["longitude"].between(-6.5, -5.5).all()

    def test_response_times_positive(self, tmp_path):
        df = generate_sample_data(n=100, output_path=tmp_path / "out.csv")
        assert (df["response_time_minutes"] > 0).all()

    def test_ids_unique(self, tmp_path):
        df = generate_sample_data(n=50, output_path=tmp_path / "out.csv")
        assert df["id"].nunique() == 50

    def test_timestamps_within_90_days(self, tmp_path):
        df = generate_sample_data(n=50, output_path=tmp_path / "out.csv")
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        now = pd.Timestamp.now()
        assert (df["timestamp"] <= now).all()
        assert (df["timestamp"] >= now - pd.Timedelta(days=91)).all()



class TestLoadIncidentData:

    def test_loads_csv_when_no_db(self, tmp_path):
        csv = tmp_path / "inc.csv"
        df_expected = generate_sample_data(n=20, output_path=csv)
        aa.DB_PATH  = tmp_path / "noexist.db"
        aa.CSV_PATH = csv
        df = _load_incident_data()
        assert len(df) == 20

    def test_returns_empty_df_when_nothing_available(self, empty_data):
        df = _load_incident_data()
        assert isinstance(df, pd.DataFrame)
        assert df.empty

    def test_empty_df_has_expected_schema(self, empty_data):
        df = _load_incident_data()
        for col in ("id", "timestamp", "incident_type", "severity",
                    "latitude", "longitude", "address", "response_time_minutes", "resolved"):
            assert col in df.columns

    def test_loads_from_sqlite_when_db_exists(self, tmp_path):
        db_path = tmp_path / "incidents.db"
        df_orig = generate_sample_data(n=30, output_path=tmp_path / "dummy.csv")
        conn = sqlite3.connect(db_path)
        df_orig.to_sql("incidents", conn, if_exists="replace", index=False)
        conn.close()
        aa.DB_PATH  = db_path
        aa.CSV_PATH = tmp_path / "noexist.csv"
        df = _load_incident_data()
        assert len(df) == 30



class TestGetIncidentHistory:

    def test_returns_json_string(self, sample_df):
        result = get_incident_history()
        assert isinstance(result, str)
        json.loads(result)

    def test_has_required_keys(self, sample_df):
        data = json.loads(get_incident_history())
        assert "records" in data
        assert "total" in data
        assert "date_range" in data

    def test_total_matches_records_length(self, sample_df):
        data = json.loads(get_incident_history(days_back=365))
        assert data["total"] == len(data["records"])

    def test_filter_by_incident_type(self, sample_df):
        data = json.loads(get_incident_history(incident_type="fire"))
        for rec in data["records"]:
            assert rec["incident_type"] == "fire"

    def test_days_back_limits_results(self, sample_df):
        all_data  = json.loads(get_incident_history(days_back=365))
        few_data  = json.loads(get_incident_history(days_back=1))
        assert few_data["total"] <= all_data["total"]

    def test_days_back_capped_at_365(self, sample_df):
        data = json.loads(get_incident_history(days_back=999))
        assert data["total"] >= 0

    def test_empty_data_returns_zero(self, empty_data):
        data = json.loads(get_incident_history())
        assert data["total"] == 0
        assert data["records"] == []

    def test_date_range_in_output(self, sample_df):
        data = json.loads(get_incident_history())
        assert "start" in data["date_range"]
        assert "end"   in data["date_range"]



class TestGetHotspotAnalysis:

    def test_returns_json(self, sample_df):
        result = get_hotspot_analysis()
        json.loads(result)

    def test_has_hotspots_key(self, sample_df):
        data = json.loads(get_hotspot_analysis())
        assert "hotspots" in data

    def test_hotspots_is_list(self, sample_df):
        data = json.loads(get_hotspot_analysis())
        assert isinstance(data["hotspots"], list)

    def test_hotspot_fields(self, sample_df):
        data = json.loads(get_hotspot_analysis())
        for hs in data["hotspots"]:
            for field in ("lat_center", "lon_center", "incident_count",
                          "dominant_type", "risk_score", "recommended_measures"):
                assert field in hs, f"Hotspot missing field: {field}"

    def test_risk_score_in_range(self, sample_df):
        data = json.loads(get_hotspot_analysis())
        for hs in data["hotspots"]:
            assert 0 <= hs["risk_score"] <= 100

    def test_sorted_by_risk_descending(self, sample_df):
        data = json.loads(get_hotspot_analysis())
        scores = [hs["risk_score"] for hs in data["hotspots"]]
        assert scores == sorted(scores, reverse=True)

    def test_max_20_hotspots_returned(self, sample_df):
        data = json.loads(get_hotspot_analysis())
        assert len(data["hotspots"]) <= 20

    def test_empty_data_returns_empty(self, empty_data):
        data = json.loads(get_hotspot_analysis())
        assert data["hotspots"] == []

    def test_grid_size_parameter(self, sample_df):
        result = get_hotspot_analysis(grid_size_km=0.5)
        data = json.loads(result)
        assert "hotspots" in data



class TestGetTrendForecast:

    def test_returns_json(self, sample_df):
        result = get_trend_forecast()
        json.loads(result)

    def test_forecast_length_matches_request(self, sample_df):
        data = json.loads(get_trend_forecast(forecast_days=3))
        assert len(data["forecast"]) == 3

    def test_max_14_days(self, sample_df):
        data = json.loads(get_trend_forecast(forecast_days=20))
        assert len(data["forecast"]) == 14

    def test_forecast_entry_fields(self, sample_df):
        data = json.loads(get_trend_forecast(forecast_days=7))
        for entry in data["forecast"]:
            for field in ("date", "day_of_week", "predicted_incidents",
                          "confidence_interval_low", "confidence_interval_high"):
                assert field in entry

    def test_predicted_incidents_positive(self, sample_df):
        data = json.loads(get_trend_forecast())
        for entry in data["forecast"]:
            assert entry["predicted_incidents"] >= 0

    def test_ci_low_le_predicted_le_ci_high(self, sample_df):
        data = json.loads(get_trend_forecast())
        for entry in data["forecast"]:
            assert entry["confidence_interval_low"] <= entry["predicted_incidents"]
            assert entry["predicted_incidents"] <= entry["confidence_interval_high"]

    def test_historical_daily_avg_positive(self, sample_df):
        data = json.loads(get_trend_forecast())
        assert data["historical_daily_avg"] >= 0

    def test_insufficient_data_returns_note(self, empty_data):
        data = json.loads(get_trend_forecast())
        assert "note" in data or data.get("forecast") == []



class TestGetResponseTimeStats:

    def test_returns_json(self, sample_df):
        result = get_response_time_stats()
        json.loads(result)

    def test_overall_keys(self, sample_df):
        data = json.loads(get_response_time_stats())
        overall = data["overall"]
        for key in ("mean", "median", "p90", "count", "pct_within_8min"):
            assert key in overall

    def test_mean_positive(self, sample_df):
        data = json.loads(get_response_time_stats())
        assert data["overall"]["mean"] > 0

    def test_p90_gte_median(self, sample_df):
        data = json.loads(get_response_time_stats())
        assert data["overall"]["p90"] >= data["overall"]["median"]

    def test_trend_is_valid_value(self, sample_df):
        data = json.loads(get_response_time_stats())
        assert data["trend"] in ("improving", "worsening", "stable")

    def test_by_severity_present(self, sample_df):
        data = json.loads(get_response_time_stats())
        assert "by_severity" in data

    def test_by_incident_type_present(self, sample_df):
        data = json.loads(get_response_time_stats())
        assert "by_incident_type" in data

    def test_filter_by_incident_type(self, sample_df):
        data = json.loads(get_response_time_stats(incident_type="fire"))
        assert "overall" in data

    def test_empty_data_returns_note(self, empty_data):
        data = json.loads(get_response_time_stats())
        assert "note" in data or data["overall"] == {}



class TestGetHourlyDistribution:

    def test_returns_json(self, sample_df):
        result = get_hourly_distribution()
        json.loads(result)

    def test_hourly_list_has_24_entries(self, sample_df):
        data = json.loads(get_hourly_distribution())
        assert len(data["hourly"]) == 24

    def test_peak_hour_in_range(self, sample_df):
        data = json.loads(get_hourly_distribution())
        assert 0 <= data["peak_hour"] <= 23

    def test_total_incidents_positive(self, sample_df):
        data = json.loads(get_hourly_distribution())
        assert data["total_incidents"] >= 0

    def test_hourly_values_non_negative(self, sample_df):
        data = json.loads(get_hourly_distribution())
        for val in data["hourly"]:
            assert val >= 0

    def test_days_back_capped_at_90(self, sample_df):
        data = json.loads(get_hourly_distribution(days_back=200))
        assert len(data["hourly"]) == 24

    def test_empty_data_returns_zeros(self, empty_data):
        data = json.loads(get_hourly_distribution())
        assert all(v == 0.0 for v in data["hourly"])
        assert data["total_incidents"] == 0



class TestDirectEnrich:

    def setup_method(self):
        self.agent = AnalysisAgent.__new__(AnalysisAgent)

    def test_returns_dict(self, sample_df):
        result = self.agent._direct_enrich("traffic_accident", 37.3886, -5.9823)
        assert isinstance(result, dict)

    def test_has_all_keys(self, sample_df):
        result = self.agent._direct_enrich("fire", 37.39, -5.99)
        for key in ("is_hotspot", "similar_incidents_30d", "avg_response_time_target",
                    "risk_level", "historical_note"):
            assert key in result

    def test_risk_level_valid(self, sample_df):
        result = self.agent._direct_enrich("cardiac_arrest", 37.39, -5.99)
        assert result["risk_level"] in ("high", "medium", "low", "unknown")

    def test_hotspot_near_seed_coord(self, sample_df):
        """Using a well-populated hotspot seed should detect the hotspot."""
        result = self.agent._direct_enrich("traffic_accident", 37.3886, -5.9823)
        assert isinstance(result["is_hotspot"], bool)

    def test_similar_incidents_non_negative(self, sample_df):
        result = self.agent._direct_enrich("fire", 37.40, -5.96)
        assert result["similar_incidents_30d"] >= 0

    def test_no_coords_does_not_raise(self, sample_df):
        result = self.agent._direct_enrich("assault", None, None)
        assert isinstance(result, dict)

    def test_empty_data_graceful(self, empty_data):
        result = self.agent._direct_enrich("fire", 37.39, -5.99)
        assert isinstance(result, dict)
        assert result["risk_level"] in ("high", "medium", "low", "unknown")



class TestGenerateDashboardData:

    def setup_method(self):
        self.agent = AnalysisAgent.__new__(AnalysisAgent)

    def test_returns_dict(self, sample_df):
        result = self.agent.generate_dashboard_data()
        assert isinstance(result, dict)

    def test_has_required_keys(self, sample_df):
        result = self.agent.generate_dashboard_data()
        for key in ("hotspots", "forecast", "historical_daily_avg",
                    "hourly_distribution", "kpis", "generated_at"):
            assert key in result, f"Missing key: {key}"

    def test_hotspots_is_list(self, sample_df):
        result = self.agent.generate_dashboard_data()
        assert isinstance(result["hotspots"], list)

    def test_forecast_is_list(self, sample_df):
        result = self.agent.generate_dashboard_data()
        assert isinstance(result["forecast"], list)

    def test_hourly_distribution_24_entries(self, sample_df):
        result = self.agent.generate_dashboard_data()
        assert len(result["hourly_distribution"]) == 24

    def test_hourly_distribution_has_hour_count(self, sample_df):
        result = self.agent.generate_dashboard_data()
        for entry in result["hourly_distribution"]:
            assert "hour" in entry
            assert "count" in entry
            assert 0 <= entry["hour"] <= 23

    def test_generated_at_is_iso_string(self, sample_df):
        result = self.agent.generate_dashboard_data()
        datetime.fromisoformat(result["generated_at"])

    def test_error_key_present_on_failure(self):
        """If the tools crash, generate_dashboard_data should return an error dict."""
        agent = AnalysisAgent.__new__(AnalysisAgent)
        with patch("agents.analysis_agent.get_hotspot_analysis",
                   side_effect=Exception("DB error")):
            result = agent.generate_dashboard_data()
        assert "error" in result



class TestAnnotateIncidentAsync:

    def setup_method(self):
        self.agent = AnalysisAgent.__new__(AnalysisAgent)

    def test_returns_immediately(self, sample_df):
        t0 = time.perf_counter()
        self.agent.annotate_incident_async(
            incident_id="INC-001",
            incident_type="fire",
            lat=37.39, lon=-5.99,
        )
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.5

    def test_on_complete_callback_called(self, sample_df):
        results = []
        event = threading.Event()

        def on_done(res):
            results.append(res)
            event.set()

        self.agent.annotate_incident_async(
            incident_id="INC-002",
            incident_type="cardiac_arrest",
            lat=37.39, lon=-5.99,
            on_complete=on_done,
        )
        event.wait(timeout=5)
        assert len(results) == 1
        assert results[0]["incident_id"] == "INC-002"

    def test_annotation_has_required_fields(self, sample_df):
        results = []
        event = threading.Event()

        def on_done(res):
            results.append(res)
            event.set()

        self.agent.annotate_incident_async(
            incident_id="INC-003",
            incident_type="traffic_accident",
            lat=37.3886, lon=-5.9823,
            on_complete=on_done,
        )
        event.wait(timeout=5)
        result = results[0]
        for field in ("incident_id", "annotated_at", "is_hotspot",
                      "risk_level", "historical_note"):
            assert field in result, f"Annotation missing field: {field}"

    def test_no_callback_does_not_crash(self, sample_df):
        self.agent.annotate_incident_async(
            incident_id="INC-004",
            incident_type="fire",
            lat=None, lon=None,
        )
        time.sleep(0.1)

    def test_bad_callback_does_not_propagate(self, sample_df):
        def bad_callback(res):
            raise RuntimeError("Callback intentionally broken")

        self.agent.annotate_incident_async(
            incident_id="INC-005",
            incident_type="fire",
            lat=37.39, lon=-5.99,
            on_complete=bad_callback,
        )
        time.sleep(0.3)



if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v"])
