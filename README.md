# gis_utils

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
pip install -e ~/dev/Gunther-Schulz/gis_utils
```

### Claude Code plugin

The plugin provides MCP tools for API discovery and safety skills for GIS development:

```bash
claude plugin marketplace add Gunther-Schulz/gis_utils
claude plugin install gis-utils@gis-utils
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
| `gis-utils:gis-safety` | Any code writing in GIS projects — CRS rules, dangerous defaults, output conventions |
| `gis-utils:geometry-workflow` | Geometry tasks — enforces data analysis before coding |
| `gis-utils:workflow-authoring` | `workflow.yaml` editing, `gis-workflow` CLI |
| `gis-utils:library-extraction` | Project-local Python — when to extract into the library |
| `gis-utils:dxf-lageplan-extraction` | Reading DXF/CAD plans, extracting layers to GeoPackage/Shapefile |
| `gis-utils:schutzgebiete-analyse` | Distance/overlap analysis with protected areas (Natura 2000, FFH, NSG, …) |
| `gis-utils:buffer-zones-workflow` | Buffer zones around BAB, Bahn, infrastructure (e.g. § 35 BauGB privileging) |
| `gis-utils:qgis-mcp-integration` | Live QGIS bridge — auto-reload, layer add, render preview |

## Live QGIS bridge (optional)

For workflows where you want regenerated outputs to appear immediately
in your open QGIS project (no manual reload), install the optional
`[qgis]` extra and the matching QGIS plugin:

```bash
uv sync --extra qgis    # legacy: pip install -e gis-utils[qgis]
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
isn't installed, this is a silent no-op — gis_utils stays
headless-capable.

For programmatic use in project scripts, see
`gis_utils.qgis_bridge.reload_paths`, `add_layer`, `execute`.  Full
setup details and pitfalls in the `gis-utils:qgis-mcp-integration`
skill.

## Starting a new project

```bash
# 1. Create project folder
mkdir "My Project"

# 2. Initialize (creates workflow.yaml, scripts/, CLAUDE.md)
gis-workflow init "My Project"

# 3. Edit CLAUDE.md — fill in the "Project notes" section with:
#    - CRS (e.g. EPSG:25832)
#    - Data sources and locations
#    - Any coordinate quirks or project-specific context

# 4. Start Claude session in the project folder — it reads CLAUDE.md
#    and knows how to use gis_utils, the workflow runner, and where
#    to put project-specific vs reusable code.
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

## Library API

All common functions importable from top level: `from gis_utils import ...`

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
- `from gis_utils.osm import download_osm_polygons` — OSM Overpass API
- `from gis_utils.wms import run` — WMS download + vectorization
- `from gis_utils.grass import main` — GRASS GIS centerline extraction

## Full API reference

See [CLAUDE.md](CLAUDE.md) for the complete API with signatures, or use the MCP `catalog` tool for live discovery.

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
claude plugin marketplace update gis-utils
claude plugin update gis-utils@gis-utils
```

Then `/reload-plugins` or restart Claude Code.

## License

MIT
