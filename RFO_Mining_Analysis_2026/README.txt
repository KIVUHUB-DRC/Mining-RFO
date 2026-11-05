RFO MINING ANALYSIS — 2026

ASSESSMENT STATUS
INCOMPLETE — reserve-wide pixel processing was terminated before completion.
The empty site layers and NoData rasters are placeholders, not zero findings and
not evidence that mining is absent. No sites are confirmed. See
PROCESSING_STATUS.txt and intermediate/run.log.

ORGANIZED CONTENTS
  RFO_2026.gpkg                  Spatial database (GeoPackage)
  gis/                           Candidate layers and status rasters
  maps/                          Maps, clearly marked not analyzed
  reports/                       DOCX assessment
  tables/                        CSV, XLSX, and JSON status/statistics
  intermediate/                  STAC scene register and processing log
  evidence/                      Site evidence panels (none generated)
  scripts/                       Reproducible analysis and database scripts

DATABASE
The GeoPackage contains rfo_boundary, potential_mining_sites, analysis_status,
quarterly_statistics, candidate_inventory, sentinel2_scene_register,
data_sources, and deliverables layers. The boundary geometry comes from RFO.json
without geometry edits and is stored in EPSG:4326. Candidate and quarterly
results are explicitly marked incomplete. Raster files remain separate in gis/.
Rebuild the database from the organized exports with:
  python RFO_Mining_Analysis_2026/scripts/build_geopackage.py

PROCESSING
Run the raster workflow from the repository root with:
  python RFO_Mining_Analysis_2026/scripts/analyze_rfo_mining_2026.py

Dependencies are listed in scripts/requirements.txt. A working network
connection is required. The source boundary uses GeoJSON's default EPSG:4326;
area analysis uses EPSG:32635. The attempted workflow used season-matched
Sentinel-2 L2A scenes and 20 m spectral indices; the full raster pass did not
complete in the available execution window. Earth Engine was not authenticated.
Sentinel-1, Landsat, NICFI, GLAD/GFW, Hansen and FIRMS were not processed.
No historical mine inventory or authoritative patrol-post dataset was supplied.
