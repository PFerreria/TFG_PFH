"""
Build the Sevilla street gazetteer used by tools/extract_location.py for
fuzzy street-name matching.

Downloads every named street and square inside the Sevilla municipality
boundary from OpenStreetMap (Overpass API) and writes a name → [lat, lon]
mapping (street center) to data/sevilla_streets.json.

Run once (and re-run occasionally to pick up new streets):
    python tools/build_street_gazetteer.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

QUERY = """
[out:json][timeout:180];
area["boundary"="administrative"]["admin_level"="8"]["name"="Sevilla"]->.sevilla;
(
  way(area.sevilla)["highway"]["name"];
  nwr(area.sevilla)["place"="square"]["name"];
);
out tags center;
"""

OUT_PATH = Path(__file__).resolve().parents[1] / "data" / "sevilla_streets.json"


def main() -> int:
    resp = None
    for url in OVERPASS_URLS:
        print(f"Querying {url} for Sevilla street names…")
        try:
            resp = requests.post(
                url,
                data={"data": QUERY},
                headers={"User-Agent": "imers_emergency_dispatch/1.0 (street gazetteer build)"},
                timeout=240,
            )
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            print(f"  failed: {e}")
            resp = None
    if resp is None:
        print("ERROR: all Overpass mirrors failed")
        return 1
    elements = resp.json().get("elements", [])

    sums: dict[str, list[float]] = {}  # name -> [lat_sum, lon_sum, count]
    for el in elements:
        name = (el.get("tags") or {}).get("name", "").strip()
        if not name:
            continue
        center = el.get("center") or {}
        lat = center.get("lat", el.get("lat"))
        lon = center.get("lon", el.get("lon"))
        if lat is None or lon is None:
            continue
        acc = sums.setdefault(name, [0.0, 0.0, 0])
        acc[0] += lat
        acc[1] += lon
        acc[2] += 1

    if len(sums) < 1000:
        print(f"ERROR: only {len(sums)} names returned — Overpass result looks "
              f"incomplete, not overwriting {OUT_PATH}")
        return 1

    gazetteer = {
        name: [round(acc[0] / acc[2], 6), round(acc[1] / acc[2], 6)]
        for name, acc in sorted(sums.items())
    }
    OUT_PATH.write_text(
        json.dumps(gazetteer, ensure_ascii=False, indent=0),
        encoding="utf-8",
    )
    print(f"Wrote {len(gazetteer)} street names to {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
