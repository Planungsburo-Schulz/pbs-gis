# pbs_gis

GIS/CAD utility library and project workflow runner.

## Install

This project uses [uv](https://docs.astral.sh/uv/) for environment and
dependency management. From the repo root:

```bash
uv sync            # creates .venv and installs from uv.lock
```

Run the CLI (or anything else) inside the managed environment via `uv run`:

```bash
uv run gis-workflow --help
```

The pinned interpreter is Python 3.14 (see `.python-version`); uv fetches
it automatically if missing.

### Development install

`uv sync` installs the package itself in editable mode, so source edits
take effect immediately. To change dependencies, edit `pyproject.toml` and
re-run `uv sync`.

### Legacy: conda + pip

The previous conda-based workflow still works:

```bash
conda activate gis
pip install -e ~/dev/Planungsbüro-Schulz/pbs-gis
```

### Claude Code plugin

The plugin provides MCP tools for API discovery and safety skills for GIS development:

```bash
claude plugin marketplace add Planungsburo-Schulz/pbs-gis
claude plugin install pbs-gis@pbs-gis
```

Restart Claude Code or run `/reload-plugins` to activate.

**MCP tools** (live introspection, stays in sync with installed library):

| Tool | What it does |
|------|-------------|
| `catalog` | Search functions, recipes, templates, CLI commands |
| `list_recipes` | Discover available data source recipes (WFS, WMS, ALKIS, OSM) |
| `list_templates` | Discover workflow templates for workflow.yaml |
| `check_recipe_layers` | Validate multi-layer recipes against live WFS |
| `get_function_help` | Get full docstring and signature for any function |

**Skills** (auto-discovered, loaded per turn):

| Skill | Triggers on |
|-------|-------------|
| `pbs-gis:gis-safety` | Any code writing in GIS projects — CRS rules, dangerous defaults, output conventions |
| `pbs-gis:geometry-workflow` | Geometry tasks — enforces data analysis before coding |
| `pbs-gis:workflow-authoring` | `workflow.yaml` editing, `gis-workflow` CLI |
| `pbs-gis:library-extraction` | Project-local Python — when to extract into the library |
| `pbs-gis:dxf-lageplan-extraction` | Reading DXF/CAD plans, extracting layers to GeoPackage/Shapefile |
| `pbs-gis:schutzgebiete-analyse` | Distance/overlap analysis with protected areas (Natura 2000, FFH, NSG, …) |
| `pbs-gis:buffer-zones-workflow` | Buffer zones around BAB, Bahn, infrastructure (e.g. § 35 BauGB privileging) |
| `pbs-gis:qgis-mcp-integration` | Live QGIS bridge — auto-reload, layer add, render preview |

## Live QGIS bridge (optional)

For workflows where you want regenerated outputs to appear immediately
in your open QGIS project (no manual reload), install the optional
`[qgis]` extra and the matching QGIS plugin:

```bash
uv sync --extra qgis    # legacy: pip install -e pbs-gis[qgis]
```

Then in QGIS: `Plugins → Manage and Install Plugins…` → search
"**QGIS MCP**" (by N. Karasiak) → install + enable + Start Server.

Activate auto-reload during a workflow run:

```bash
GIS_WORKFLOW_QGIS_RELOAD=1 gis-workflow run
```

The runner refreshes any layer in the open QGIS project whose data
source matches the step's outputs after each successful step.  When
QGIS isn't running, the env var is unset, or the `[qgis]` extra
isn't installed, this is a silent no-op — pbs_gis stays
headless-capable.

For programmatic use in project scripts, see
`pbs_gis.qgis_bridge.reload_paths`, `add_layer`, `execute`.  Full
setup details and pitfalls in the `pbs-gis:qgis-mcp-integration`
skill.

## Starting a new project

```bash
# 1. Create project folder
mkdir "My Project"

# 2. Initialize — creates the canonical folders (Grundlagen/, Geodaten/,
#    Karten/, Reports/, scripts/) and a workflow.yaml
gis-workflow init "My Project"

# 3. Edit workflow.yaml — define your pipeline steps. Discover available
#    templates and recipes with `gis-workflow catalog` or the MCP
#    `catalog`/`list_templates`/`list_recipes` tools.

# 4. Start a Claude session in the project folder — the pbs-gis plugin
#    skills know how to use pbs_gis, the workflow runner, and where to
#    put project-specific vs reusable code.
```

## Workflow runner

Each project has a `workflow.yaml` defining the execution pipeline.

```bash
# Run full workflow
gis-workflow run

# Preview execution plan
gis-workflow run --dry-run

# Run single step + its dependencies
gis-workflow run --step "Step Name"

# Initialize new project
gis-workflow init [project_dir]
```

Steps marked `run: auto` (default) are skipped if outputs exist and are up-to-date.
Steps marked `run: always` execute every time.
Dependencies are resolved automatically (topological sort).

### Ops steps (declarative geometry pipelines)

Alongside `template:` / `recipe:` / `script:` steps, a step may declare an
`ops:` chain — a list of geometry operations applied `gdf → gdf` in order to a
single input layer:

```yaml
steps:
  - name: Baufeld säubern
    ops:
      - {op: clean_line, min_segment_length: 0.5}
      - {makro: saeuberung_standard}
    input: Geodaten/baufeld_roh.gpkg
    output: Geodaten/baufeld.gpkg
```

Each element is either an op `{op: <name>, ...params}` (params are passed as
keyword arguments) or a makro reference `{makro: <name>}`. Registered ops are
the single-input `gdf → gdf` functions from `pbs_gis.operations` and the
`pbs_gis.geometry` cleaning family (e.g. `clean_line`, `remove_degenerate_spikes`,
`remove_protrusions`, `remove_slivers_erosion`, `simplify_slivers`,
`morphological_filter`, `subtract_smaller_overlaps`, `repair`). An unknown op
name is a hard error.

**Makros** are named, reusable op lists. One ships built-in —
`saeuberung_standard` (`remove_degenerate_spikes → remove_protrusions →
remove_slivers_erosion → repair`). Define your own under a top-level `makros:`
key (same op-list form) and reference them from any ops step:

```yaml
makros:
  trassen_clean:
    - {op: clean_line, min_segment_length: 0.5}
    - {op: repair}

steps:
  - name: Trassen säubern
    ops:
      - {makro: trassen_clean}
    input: Geodaten/trassen_roh.gpkg
    output: Geodaten/trassen.gpkg
```

A makro is expanded inline (order preserved, reusable multiple times). A makro
body may not reference another makro (no recursion), an unknown makro name is a
hard error, and a project makro may not reuse a built-in makro's name — all are
loud errors, never silent skips or overrides.

## Library API

All common functions importable from top level: `from pbs_gis import ...`

### DXF
- `extract_dxf_layers()` — extract all geometry from DXF → `{layer: {geom_type: GeoDataFrame}}`
- `extract_dxf_circles()` — circle centers as Point GeoDataFrame with radius
- `save_layers_as_shapefiles()` — write extracted layers to organized shapefiles
- `new_dxf_document()` — new DXF with proper CAD headers
- `ensure_layer()` — create layer if missing
- `shapefile_to_dxf()` — convert SHP→DXF with optional Map OD and labels
- `attach_od_to_entity()` / `encode_od_1004()` — AutoCAD Map Object Data

### Geometry
- `remove_inner_rings()` — remove holes from polygons
- `make_valid_gdf()` — repair invalid geometries
- `subtract_geometries()` — set difference (base minus subtract)
- `subtract_smaller_overlaps()` — remove overlaps by area
- `morphological_filter()` — buffer-dissolve-buffer polygon cleanup
- `distance_to_nearest()` — min distance to reference features
- `points_with_buffers()` — create points + buffer union from coordinate data
- `load_and_union()` — load shapefile, union all geometries

### Reporting
- `markdown_table()` — fixed-width markdown table (aligns in raw view)
- `area_report()` — full markdown area report with optional parcel intersection
- `area_by_category()` — intersection areas grouped by category

### Specialized (import from submodule)
- `from pbs_gis.osm import download_osm_polygons` — OSM Overpass API
- `from pbs_gis.wms import run` — WMS download + vectorization
- `from pbs_gis.grass import main` — GRASS GIS centerline extraction
- `from pbs_gis.operations import clean_line` — drop near-duplicate vertices and
  short segments from (Multi)LineStrings (gdf in/out)
- `from pbs_gis.cad import insert_block_array` — emit block references at even
  spacing along a line, optionally rotated to follow the path

## Full API reference

For the complete API with signatures, use the MCP `catalog` and
`get_function_help` tools for live discovery, or run `gis-workflow catalog`
from the CLI.

## Development

Plugin files:

```
plugin/
├── .claude-plugin/
│   └── plugin.json
├── .mcp.json                          # MCP server config
└── skills/
    ├── gis-safety/SKILL.md
    └── geometry-workflow/SKILL.md

mcp/
└── server.py                          # FastMCP server wrapping catalog/recipes/templates
```

After editing skills or the MCP server, push and update:

```bash
git add -A && git commit -m "..." && git push
claude plugin marketplace update pbs-gis
claude plugin update pbs-gis@pbs-gis
```

Then `/reload-plugins` or restart Claude Code.

## License

MIT
