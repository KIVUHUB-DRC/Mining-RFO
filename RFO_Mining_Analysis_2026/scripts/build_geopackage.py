#!/usr/bin/env python3
"""Build a GeoPackage containing the reserve, candidate layer, and analysis tables."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio


ROOT = Path(__file__).resolve().parents[2]
ANALYSIS = Path(__file__).resolve().parents[1]
DATABASE = ANALYSIS / "RFO_Mining_Analysis_2026.gpkg"
CRS = "EPSG:4326"


def read_table(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def write_table(frame: pd.DataFrame, layer: str, *, first: bool = False) -> None:
    clean = frame.copy()
    for column in clean.columns:
        if clean[column].dtype == "object":
            clean[column] = clean[column].map(
                lambda value: json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else value
            )
    pyogrio.write_dataframe(
        clean,
        DATABASE,
        layer=layer,
        driver="GPKG",
        append=not first,
    )


def main() -> None:
    boundary_path = ROOT / "RFO.json"
    boundary = gpd.read_file(boundary_path)
    if boundary.crs is None:
        boundary = boundary.set_crs(CRS)
    else:
        boundary = boundary.to_crs(CRS)
    if not boundary.geometry.is_valid.all():
        raise ValueError("The source boundary contains invalid geometry.")
    boundary["source_file"] = boundary_path.name
    boundary["analysis_status"] = "reference boundary; source geometry preserved"
    boundary["area_ha_utm35"] = boundary.to_crs("EPSG:32635").area / 10000

    sites_path = ANALYSIS / "gis" / "RFO_Mining_Sites_2026.geojson"
    sites = gpd.read_file(sites_path)
    if sites.crs is None:
        sites = sites.set_crs(CRS)
    else:
        sites = sites.to_crs(CRS)
    expected_status = "incomplete"
    summary_path = ANALYSIS / "tables" / "analysis_summary.json"
    with summary_path.open(encoding="utf-8") as source:
        summary = json.load(source)
    if summary.get("analysis_status") != expected_status:
        raise ValueError(
            "This database builder is for incomplete status outputs only; "
            "use a status-aware builder for completed analysis."
        )

    if DATABASE.exists():
        DATABASE.unlink()
    pyogrio.write_dataframe(
        boundary,
        DATABASE,
        layer="rfo_boundary",
        driver="GPKG",
        layer_metadata={
            "description": "Original RFO boundary; EPSG:4326; area attribute uses EPSG:32635.",
        },
    )
    sites["analysis_status"] = "incomplete; empty layer is not evidence of no mining"
    pyogrio.write_dataframe(
        sites,
        DATABASE,
        layer="potential_mining_sites",
        driver="GPKG",
        append=True,
        geometry_type="Polygon",
        layer_metadata={
            "description": "No site results available; reserve-wide screening did not finish.",
        },
    )

    scene_register = read_table(ANALYSIS / "intermediate" / "sentinel2_scene_register.csv")
    quarters = read_table(ANALYSIS / "tables" / "quarterly_statistics.csv")
    inventory = read_table(ANALYSIS / "tables" / "RFO_Mining_Inventory_2026.csv")
    try:
        workbook = pd.ExcelFile(ANALYSIS / "tables" / "RFO_Mining_Statistics_2026.xlsx")
        sources = pd.read_excel(workbook, sheet_name="Data Sources")
    except (FileNotFoundError, ValueError):
        sources = pd.DataFrame(columns=["dataset", "status", "source_url"])

    outputs = [
        ("report", "reports/RFO_Mining_Activity_Assessment_2026.docx", "assessment report; incomplete"),
        ("change_raster", "gis/RFO_Mining_Change_Detection_2026.tif", "all NoData; not computed"),
        ("disturbance_raster", "gis/RFO_Mining_Disturbance_2026.tif", "all NoData; not computed"),
        ("site_geojson", "gis/RFO_Mining_Sites_2026.geojson", "empty; not evidence of no mining"),
        ("site_shapefile", "gis/RFO_Mining_Sites_2026.shp", "empty; not evidence of no mining"),
    ]
    status_table = pd.DataFrame(
        [
            {"key": "analysis_status", "value": summary["analysis_status"]},
            {"key": "status_message", "value": summary["status_message"]},
            {"key": "interpretation", "value": summary["interpretation"]},
            {"key": "boundary_file", "value": summary["boundary_file"]},
            {"key": "boundary_geometry", "value": summary["boundary_geometry"]},
            {"key": "boundary_crs", "value": summary["boundary_crs"]},
            {"key": "analysis_crs", "value": summary["analysis_crs"]},
            {"key": "boundary_area_ha", "value": summary["boundary_area_ha"]},
            {"key": "candidate_site_count", "value": summary["candidate_site_count"]},
            {"key": "confirmed_mining_site_count", "value": summary["confirmed_mining_site_count"]},
            {"key": "earth_engine_status", "value": summary["earth_engine_status"]},
            {"key": "patrol_posts", "value": summary["patrol_posts"]},
        ]
    )
    first_table = True
    for frame, layer in (
        (status_table, "analysis_status"),
        (quarters, "quarterly_statistics"),
        (inventory, "candidate_inventory"),
        (scene_register, "sentinel2_scene_register"),
        (sources, "data_sources"),
        (pd.DataFrame(outputs, columns=["output_type", "relative_path", "status"]), "deliverables"),
    ):
        write_table(frame, layer, first=first_table)
        first_table = False

    print(f"Created {DATABASE}")
    print(f"Layers: {', '.join(name for name, _ in pyogrio.list_layers(DATABASE))}")
    print(
        f"Boundary features: {len(boundary)}; candidate features: {len(sites)}; "
        f"catalog scenes: {len(scene_register)}."
    )


if __name__ == "__main__":
    main()
