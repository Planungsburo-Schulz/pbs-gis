# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **`recipes/mv_dop.yaml`** — MV DOP20 Open Data WMS recipe (© GeoBasis-DE/M-V). Pair to `sh_dop20`; supports EPSG:25833 natively.
- **`templates/lines_to_polygon`** — generic counterpart to `dxf_lines_to_polygon`; converts (Multi)LineString features from any OGR source (SHP/GPKG/GeoJSON) into a closed polygon.
- **`templates/fetch_flurstuecke`** — thin workflow wrapper around `find_flurstuecke()` so ALKIS parcel lookups can live in `workflow.yaml` as a one-step template.
- **`reporting.area_length_report`** — markdown report combining perimeter (length) and area per layer; auto-detects metrics by geometry type (polygons → length + area; lines → length). Override via `metrics_per_layer`.
- **`recipes.qgis_uri`** — build a QGIS layer URI string (WMS / WFS / XYZ) from a recipe so qgis-mcp's `add_web_layer` (or `QgsRasterLayer`/`QgsVectorLayer` directly) can consume recipes without hand-rolled URIs.

### Changed

- **`templates/polygon_difference`** — `overlay:` now accepts either a single path or a list of paths (unioned before subtraction). `overlay_layer:` correspondingly accepts a list or a single string. Single-string usage is fully back-compat.
- **`recipes/mv_alkis.yaml`** — description now warns against loading the full Flurstück layer as a WFS layer in QGIS and attribute-filtering client-side (60s+ timeouts on ~2M features); points users at `find_flurstuecke()` / `fetch_flurstuecke`.
- **`gis-workflow init`** — default `workflow.yaml` no longer emits an `Example step` referencing a non-existent `scripts/example.py` (which made `gis-workflow run` fail on every fresh project). New template starts with `steps: []` and a commented-out example block.
- **`find_flurstuecke`** error when no search scope is provided now lists all four options (`input_boundary` / `extent` / `gemarkung_schluessel` / `gemeinde_schluessel`) with `input_boundary` marked as recommended.

[Unreleased]: https://github.com/Gunther-Schulz/gis_utils/compare/main...HEAD
