"""Background analytics agent for dashboard hotspots, forecasts and post-dispatch enrichment."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd
from smolagents import CodeAgent, tool, Model

logger = logging.getLogger(__name__)

DB_PATH  = Path(os.getenv("INCIDENTS_DB_PATH",  "./data/incidents.db"))
CSV_PATH = Path(os.getenv("INCIDENTS_CSV_PATH", "./data/incidents.csv"))


def _load_incident_data() -> pd.DataFrame:
    """Loads incidents from SQLite via json_extract() on the data blob, or falls back to CSV."""
    if DB_PATH.exists():
        try:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                df = pd.read_sql_query(
                    """SELECT
                        id,
                        status,
                        timestamp,
                        incident_type,
                        severity,
                        json_extract(data, '$.latitude')           AS latitude,
                        json_extract(data, '$.longitude')          AS longitude,
                        json_extract(data, '$.address')            AS address,
                        json_extract(data, '$.response_time_min')  AS response_time_minutes,
                        json_extract(data, '$.victims')            AS victims,
                        json_extract(data, '$.resolved')           AS resolved
                    FROM incidents""",
                    conn,
                )
            finally:
                conn.close()
            return df
        except Exception as e:
            logger.warning(f"[AnalysisAgent] DB load failed: {e} — trying CSV")

    if CSV_PATH.exists():
        return pd.read_csv(CSV_PATH)

    return pd.DataFrame(columns=[
        "id", "timestamp", "incident_type", "severity",
        "latitude", "longitude", "address", "response_time_minutes", "resolved",
    ])

@tool
def get_incident_history(
    days_back: int = 90,
    incident_type: str = "all",
    area: str = "all",
) -> str:
    """Retrieves historical incident records for trend analysis.

    Args:
        days_back: Number of days of history to retrieve (default 90, max 365).
        incident_type: Filter by type, e.g. 'traffic_accident', or 'all'.
        area: Filter by area/neighbourhood name, or 'all'.

    Returns:
        JSON with keys: records (list), total (int), date_range (dict).
    """
    days_back = min(int(days_back), 365)
    since = datetime.now(timezone.utc) - timedelta(days=days_back)

    df = _load_incident_data()
    if df.empty:
        return json.dumps({"records": [], "total": 0,
                           "date_range": {"start": since.isoformat(), "end": datetime.now(timezone.utc).isoformat()}})

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    mask = df["timestamp"] >= since
    if incident_type != "all":
        mask &= df["incident_type"] == incident_type
    if area != "all":
        mask &= df["address"].str.contains(area, case=False, na=False)

    filtered = df[mask]
    return json.dumps({
        "records":    filtered.to_dict(orient="records"),
        "total":      len(filtered),
        "date_range": {"start": since.isoformat(), "end": datetime.now(timezone.utc).isoformat()},
    }, default=str)


_KNOWN_AREAS = [
    ("Centro Histórico",        37.3886, -5.9823),
    ("Triana",                  37.3818, -5.9965),
    ("Nervión",                 37.3849, -5.9714),
    ("Macarena",                37.4023, -5.9856),
    ("Los Remedios",            37.3736, -5.9913),
    ("San Pablo - Santa Justa", 37.4068, -5.9628),
    ("Este - Alcosa",           37.3783, -5.9432),
    ("Sur - Heliópolis",        37.3572, -5.9836),
    ("Palmera - Bellavista",    37.3627, -5.9757),
    ("Torreblanca",             37.3718, -5.8962),
    ("Casco Norte",             37.4213, -5.9782),
    ("Polígono Sur",            37.3480, -5.9810),
]

def _nearest_area_label(lat: float, lon: float) -> str:
    """Return the name of the nearest known neighbourhood for a coordinate pair."""
    best_name, best_dist = "Zona desconocida", float("inf")
    for name, alat, alon in _KNOWN_AREAS:
        dist = (lat - alat) ** 2 + (lon - alon) ** 2
        if dist < best_dist:
            best_dist = dist
            best_name = name
    return best_name


@tool
def get_hotspot_analysis(grid_size_km: float = 0.3) -> str:
    """Clusters incidents into grid cells; flags cells above 1.5× mean count as hotspots.

    Args:
        grid_size_km: Size of each grid cell in kilometres (default 0.3 = 300m blocks).

    Returns:
        JSON with key 'hotspots'. Each hotspot has:
          area_label, lat_center, lon_center, incident_count, dominant_type,
          dominant_count, risk_score (0-100), measures (list of strings).
    """
    df = _load_incident_data()
    if df.empty or len(df) < 10:
        return json.dumps({"hotspots": [], "total_cells_analysed": 0,
                           "note": "Insufficient data for hotspot analysis"})

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df[df["timestamp"] >= datetime.now(timezone.utc) - timedelta(days=90)]

    cell_deg = grid_size_km / 111.0
    df["lat_cell"] = (df["latitude"]  / cell_deg).round() * cell_deg
    df["lon_cell"] = (df["longitude"] / cell_deg).round() * cell_deg

    cells = (df.groupby(["lat_cell", "lon_cell"])
               .agg(incident_count=("id", "count"),
                    dominant_type=("incident_type", lambda x: x.value_counts().index[0]),
                    dominant_count=("incident_type", lambda x: int(x.value_counts().iloc[0])),
                    sev_critical=("severity", lambda x: (x == "critical").sum()),
                    sev_high=    ("severity", lambda x: (x == "high").sum()))
               .reset_index())

    mean_count = cells["incident_count"].mean()
    threshold  = max(mean_count * 1.5, 5)
    hotspots_df = cells[
        (cells["incident_count"] >= threshold) & (cells["dominant_count"] >= 2)
    ].copy()

    hotspots = []
    for _, row in hotspots_df.iterrows():
        total = int(row["incident_count"])
        crit_rate = row["sev_critical"] / total
        high_rate = row["sev_high"]     / total
        sev_factor = 1.0 + crit_rate * 2.0 + high_rate * 0.5
        vol_factor = total / threshold
        risk = min(99, max(10, int(vol_factor * sev_factor * 25)))
        clat = round(float(row["lat_cell"]), 5)
        clon = round(float(row["lon_cell"]), 5)
        hotspots.append({
            "area_label":     _nearest_area_label(clat, clon),
            "lat_center":     clat,
            "lon_center":     clon,
            "incident_count": int(row["incident_count"]),
            "dominant_count": int(row["dominant_count"]),
            "dominant_type":  row["dominant_type"],
            "risk_score":     risk,
        })

    area_merged: dict[str, dict] = {}
    for hs in hotspots:
        label = hs["area_label"]
        if label not in area_merged:
            area_merged[label] = hs.copy()
        else:
            prev = area_merged[label]
            new_total = prev["incident_count"] + hs["incident_count"]
            if hs["incident_count"] > prev["incident_count"]:
                area_merged[label] = {
                    **hs,
                    "incident_count": new_total,
                    "risk_score": max(hs["risk_score"], prev["risk_score"]),
                }
            else:
                prev["incident_count"] = new_total
                prev["risk_score"] = max(prev["risk_score"], hs["risk_score"])
    hotspots = list(area_merged.values())

    hotspots.sort(key=lambda x: x["risk_score"], reverse=True)
    return json.dumps({"hotspots": hotspots[:20], "total_cells_analysed": len(cells)})


@tool
def get_trend_forecast(forecast_days: int = 7) -> str:
    """Predicts daily incident counts using day-of-week weights and a rolling 7-day average.

    Args:
        forecast_days: Number of days to forecast (default 7, max 14).

    Returns:
        JSON with keys: forecast (list of daily predictions), historical_daily_avg.
        Each forecast entry has: date, day_of_week, predicted_incidents,
        confidence_interval_low, confidence_interval_high.
    """
    df = _load_incident_data()
    if df.empty or len(df) < 30:
        return json.dumps({
            "forecast": [], "historical_daily_avg": 0,
            "note": f"Need ≥30 records for forecast (have {len(df)})"
        })

    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["date"]    = df["timestamp"].dt.date
    df["weekday"] = df["timestamp"].dt.dayofweek

    daily     = df.groupby("date").size().reset_index(name="count")
    base_rate = float(daily["count"].mean())

    dow_rates = (df.groupby("weekday").size() / df.groupby("weekday").size().mean()).to_dict()
    recent_7  = float(daily.tail(7)["count"].mean()) if len(daily) >= 7 else base_rate

    _MONTH_MULT = {
        1: 0.928, 2: 0.845, 3: 0.929, 4: 0.901, 5: 1.000, 6: 1.018,
        7: 1.123, 8: 1.186, 9: 1.037, 10: 0.997, 11: 0.957, 12: 1.080,
    }

    forecast = []
    for d in range(1, min(forecast_days, 14) + 1):
        target  = datetime.now().date() + timedelta(days=d)
        weekday = target.weekday()
        mult    = dow_rates.get(weekday, 1.0) * _MONTH_MULT.get(target.month, 1.0)
        pred    = round((base_rate * 0.4 + recent_7 * 0.6) * mult, 1)
        std     = float(daily["count"].std()) if len(daily) > 1 else pred * 0.3
        forecast.append({
            "date":                    target.isoformat(),
            "day_of_week":             target.strftime("%A"),
            "predicted_incidents":     pred,
            "confidence_interval_low": round(max(0, pred - std), 1),
            "confidence_interval_high": round(pred + std, 1),
        })

    return json.dumps({"forecast": forecast, "historical_daily_avg": round(base_rate, 1)})


@tool
def get_response_time_stats(
    incident_type: str = "all",
    days_back: int = 30,
) -> str:
    """Computes response time statistics for KPI reporting.

    Args:
        incident_type: Filter by type, or 'all'.
        days_back: Analysis window in days (default 30).

    Returns:
        JSON with keys: overall (mean, median, p90, count),
        by_severity (dict), by_incident_type (dict), trend (str).
    """
    df = _load_incident_data()
    if df.empty or "response_time_minutes" not in df.columns:
        return json.dumps({"overall": {}, "by_severity": {}, "by_incident_type": {},
                           "trend": "unknown", "note": "No response time data"})

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    since = datetime.now(timezone.utc) - timedelta(days=days_back)
    df    = df[df["timestamp"] >= since].copy()

    if incident_type != "all":
        df = df[df["incident_type"] == incident_type]

    rt = df["response_time_minutes"].dropna()
    if rt.empty:
        return json.dumps({"overall": {}, "note": "No records in window"})

    TARGET = {"critical": 6, "high": 8, "medium": 10, "low": 15}

    def sev_stats(sub_df):
        rts = sub_df["response_time_minutes"].dropna()
        tgt = TARGET.get(sub_df["severity"].iloc[0] if len(sub_df) else "medium", 10)
        return {
            "mean":                round(float(rts.mean()), 2),
            "count":               len(rts),
            "target":              tgt,
            "pct_meeting_target":  round(float((rts <= tgt).mean() * 100), 1),
        }

    by_sev = {s: sev_stats(df[df["severity"] == s])
              for s in ["critical", "high", "medium", "low"]
              if (df["severity"] == s).any()}

    by_type = {}
    for t in df["incident_type"].unique():
        sub = df[df["incident_type"] == t]["response_time_minutes"].dropna()
        if len(sub) > 0:
            by_type[t] = {"mean": round(float(sub.mean()), 2), "count": len(sub)}

    mid = len(df) // 2
    trend = "stable"
    if mid > 0:
        first_half_mean  = float(df.iloc[:mid]["response_time_minutes"].mean())
        second_half_mean = float(df.iloc[mid:]["response_time_minutes"].mean())
        if   second_half_mean < first_half_mean * 0.95: trend = "improving"
        elif second_half_mean > first_half_mean * 1.05: trend = "worsening"

    return json.dumps({
        "overall": {
            "mean":            round(float(rt.mean()),           2),
            "median":          round(float(rt.median()),         2),
            "p90":             round(float(rt.quantile(0.9)),    2),
            "count":           len(rt),
            "pct_within_8min": round(float((rt <= 8.0).mean() * 100), 1),
        },
        "by_severity":      by_sev,
        "by_incident_type": by_type,
        "trend":            trend,
    })


@tool
def get_hourly_distribution(days_back: int = 30) -> str:
    """Averages incident count per hour across the analysis window for the 24-hour chart.

    Args:
        days_back: Number of days of history to analyse (default 30, max 90).

    Returns:
        JSON with keys:
          hourly (list[float]): 24 average counts, one per hour 0–23.
          peak_hour (int): hour with the highest average count.
          total_incidents (int): total incidents analysed.
    """
    days_back = min(int(days_back), 90)
    df = _load_incident_data()
    if df.empty or "timestamp" not in df.columns:
        return json.dumps({
            "hourly": [0.0] * 24, "peak_hour": 0, "total_incidents": 0,
            "note": "No data available",
        })

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    since = datetime.now(timezone.utc) - timedelta(days=days_back)
    df = df[df["timestamp"] >= since].copy()

    if df.empty:
        return json.dumps({
            "hourly": [0.0] * 24, "peak_hour": 0, "total_incidents": 0,
            "note": f"No incidents in the last {days_back} days",
        })

    df["hour"] = df["timestamp"].dt.hour
    df["date"] = df["timestamp"].dt.date

    hourly_per_day = df.groupby(["date", "hour"]).size().reset_index(name="count")
    avg_per_hour   = hourly_per_day.groupby("hour")["count"].mean()

    hourly    = [round(float(avg_per_hour.get(h, 0.0)), 2) for h in range(24)]
    peak_hour = int(np.argmax(hourly)) if any(v > 0 for v in hourly) else 0

    return json.dumps({
        "hourly":          hourly,
        "peak_hour":       peak_hour,
        "total_incidents": len(df),
    })


ANALYSIS_TOOLS = [
    get_incident_history,
    get_hotspot_analysis,
    get_trend_forecast,
    get_response_time_stats,
    get_hourly_distribution,
]

class AnalysisAgent:
    """Wraps dashboard analytics tools; runs independently of the dispatch pipeline."""

    def __init__(self, model: Model, max_steps: int = 10):
        self.model = model
        self._agent = CodeAgent(
            tools=ANALYSIS_TOOLS,
            model=model,
            name="analysis_agent",
            description=(
                "Analyses historical emergency incident data to detect geographic hotspots, "
                "forecast daily incident counts, compute response-time KPIs and generate "
                "preventive recommendations. Runs independently of the dispatch pipeline."
            ),
            max_steps=max_steps,
            verbosity_level=1,
            additional_authorized_imports=[
                "json", "pandas", "numpy", "datetime", "collections", "statistics"
            ],
        )
        logger.info("[AnalysisAgent] Initialised (background-only mode)")


    def generate_dashboard_data(self) -> dict:
        """Aggregates hotspot, forecast, KPI and hourly data for the dashboard cache.

        Returns:
            dict with: hotspots, forecast, historical_daily_avg, kpis, generated_at.
        """
        try:
            hotspots = json.loads(get_hotspot_analysis())
            forecast = json.loads(get_trend_forecast())
            kpis     = json.loads(get_response_time_stats())
            hourly   = json.loads(get_hourly_distribution())
            return {
                "hotspots":             hotspots.get("hotspots", []),
                "forecast":             forecast.get("forecast", []),
                "historical_daily_avg": forecast.get("historical_daily_avg", 0),
                "hourly_distribution":  [
                    {"hour": h, "count": v}
                    for h, v in enumerate(hourly.get("hourly", [0.0] * 24))
                ],
                "kpis":                 kpis,
                "generated_at":         datetime.now().isoformat(),
            }
        except Exception as exc:
            logger.error(f"[AnalysisAgent] generate_dashboard_data failed: {exc}")
            return {"error": str(exc), "generated_at": datetime.now().isoformat()}


    def annotate_incident_async(
        self,
        incident_id: str,
        incident_type: str,
        lat: Optional[float],
        lon: Optional[float],
        on_complete: Optional[Callable[[dict], None]] = None,
    ) -> None:
        """Enriches a completed incident with historical context in a background daemon thread.

        Args:
            incident_id:   The incident ID to annotate.
            incident_type: e.g. "traffic_accident"
            lat, lon:      Incident coordinates (can be None).
            on_complete:   Optional callback receiving the annotation dict.
        """
        def _work():
            logger.info(f"[AnalysisAgent] Starting background annotation for {incident_id}")
            result = self._direct_enrich(incident_type, lat, lon)
            result["incident_id"] = incident_id
            result["annotated_at"] = datetime.now().isoformat()
            logger.info(f"[AnalysisAgent] Annotation complete for {incident_id}: "
                        f"hotspot={result.get('is_hotspot')}, risk={result.get('risk_level')}")
            if on_complete:
                try:
                    on_complete(result)
                except Exception as e:
                    logger.error(f"[AnalysisAgent] on_complete callback failed: {e}")

        thread = threading.Thread(target=_work, daemon=True)
        thread.start()


    def _direct_enrich(
        self,
        incident_type: str,
        lat: Optional[float],
        lon: Optional[float],
    ) -> dict:
        """Computes historical context via direct tool calls without an LLM round-trip."""
        try:
            hist = json.loads(get_incident_history(days_back=30, incident_type=incident_type))
            hots = json.loads(get_hotspot_analysis())
            kpis = json.loads(get_response_time_stats(incident_type=incident_type))

            similar_count = hist.get("total", 0)
            is_hotspot    = False

            if lat and lon:
                for h in hots.get("hotspots", []):
                    if (abs(h.get("lat_center", 0) - lat) < 0.003 and
                            abs(h.get("lon_center", 0) - lon) < 0.003):
                        is_hotspot = True
                        break

            overall = kpis.get("overall", {})
            risk    = "high" if is_hotspot else ("medium" if similar_count > 5 else "low")

            return {
                "is_hotspot":               is_hotspot,
                "similar_incidents_30d":    similar_count,
                "avg_response_time_target": overall.get("mean", 0),
                "risk_level":               risk,
                "historical_note": (
                    f"{similar_count} incidente(s) similar(es) en los últimos 30 días. "
                    + ("Esta ubicación es una zona de alta incidencia — "
                       "considerar medidas preventivas." if is_hotspot
                       else "Sin patrón histórico especial detectado.")
                ),
            }

        except Exception as exc:
            return {
                "is_hotspot": False, "similar_incidents_30d": 0,
                "avg_response_time_target": 0, "risk_level": "unknown",
                "historical_note": f"Análisis no disponible: {exc}",
            }


def start_dashboard_scheduler(
    agent: AnalysisAgent,
    on_refresh: Callable[[dict], None],
    interval_minutes: int = 60,
) -> None:
    """Starts a BackgroundScheduler that calls on_refresh(data) every interval_minutes.

    Args:
        agent:            An initialised AnalysisAgent instance.
        on_refresh:       Callback receiving the fresh dashboard dict.
        interval_minutes: Refresh interval in minutes (default 60).
    """
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler()

        def _job():
            data = agent.generate_dashboard_data()
            on_refresh(data)
            logger.info(f"[AnalysisAgent] Dashboard refreshed at {data.get('generated_at')}")

        scheduler.add_job(_job, "interval", minutes=interval_minutes, id="dashboard_refresh")
        scheduler.start()
        _job()
        logger.info(f"[AnalysisAgent] Dashboard scheduler started (every {interval_minutes} min)")

    except ImportError:
        logger.warning("[AnalysisAgent] apscheduler not installed — running single refresh. "
                       "Run: pip install apscheduler")
        on_refresh(agent.generate_dashboard_data())


def generate_sample_data(n: int = 500, output_path: Path = CSV_PATH) -> pd.DataFrame:
    """Generates a synthetic incident CSV calibrated to 2019 Andalucía 112 hourly/seasonal distributions."""
    rng = np.random.default_rng(42)
    now = datetime.now()

    HOTSPOT_SEEDS = [
        (37.3886, -5.9823), (37.3922, -5.9909), (37.3810, -5.9965),
        (37.3750, -5.9700), (37.4000, -5.9600),
    ]
    TYPES = [
        "traffic_accident", "traffic_disruption", "cardiac_arrest", "fire",
        "assault", "gas_leak", "fall_injury", "stroke", "utility_failure",
    ]
    TYPE_W = [0.055, 0.087, 0.220, 0.070, 0.120, 0.030, 0.120, 0.080, 0.018]
    TYPE_W_NORM = [w / sum(TYPE_W) for w in TYPE_W]

    SEVS  = ["critical", "high", "medium", "low"]
    SEV_W = [0.08, 0.22, 0.45, 0.25]

    _HOUR_WEIGHTS = np.array([
        69.6, 56.0, 45.7, 39.3, 35.6, 32.1, 33.8, 41.5, 52.6, 69.4,
        82.4, 94.9, 100.6, 100.5, 100.8, 95.3, 87.2, 90.3, 98.8, 106.1,
        109.4, 106.9, 97.7, 84.2,
    ])
    _HOUR_PROBS = _HOUR_WEIGHTS / _HOUR_WEIGHTS.sum()

    _DOW_WEIGHTS = np.array([1774.4, 1699.4, 1680.7, 1727.0, 1902.6, 2043.8, 1990.2])
    _DOW_PROBS   = _DOW_WEIGHTS / _DOW_WEIGHTS.sum()

    records = []
    for i in range(n):
        if rng.random() < 0.65:
            hs  = HOTSPOT_SEEDS[rng.integers(0, len(HOTSPOT_SEEDS))]
            lat = hs[0] + rng.normal(0, 0.002)
            lon = hs[1] + rng.normal(0, 0.003)
        else:
            lat = 37.3886 + rng.normal(0, 0.015)
            lon = -5.9823 + rng.normal(0, 0.020)

        sev        = rng.choice(SEVS, p=SEV_W)
        day_offset = rng.uniform(0, 90)
        hour       = int(rng.choice(24, p=_HOUR_PROBS))
        ts         = now - timedelta(days=day_offset, hours=now.hour - hour)
        records.append({
            "id":                    f"INC-{i:05d}",
            "timestamp":             ts.isoformat(),
            "incident_type":         rng.choice(TYPES, p=TYPE_W_NORM),
            "severity":              sev,
            "latitude":              round(float(lat), 5),
            "longitude":             round(float(lon), 5),
            "address":               "Sevilla",
            "response_time_minutes": round(float(rng.normal(6.5, 2.0)), 1),
            "resolved":              True,
        })

    df = pd.DataFrame(records)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    print(f"[generate] Saved {n} records : {output_path}")
    return df



if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AnalysisAgent standalone test")
    parser.add_argument("--generate", action="store_true", help="Generate sample data first")
    parser.add_argument("--n", type=int, default=500)
    cli = parser.parse_args()

    if cli.generate:
        generate_sample_data(n=cli.n)

    print("\nAnalysisAgent — direct tool test (no LLM)\n" + "-" * 50)

    print("\n1. Hotspot analysis:")
    h = json.loads(get_hotspot_analysis())
    for hs in h.get("hotspots", [])[:3]:
        print(f"   lat={hs['lat_center']} lon={hs['lon_center']} "
              f"count={hs['incident_count']} risk={hs['risk_score']} type={hs['dominant_type']}")

    print("\n2. 3-day forecast:")
    f = json.loads(get_trend_forecast(forecast_days=3))
    for day in f.get("forecast", []):
        print(f"   {day['date']} ({day['day_of_week'][:3]}): "
              f"~{day['predicted_incidents']} "
              f"[{day['confidence_interval_low']}–{day['confidence_interval_high']}]")

    print("\n3. Response time KPIs:")
    k = json.loads(get_response_time_stats())
    o = k.get("overall", {})
    print(f"   mean={o.get('mean')}min  p90={o.get('p90')}min  n={o.get('count')}  trend={k.get('trend')}")

    print("\n4. Post-dispatch annotation (background thread):")
    print("   (No LLM needed — uses direct tool calls)")

    class _MockModel:
        pass

    agent = AnalysisAgent.__new__(AnalysisAgent)
    agent.model = _MockModel()

    result = agent._direct_enrich("traffic_accident", 37.3886, -5.9823)
    print(f"   hotspot={result['is_hotspot']}  risk={result['risk_level']}")
    print(f"   note: {result['historical_note']}")