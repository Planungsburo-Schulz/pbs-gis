"""
Simple YAML-based workflow runner for GIS projects.

Reads a workflow.yaml from a project directory, resolves step dependencies,
and executes scripts in the correct order. Steps can be marked as "auto"
(skip if outputs exist and are up-to-date) or "always" (run every time).
Default is "auto".

Usage:
    # From project directory:
    python -m gis_utils.runner

    # Or specify project path:
    python -m gis_utils.runner /path/to/project

    # Run a single step:
    python -m gis_utils.runner --step "Extract DXF layers"

    # Dry run (show execution plan):
    python -m gis_utils.runner --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import yaml


def load_workflow(project_dir: Path) -> dict:
    """Load workflow.yaml from project directory."""
    wf_path = project_dir / "workflow.yaml"
    if not wf_path.exists():
        raise FileNotFoundError(f"No workflow.yaml found in {project_dir}")
    with open(wf_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_order(steps: list[dict]) -> list[dict]:
    """Topological sort of steps based on depends_on."""
    by_name = {s["name"]: s for s in steps}
    in_degree: dict[str, int] = defaultdict(int)
    dependents: dict[str, list[str]] = defaultdict(list)

    for s in steps:
        name = s["name"]
        if name not in in_degree:
            in_degree[name] = 0
        for dep in s.get("depends_on", []):
            if dep not in by_name:
                raise ValueError(f"Step '{name}' depends on unknown step '{dep}'")
            dependents[dep].append(name)
            in_degree[name] += 1

    queue = [name for name, deg in in_degree.items() if deg == 0]
    ordered: list[str] = []

    while queue:
        # Stable sort: process in definition order among zero-degree nodes
        queue.sort(key=lambda n: next(i for i, s in enumerate(steps) if s["name"] == n))
        name = queue.pop(0)
        ordered.append(name)
        for dep in dependents[name]:
            in_degree[dep] -= 1
            if in_degree[dep] == 0:
                queue.append(dep)

    if len(ordered) != len(steps):
        remaining = set(s["name"] for s in steps) - set(ordered)
        raise ValueError(f"Circular dependency involving: {remaining}")

    return [by_name[name] for name in ordered]


def _collect_outputs(step: dict) -> list[str]:
    """Collect all expected output paths from a step definition."""
    outputs = list(step.get("outputs", []))
    if not outputs and step.get("output"):
        outputs = [step["output"]]
    # Multi-layer recipe: output_dir + layers → one .gpkg per alias
    if not outputs and step.get("output_dir") and step.get("layers"):
        output_dir = step["output_dir"]
        for alias in step["layers"]:
            outputs.append(f"{output_dir.rstrip('/')}/{alias}.gpkg")
    return outputs


def _newest_mtime(path: Path) -> float:
    """Newest mtime at/under path (file → its mtime; dir → max over files).

    Returns 0.0 if the path is missing — a declared input that doesn't exist
    can't make outputs stale.
    """
    if not path.exists():
        return 0.0
    if path.is_file():
        return path.stat().st_mtime
    mtimes = [p.stat().st_mtime for p in path.rglob("*") if p.is_file()]
    return max(mtimes) if mtimes else path.stat().st_mtime


def should_skip(step: dict, project_dir: Path) -> bool:
    """Check if an 'auto' step can be skipped.

    Skip only if all outputs exist AND no declared input is newer than the
    oldest output (make-style staleness). Inputs are taken from ``inputs:``
    (any step), the step's own ``script:`` (so a code edit re-runs it), plus,
    for recipe steps, ``input_boundary:`` (the scope file).
    Declaring a step's real input files — including upstream steps' outputs —
    lets a single ``gis-workflow run`` re-run the step, and cascade downstream,
    when the data changes, instead of skipping on mere output existence.
    Without declared inputs the old behaviour stands: skip if outputs exist.
    """
    if step.get("run", "auto") != "auto":
        return False
    outputs = _collect_outputs(step)
    if not outputs:
        return False

    output_paths = [project_dir / out for out in outputs]
    if not all(p.exists() for p in output_paths):
        return False
    oldest_output = min(p.stat().st_mtime for p in output_paths)

    declared_inputs = list(step.get("inputs", []))
    # A step depends on its own script (make-style: target depends on its recipe),
    # so editing the script re-runs the step and cascades downstream.
    if step.get("script"):
        declared_inputs.append(step["script"])
    # Recipe scope file behaves as an input too (back-compat).
    input_boundary = step.get("input_boundary")
    if input_boundary and step.get("recipe"):
        declared_inputs.append(input_boundary)
    for inp in declared_inputs:
        if _newest_mtime(project_dir / inp) > oldest_output:
            return False  # input changed — re-run

    return True


def run_step(step: dict, project_dir: Path, conda_env: str | None = None) -> bool:
    """Execute a single workflow step. Returns True on success.

    A step may override the project-wide conda env via ``conda_env:`` —
    e.g. a PyQGIS step that needs a qgis env while the pipeline runs in a
    plain geopandas env.
    """
    conda_env = step.get("conda_env") or conda_env
    # Template steps: run built-in processing templates
    if step.get("template"):
        return _run_template_step(step, project_dir)

    # Recipe steps: run directly via run_recipe()
    if step.get("recipe"):
        return _run_recipe_step(step, project_dir)

    script = step.get("script")
    if not script:
        print(f"  [skip] No script or recipe defined")
        return True

    script_path = project_dir / script
    if not script_path.exists():
        print(f"  [ERROR] Script not found: {script_path}")
        return False

    args = step.get("args", [])
    if isinstance(args, str):
        args = args.split()

    cmd = [sys.executable, str(script_path)] + [str(a) for a in args]

    if conda_env:
        cmd = ["conda", "run", "-n", conda_env, "--no-capture-output",
               "python", str(script_path)] + [str(a) for a in args]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(project_dir),
            capture_output=False,
            timeout=step.get("timeout", 600),
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  [ERROR] Timed out after {step.get('timeout', 600)}s")
        return False
    except Exception as e:
        print(f"  [ERROR] {e}")
        return False


def _resolve_extent(step: dict, project_dir: Path) -> dict:
    """Resolve input_boundary + buffer_m into kwargs for recipe runners."""
    input_boundary = step.get("input_boundary")
    crs = step.get("crs")
    buffer_m = step.get("buffer_m")
    kwargs = {}
    if crs:
        kwargs["crs"] = crs
    if input_boundary:
        input_path = project_dir / input_boundary
        if not input_path.exists():
            raise FileNotFoundError(f"input_boundary not found: {input_path}")
        if buffer_m:
            import geopandas as gpd
            if not crs:
                raise ValueError(f"Recipe step '{step['name']}': crs is required when using buffer_m.")
            gdf = gpd.read_file(input_path).to_crs(crs)
            b = gdf.total_bounds
            kwargs["extent"] = (
                b[0] - buffer_m, b[1] - buffer_m,
                b[2] + buffer_m, b[3] + buffer_m,
            )
        else:
            kwargs["input_boundary"] = input_path
    return kwargs


def _run_template_step(step: dict, project_dir: Path) -> bool:
    """Execute a template-based workflow step."""
    from gis_utils.templates import get_template

    template_name = step["template"]
    params = step.get("params", {})
    output = step.get("output")
    output_path = project_dir / output if output else None

    try:
        handler = get_template(template_name)
    except KeyError as e:
        print(f"  [ERROR] {e}")
        return False

    try:
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
        return handler(params, project_dir, output_path)
    except Exception as e:
        print(f"  [ERROR] Template '{template_name}' failed: {e}")
        return False


def _run_recipe_step(step: dict, project_dir: Path) -> bool:
    """Execute a recipe-based workflow step (single or multi-layer)."""
    recipe_name = step["recipe"]
    layer_aliases = step.get("layers")
    attr_filter = step.get("filter")

    try:
        extent_kwargs = _resolve_extent(step, project_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"  [ERROR] {e}")
        return False

    if attr_filter:
        extent_kwargs["filter"] = attr_filter

    # Multi-layer recipe
    if layer_aliases:
        from gis_utils.recipes import run_multi_layer_recipe
        output_dir = step.get("output_dir")
        output_dir_path = project_dir / output_dir if output_dir else None
        try:
            run_multi_layer_recipe(
                recipe_name,
                layer_aliases,
                output_dir=output_dir_path,
                recipe_dir=project_dir,
                **extent_kwargs,
            )
            return True
        except Exception as e:
            print(f"  [ERROR] Recipe '{recipe_name}' failed: {e}")
            return False

    # Single-layer recipe
    from gis_utils.recipes import run_recipe
    output = step.get("output")
    output_path = project_dir / output if output else None
    try:
        run_recipe(
            recipe_name,
            output_path=output_path,
            recipe_dir=project_dir,
            **extent_kwargs,
        )
        return True
    except Exception as e:
        print(f"  [ERROR] Recipe '{recipe_name}' failed: {e}")
        return False


def run_workflow(
    project_dir: str | Path,
    *,
    step_name: str | None = None,
    dry_run: bool = False,
    conda_env: str | None = None,
) -> bool:
    """
    Execute a project workflow.

    Args:
        project_dir: Path to project root (must contain workflow.yaml).
        step_name: Run only this step (and its dependencies). None = all.
        dry_run: If True, show plan without executing.
        conda_env: Conda environment name to run scripts in. None = current env.

    Returns:
        True if all steps succeeded.
    """
    project_dir = Path(project_dir).resolve()
    wf = load_workflow(project_dir)
    project_name = wf.get("project", {}).get("name", project_dir.name)
    default_env = wf.get("project", {}).get("conda_env")
    env = conda_env or default_env

    steps = wf.get("steps", [])
    if not steps:
        print("No steps defined in workflow.yaml")
        return True

    ordered = resolve_order(steps)

    # Filter to single step + dependencies if requested
    if step_name:
        needed = _collect_deps(step_name, {s["name"]: s for s in ordered})
        ordered = [s for s in ordered if s["name"] in needed]

    print(f"{'[DRY RUN] ' if dry_run else ''}Workflow: {project_name}")
    print(f"Steps: {len(ordered)}\n")

    all_ok = True
    for i, step in enumerate(ordered, 1):
        name = step["name"]
        mode = step.get("run", "auto")
        skip = should_skip(step, project_dir)

        status = "SKIP (outputs exist)" if skip else mode
        prefix = f"[{i}/{len(ordered)}]"

        if dry_run:
            deps = step.get("depends_on", [])
            dep_str = f" (after: {', '.join(deps)})" if deps else ""
            print(f"  {prefix} {name} [{status}]{dep_str}")
            if step.get("script"):
                print(f"        → {step['script']}")
            elif step.get("recipe"):
                detail = f"recipe:{step['recipe']}"
                if step.get("layers"):
                    detail += f" [{len(step['layers'])} layers]"
                if step.get("output"):
                    detail += f" → {step['output']}"
                if step.get("output_dir"):
                    detail += f" → {step['output_dir']}"
                print(f"        → {detail}")
            continue

        if skip:
            print(f"  {prefix} {name} — skipped (outputs exist)")
            continue

        print(f"  {prefix} {name}...")
        t0 = time.time()
        ok = run_step(step, project_dir, conda_env=env)
        elapsed = time.time() - t0

        if ok:
            print(f"  {prefix} {name} — done ({elapsed:.1f}s)")
            _maybe_reload_qgis(step, project_dir)
        else:
            print(f"  {prefix} {name} — FAILED ({elapsed:.1f}s)")
            if step.get("required", True):
                print(f"\nAborting: required step '{name}' failed.")
                return False
            all_ok = False

    if not dry_run:
        print(f"\n{'All steps completed.' if all_ok else 'Completed with errors.'}")
    return all_ok


def _maybe_reload_qgis(step: dict, project_dir: Path) -> None:
    """Optionally interact with a running QGIS instance after a step.

    Three independent post-step actions, each opt-in:

    * **Auto-reload** (``GIS_WORKFLOW_QGIS_RELOAD=1``): refresh layers
      already in the project whose source matches the step's outputs.
    * **Auto-open** (``GIS_WORKFLOW_QGIS_OPEN=1`` or ``qgis_open: true``
      on the step): add the step's outputs as new layers if not already
      present (vector + raster auto-detected by extension).  Sibling
      ``.qml`` files (e.g. ``Shape/foo.shp`` + ``Shape/foo.qml``) are
      applied automatically.
    * **Auto-screenshot** (``GIS_WORKFLOW_QGIS_SCREENSHOTS=/path``):
      save the current canvas to ``<path>/<step-name>.png`` for an
      audit trail of the workflow run.

    Silent no-op when disabled, when QGIS is not running, or when the
    optional ``[qgis]`` extra is not installed.
    """
    from gis_utils import qgis_bridge

    do_reload = qgis_bridge.auto_reload_enabled()
    do_open = qgis_bridge.auto_open_enabled() or bool(step.get("qgis_open"))
    shots_dir = qgis_bridge.screenshots_dir()
    if not (do_reload or do_open or shots_dir):
        return
    if not qgis_bridge.is_available():
        return

    outputs = _collect_outputs(step)
    paths = [project_dir / out for out in outputs] if outputs else []

    if do_open and paths:
        for p in paths:
            if Path(p).is_file() or Path(p).is_dir():
                qgis_bridge.open_path(p)
                _maybe_apply_sibling_qml(p)

    if do_reload and paths:
        qgis_bridge.reload_paths(paths)
        for p in paths:
            _maybe_apply_sibling_qml(p)

    if shots_dir is not None:
        # Sanitize step name → file-system-safe png name
        name = step.get("name", "step")
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name).strip("_")
        out_png = shots_dir / f"{safe}.png"
        qgis_bridge.take_canvas_screenshot(out_png)


def _maybe_apply_sibling_qml(layer_path) -> None:
    """If a sibling ``.qml`` exists next to *layer_path*, apply it via the
    QGIS bridge.  Handles GPKG paths with ``|layername=…`` suffix.
    """
    from gis_utils import qgis_bridge

    p = Path(str(layer_path).split("|", 1)[0])
    qml = p.with_suffix(".qml")
    if qml.is_file():
        qgis_bridge.apply_qml(p, qml)


def _collect_deps(name: str, by_name: dict[str, dict]) -> set[str]:
    """Collect a step and all its transitive dependencies."""
    if name not in by_name:
        raise ValueError(f"Unknown step: '{name}'")
    result = {name}
    for dep in by_name[name].get("depends_on", []):
        result |= _collect_deps(dep, by_name)
    return result


WORKFLOW_TEMPLATE = """\
project:
  name: {name}

# Steps run in the current Python environment (`uv run gis-workflow run`).
# A step may opt into a separate conda env via `conda_env: <name>` — e.g. a
# headless-PyQGIS layout step that needs a QGIS env — see run_step().
#
# Define your pipeline steps below. Run with `gis-workflow run`.
# Discover available templates via the gis-utils MCP tool `list_templates`
# or `gis-workflow catalog`.
#
# Example template-based step:
#
#   - name: Build boundary polygon
#     template: lines_to_polygon
#     params:
#       input: Grundlagen/boundary.shp
#       crs: "EPSG:25833"
#     output: Geodaten/boundary.gpkg
#
# Example script-based step:
#
#   - name: Custom processing
#     script: scripts/my_script.py
#     output: Geodaten/result.gpkg
#     depends_on:
#       - Build boundary polygon

steps: []
"""

# --- Canonical project directory layout (single source of truth) --------------
# Every PBS GIS project uses these exact folder names so scripts, templates, and
# the layout/report conventions can rely on them instead of re-inventing variants.
# `gis-workflow init` creates them; the workflow-authoring skill documents them.
PROJECT_DIRS: dict[str, str] = {
    "Grundlagen": "Source/input data as received (DXF, DWG, plans, CSV) — never modified",
    "Geodaten": "Generated geodata (GeoPackage, Shapefile)",
    "Karten": "Map exports (PDF/PNG) and the QGIS project",
    "Reports": "Generated markdown reports",
    "scripts": "Project Python scripts run via workflow.yaml",
}

def init_project(project_dir: str | Path) -> None:
    """
    Initialize a GIS project: create the canonical :data:`PROJECT_DIRS` layout
    (Grundlagen/, Geodaten/, Karten/, Reports/, scripts/) and a workflow.yaml.

    Args:
        project_dir: Path to project directory.
    """
    project_dir = Path(project_dir).resolve()
    name = project_dir.name

    # Create the canonical project directory layout (PROJECT_DIRS)
    for d in PROJECT_DIRS:
        p = project_dir / d
        existed = p.is_dir()
        p.mkdir(exist_ok=True)
        print(f"  {d}/ — {'exists' if existed else 'created'}")

    # Create workflow.yaml (don't overwrite)
    wf_path = project_dir / "workflow.yaml"
    if wf_path.exists():
        print(f"  workflow.yaml — already exists (skipped)")
    else:
        wf_path.write_text(WORKFLOW_TEMPLATE.format(name=name), encoding="utf-8")
        print(f"  workflow.yaml — created")

    print(f"\nProject initialized: {name}")
    print(f"\nGIS skills and MCP tools provided by the gis-utils Claude Code plugin.")
    print(f"Install: claude plugin marketplace add Gunther-Schulz/gis_utils")


def _run_check_recipes(recipe_name: str | None, project_dir: str) -> None:
    """Run check-recipes command: compare multi-layer recipes against live WFS."""
    from gis_utils.recipes import check_recipe_layers, list_recipes, load_recipe

    project_path = Path(project_dir).resolve()

    if recipe_name:
        recipes = [load_recipe(recipe_name, project_dir=project_path)]
    else:
        recipes = [r for r in list_recipes(project_path) if r.is_multi_layer]

    if not recipes:
        print("No multi-layer recipes found.")
        return

    for recipe in recipes:
        print(f"\n--- {recipe.name} ---")
        print(f"URL: {recipe.connection.get('url', '?')}")
        try:
            result = check_recipe_layers(recipe, project_dir=project_path)
        except Exception as e:
            print(f"  [ERROR] {e}")
            continue
        print(f"  OK: {len(result['ok'])} layers match")
        if result["missing"]:
            print(f"  MISSING from endpoint ({len(result['missing'])}):")
            for l in result["missing"]:
                print(f"    - {l}")
        if result["new"]:
            print(f"  NEW on endpoint ({len(result['new'])}):")
            for l in result["new"]:
                print(f"    + {l}")
        if not result["missing"] and not result["new"]:
            print("  All layers up to date.")


def main():
    parser = argparse.ArgumentParser(
        description="GIS project workflow runner.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # `gis-workflow run` (default when no subcommand)
    run_parser = subparsers.add_parser("run", help="Run workflow (default)")
    run_parser.add_argument(
        "project_dir", nargs="?", default=".",
        help="Project directory (default: current directory)",
    )
    run_parser.add_argument("--step", "-s", default=None, help="Run only this step (and its dependencies)")
    run_parser.add_argument("--dry-run", "-n", action="store_true", help="Show execution plan without running")
    run_parser.add_argument("--env", "-e", default=None, help="Conda environment (overrides workflow.yaml)")

    # `gis-workflow init`
    init_parser = subparsers.add_parser("init", help="Initialize a new project")
    init_parser.add_argument(
        "project_dir", nargs="?", default=".",
        help="Project directory (default: current directory)",
    )

    # `gis-workflow check-recipes`
    check_parser = subparsers.add_parser("check-recipes", help="Compare multi-layer recipes against live WFS")
    check_parser.add_argument(
        "recipe_name", nargs="?", default=None,
        help="Recipe name to check (default: all multi-layer recipes)",
    )
    check_parser.add_argument(
        "project_dir", nargs="?", default=".",
        help="Project directory (default: current directory)",
    )

    # `gis-workflow catalog`
    cat_parser = subparsers.add_parser("catalog", help="Print library catalog as JSON")
    cat_parser.add_argument("--search", "-q", default=None, help="Filter by keyword")
    cat_parser.add_argument(
        "project_dir", nargs="?", default=".",
        help="Project directory for local recipes (default: current directory)",
    )

    args = parser.parse_args()

    # Default to "run" if no subcommand given but args look like a path
    if args.command is None:
        # Re-parse as "run" with remaining args
        args = run_parser.parse_args()
        args.command = "run"

    if args.command == "init":
        init_project(args.project_dir)
        return

    if args.command == "check-recipes":
        _run_check_recipes(args.recipe_name, args.project_dir)
        return

    if args.command == "catalog":
        from gis_utils.catalog import catalog as _catalog
        result = _catalog(search=args.search, project_dir=args.project_dir)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    ok = run_workflow(
        args.project_dir,
        step_name=args.step,
        dry_run=args.dry_run,
        conda_env=args.env,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
