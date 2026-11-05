#!/usr/bin/env python3
"""Build RFO-clipped Sentinel-2 catalog coverage footprints from STAC scenes."""
import csv
import json
from collections import defaultdict
from pathlib import Path
from urllib.request import urlopen

from shapely.geometry import mapping, shape
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[2]
ANALYSIS = ROOT / "RFO_Mining_Analysis_2026"
SCENES = ANALYSIS / "intermediate" / "scenes.csv"
OUTPUT = ANALYSIS / "gis" / "coverage.geojson"
STAC_ITEMS = "https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-2-l2a/items/"


def main():
    boundary_data = json.loads((ROOT / "RFO.json").read_text())
    boundary = unary_union([shape(feature["geometry"]) for feature in boundary_data["features"]])
    groups = defaultdict(list)
    representatives = {}
    with SCENES.open(newline="") as stream:
        for record in csv.DictReader(stream):
            groups[record["tile"]].append(record)
            representatives.setdefault(record["tile"], record["scene_id"])

    features = []
    for tile, scene_id in sorted(representatives.items()):
        request = urlopen(STAC_ITEMS + scene_id, timeout=30)
        item = json.load(request)
        if item["properties"].get("s2:mgrs_tile") != tile:
            raise ValueError(f"STAC tile mismatch for {scene_id}")
        footprint = shape(item["geometry"]).intersection(boundary)
        if footprint.is_empty:
            continue
        records = groups[tile]
        periods = []
        for year in sorted({record["year"] for record in records}):
            for period in ("Q1", "Q2", "Q3", "Q4"):
                rows = [r for r in records if r["year"] == year and r["period"] == period]
                if rows:
                    representative = next((row for row in rows if row["selected"].lower() == "true"), rows[0])
                    periods.append({
                        "year": int(year),
                        "period": period,
                        "count": len(rows),
                        "selected": sum(row["selected"].lower() == "true" for row in rows),
                        "mean_cloud_pct": round(sum(float(row["cloud_cover_pct"]) for row in rows) / len(rows), 6),
                        "first_date": min(row["date"] for row in rows),
                        "last_date": max(row["date"] for row in rows),
                        "source_item": STAC_ITEMS + representative["scene_id"],
                    })
        features.append({
            "type": "Feature",
            "geometry": mapping(footprint),
            "properties": {
                "tile": tile,
                "catalog_records": len(records),
                "periods": periods,
                "source_scene": scene_id,
                "source": "Microsoft Planetary Computer Sentinel-2 L2A STAC",
                "source_item": STAC_ITEMS + scene_id,
                "interpretation": "Imagery catalog footprint only; not a mining detection.",
            },
        })

    result = {
        "type": "FeatureCollection",
        "name": "RFO Sentinel-2 catalog coverage",
        "description": "Catalog scene footprints clipped to the authoritative RFO boundary. These are imagery coverage, not mining observations.",
        "source": "https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-2-l2a",
        "features": features,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, separators=(",", ":")) + "\n")
    print(f"Wrote {len(features)} clipped tile footprints to {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
