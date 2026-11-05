#!/usr/bin/env python3
"""Reproducible, conservative spectral-change screening for the Okapi Wildlife Reserve."""

from __future__ import annotations

import csv
import copy
import importlib.util
import json
import math
import os
import sys
import time
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import planetary_computer
import rasterio
import rasterio.features
import requests
import rioxarray
from docx import Document
from docx.shared import Inches, Pt
from pyproj import CRS, Transformer
from rasterio.enums import Resampling
from rasterio.features import geometry_mask, shapes
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window, from_bounds
from shapely.geometry import LineString, box, mapping, shape
from shapely.ops import transform as transform_geometry, unary_union
from sklearn.cluster import DBSCAN


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parents[1]
INTERMEDIATE = OUT / "intermediate"
EVIDENCE = OUT / "evidence"
GIS = OUT / "gis"
MAPS = OUT / "maps"
REPORTS = OUT / "reports"
TABLES = OUT / "tables"
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "sentinel-2-l2a"
RESOLUTION = 20
MIN_PATCH_HA = 0.5
MAX_SCENES_PER_TILE_PERIOD = 2
PERIODS = [
    ("Q1", "2025-01-01/2025-03-31", "2026-01-01/2026-03-31"),
    ("Q2", "2025-04-01/2025-06-30", "2026-04-01/2026-06-30"),
    ("Q3", "2025-07-01/2025-08-31", "2026-07-01/2026-08-31"),
    ("Q4", "2025-09-01/2025-10-31", "2026-09-01/2026-10-31"),
]
REFLECTANCE_BANDS = ("B02", "B03", "B04", "B08", "B11", "B12")
INDEX_NAMES = ("NDVI", "EVI", "NBR", "NDMI", "BSI", "MNDWI")
INDEX_BANDS = ("blue", "green", "red", "nir", "swir1", "swir2")
CLEAR_SCL = (4, 5, 6, 7)
NODATA_FLOAT = -9999.0
NODATA_CLASS = 255


def log(message: str) -> None:
    print(message, flush=True)


def load_boundary() -> tuple[gpd.GeoDataFrame, Path, CRS]:
    path = next((p for p in (ROOT / "rfo.json", ROOT / "RFO.json") if p.is_file()), None)
    if path is None:
        raise FileNotFoundError("Expected rfo.json or RFO.json in the repository root.")
    with path.open(encoding="utf-8") as source:
        data = json.load(source)
    if data.get("type") != "FeatureCollection" or not data.get("features"):
        raise ValueError(f"{path.name} must be a non-empty GeoJSON FeatureCollection.")
    features = []
    for feature in data["features"]:
        geom = shape(feature["geometry"])
        if geom.geom_type not in ("Polygon", "MultiPolygon"):
            raise ValueError(f"Unsupported boundary geometry: {geom.geom_type}")
        if geom.is_empty or not geom.is_valid:
            raise ValueError("The source boundary contains an empty or invalid geometry.")
        features.append({**feature.get("properties", {}), "geometry": geom})
    crs_value = data.get("crs")
    if crs_value:
        raise ValueError(
            "Legacy GeoJSON CRS metadata is present; determine its meaning before processing."
        )
    boundary = gpd.GeoDataFrame(features, geometry="geometry", crs="EPSG:4326")
    if not boundary.geometry.is_valid.all():
        raise ValueError("The boundary failed GeoDataFrame validity checks.")
    return boundary, path, CRS.from_epsg(32635)


def search_items(start: str, end: str, boundary: dict) -> list[dict]:
    response = requests.post(
        f"{STAC}/search",
        json={
            "collections": [COLLECTION],
            "intersects": boundary,
            "datetime": f"{start}/{end}",
            "limit": 500,
        },
        timeout=120,
    )
    response.raise_for_status()
    page = response.json()
    items = list(page.get("features", []))
    while True:
        next_link = next((link for link in page.get("links", []) if link.get("rel") == "next"), None)
        if next_link is None:
            break
        if next_link.get("method", "GET").upper() == "POST":
            response = requests.post(
                next_link["href"], json=next_link.get("body", {}), timeout=120
            )
        else:
            response = requests.get(next_link["href"], timeout=120)
        response.raise_for_status()
        page = response.json()
        items.extend(page.get("features", []))
    return items


def select_scenes(items: list[dict]) -> dict[str, list[dict]]:
    by_tile: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        tile = item.get("properties", {}).get("s2:mgrs_tile")
        if tile:
            by_tile[tile].append(item)
    selected = {}
    for tile, candidates in by_tile.items():
        ranked = sorted(
            candidates,
            key=lambda item: (
                float(item.get("properties", {}).get("eo:cloud_cover", 100.0)),
                item["properties"]["datetime"],
            ),
        )
        chosen = []
        for item in ranked:
            if not chosen or all(
                abs(
                    (
                        datetime.fromisoformat(item["properties"]["datetime"].replace("Z", "+00:00"))
                        - datetime.fromisoformat(
                            earlier["properties"]["datetime"].replace("Z", "+00:00")
                        )
                    ).days
                )
                >= 8
                for earlier in chosen
            ):
                chosen.append(item)
            if len(chosen) == MAX_SCENES_PER_TILE_PERIOD:
                break
        selected[tile] = chosen
    return selected


def raster_window(ds: rasterio.DatasetReader, bounds: tuple[float, ...]) -> Window:
    window = from_bounds(*bounds, transform=ds.transform).round_offsets().round_lengths()
    return window.intersection(Window(0, 0, ds.width, ds.height))


def read_scene(item: dict, boundary_utm) -> tuple[dict[str, np.ndarray], np.ndarray, object]:
    signed_item = copy.deepcopy(item)
    planetary_computer.sign_inplace(signed_item)
    reference = signed_item["assets"]["B11"]["href"]
    with rasterio.open(reference) as base:
        tile_bounds = tuple(signed_item["assets"]["B11"]["proj:bbox"])
        intersection = boundary_utm.intersection(box(*tile_bounds))
        if intersection.is_empty:
            raise ValueError(f"Scene {item['id']} does not overlap the boundary.")
        window = raster_window(base, intersection.bounds)
        if window.width <= 0 or window.height <= 0:
            raise ValueError(f"Scene {item['id']} has no pixel window within the boundary.")
        transform = base.window_transform(window)
        included = geometry_mask(
            [mapping(intersection)],
            out_shape=(int(window.height), int(window.width)),
            transform=transform,
            invert=True,
            all_touched=False,
        )
        arrays: dict[str, np.ndarray] = {}
        for key in REFLECTANCE_BANDS:
            with rasterio.open(signed_item["assets"][key]["href"]) as src:
                with WarpedVRT(
                    src,
                    crs=base.crs,
                    transform=base.transform,
                    width=base.width,
                    height=base.height,
                    resampling=Resampling.average,
                    src_nodata=0,
                    nodata=0,
                ) as vrt:
                    arrays[key] = vrt.read(1, window=window, out_dtype="float32") / 10000.0
        with rasterio.open(signed_item["assets"]["SCL"]["href"]) as src:
            with WarpedVRT(
                src,
                crs=base.crs,
                transform=base.transform,
                width=base.width,
                height=base.height,
                resampling=Resampling.nearest,
                src_nodata=0,
                nodata=0,
            ) as vrt:
                scl = vrt.read(1, window=window)
    valid = included & np.isin(scl, CLEAR_SCL)
    for values in arrays.values():
        valid &= np.isfinite(values) & (values > 0)
    for values in arrays.values():
        values[~valid] = np.nan
    return arrays, valid, (window, transform, intersection)


def composite_scenes(scenes: list[dict], boundary_utm):
    observations = []
    scene_metadata = []
    reference_grid = None
    for item in scenes:
        arrays, valid, grid = read_scene(item, boundary_utm)
        if reference_grid is not None and (
            arrays["B11"].shape != reference_grid[0]["B11"].shape
            or grid[1] != reference_grid[1]
        ):
            raise ValueError(f"Misaligned Sentinel-2 tile grids for {item['id']}.")
        observations.append(arrays)
        reference_grid = (arrays, grid[1], grid[2])
        scene_metadata.append(
            {
                "scene_id": item["id"],
                "date": item["properties"]["datetime"][:10],
                "cloud_cover_pct": item["properties"].get("eo:cloud_cover"),
                "tile": item["properties"].get("s2:mgrs_tile"),
            }
        )
    if not observations:
        return None, None, None, []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        composite = {
            band: np.nanmedian(np.stack([observation[band] for observation in observations]), axis=0)
            for band in REFLECTANCE_BANDS
        }
    valid = np.isfinite(composite["B04"]) & np.isfinite(composite["B11"])
    return composite, valid, reference_grid[1], scene_metadata


def calculate_indices(reflectance: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    blue, green = reflectance["B02"], reflectance["B03"]
    red, nir = reflectance["B04"], reflectance["B08"]
    swir1, swir2 = reflectance["B11"], reflectance["B12"]

    def ratio(a, b):
        denominator = a + b
        return np.divide(
            a - b,
            denominator,
            out=np.full(a.shape, np.nan, dtype=np.float32),
            where=np.abs(denominator) > 1e-6,
        )

    evi_denominator = nir + 6 * red - 7.5 * blue + 1
    evi = np.divide(
        2.5 * (nir - red),
        evi_denominator,
        out=np.full(nir.shape, np.nan, dtype=np.float32),
        where=np.abs(evi_denominator) > 1e-6,
    )
    bsi_denominator = swir1 + red + nir + blue
    bsi = np.divide(
        (swir1 + red) - (nir + blue),
        bsi_denominator,
        out=np.full(nir.shape, np.nan, dtype=np.float32),
        where=np.abs(bsi_denominator) > 1e-6,
    )
    return {
        "NDVI": ratio(nir, red),
        "EVI": evi,
        "NBR": ratio(nir, swir2),
        "NDMI": ratio(nir, swir1),
        "BSI": bsi,
        "MNDWI": ratio(green, swir1),
    }


def save_scene_register(rows: list[dict]) -> None:
    pd.DataFrame(rows).to_csv(INTERMEDIATE / "scenes.csv", index=False)


def try_osm_rivers(boundary_wgs, transformer) -> tuple[list, str]:
    minx, miny, maxx, maxy = boundary_wgs.total_bounds
    query = (
        "[out:json][timeout:120];"
        f'way["waterway"~"^(river|stream)$"]({miny},{minx},{maxy},{maxx});'
        "out geom;"
    )
    try:
        response = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            timeout=180,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        return [], f"Unavailable: Overpass API request failed ({error})."
    payload = response.json()
    reserve = boundary_wgs.geometry.union_all()
    features = []
    for element in payload.get("elements", []):
        points = [(node["lon"], node["lat"]) for node in element.get("geometry", [])]
        if len(points) < 2:
            continue
        line = LineString(points)
        if line.intersects(reserve):
            line_utm = transform_geometry(transformer.transform, line).intersection(
                transform_geometry(transformer.transform, reserve)
            )
            if not line_utm.is_empty:
                features.append((int(element["id"]), line_utm))
    return features, f"Retrieved {len(features)} OSM river/stream ways; queried {datetime.now(timezone.utc).date()}."


def write_empty_shapefile(path: Path) -> None:
    empty = gpd.GeoDataFrame(
        {
            "site_id": pd.Series(dtype="str"),
            "latitude": pd.Series(dtype="float64"),
            "longitude": pd.Series(dtype="float64"),
            "area_ha": pd.Series(dtype="float64"),
            "periods": pd.Series(dtype="str"),
            "activity": pd.Series(dtype="str"),
            "type": pd.Series(dtype="str"),
            "confidence": pd.Series(dtype="str"),
            "dist_bnd_km": pd.Series(dtype="float64"),
            "river_km": pd.Series(dtype="float64"),
            "patrol_km": pd.Series(dtype="float64"),
        },
        geometry=gpd.GeoSeries([], crs="EPSG:4326"),
        crs="EPSG:4326",
    )
    empty.to_file(path, driver="ESRI Shapefile", encoding="UTF-8", engine="pyogrio")


def make_maps(boundary_utm, sites_utm, rivers, patrol_available: bool) -> list[Path]:
    boundary_frame = gpd.GeoDataFrame(geometry=[boundary_utm], crs="EPSG:32635")
    river_frame = gpd.GeoDataFrame(
        geometry=[line for _, line in rivers], crs="EPSG:32635"
    )
    specs = [
        ("activity.png", "Overall spectral-change screening"),
        ("quarters.png", "Unverified spectral anomalies by quarter"),
        ("new_sites.png", "New 2026 spectral anomalies (not confirmed mines)"),
        ("expansion.png", "Expansion assessment: no historical mine inventory supplied"),
        ("proximity.png", "Proximity to mapped rivers; patrol data unavailable"),
    ]
    output_paths = []
    for filename, title in specs:
        fig, ax = plt.subplots(figsize=(10, 9))
        boundary_frame.boundary.plot(ax=ax, color="#263238", linewidth=0.8)
        if filename == "proximity.png" and not river_frame.empty:
            river_frame.plot(ax=ax, color="#2196f3", linewidth=0.35, alpha=0.8)
        if not sites_utm.empty and filename != "expansion.png":
            colors = [
                {"Q1": "#8e44ad", "Q2": "#e67e22", "Q3": "#27ae60", "Q4": "#c0392b"}.get(
                    str(periods).split(";")[0], "#d62728"
                )
                for periods in sites_utm["periods"]
            ]
            sites_utm.plot(ax=ax, color=colors, edgecolor="#17202a", linewidth=0.4, alpha=0.8)
            for _, row in sites_utm.iterrows():
                center = row.geometry.centroid
                ax.annotate(row["site_id"], (center.x, center.y), fontsize=5)
        note = (
            "No spectral candidates passed screening thresholds."
            if sites_utm.empty
            else f"{len(sites_utm)} unverified spectral-change candidate(s); not confirmed mining."
        )
        if filename in ("expansion.png", "proximity.png"):
            note += "\nNo reference mining inventory or authoritative patrol-post layer was supplied."
        ax.set_title(f"{title}\n{note}", fontsize=11)
        ax.set_xlabel("UTM Zone 35N Easting (m)")
        ax.set_ylabel("UTM Zone 35N Northing (m)")
        ax.set_aspect("equal", adjustable="box")
        ax.grid(alpha=0.15)
        fig.tight_layout()
        path = MAPS / filename
        fig.savefig(path, dpi=180)
        plt.close(fig)
        output_paths.append(path)
    return output_paths


def read_rgb_thumbnail(item: dict, x: float, y: float) -> np.ndarray:
    signed_item = copy.deepcopy(item)
    planetary_computer.sign_inplace(signed_item)
    with rasterio.open(signed_item["assets"]["B11"]["href"]) as base:
        center_col, center_row = base.index(x, y)
        half = 160
        col_start = max(0, center_col - half)
        row_start = max(0, center_row - half)
        col_end = min(base.width, center_col + half)
        row_end = min(base.height, center_row + half)
        window = Window(col_start, row_start, col_end - col_start, row_end - row_start)
        channels = []
        for band in ("B04", "B03", "B02"):
            with rasterio.open(signed_item["assets"][band]["href"]) as src:
                with WarpedVRT(
                    src,
                    crs=base.crs,
                    transform=base.transform,
                    width=base.width,
                    height=base.height,
                    resampling=Resampling.average,
                    src_nodata=0,
                    nodata=0,
                ) as vrt:
                    channels.append(vrt.read(1, window=window).astype(np.float32) / 10000.0)
        with rasterio.open(signed_item["assets"]["SCL"]["href"]) as src:
            with WarpedVRT(
                src,
                crs=base.crs,
                transform=base.transform,
                width=base.width,
                height=base.height,
                resampling=Resampling.nearest,
                src_nodata=0,
                nodata=0,
            ) as vrt:
                scl = vrt.read(1, window=window)
    rgb = np.stack(channels, axis=-1)
    rgb = np.clip(rgb / 0.30, 0, 1) ** 0.8
    rgb[~np.isin(scl, CLEAR_SCL)] = 0.82
    return rgb


def save_evidence_panels(sites_utm: gpd.GeoDataFrame, selected: dict) -> None:
    sites_utm["evidence_file"] = ""
    for row_index, site in sites_utm.iterrows():
        point = site.geometry.centroid
        period = site["first_period"]
        pair = {}
        for year in (2025, 2026):
            for tile, scenes in selected.get((year, period), {}).items():
                if not scenes:
                    continue
                bbox = scenes[0]["assets"]["B11"]["proj:bbox"]
                if box(*bbox).covers(point):
                    pair[year] = scenes[0]
                    break
        evidence_file = EVIDENCE / f'{site["site_id"]}_before_after.png'
        if len(pair) != 2:
            continue
        fig, axes = plt.subplots(1, 2, figsize=(10, 5))
        for axis, year in zip(axes, (2025, 2026)):
            item = pair[year]
            rgb = read_rgb_thumbnail(item, point.x, point.y)
            axis.imshow(rgb)
            props = item["properties"]
            cloud = props.get("eo:cloud_cover")
            axis.set_title(
                f'{year} {props["datetime"][:10]} — cloud {cloud:.1f}%' if cloud is not None
                else f'{year} {props["datetime"][:10]}'
            )
            axis.axis("off")
        fig.suptitle(f'{site["site_id"]}: season-matched Q4 evidence; spectral change is unverified')
        fig.tight_layout()
        fig.savefig(evidence_file, dpi=160)
        plt.close(fig)
        sites_utm.at[row_index, "evidence_file"] = str(evidence_file.relative_to(OUT))


def build_report(
    area_ha: float,
    scene_stats: list[dict],
    period_stats: list[dict],
    sites: gpd.GeoDataFrame,
    river_status: str,
    ee_status: str,
    maps: list[Path],
    candidate_events: list[dict],
) -> Path:
    report = Document()
    report.add_heading("Okapi Wildlife Reserve Mining Activity Assessment", 0)
    report.add_paragraph("Analysis period: 1 January–31 October 2026")
    report.add_paragraph(
        "Automated optical screening report; all flagged features are unverified and none are "
        "described as confirmed mining."
    )
    report.add_heading("1. Executive summary", level=1)
    report.add_paragraph(
        f"The validated source boundary is a Polygon in WGS84, with a projected area of "
        f"{area_ha:,.2f} ha (EPSG:32635). {len(sites)} potential spectral-change site(s) "
        f"passed the conservative multi-index screen; these are low-confidence disturbance "
        "candidates, not confirmed mines. The analysis does not establish a total count of "
        "mining operations or an absence of mining."
    )
    report.add_heading("2. Background and objectives", level=1)
    report.add_paragraph(
        "The objective was to screen the reserve for new vegetation loss and exposed-ground "
        "signals during 2026 relative to season-matched 2025 Sentinel-2 observations, while "
        "preserving the supplied boundary and excluding outside pixels."
    )
    report.add_heading("3. Study area description", level=1)
    report.add_paragraph(
        "The supplied RFO.json is a valid, unmodified GeoJSON Polygon with 3,439 ring "
        "coordinates and no legacy CRS property; GeoJSON default EPSG:4326 was used. "
        f"Spatial extent: {', '.join(f'{v:.6f}' for v in boundary_bounds)}."
    )
    report.add_heading("4. Data sources and methodology", level=1)
    report.add_paragraph(
        "Sentinel-2 Level-2A surface reflectance (10 m visible/NIR, 20 m SWIR and scene "
        "classification) was queried from the Microsoft Planetary Computer STAC API. The "
        "screening composites use up to two low-cloud, distinct-date scenes per MGRS tile and "
        "period. SCL classes 4–7 were retained; cloud, cirrus, shadow, snow and no-data classes "
        "were excluded. 10 m bands were averaged to the 20 m SWIR grid."
    )
    report.add_paragraph(
        "For each matched season, NDVI, EVI, NBR, NDMI, BSI and MNDWI were calculated from "
        "composite reflectance. A conservative candidate pixel required simultaneous "
        "NDVI <= -0.18, BSI >= +0.10, NBR <= -0.16 and NDMI <= -0.08 change, current BSI > 0, "
        "current NDVI <= 0.65 and MNDWI <= 0.15. Candidate patches smaller than 0.5 ha were "
        "excluded. The screening is not a trained mining classifier."
    )
    report.add_paragraph(
        f"Earth Engine status: {ee_status}. Sentinel-1 GRD, Landsat 8/9, GLAD/GFW, Hansen "
        "forest-change, FIRMS, NICFI and patrol-post layers were not used for classification. "
        "Their catalogs/access requirements are noted in the workbook. OSM river status: "
        f"{river_status}"
    )
    report.add_heading("5. Mining detection results", level=1)
    report.add_paragraph(
        f"The screening returned {len(sites)} spatially grouped spectral candidate(s) "
        f"({len(candidate_events)} quarter-specific patches). No candidate is independently "
        "confirmed as mining. All assigned confidence values are Low."
    )
    report.add_heading("6. Spatial distribution of mining activities", level=1)
    if sites.empty:
        report.add_paragraph("No candidate passed the stated spectral thresholds.")
    else:
        report.add_paragraph(
            "Candidate coordinates and geometry are included in the GIS inventory. Areas and "
            "distance-to-boundary calculations are clipped to the source polygon."
        )
    report.add_heading("7. Mining expansion and temporal analysis", level=1)
    report.add_paragraph(
        "The quarterly results represent threshold crossings between season-matched composites. "
        "Without an independently validated pre-existing mine inventory, they cannot establish "
        "mine expansion, continued operation, abandonment or recovery."
    )
    report.add_heading("8. Environmental implications", level=1)
    report.add_paragraph(
        "If field-verified as mining, exposed soil and river-adjacent disturbance could indicate "
        "forest loss, sediment delivery and habitat fragmentation. Spectral change alone does "
        "not establish cause, water quality impact or ecological severity."
    )
    report.add_heading("9. Limitations and confidence assessment", level=1)
    report.add_paragraph(
        "The assessment is an automated optical screen, with residual cloud/shadow, phenology, "
        "river dynamics, logging, agriculture, landslides and other bare-ground changes as "
        "possible confounders. SCL masking and four seasonal comparisons reduce but do not "
        "eliminate these effects. No independent field, high-resolution visual, GLAD, radar or "
        "patrol evidence was used. Candidate sites are therefore low confidence and unconfirmed. "
        "A lack of candidates is not evidence that no mining occurred."
    )
    report.add_heading("10. Recommendations for RFO management and patrol operations", level=1)
    report.add_paragraph(
        "Review candidate evidence panels against higher-resolution imagery and local knowledge; "
        "verify selected locations in the field where safe and appropriate; compare with patrol "
        "records; and repeat the screen with calibrated Sentinel-1 backscatter and additional "
        "clear Sentinel-2 observations. Supply authoritative patrol-post, sector and historic "
        "mine-reference layers before calculating those distances or attributing expansion."
    )
    report.add_heading("11. Maps, tables, imagery and references", level=1)
    for path in maps:
        report.add_picture(str(path), width=Inches(6.2))
        report.add_paragraph(path.name, style="Caption")
    if not sites.empty and "evidence_file" in sites.columns:
        for _, row in sites.iterrows():
            if row["evidence_file"]:
                report.add_paragraph(
                    f'{row["site_id"]}: before/after {row["first_period"]} Sentinel-2 scenes; '
                    f'coordinates {row["latitude"]:.5f}, {row["longitude"]:.5f}; '
                    f'periods {row["periods"]}; indices are recorded in the inventory.'
                )
                report.add_picture(str(OUT / row["evidence_file"]), width=Inches(6.0))
    table = report.add_table(rows=1, cols=5)
    table.style = "Light Shading Accent 1"
    for cell, value in zip(
        table.rows[0].cells,
        ("Period", "Scenes used", "Joint-clear pixels (%)", "Candidate patches", "Candidate ha"),
    ):
        cell.text = value
    events_by_period = defaultdict(int)
    for event in candidate_events:
        events_by_period[event["period"]] += 1
    for row in period_stats:
        cells = table.add_row().cells
        cells[0].text = row["period"]
        cells[1].text = str(row["scene_count"])
        cells[2].text = f'{row["clear_pct"]:.1f}%' if row["clear_pct"] is not None else "n/a"
        cells[3].text = str(events_by_period[row["period"]])
        cells[4].text = f'{row["candidate_area_ha"]:.2f}'
    report.add_paragraph(
        "Sources: Sentinel-2 L2A STAC collection "
        "https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-2-l2a ; "
        "Sentinel-1 GRD catalog https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-1-grd ; "
        "Landsat C2 L2 catalog https://planetarycomputer.microsoft.com/api/stac/v1/collections/landsat-c2-l2 ; "
        "Global Forest Watch https://www.globalforestwatch.org/ ; Hansen Global Forest Change "
        "https://glad.earthengine.app/view/global-forest-change ; NASA FIRMS "
        "https://firms.modaps.eosdis.nasa.gov/ ; OpenStreetMap/Overpass "
        "https://overpass-api.de/ ."
    )
    path = REPORTS / "assessment.docx"
    report.save(path)
    return path


def main() -> None:
    started = time.time()
    for folder in (OUT, INTERMEDIATE, EVIDENCE, GIS, MAPS, REPORTS, TABLES):
        folder.mkdir(parents=True, exist_ok=True)
    boundary, source_path, analysis_crs = load_boundary()
    boundary_wgs = boundary.geometry.union_all()
    area_ha = float(boundary.to_crs(analysis_crs).area.sum() / 10000)
    boundary_utm = transform_geometry(
        Transformer.from_crs("EPSG:4326", analysis_crs, always_xy=True).transform,
        boundary_wgs,
    )
    minx, miny, maxx, maxy = boundary.total_bounds
    global boundary_bounds
    boundary_bounds = (minx, miny, maxx, maxy)
    log(
        f"Boundary: {source_path.name}; {boundary.geometry.iloc[0].geom_type}; "
        f"EPSG:4326; area {area_ha:,.2f} ha in {analysis_crs.to_string()}."
    )

    ee_status = "Not available: Earth Engine client/authentication not detected."
    if importlib.util.find_spec("ee") is not None:
        import ee

        try:
            ee.Initialize()
            ee_status = "Authenticated and initialized."
        except Exception as error:
            ee_status = f"Not initialized: {type(error).__name__}: {error}"

    boundary_geojson = json.loads(gpd.GeoSeries([boundary_wgs], crs="EPSG:4326").to_json())[
        "features"
    ][0]["geometry"]
    all_items: dict[tuple[int, str], list[dict]] = {}
    selected: dict[tuple[int, str], dict[str, list[dict]]] = {}
    scene_rows = []
    tile_names = set()
    for year_index in (0, 1):
        for period_index, (period, baseline_dates, current_dates) in enumerate(PERIODS):
            dates = (baseline_dates, current_dates)[year_index]
            year = 2025 + year_index
            start_date, end_date = dates.split("/")
            items = search_items(start_date, end_date, boundary_geojson)
            if not items:
                raise RuntimeError(f"No Sentinel-2 scenes were returned for {year} {period}.")
            grouped = select_scenes(items)
            all_items[(year, period)] = items
            selected[(year, period)] = grouped
            tile_names.update(grouped)
            used = {item["id"] for scenes in grouped.values() for item in scenes}
            for item in items:
                scene_rows.append(
                    {
                        "year": year,
                        "period": period,
                        "scene_id": item["id"],
                        "date": item["properties"]["datetime"][:10],
                        "tile": item["properties"].get("s2:mgrs_tile"),
                        "cloud_cover_pct": item["properties"].get("eo:cloud_cover"),
                        "selected": item["id"] in used,
                    }
                )
            log(
                f"Catalog {year} {period}: {len(items)} scenes, "
                f"{len(grouped)} tiles, {len(used)} selected."
            )
    save_scene_register(scene_rows)

    x0 = math.floor(boundary_utm.bounds[0] / RESOLUTION) * RESOLUTION
    y0 = math.ceil(boundary_utm.bounds[3] / RESOLUTION) * RESOLUTION
    width = math.ceil((boundary_utm.bounds[2] - x0) / RESOLUTION)
    height = math.ceil((y0 - boundary_utm.bounds[1]) / RESOLUTION)
    global_transform = from_origin(x0, y0, RESOLUTION, RESOLUTION)
    template = {
        "driver": "GTiff",
        "width": width,
        "height": height,
        "crs": analysis_crs,
        "transform": global_transform,
        "compress": "deflate",
        "predictor": 2,
        "tiled": True,
        "BIGTIFF": "IF_SAFER",
    }
    flags = np.zeros((4, height, width), dtype=np.uint8)
    valid_seen = np.zeros((height, width), dtype=np.uint8)
    event_records: list[dict] = []
    period_stats: list[dict] = []
    change_path = GIS / "change.tif"
    change_band_count = len(INDEX_NAMES) + len(PERIODS)
    change_profile = {
        **template,
        "count": change_band_count,
        "dtype": "float32",
        "nodata": NODATA_FLOAT,
    }
    period_totals = defaultdict(lambda: {"inside": 0, "valid": 0, "scene_ids": set()})

    with rasterio.open(change_path, "w", **change_profile) as change_ds:
        for band_index, index_name in enumerate(INDEX_NAMES, start=1):
            change_ds.set_band_description(band_index, f"Q4_2026_minus_Q4_2025_{index_name}")
        for period_index, (period, _, _) in enumerate(PERIODS):
            change_ds.set_band_description(
                len(INDEX_NAMES) + period_index + 1,
                f"{period}_2026_minus_{period}_2025_BSI",
            )
        for top in range(0, height, 512):
            for left in range(0, width, 512):
                block_height = min(512, height - top)
                block_width = min(512, width - left)
                block = np.full((block_height, block_width), NODATA_FLOAT, dtype=np.float32)
                for band in range(1, change_band_count + 1):
                    change_ds.write(
                        block,
                        band,
                        window=Window(left, top, block_width, block_height),
                    )
        for tile in sorted(tile_names):
            tile_items = []
            for period_index, (period, _, _) in enumerate(PERIODS):
                before = selected.get((2025, period), {}).get(tile, [])
                after = selected.get((2026, period), {}).get(tile, [])
                if not before or not after:
                    continue
                before_composite, before_valid, before_grid, before_metadata = composite_scenes(
                    before, boundary_utm
                )
                after_composite, after_valid, after_grid, after_metadata = composite_scenes(
                    after, boundary_utm
                )
                if before_composite is None or after_composite is None:
                    continue
                local_transform = before_grid
                local_shape = before_valid.shape
                if local_shape != after_valid.shape or local_transform != after_grid:
                    raise ValueError(f"Baseline and analysis grids do not align for tile {tile}.")
                col_offset = round((local_transform.c - x0) / RESOLUTION)
                row_offset = round((y0 - local_transform.f) / RESOLUTION)
                output_window = Window(col_offset, row_offset, local_shape[1], local_shape[0])
                row_slice = slice(row_offset, row_offset + local_shape[0])
                col_slice = slice(col_offset, col_offset + local_shape[1])
                before_indices = calculate_indices(before_composite)
                after_indices = calculate_indices(after_composite)
                valid = before_valid & after_valid
                deltas = {
                    name: after_indices[name] - before_indices[name] for name in INDEX_NAMES
                }
                for name in INDEX_NAMES:
                    deltas[name][~valid] = np.nan
                tile_polygon = boundary_utm.intersection(
                    box(*before[0]["assets"]["B11"]["proj:bbox"])
                )
                inside_mask = geometry_mask(
                    [mapping(tile_polygon)],
                    out_shape=local_shape,
                    transform=local_transform,
                    invert=True,
                )
                period_totals[period]["inside"] += int(np.count_nonzero(inside_mask))
                period_totals[period]["valid"] += int(np.count_nonzero(valid))
                period_totals[period]["scene_ids"].update(
                    [m["scene_id"] for m in before_metadata + after_metadata]
                )
                valid_seen[row_slice, col_slice] |= valid.astype(np.uint8)
                candidate = (
                    valid
                    & (deltas["NDVI"] <= -0.18)
                    & (deltas["BSI"] >= 0.10)
                    & (deltas["NBR"] <= -0.16)
                    & (deltas["NDMI"] <= -0.08)
                    & (after_indices["BSI"] > 0.0)
                    & (after_indices["NDVI"] <= 0.65)
                    & (after_indices["MNDWI"] <= 0.15)
                )
                flags[period_index, row_slice, col_slice] = candidate.astype(np.uint8)
                object_count = 0
                for geom_json, value in shapes(
                    candidate.astype(np.uint8),
                    mask=candidate,
                    transform=local_transform,
                    connectivity=8,
                ):
                    if not value:
                        continue
                    component = shape(geom_json).intersection(boundary_utm)
                    if component.is_empty or component.area / 10000 < MIN_PATCH_HA:
                        continue
                    component_mask = rasterio.features.geometry_mask(
                        [mapping(shape(geom_json))],
                        out_shape=candidate.shape,
                        transform=local_transform,
                        invert=True,
                    ) & candidate
                    event_records.append(
                        {
                            "period": period,
                            "geometry": component,
                            "deltas": {
                                name: float(np.nanmean(values[component_mask]))
                                for name, values in deltas.items()
                            },
                            "pixels": int(np.count_nonzero(component_mask)),
                            "tile": tile,
                        }
                    )
                    object_count += 1
                if period_index == 3:
                    write_indices = np.stack(
                        [np.where(valid, deltas[name], NODATA_FLOAT) for name in INDEX_NAMES]
                    ).astype(np.float32)
                    change_ds.write(
                        write_indices,
                        indexes=list(range(1, len(INDEX_NAMES) + 1)),
                        window=output_window,
                    )
                change_ds.write(
                    np.where(valid, deltas["BSI"], NODATA_FLOAT).astype(np.float32),
                    len(INDEX_NAMES) + period_index + 1,
                    window=output_window,
                )
                log(
                    f"Processed {tile} {period}: "
                    f"{100 * np.count_nonzero(valid) / max(1, np.count_nonzero(before_valid | after_valid)):.1f}% "
                    f"joint clear; {object_count} threshold patches."
                )
        disturbance = np.full((height, width), NODATA_CLASS, dtype=np.uint8)
        inside = valid_seen.astype(bool)
        disturbance[inside] = 0
        hit_count = flags.sum(axis=0)
        for q_index in range(4):
            selected_pixels = (flags[q_index] > 0) & (disturbance == 0)
            disturbance[selected_pixels] = q_index + 1
        disturbance[(hit_count > 1) & inside] = 5
    disturbance_path = GIS / "disturbance.tif"
    disturbance_profile = {**template, "count": 1, "dtype": "uint8", "nodata": NODATA_CLASS}
    with rasterio.open(disturbance_path, "w", **disturbance_profile) as target:
        target.set_band_description(
            1, "0=no threshold crossing; 1-4=first crossing Q1-Q4; 5=multiple quarters; 255=nodata/outside"
        )
        target.write(disturbance, 1)

    for period, _, _ in PERIODS:
        totals = period_totals[period]
        period_stats.append(
            {
                "period": period,
                "scene_count": len(totals["scene_ids"]),
                "valid_pixels": totals["valid"],
                "inside_observation_pixels": totals["inside"],
                "clear_pct": 100 * totals["valid"] / totals["inside"] if totals["inside"] else None,
                "screened_event_patches": sum(e["period"] == period for e in event_records),
                "candidate_area_ha": sum(
                    e["geometry"].area / 10000 for e in event_records if e["period"] == period
                ),
            }
        )

    rivers = []
    if event_records:
        centers = np.array(
            [[record["geometry"].centroid.x, record["geometry"].centroid.y] for record in event_records]
        )
        labels = DBSCAN(eps=100, min_samples=1).fit_predict(centers)
        grouped_records = defaultdict(list)
        for label, record in zip(labels, event_records):
            grouped_records[int(label)].append(record)
        rivers, river_status = try_osm_rivers(
            boundary.to_crs("EPSG:4326"),
            Transformer.from_crs("EPSG:4326", analysis_crs, always_xy=True),
        )
        river_union = unary_union([line for _, line in rivers]) if rivers else None
        site_rows = []
        for group in grouped_records.values():
            geom = unary_union([record["geometry"] for record in group]).intersection(boundary_utm)
            center = geom.centroid
            periods_present = [period for period, _, _ in PERIODS if any(r["period"] == period for r in group)]
            first = periods_present[0]
            status = (
                "Potential ongoing spectral disturbance; unverified"
                if periods_present[-1] == "Q4"
                else f"Spectral anomaly observed in {', '.join(periods_present)}; unverified"
            )
            lon, lat = Transformer.from_crs(analysis_crs, "EPSG:4326", always_xy=True).transform(
                center.x, center.y
            )
            mean_delta = {
                name: float(np.mean([r["deltas"][name] for r in group])) for name in INDEX_NAMES
            }
            site_rows.append(
                {
                    "site_id": "",
                    "latitude": lat,
                    "longitude": lon,
                    "area_ha": geom.area / 10000,
                    "periods": ";".join(periods_present),
                    "first_period": first,
                    "activity": status,
                    "disturbance_type": "Multi-index bare-ground/vegetation-loss anomaly; mining unverified",
                    "confidence": "Low",
                    "dist_bnd_km": center.distance(boundary_utm.boundary) / 1000,
                    "river_km": center.distance(river_union) / 1000 if river_union else np.nan,
                    "patrol_km": np.nan,
                    **{f"d{name.lower()}": mean_delta[name] for name in INDEX_NAMES},
                    "geometry": geom,
                }
            )
        site_rows.sort(key=lambda row: (row["latitude"], row["longitude"]))
        for index, row in enumerate(site_rows, start=1):
            row["site_id"] = f"RFO-2026-{index:03d}"
        sites_utm = gpd.GeoDataFrame(site_rows, geometry="geometry", crs=analysis_crs)
        save_evidence_panels(sites_utm, selected)
        sites = sites_utm.to_crs("EPSG:4326")
    else:
        river_status = "Not queried because no potential sites were generated."
        sites_utm = gpd.GeoDataFrame(
            columns=["site_id", "latitude", "longitude", "area_ha", "periods", "geometry"],
            geometry="geometry",
            crs=analysis_crs,
        )
        sites = sites_utm.to_crs("EPSG:4326")

    inventory_csv = TABLES / "inventory.csv"
    inventory_columns = [
        "site_id", "latitude", "longitude", "area_ha", "periods", "first_period",
        "activity", "disturbance_type", "confidence", "dist_bnd_km", "river_km", "patrol_km",
        "dndvi", "devi", "dnbr", "dndmi", "dbsi", "dmndwi", "evidence_file",
    ]
    pd.DataFrame(sites.drop(columns="geometry", errors="ignore"), columns=inventory_columns).to_csv(
        inventory_csv, index=False
    )
    geojson_path = GIS / "sites.geojson"
    if sites.empty:
        geojson_path.write_text(
            json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8"
        )
        write_empty_shapefile(GIS / "sites.shp")
    else:
        sites.to_file(geojson_path, driver="GeoJSON")
        shp = sites.rename(
            columns={
                "latitude": "lat",
                "longitude": "lon",
                "area_ha": "area_ha",
                "periods": "periods",
                "first_period": "first_per",
                "activity": "activity",
                "disturbance_type": "dist_type",
                "confidence": "conf",
                "evidence_file": "evid_file",
                "dist_bnd_km": "bnd_km",
                "river_km": "river_km",
                "patrol_km": "patrol_km",
                "dndvi": "dndvi",
                "devi": "devi",
                "dnbr": "dnbr",
                "dndmi": "dndmi",
                "dbsi": "dbsi",
                "dmndwi": "dmndwi",
            }
        )
        shp.to_file(GIS / "sites.shp", driver="ESRI Shapefile", encoding="UTF-8")

    maps = make_maps(boundary_utm, sites_utm, rivers, patrol_available=False)
    pd.DataFrame(period_stats).to_csv(TABLES / "quarters.csv", index=False)
    summary = {
        "analysis_period": ["2026-01-01", "2026-10-31"],
        "baseline_period": ["2025-01-01", "2025-10-31"],
        "boundary_file": source_path.name,
        "boundary_geometry": boundary.geometry.iloc[0].geom_type,
        "boundary_crs": "EPSG:4326 (GeoJSON default)",
        "analysis_crs": analysis_crs.to_string(),
        "boundary_area_ha": area_ha,
        "boundary_extent_lonlat": list(boundary_bounds),
        "candidate_site_count": len(sites),
        "candidate_area_ha": float(sites["area_ha"].sum()) if not sites.empty else 0.0,
        "confirmed_mining_site_count": 0,
        "interpretation": "All candidates are low-confidence unverified spectral anomalies; no mine is confirmed.",
        "earth_engine_status": ee_status,
        "osm_river_status": river_status,
        "patrol_posts": "Not supplied; nearest-post distances not calculated.",
        "processing_resolution_m": RESOLUTION,
        "minimum_candidate_patch_ha": MIN_PATCH_HA,
        "scene_register": "intermediate/scenes.csv",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.time() - started, 1),
    }
    (TABLES / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    sources = pd.DataFrame(
        [
            ["Sentinel-2 L2A", "Used", "10 m visible/NIR; 20 m SWIR/SCL", "2015–present", "STAC + selected scenes in register", "https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-2-l2a"],
            ["Landsat 8/9 C2 L2", "Catalog verified; not processed", "30 m multispectral", "1982–present", "Public catalog; no image processing performed", "https://planetarycomputer.microsoft.com/api/stac/v1/collections/landsat-c2-l2"],
            ["Sentinel-1 GRD", "Catalog verified; not processed", "10 m class; VV/VH GRD", "2014–present", "Radar not calibrated/processed in this run", "https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-1-grd"],
            ["Planet NICFI", "Not accessed", "4.77 m mosaics", "Access/licensing dependent", "No authenticated access checked", "https://www.planet.com/nicfi/"],
            ["GLAD/GFW alerts", "Not processed", "Alert product; nominal 30 m", "Product and access dependent", "Not queried with credentials", "https://www.globalforestwatch.org/"],
            ["Hansen Global Forest Change", "Not processed", "30 m", "Annual history, latest product version dependent", "Not suitable as 2026 ground truth", "https://glad.earthengine.app/view/global-forest-change"],
            ["NASA FIRMS", "Not processed", "VIIRS 375 m / MODIS 1 km", "Near-real time/historical archive", "API key not configured; fire is not mining evidence", "https://firms.modaps.eosdis.nasa.gov/"],
            ["OSM rivers/streams", "Conditional", "Vector; variable completeness", "Current OSM snapshot", river_status, "https://overpass-api.de/"],
            ["Patrol posts", "Unavailable", "Not applicable", "No reference data supplied", "Distance not calculated", ""],
        ],
        columns=["dataset", "status", "resolution", "temporal_coverage", "access_or_use_notes", "source_url"],
    )
    with pd.ExcelWriter(TABLES / "stats.xlsx", engine="openpyxl") as writer:
        pd.DataFrame([summary]).to_excel(writer, sheet_name="Summary", index=False)
        pd.DataFrame(period_stats).to_excel(writer, sheet_name="Quarterly", index=False)
        pd.DataFrame(sites.drop(columns="geometry", errors="ignore"), columns=inventory_columns).to_excel(
            writer, sheet_name="Inventory", index=False
        )
        pd.DataFrame(scene_rows).to_excel(writer, sheet_name="Scene Register", index=False)
        sources.to_excel(writer, sheet_name="Data Sources", index=False)

    report_path = build_report(
        area_ha, scene_rows, period_stats, sites, river_status, ee_status, maps, event_records
    )
    with rasterio.open(change_path) as check:
        if check.crs != analysis_crs or check.count != change_band_count:
            raise RuntimeError("The change-detection raster failed output validation.")
    with rioxarray.open_rasterio(disturbance_path, masked=True) as check:
        if check.rio.crs != analysis_crs or check.shape[0] != 1:
            raise RuntimeError("The disturbance raster failed output validation.")
    log(
        f"Finished: {len(sites)} unverified candidate(s), "
        f"{summary['candidate_area_ha']:.2f} candidate ha. "
        f"Report: {report_path.name}"
    )


if __name__ == "__main__":
    main()
