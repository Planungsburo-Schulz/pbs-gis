---
name: qgis-mcp-integration
description: This skill should be used when the user asks to "set up QGIS MCP", "QGIS-Live-Bridge", "auto-reload layers in QGIS", "render map from QGIS", "open generated layer in QGIS", "qgis_bridge", "GIS_WORKFLOW_QGIS_RELOAD", "QGIS plugin installieren", "Claude soll QGIS steuern", "MCP server QGIS", or any task involving the live integration between gis_utils workflows and a running QGIS instance via the qgis-mcp plugin.
license: MIT
---

## QGIS-MCP Integration with gis_utils

The optional **`gis_utils.qgis_bridge`** module connects gis_utils workflows
to a running QGIS instance via the `qgis-mcp` plugin (by N. Karasiak).
Use cases: auto-reload regenerated layers, open output files in QGIS,
render preview maps, run Processing algorithms via QGIS.

## Setup (one-time, per machine)

### 1. QGIS Plugin

In QGIS: `Plugins → Manage and Install Plugins…` → search "**QGIS MCP**"
(by Nicolas Karasiak, repository nkarasiak/qgis-mcp) → install + enable.

Then: `Plugins → QGIS MCP → Start Server` (default port 9876).

Plugin version compatibility:
- Plugin v0.2.1 needs QGIS 3.28+ and Python 3.12+ (uses `datetime.UTC`)
- For older QGIS conda envs (Python 3.9): create a new env with
  `qgis>=3.44 python=3.12` — see "Common pitfalls" below

### 2. MCP Server (Python side)

Recommended: install via the upstream installer:

```bash
git clone https://github.com/nkarasiak/qgis-mcp.git ~/dev/Gunther-Schulz/qgis-mcp
cd ~/dev/Gunther-Schulz/qgis-mcp
uv sync
python install.py --non-interactive --clients claude-code
```

The installer prints a `claude mcp add` command — but on Linux, the
**suggested command needs PYTHONPATH** to find the qgis_mcp package
(the project isn't pip-packaged with src-layout).  Use this instead:

```bash
claude mcp add qgis --scope user \
  -e PYTHONPATH=/home/<USER>/dev/Gunther-Schulz/qgis-mcp/src \
  -- uv run --no-sync \
  --directory /home/<USER>/dev/Gunther-Schulz/qgis-mcp \
  src/qgis_mcp/server.py
```

(NOT in `~/.claude/settings.json` `mcpServers` block — Claude Code does
not read that field.  Use `claude mcp add` which writes to
`~/.claude.json`.)

### 3. gis_utils with optional `[qgis]` extra

```bash
pip install -e gis-utils[qgis]
# or, from a fresh PyPI install:  pip install gis-utils[qgis]
```

This pulls in `qgis-mcp` from git as the Python client dependency.
Without this extra, `gis_utils.qgis_bridge` becomes a silent no-op
(headless-safe).

### 4. Verify

```bash
claude mcp list                          # qgis: ✓ Connected
```

In Claude Code, after `/reload-plugins` or full restart, run
`mcp__qgis__diagnose` — should report all checks ✓.

## Multiple QGIS instances

The qgis-mcp plugin binds a single TCP port (default 9876) per QGIS
process.  A second QGIS started on the same port silently fails to
serve MCP.  Both halves of the stack already support multi-port —
no fork or wrapper script needed:

- **Plugin (QGIS side)**: toolbar dropdown next to the "Run MCP"
  button has a port spin-box.  Choice persists in QSettings
  per-profile.
- **Server (Claude side)**: reads `QGIS_MCP_PORT` env var.

**Convention: name registrations by port number, not by project.**
The MCP is port-bound and project-blind — it sees whatever project
is loaded behind the port at the moment of the call.  Use names like
`qgis_9877`, `qgis_9878` so the registration name encodes the port,
making it obvious which spinbox value to set in QGIS.

```bash
# Default instance — port 9876, registration name "qgis":
claude mcp add qgis --scope user \
  -e PYTHONPATH=/home/<USER>/dev/Gunther-Schulz/qgis-mcp/src \
  -- uv run --no-sync \
  --directory /home/<USER>/dev/Gunther-Schulz/qgis-mcp \
  src/qgis_mcp/server.py

# Second instance — port 9877, registration name "qgis_9877":
claude mcp add qgis_9877 --scope user \
  -e QGIS_MCP_PORT=9877 \
  -e PYTHONPATH=/home/<USER>/dev/Gunther-Schulz/qgis-mcp/src \
  -- uv run --no-sync \
  --directory /home/<USER>/dev/Gunther-Schulz/qgis-mcp \
  src/qgis_mcp/server.py
```

Both env vars (`QGIS_MCP_PORT` *and* `PYTHONPATH`) are required for
non-default ports — `claude mcp list` reports `✗ Failed to connect`
if `PYTHONPATH` is missing because the server crashes at import
before it ever reaches the port logic.  A quick `claude mcp list`
sanity check will catch this immediately.

After registration, `/reload-plugins` in Claude Code is sufficient —
no full restart required.  Each port appears as its own tool family:
`mcp__qgis__*`, `mcp__qgis_9877__*`, etc.

**Per-profile gotcha**: the plugin saves its port choice in QSettings
scoped to the QGIS user profile.  Two QGIS instances using the same
default profile will both restore the same saved port and the second
one fails to bind.  For habitual multi-instance use, launch each with
its own profile:

```bash
qgis --profile inst1     # remembers port 9876
qgis --profile inst2     # remembers port 9877
```

This way the spinbox-and-save sequence only needs doing once per
profile, and each instance comes back up on the right port on its
own.

## Usage patterns

### Pattern A — Auto-reload existing layers after `gis-workflow run`

Set the env var; after each successful step the runner refreshes all
layers in the open QGIS project whose source matches the step's
outputs.

```bash
GIS_WORKFLOW_QGIS_RELOAD=1 gis-workflow run
```

Layers must already be in the QGIS project — this only **reloads**,
not adds.

### Pattern B — Auto-open new outputs (vector + raster)

Adds new outputs to the project as layers (with idempotent dedup).
Two ways to enable:

```bash
GIS_WORKFLOW_QGIS_OPEN=1 gis-workflow run    # globally
```

```yaml
# Per-step, in workflow.yaml:
- name: My step
  ...
  qgis_open: true
```

Vector vs raster is auto-detected by file extension.

**Sibling QML auto-applied**: if `Shape/foo.shp` has a sibling
`Shape/foo.qml`, the runner applies it on add/reload.  Convention over
configuration — drop a QML next to a generated layer and it just works.

Recommended for live development:

```bash
GIS_WORKFLOW_QGIS_RELOAD=1 GIS_WORKFLOW_QGIS_OPEN=1 gis-workflow run
```

### Pattern C — Auto-screenshot audit trail

Save canvas screenshots after every successful step to a directory:

```bash
GIS_WORKFLOW_QGIS_SCREENSHOTS=Output/audit/screenshots gis-workflow run
```

Filenames are derived from step names (sanitized).  Useful for
review / Behörden-Nachweis: documents the visual state of the QGIS
project after each generation step.

### Pattern D — Apply a saved QML style explicitly

When the QML lives elsewhere than next to the data file (central
styles directory), use the `apply_qml_style` template:

```yaml
- name: Pufferzonen stylen
  template: apply_qml_style
  params:
    layer: Shape/bab_pufferzonen.gpkg
    qml: ~/dev/Gunther-Schulz/PBS-Templates/styles/bab_pufferzonen.qml
```

### Pattern E — Print layout from a QGIS .qpt template

For deterministic Lageplan-PDFs:

```yaml
- name: Lageplan PDF erzeugen
  template: layout_from_qpt
  params:
    template: ~/dev/Gunther-Schulz/PBS-Templates/Lageplan-A3.qpt
    layout_name: "Wölzow Lageplan A3"
    items:
      title: "PV-Anlage Wölzow — Privilegierungsplan"
      subtitle: "B-Plan Nr. 25-01, Stand Mai 2026"
    map:
      id: main_map
      layers: ["Modulflächen", "BAB-Pufferzonen"]
      extent_from_layer: "Projektfläche"
      buffer_m: 200
    format: pdf
    dpi: 300
  output: Output/Karten/Lageplan_A3.pdf
```

To create the `.qpt`: build the layout once in QGIS (Page Setup,
schriftfeld, logo, legend, scale bar, north arrow), assign meaningful
**Item IDs** in the Item Properties panel, then Layout Manager →
Save as Template.

### Pattern F — Programmatic from a project script

```python
from gis_utils import qgis_bridge

# Generate file ...
gdf.to_file("Shape/MyResult.gpkg", driver="GPKG")

# Reload existing matching layers (no-op if QGIS not running)
qgis_bridge.reload_paths(["Shape/MyResult.gpkg"])

# Or add as a new layer (auto-detects vector/raster, idempotent)
qgis_bridge.open_path("Shape/MyResult.gpkg", name="My Result")

# Apply a QML style
qgis_bridge.apply_qml("Shape/MyResult.gpkg", "styles/result.qml")

# Take a canvas screenshot
qgis_bridge.take_canvas_screenshot("Output/preview.png")

# Or arbitrary PyQGIS:
qgis_bridge.execute("iface.mapCanvas().refresh()")
```

### Pattern G — Claude orchestration via MCP tools directly

When working interactively in a Claude Code session with the qgis MCP
loaded, prefer `mcp__qgis__*` tools for ad-hoc layer manipulation,
canvas screenshots, layout exports, etc.  The bridge is for *workflow*
integration; the MCP tools are for *interactive* control.

## Environment variable summary

| Variable | Effect | Independent of others? |
|---|---|---|
| `GIS_WORKFLOW_QGIS_RELOAD=1` | Refresh existing matching layers after each step | Yes |
| `GIS_WORKFLOW_QGIS_OPEN=1` | Add new outputs as layers + auto-apply sibling QML | Yes |
| `GIS_WORKFLOW_QGIS_SCREENSHOTS=path/to/dir` | Save canvas PNG after each step | Yes |

All three are independent and can combine.  None require QGIS to be
running — they all gracefully no-op when QGIS is unreachable.

## Common pitfalls (lessons learned the hard way)

| Symptom | Cause | Fix |
|---|---|---|
| `ImportError: cannot import name 'UTC' from 'datetime'` when loading the plugin | QGIS conda env on Python 3.9; plugin needs 3.11+ for `datetime.UTC` | Create new env: `conda create -n qgis-ltr -c conda-forge qgis=3.44.7 python=3.12` and update Desktop launcher |
| `mcp__qgis__*` tools return `null` on every call | Wire-format mismatch — installed plugin is `nkarasiak/qgis-mcp` (length-prefixed framing) but registered MCP server is `jjsantos01/qgis_mcp` (newline-delimited) | Use the matching server: `nkarasiak/qgis-mcp` (51 tools, what's in the QGIS Plugin Registry today) |
| `ModuleNotFoundError: No module named 'qgis_mcp'` when starting the server | Project uses src-layout but is not pip-packaged | Set `PYTHONPATH=...src` via `claude mcp add -e PYTHONPATH=...` |
| `claude mcp list` doesn't show qgis even though `~/.claude/settings.json` has it | Claude Code does not read `mcpServers` from settings.json | Register via `claude mcp add` (writes to `~/.claude.json`) |
| Tools listed with old schemas after server change | Tool schemas cached per session | Full Claude Code restart — `/reload-plugins` is not enough |
| `is_available()` True but `reload_paths` returns 0 | No layers in the open QGIS project match the given paths | Either pre-load the layer (`qgis_bridge.add_layer(...)`) or expect this — the bridge does NOT auto-add files |
| `execute()` / `mcp__qgis__execute_code` returns `{executed, stdout, stderr}` — your `result = {...}` variable is dropped | Current plugin captures stdout/stderr, not a `result` namespace var | `print()` status to stdout; treat the **written output file as the success signal**, not a returned value |
| Passing `None`/wrong type into a PyQGIS C++ call **crashes the whole QGIS process** (e.g. `QgsMapThemeCollection.createThemeFromCurrentState(root, None)`) | C++ binding, no Python type guard → segfault | Validate args before the call; build it incrementally; never pass `None` where an object is expected |
| `layerTreeRoot().removeAllChildren()` **deletes the layers from the project**, not just the tree nodes | Layer-tree↔registry bridge drops a layer when its last tree node is removed | Reorder via `root.setCustomLayerOrder([...])`; never `removeAllChildren()` to reorder |
| Layer renders at the wrong location after you regenerate its file in a different CRS | QGIS caches the layer's declared CRS from first load | `layer.dataProvider().reloadData(); layer.setCrs(new_crs)` after regenerating |

## Architecture quick-reference

```
gis_utils.qgis_bridge          ←  thin wrapper, lazy import of qgis-mcp client
        │
        │  via qgis-mcp's QgisMCPClient
        ▼
qgis-mcp Python server          ←  outside QGIS; speaks MCP and TCP
        │
        │  TCP socket :9876, length-prefixed JSON
        ▼
qgis-mcp QGIS plugin           ←  inside QGIS; QTimer-driven non-blocking server
        │
        │  PyQGIS API
        ▼
QGIS app
```

The bridge ships in gis_utils so workflow code can call it directly.
The qgis-mcp Python client is the optional dependency.  The QGIS plugin
must be installed separately via QGIS Plugin Manager.

## Discovery

```
mcp__gis-utils__catalog(search="qgis_bridge")
```
