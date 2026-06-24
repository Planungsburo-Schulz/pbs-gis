# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **Layout maps follow a map theme** — `qgis_bridge.define_map_theme(name, visible_layers)` builds/updates a QGIS map theme (idempotent, layers matched by name, builds a valid `QgsLayerTreeModel` so the `createThemeFromCurrentState` call can't segfault QGIS). `render_layout_template(..., map_theme=…)` and the `layout_from_qpt` template's `map.theme:` make the `main_map` item **follow** that theme instead of the live canvas, so canvas edits never silently change an exported layout. Prior `setFollowVisibilityPreset(False)` is now the no-theme fallback with a printed advisory. Convention codified in the `gis-safety` (any-project net), `qgis-mcp-integration`, and `layout-from-template` skills.
- **`runner` — generic `inputs:` step field** — declare the files a step reads (incl. upstream steps' outputs); under `run: auto` the step re-runs when any input is newer than its outputs (make-style staleness) and re-runs cascade downstream in one `gis-workflow run`. Generalizes the recipe-only `input_boundary` mtime check to all steps. Without it `auto` skips whenever outputs merely exist, so an edited input (e.g. a re-digitized shapefile) was silently ignored. A step's own `script:` is now an implicit input too (make-style: target depends on its recipe), so editing the code re-runs the step and cascades downstream. `workflow-authoring` skill documents the field and connects it to the report convention.
- **`reporting.conflict_matrix`** — per source feature × named target: overlap area, else minimum distance. Generalizes `intersection_areas` to several named targets and to the no-overlap (distance) case. Reusable for any proximity/overlap conflict analysis (Baumkronen ↔ Bauteile, Vorhaben ↔ Schutzgebiete, …). Returns a tidy DataFrame (`source, source_area_m2, target, overlap_m2, distance_m, contact`).
- **`workflow-authoring` skill — "Reports are steps too" convention** — a report whose figures derive from the geodata is a pipeline step generated mechanically (static prose + computed values via `conflict_matrix` / `area_report` / `markdown_table`), never hand-typed numbers. Description widened to trigger on report / write-up requests.
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
