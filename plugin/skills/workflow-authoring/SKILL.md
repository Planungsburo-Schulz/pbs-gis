---
name: workflow-authoring
description: This skill should be used when the user asks to "create a workflow", "add a step", "edit workflow.yaml", "run the pipeline", "initialize a project", "use a template", "generate a report", "write up the results", or any task involving gis-workflow CLI, workflow.yaml configuration, or producing a project report from the geodata.
license: MIT
---

## Workflow Authoring

### Project setup

```bash
gis-workflow init [project_dir]    # creates workflow.yaml, scripts/
gis-workflow run                   # run full pipeline
gis-workflow run --dry-run         # preview execution plan
gis-workflow run --step "Name"     # run single step + dependencies
```

### Discovery first, then lock the pipeline

Use ad-hoc scripts only while *discovering* the data (unknown CRS, layer semantics, georeferencing). Once the method is known, move every step into `workflow.yaml` + `scripts/` — then requirement changes (drop a layer, change CRS, add an analysis) become a one-line edit + `gis-workflow run`, not a manual redo.

### Reports are steps too — never hand-type numbers

A report (MD/PDF) whose figures derive from the geodata is a pipeline step, not a hand-written document — any number typed by hand goes stale silently the moment the data changes, and nothing flags it. Generate it mechanically: static/templated prose with computed values filled from the analysis layers (`conflict_matrix`, `area_report`, `area_by_category`, `intersection_areas`, `markdown_table` in `gis_utils.reporting` / `md_table`). Test: the only edits a data change requires are to the data + `gis-workflow run` — if a data change leaves a stale figure anywhere in the report, the report is not yet a step. Declare the report's data files under `inputs:` (see Step execution rules) or `auto` skips it and the edit is silently ignored. Applies to every project, not just the one at hand.

### workflow.yaml format

Steps can use **scripts** (Python files) or **templates** (built-in patterns):

```yaml
# Script-based step
- name: Extract parcels
  script: scripts/extract_parcels.py
  output: output/parcels.gpkg

# Template-based step (no script needed)
- name: Extract DXF boundaries
  template: dxf_lines_to_polygon
  params:
    dxf: data/boundary.dxf
    layer: GRENZE
    crs: "EPSG:25832"
    extend: 10.0
  output: output/boundary.gpkg

# Multi-layer recipe download
- name: Download ALKIS
  recipe: sh_alkis
  layers: [flurstuecke, gebaeudeflaechen]
  output_dir: output/alkis/
```

**Step execution rules:**
- `run: auto` (default) — skipped if outputs exist and are up-to-date
- `inputs:` — files the step reads (incl. upstream steps' outputs). Under `run: auto` the step re-runs when any input is newer than its outputs (make-style staleness), and re-runs cascade downstream in one `gis-workflow run`. The step's own `script:` is an implicit input — editing the code re-runs the step automatically. Declare a step's real *data* inputs so a data edit + `gis-workflow run` regenerates everything; without it, `auto` skips whenever outputs merely exist — so an edited input (e.g. a re-digitized shapefile) is silently ignored.
- `run: always` — executes every time
- Dependencies resolved automatically (topological sort)

### Discover available templates and recipes

Use the MCP tools before writing workflow steps:
- `mcp__gis-utils__list_templates` — find reusable processing patterns
- `mcp__gis-utils__list_recipes` — find data source recipes
- `mcp__gis-utils__get_function_help` — get function details for scripts

Prefer templates over scripts when a template exists for the task — they're faster, tested, and require no code.

### Catalog sync

When adding new submodules or CLI commands to gis_utils, update `catalog.py`:
- **New submodule** → add to `_MODULES` list
- **New CLI subcommand** → add to `_CLI_COMMANDS` list

Function signatures and docstrings are introspected automatically.
