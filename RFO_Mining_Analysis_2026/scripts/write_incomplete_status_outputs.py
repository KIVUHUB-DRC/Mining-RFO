#!/usr/bin/env python3
"""Write explicit status deliverables when full-reserve processing cannot finish."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from docx import Document
from docx.shared import Inches
from pyproj import CRS
from rasterio.transform import from_origin
from rasterio.windows import Window
from shapely.ops import transform as transform_geometry
from pyproj import Transformer

from analyze_rfo_mining_2026 import load_boundary, write_empty_shapefile


OUT = Path(__file__).resolve().parents[1]
INTERMEDIATE = OUT / "intermediate"
GIS = OUT / "gis"
MAPS = OUT / "maps"
REPORTS = OUT / "reports"
TABLES = OUT / "tables"
CRS_METRIC = CRS.from_epsg(32635)
RESOLUTION = 20
STATUS = "INCOMPLETE — pixel-level screening did not finish for the full reserve."
INVENTORY_COLUMNS = [
    "site_id", "latitude", "longitude", "area_ha", "periods", "first_period",
    "activity", "disturbance_type", "confidence", "dist_bnd_km", "river_km",
    "patrol_km", "dndvi", "devi", "dnbr", "dndmi", "dbsi", "dmndwi", "evidence_file",
]


def main() -> None:
    for folder in (OUT, INTERMEDIATE, GIS, MAPS, REPORTS, TABLES, OUT / "evidence"):
        folder.mkdir(parents=True, exist_ok=True)
    boundary, source_path, _ = load_boundary()
    geometry = boundary.geometry.union_all()
    projected = transform_geometry(
        Transformer.from_crs("EPSG:4326", CRS_METRIC, always_xy=True).transform,
        geometry,
    )
    area_ha = float(boundary.to_crs(CRS_METRIC).area.sum() / 10000)
    minx, miny, maxx, maxy = projected.bounds
    x0 = math.floor(minx / RESOLUTION) * RESOLUTION
    y0 = math.ceil(maxy / RESOLUTION) * RESOLUTION
    width = math.ceil((maxx - x0) / RESOLUTION)
    height = math.ceil((y0 - miny) / RESOLUTION)
    transform = from_origin(x0, y0, RESOLUTION, RESOLUTION)
    base_profile = {
        "driver": "GTiff",
        "width": width,
        "height": height,
        "crs": CRS_METRIC,
        "transform": transform,
        "compress": "deflate",
        "tiled": True,
        "BIGTIFF": "IF_SAFER",
    }

    disturbance_path = GIS / "RFO_Mining_Disturbance_2026.tif"
    with rasterio.open(
        disturbance_path,
        "w",
        **base_profile,
        count=1,
        dtype="uint8",
        nodata=255,
    ) as dst:
        dst.set_band_description(1, "NOT COMPUTED — all pixels are nodata")
        for top in range(0, height, 512):
            for left in range(0, width, 512):
                h = min(512, height - top)
                w = min(512, width - left)
                dst.write(
                    np.full((h, w), 255, dtype=np.uint8),
                    1,
                    window=Window(left, top, w, h),
                )

    index_names = ("NDVI", "EVI", "NBR", "NDMI", "BSI", "MNDWI")
    bands = index_names + ("Q1_BSI_delta", "Q2_BSI_delta", "Q3_BSI_delta", "Q4_BSI_delta")
    change_path = GIS / "RFO_Mining_Change_Detection_2026.tif"
    with rasterio.open(
        change_path,
        "w",
        **base_profile,
        count=len(bands),
        dtype="float32",
        nodata=-9999.0,
    ) as dst:
        for index, name in enumerate(bands, start=1):
            dst.set_band_description(index, f"NOT COMPUTED — {name}")
            for top in range(0, height, 512):
                for left in range(0, width, 512):
                    h = min(512, height - top)
                    w = min(512, width - left)
                    dst.write(
                        np.full((h, w), -9999.0, dtype=np.float32),
                        index,
                        window=Window(left, top, w, h),
                    )

    inventory = TABLES / "RFO_Mining_Inventory_2026.csv"
    pd.DataFrame(columns=INVENTORY_COLUMNS).to_csv(inventory, index=False)
    (GIS / "RFO_Mining_Sites_2026.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [],
                "analysis_status": STATUS,
                "note": "Empty because reserve-wide processing was incomplete; not evidence of no mining.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    write_empty_shapefile(GIS / "RFO_Mining_Sites_2026.shp")

    scene_path = INTERMEDIATE / "sentinel2_scene_register.csv"
    scene_register = pd.read_csv(scene_path) if scene_path.exists() else pd.DataFrame()
    quarter_rows = [
        {
            "period": quarter,
            "candidate_patches": pd.NA,
            "candidate_area_ha": pd.NA,
            "status": "NOT COMPUTED",
        }
        for quarter in ("Q1", "Q2", "Q3", "Q4")
    ]
    pd.DataFrame(quarter_rows).to_csv(TABLES / "quarterly_statistics.csv", index=False)

    data_sources = pd.DataFrame(
        [
            ["Sentinel-2 L2A", "Catalog and sample pixels accessible; full AOI processing incomplete", "10 m visible/NIR; 20 m SWIR/SCL", "2015–present", "Selected scene list in intermediate/sentinel2_scene_register.csv", "https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-2-l2a"],
            ["Landsat 8/9 C2 L2", "Catalog queried; not processed", "30 m multispectral", "1982–present", "No change analysis", "https://planetarycomputer.microsoft.com/api/stac/v1/collections/landsat-c2-l2"],
            ["Sentinel-1 GRD", "Catalog queried; not processed", "10 m class; VV/VH GRD", "2014–present", "No radar processing", "https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-1-grd"],
            ["Planet NICFI", "Not accessed", "4.77 m mosaics", "Access/licensing dependent", "No credentials or account access checked", "https://www.planet.com/nicfi/"],
            ["GLAD/GFW alerts", "Not processed", "Nominal 30 m alert products", "Product dependent", "No authenticated query", "https://www.globalforestwatch.org/"],
            ["Hansen Global Forest Change", "Not processed", "30 m", "Annual; product-version dependent", "Not used as 2026 ground truth", "https://glad.earthengine.app/view/global-forest-change"],
            ["NASA FIRMS", "Not processed", "VIIRS 375 m / MODIS 1 km", "Near-real time and archive", "API key not configured; fire is not mining evidence", "https://firms.modaps.eosdis.nasa.gov/"],
            ["OpenStreetMap rivers", "Not queried for sites", "Variable vector detail", "Current snapshot", "No sites available for proximity screening", "https://overpass-api.de/"],
            ["Patrol posts", "Unavailable", "Not applicable", "No layer supplied", "Distance not calculated", ""],
        ],
        columns=["dataset", "status", "resolution", "temporal_coverage", "access_or_use_notes", "source_url"],
    )

    manifest = {
        "analysis_status": "incomplete",
        "status_message": STATUS,
        "boundary_file": source_path.name,
        "boundary_geometry": boundary.geometry.iloc[0].geom_type,
        "boundary_crs": "EPSG:4326 (GeoJSON default)",
        "analysis_crs": CRS_METRIC.to_string(),
        "boundary_area_ha": area_ha,
        "boundary_extent_lonlat": list(boundary.total_bounds),
        "candidate_site_count": None,
        "candidate_disturbed_area_ha": None,
        "confirmed_mining_site_count": None,
        "interpretation": "No reserve-wide count or area estimate is available; blank/NoData outputs are not zero findings.",
        "earth_engine_status": "Not authenticated; public STAC imagery was used for the attempted optical pass.",
        "patrol_posts": "No authoritative reference layer supplied.",
        "processing_resolution_m": RESOLUTION,
        "scene_register": "intermediate/sentinel2_scene_register.csv",
        "processing_attempt_log": "intermediate/run.log",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }
    (TABLES / "analysis_summary.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    maps = []
    boundary_map = gpd.GeoDataFrame(geometry=[geometry], crs="EPSG:4326").to_crs(CRS_METRIC)
    map_specs = [
        ("Overall_RFO_Mining_Activity_Map.png", "Overall activity"),
        ("Mining_Locations_by_Quarter_Map.png", "Quarterly locations"),
        ("New_Mining_Locations_2026_Map.png", "New locations"),
        ("Mining_Expansion_Map.png", "Expansion"),
        ("Mining_River_and_Patrol_Proximity_Map.png", "River and patrol proximity"),
    ]
    for filename, title in map_specs:
        fig, ax = plt.subplots(figsize=(9, 8))
        boundary_map.boundary.plot(ax=ax, color="#263238", linewidth=0.8)
        ax.set_title(f"NOT ANALYZED — {title}\nFull-reserve pixel processing did not finish")
        ax.text(
            0.5,
            0.5,
            "No mining result available\nDo not interpret as no mining",
            transform=ax.transAxes,
            ha="center",
            va="center",
            color="#a93226",
            fontsize=12,
            bbox={"facecolor": "white", "alpha": 0.85, "edgecolor": "#a93226"},
        )
        ax.set_xlabel("UTM Zone 35N Easting (m)")
        ax.set_ylabel("UTM Zone 35N Northing (m)")
        ax.set_aspect("equal", adjustable="box")
        fig.tight_layout()
        path = MAPS / filename
        fig.savefig(path, dpi=160)
        plt.close(fig)
        maps.append(path)

    workbook = TABLES / "RFO_Mining_Statistics_2026.xlsx"
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        pd.DataFrame([manifest]).to_excel(writer, sheet_name="Status", index=False)
        pd.DataFrame(quarter_rows).to_excel(writer, sheet_name="Quarterly", index=False)
        pd.DataFrame(columns=INVENTORY_COLUMNS).to_excel(writer, sheet_name="Inventory", index=False)
        scene_register.to_excel(writer, sheet_name="Scene Register", index=False)
        data_sources.to_excel(writer, sheet_name="Data Sources", index=False)

    report = Document()
    report.add_heading("Okapi Wildlife Reserve Mining Activity Assessment", 0)
    report.add_paragraph("Requested period: 1 January–31 October 2026")
    report.add_heading("Assessment status: incomplete", level=1)
    report.add_paragraph(
        "Sentinel-2 catalog records and sample cloud-optimized pixels were accessible. "
        "The full-reserve, season-matched raster pass exceeded the available command execution "
        "window and was terminated before all tiles and quarters were processed. Consequently, "
        "this report contains no reserve-wide mining detection result."
    )
    report.add_paragraph(
        "No count, area, coordinates, activity status or confidence assessment is reported. "
        "The empty site layers and NoData rasters are explicit status placeholders, not zero "
        "detections and not evidence that mining is absent. No site is confirmed."
    )
    sections = [
        ("1. Executive summary", f"Boundary area: {area_ha:,.2f} ha. Mining result: not determined."),
        ("2. Background and objectives", "The objective is to screen the RFO for mining-related disturbance from January through October 2026."),
        ("3. Study area description", f"Source {source_path.name}: valid {boundary.geometry.iloc[0].geom_type}; WGS84 GeoJSON; EPSG:32635 area calculation. WGS84 extent: {', '.join(f'{v:.6f}' for v in boundary.total_bounds)}."),
        ("4. Data sources and methodology", "Public Sentinel-2 L2A STAC scenes were catalogued for season-matched 2025 and 2026 periods. The attempted script masks SCL cloud/shadow classes and computes NDVI, EVI, NBR, NDMI, BSI and MNDWI at 20 m. It did not complete reserve-wide raster processing. Other source status and links are in the workbook."),
        ("5. Mining detection results", "Not determined. No reserve-wide candidates were persisted or validated."),
        ("6. Spatial distribution of mining activities", "Not determined; no coordinates or candidate polygons are reported."),
        ("7. Mining expansion and temporal analysis", "Not determined. A reference inventory of previously known mines was not supplied."),
        ("8. Environmental implications", "No site-specific impacts can be assessed without validated detections."),
        ("9. Limitations and confidence assessment", "Processing was incomplete; cloud/phenology/confounder review and independent visual or field validation were not performed. Earth Engine authentication was absent. Sentinel-1, Landsat, GLAD/GFW, Hansen and FIRMS were not processed."),
        ("10. Recommendations for RFO management and patrol operations", "Complete the script in a runtime that permits the full raster job; review all candidates with high-resolution imagery and local knowledge; provide patrol-post and historical mine layers; then validate in the field where safe and appropriate."),
        ("11. Maps, tables, imagery and references", "The maps below show the reserve boundary only and are explicitly marked NOT ANALYZED. No site evidence imagery exists because site detection did not complete."),
    ]
    for heading, text in sections:
        report.add_heading(heading, level=1)
        report.add_paragraph(text)
        if heading.startswith("11."):
            for path in maps:
                report.add_picture(str(path), width=Inches(5.8))
                report.add_paragraph(path.name, style="Caption")
    report.add_paragraph(
        "References: Sentinel-2 L2A catalog "
        "https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-2-l2a ; "
        "Landsat C2 L2 https://planetarycomputer.microsoft.com/api/stac/v1/collections/landsat-c2-l2 ; "
        "Sentinel-1 GRD https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-1-grd ; "
        "Global Forest Watch https://www.globalforestwatch.org/ ; NASA FIRMS "
        "https://firms.modaps.eosdis.nasa.gov/ ."
    )
    report.save(REPORTS / "RFO_Mining_Activity_Assessment_2026.docx")

    (OUT / "PROCESSING_STATUS.txt").write_text(
        STATUS
        + "\n"
        + "Sentinel-2 catalog and sample pixels were accessible. Full-reserve processing was "
        "terminated before completion by the execution environment.\n"
        + "All empty site outputs and NoData rasters are status placeholders, not zero findings. "
        + "Do not use them as evidence that no mining occurred.\n"
        + "See intermediate/run.log, intermediate/sentinel2_scene_register.csv, and "
        + "analyze_rfo_mining_2026.py.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
