"""Lifecycle manager for AnalysisAgent: dashboard scheduler and post-dispatch annotation hook."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class AnalysisRunner:
    """Owns the AnalysisAgent, its dashboard cache, the APScheduler job and the post-dispatch hook."""

    def __init__(
        self,
        hf_token:  Optional[str] = None,
        model_id:  str           = "Qwen/Qwen2.5-72B-Instruct",
    ):
        import os
        import sys
        from agents.analysis_agent   import AnalysisAgent

        parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
        from llm_clients import OllamaModel

        model = OllamaModel("llama-3.1-8b-instant")
        logger.info("[AnalysisRunner] Initialising AnalysisAgent (cascade: Fireworks → Ollama → Groq → HuggingFace).")

        self._agent  = AnalysisAgent(model)
        self._cache: Optional[dict] = None
        self._scheduler = None
        logger.info("[AnalysisRunner] Ready")


    def refresh_dashboard(self) -> dict:
        """Computes fresh dashboard analytics and stores in cache.

        Returns:
            dict with hotspots, forecast, kpis, generated_at.
        """
        logger.info("[AnalysisRunner] Refreshing dashboard data...")
        t0   = time.perf_counter()
        data = self._agent.generate_dashboard_data()
        self._cache = data
        elapsed = time.perf_counter() - t0
        logger.info(f"[AnalysisRunner] Dashboard refreshed in {elapsed:.1f}s "
                    f"({len(data.get('hotspots', []))} hotspots, "
                    f"{len(data.get('forecast', []))} forecast days)")
        return data

    def get_dashboard_data(self) -> dict:
        """Returns cached dashboard data, calling refresh_dashboard() on first access."""
        if self._cache is None:
            self.refresh_dashboard()
        return self._cache

    def start_scheduler(self, interval_minutes: int = 60) -> None:
        """Starts a BackgroundScheduler that calls refresh_dashboard() every interval_minutes.

        Args:
            interval_minutes: Refresh interval (default 60).
        """
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            self._scheduler = BackgroundScheduler()
            self._scheduler.add_job(
                self.refresh_dashboard,
                "interval",
                minutes=interval_minutes,
                id="dashboard_refresh",
            )
            self._scheduler.start()
            self.refresh_dashboard()
            logger.info(f"[AnalysisRunner] Scheduler started — "
                        f"refreshing every {interval_minutes} min")
        except ImportError:
            logger.warning(
                "[AnalysisRunner] apscheduler not installed — "
                "running single refresh only. pip install apscheduler"
            )
            self.refresh_dashboard()

    def stop_scheduler(self) -> None:
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("[AnalysisRunner] Scheduler stopped")


    def annotate_incident(
        self,
        incident_id:   str,
        incident_type: str,
        lat:           Optional[float],
        lon:           Optional[float],
        on_complete=None,
    ) -> None:
        """Gives a completed incident historical context in a background thread.

        Args:
            incident_id:   Incident identifier.
            incident_type: e.g. "traffic_accident".
            lat, lon:      Incident coordinates (can be None).
            on_complete:   Optional callback receiving the annotation dict.
        """
        self._agent.annotate_incident_async(
            incident_id=incident_id,
            incident_type=incident_type,
            lat=lat,
            lon=lon,
            on_complete=on_complete,
        )
        logger.debug(f"[AnalysisRunner] Background annotation started for {incident_id}")

    def attach_to_pipeline(self, pipeline, on_complete=None) -> None:
        """Registers the post-dispatch annotation hook on an IMERSPipeline instance.

        Args:
            pipeline:    An IMERSPipeline instance.
            on_complete: Optional callback for each annotation result.
        """
        def _hook(report: dict) -> None:
            loc           = report.get("location") or {}
            incident_id   = report.get("incident_id", "UNKNOWN")
            incident_type = report.get("incident_type", "other")
            lat           = loc.get("latitude")
            lon           = loc.get("longitude")

            _cb = on_complete or (
                lambda ann: logger.info(
                    f"[AnalysisRunner] Annotation for {ann.get('incident_id')}: "
                    f"hotspot={ann.get('is_hotspot')}, "
                    f"risk={ann.get('risk_level')}"
                )
            )
            self.annotate_incident(incident_id, incident_type, lat, lon, on_complete=_cb)

        pipeline.add_post_dispatch_hook(_hook)
        logger.info("[AnalysisRunner] Post-dispatch hook attached to pipeline")



if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="IMERS Analysis Runner — standalone tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Refresh dashboard data once and print results:
  python analysis_runner.py --dashboard

  # Annotate a specific incident:
  python analysis_runner.py --annotate INC-00142 traffic_accident 37.3886 -5.9823

  # Run continuous scheduler (Ctrl-C to stop):
  python analysis_runner.py --scheduler --interval 60

  # Generate 500 synthetic incidents for testing:
  python analysis_runner.py --generate-data --n 500
        """,
    )
    parser.add_argument("--dashboard",     action="store_true", help="Generate dashboard data once")
    parser.add_argument("--annotate",      nargs=4,             metavar=("ID","TYPE","LAT","LON"))
    parser.add_argument("--scheduler",     action="store_true", help="Run continuous scheduler")
    parser.add_argument("--interval",      type=int, default=60)
    parser.add_argument("--generate-data", action="store_true", help="Generate synthetic test data")
    parser.add_argument("--n",             type=int, default=500)
    args = parser.parse_args()

    if args.generate_data:
        from agents.analysis_agent import generate_sample_data
        generate_sample_data(n=args.n)
        print(f"Generated {args.n} synthetic incidents.")

    if args.dashboard:
        from agents.analysis_agent import (
            get_hotspot_analysis, get_trend_forecast, get_response_time_stats
        )
        print("\n-- Dashboard data (direct tool calls, no LLM) --\n")

        print("Hotspot analysis:")
        h = json.loads(get_hotspot_analysis())
        for hs in h.get("hotspots", [])[:5]:
            print(f"  [{hs['risk_score']:3d}] {hs['lat_center']:.4f},{hs['lon_center']:.4f}"
                  f"  n={hs['incident_count']}  type={hs['dominant_type']}")
        if not h.get("hotspots"):
            print("  No hotspots detected (need more data — run --generate-data first)")

        print("\n7-day forecast:")
        f = json.loads(get_trend_forecast(forecast_days=7))
        for day in f.get("forecast", []):
            print(f"  {day['date']} ({day['day_of_week'][:3]}) "
                  f"~{day['predicted_incidents']} incidents "
                  f"[{day['confidence_interval_low']}–{day['confidence_interval_high']}]")
        if not f.get("forecast"):
            print(f"  {f.get('note', 'No forecast available')}")

        print("\nResponse time KPIs:")
        k = json.loads(get_response_time_stats())
        o = k.get("overall", {})
        if o:
            print(f"  mean={o.get('mean')}min  median={o.get('median')}min  "
                  f"p90={o.get('p90')}min  n={o.get('count')}  trend={k.get('trend')}")
        else:
            print(f"  {k.get('note', 'No data')}")

    if args.annotate:
        inc_id, inc_type, lat_s, lon_s = args.annotate
        lat = float(lat_s)
        lon = float(lon_s)
        print(f"\n-- Annotating {inc_id} ({inc_type}, {lat}, {lon}) --\n")

        from agents.analysis_agent import AnalysisAgent as _AA

        class _DirectAgent(_AA):
            def __init__(self): self.model = None
            def _build_agent(self, *a, **kw): pass

        agent = _AA.__new__(_AA)
        result = agent._direct_enrich(inc_type, lat, lon)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.scheduler:
        print(f"\n-- Starting scheduler (interval={args.interval}min, Ctrl-C to stop) --\n")
        try:
            from agents.analysis_agent import (
                get_hotspot_analysis, get_trend_forecast, get_response_time_stats
            )
            import time as _time

            iteration = 0
            while True:
                iteration += 1
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Refresh #{iteration}")
                h = json.loads(get_hotspot_analysis())
                f = json.loads(get_trend_forecast(forecast_days=3))
                k = json.loads(get_response_time_stats())
                print(f"  hotspots={len(h.get('hotspots',[]))}  "
                      f"forecast_days={len(f.get('forecast',[]))}  "
                      f"rt_mean={k.get('overall',{}).get('mean','—')}min")
                _time.sleep(args.interval * 60)

        except KeyboardInterrupt:
            print("\nScheduler stopped.")
            sys.exit(0)

    if not any([args.dashboard, args.annotate, args.scheduler, args.generate_data]):
        parser.print_help()