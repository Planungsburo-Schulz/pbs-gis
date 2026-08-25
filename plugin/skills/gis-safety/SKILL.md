---
name: gis-safety
description: This skill should be used when the user asks to "write code", "implement", "create a script", "add a function", or any code-writing task in a GIS/CAD project that uses pbs_gis. Enforces CRS safety, dangerous defaults prevention, and output conventions.
license: MIT
---

## GIS Safety Rules

Apply these rules to ALL code written in pbs_gis projects.

### CRITICAL: No dangerous defaults or silent fallbacks

- **CRS**: NEVER default to a specific EPSG code. Different projects use different zones (25832 vs 25833). A wrong CRS silently shifts geometries by hundreds of meters. Always require CRS explicitly. For Behörden/authority deliverables the CRS is a **requirement**, not a convenience: resolve the official Landes-CRS for the project's state — the data's current CRS or the geographic UTM zone may differ from what the authority expects (a state can keep one UTM zone across a zone boundary). Take it from the recipe / amtliches ALKIS, not by assumption.
- **URLs, layer names, file paths**: NEVER hardcode project-specific values as defaults. Require them as parameters or get them from recipes.
- **Any parameter where a wrong default produces valid-looking but incorrect output**: make it required, not optional with a default.
- **Safe defaults are OK**: `timeout=120`, `dissolve=True`, `simplify_tolerance=1.0` — wrong values cause obvious failures, not silent corruption.
- **When in doubt**: require the parameter with no default. An explicit error is always better than silently wrong data.

### Output & symbology conventions

**Vector output = GeoPackage.** Write `.gpkg` (`gdf.to_file(path, driver="GPKG")`, `layer=` for multi-layer) under `Geodaten/`. Never default to Shapefile: it truncates field names to 10 chars (`area_label` → `area_lab`), is multi-file + single-layer, 2 GB-capped, and lacks UTF-8 / proper NULL. Shapefile only when an external recipient explicitly requires it. (Inputs received as `.shp` stay as-is — convert into `.gpkg` outputs.)

When producing GeoDataFrame outputs, always explode MultiPolygons into individual Polygon features (`.explode(index_parts=False)`). MultiPolygons make styling, labeling, and area calculations unreliable in QGIS — and a multi-part result gets only one label per feature, leaving disjoint patches unlabelled; exploding gives every patch its own value.

When styling outputs over a basemap (DOP, ALKIS):
- **One colour = one role.** Reserve one high-contrast colour for the *alert* role — the conflict/result the map exists to show — and use it for nothing else (not the fence, not a boundary).
- **Distinguish by hue, not by stacked alpha.** Semi-transparent fills let the basemap read through, but several transparent fills of similar hue stacked together turn muddy and unreadable. One role → one hue.

### Basemaps: official survey data before commercial tiles

**Where an official source exists, it is the one used.** DOP for imagery, ALKIS for
parcels, ATKIS for topography — recipes exist per Bundesland (`mv_dop`, `ni_dop`,
`sh_dop20`, `th_dop20`; `list_recipes(search="dop")`). Google, Bing and Esri tiles are
an exploration convenience and three separate problems in a deliverable: their licence
does not cover reproduction in a plan, they are not survey-accurate, and their imagery
carries no documented date, so nothing measured against them can be cited.

The failure is quiet and it compounds: a commercial layer added once as a quick backdrop
stays in the project file and rides into every later map, and nothing asks about it —
measured across the office's projects, 31 of 40 QGIS project folders carried one.

- **Adding a backdrop**: reach for the state's DOP recipe first. Only if none exists for
  that state does a commercial tile service come into question.
- **Opening an existing project**: a commercial layer already in it is a finding to
  raise, not inherited furniture. Say so before building on top of it.
- **Deliberate exception**: declare it in `workflow.yaml` as
  `project: basemap_exception: "<reason>"`. The check reads the reason and goes quiet.
- **Mechanism**: `gis-workflow check-basemap [dir]` names every commercial layer and the
  official recipes that replace it; `gis-workflow run` reports the same at the end of
  each run. It matches the tile HOST, so a layer renamed "Luftbild" is still caught.

### Validate inputs and outputs — don't assume

- **Layer mapping by geometry, not name**: before using a named layer, confirm it actually holds the expected geometry (type, area, count). Names mislead — a "fence" layer may carry only dimensioning, a "components" layer may be a 0.3 m² stub. Surface any mismatch instead of proceeding.
- **A success code is not verification**: `rc=0` / "saved" does not mean the output is right. Render it and look — visual review catches muddy symbology, clipped legends, broken-image logos, and off-page scale bars that return codes never report.
- **Never overwrite source inputs**: the surveyor's DXF, a digitised shapefile, a received plan stay as received. Reproject/convert into *derived* outputs; touch the source only on explicit instruction.

### Alpha stage — no backward compatibility

This library is in alpha. Do not add backward-compatibility shims, deprecated aliases, re-exports of renamed symbols, or any code whose sole purpose is keeping old callers working. When something changes, just change it.

### Project context

When starting work in a GIS project:
1. Read `workflow.yaml` to understand the pipeline, CRS, and data sources
2. If CRS is not apparent from the workflow, ask the user before writing any code
3. Check existing scripts in `scripts/` for conventions used in this project

### Discovery before coding

Before writing code, use the `pbs-gis` MCP tools to discover available functions:
- `mcp__pbs-gis__catalog` — search the full API
- `mcp__pbs-gis__list_recipes` — find data source recipes
- `mcp__pbs-gis__list_templates` — find workflow templates
- `mcp__pbs-gis__get_function_help` — get detailed function docs

### Print layouts — the map item follows a map theme

Any QGIS print layout (`.qpt` template or hand-rolled PyQGIS, any project): the map item follows a **named map theme**, not the live canvas and not a bare `setLayers([...])` — so later canvas edits can't change an exported layout.

- `m.setFollowVisibilityPreset(True); m.setFollowVisibilityPresetName(theme)`.
- Build the theme with `pbs_gis.qgis_bridge.define_map_theme(name, visible_layers)` — never hand-roll `createThemeFromCurrentState(root, None)` (`None` model segfaults QGIS).
- Library helpers take it directly: `render_layout_template(..., map_theme=...)`, `layout_from_qpt` `map.theme:`.
- `setLayers([...])` may still seed the legend tree; visibility comes from the theme.
- Full AOI/scale/legend workflow: `layout-from-template` skill.

### WMS layers in QGIS — set extent close after add

When adding a WMS raster (DOP, ALKIS, etc.) to a fresh QGIS project: **set the canvas extent to a close-up area (a few km) AFTER `addMapLayer` and after any `refreshAllLayers()`**. Auto-zoom to the layer's full extent (e.g. a Bundesland-wide DOP) usually exceeds the service's scale-denominator limit and the canvas stays blank.

**How to apply:** add the layer → compute target extent (project AOI / city centre + ~5 km box) → `iface.mapCanvas().setExtent(rect)` as the **last** step (or use the `mcp__qgis__set_canvas_extent` MCP tool). If a refresh is needed in between, set the extent twice — once before and once after refresh. Always verify with a screenshot before declaring done.

For German DOP / ALKIS WMS specifically: parse `GetCapabilities` as XML (not regex) to find `<Layer><Name>` plus required `<Style><Name>` — some MV services (e.g. `adv_dop`) require an explicit non-empty `styles=` parameter (use `palette_rgb` for `mv_dop`).
