"""Auto-generated catalog of gis_utils public API, recipes, and CLI commands."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Any


# Modules to introspect — top-level first, then submodules.
# Only list modules with public functions an AI/user would call directly.
_MODULES = [
    "gis_utils",           # top-level re-exports
    "gis_utils.osm",       # OSM downloads
    "gis_utils.wfs",       # WFS downloads
    "gis_utils.wms",       # WMS vectorization
    "gis_utils.recipes",   # recipe management
    "gis_utils.geometry",  # geometry utilities
    "gis_utils.reporting", # area reports, markdown tables
    "gis_utils.alkis",     # Flurstück lookup
    "gis_utils.georef",    # feature-match georeferencing (DXF/local -> reference CRS)
    "gis_utils.dxf.extract",   # DXF extraction
    "gis_utils.dxf.convert",   # SHP→DXF conversion
    "gis_utils.dxf.document",  # DXF document creation
    "gis_utils.dxf.map_od",    # AutoCAD Map Object Data
    "gis_utils.runner",    # workflow runner
    "gis_utils.templates", # built-in workflow templates
]

_CLI_COMMANDS = [
    {"command": "gis-workflow run", "description": "Run workflow steps (default subcommand)"},
    {"command": "gis-workflow run --step NAME", "description": "Run a single step and its dependencies"},
    {"command": "gis-workflow run --dry-run", "description": "Show execution plan without running"},
    {"command": "gis-workflow init", "description": "Initialize a new project with workflow.yaml"},
    {"command": "gis-workflow check-recipes", "description": "Compare recipe layers against live WFS GetCapabilities"},
    {"command": "gis-workflow catalog", "description": "Print library catalog as JSON"},
]


def _first_line(text: str | None) -> str:
    """Return first non-empty line of text, or empty string."""
    if not text:
        return ""
    for line in text.strip().splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _sig_str(func) -> str:
    """Return signature string, or fallback."""
    try:
        return str(inspect.signature(func))
    except (ValueError, TypeError):
        return "(*args, **kwargs)"


def _discover_functions(*, include_private: bool = False) -> dict[str, list[dict[str, Any]]]:
    """Discover public functions from all known modules."""
    # First, figure out which names are top-level exports
    try:
        top_mod = importlib.import_module("gis_utils")
        top_all = set(getattr(top_mod, "__all__", []))
    except ImportError:
        top_all = set()

    result: dict[str, list[dict[str, Any]]] = {}

    for mod_path in _MODULES:
        try:
            mod = importlib.import_module(mod_path)
        except Exception as exc:
            result[mod_path] = [{"name": "(import error)", "description": str(exc)}]
            continue

        funcs = []
        for name, obj in inspect.getmembers(mod, inspect.isfunction):
            if name.startswith("_") and not include_private:
                continue
            # For gis_utils top-level, only list what's in __all__
            if mod_path == "gis_utils" and name not in top_all:
                continue
            # Skip functions that belong to a different module (re-exports)
            # For top-level, we DO want them (that's the point)
            if mod_path != "gis_utils":
                fn_mod = getattr(obj, "__module__", "")
                if fn_mod and fn_mod != mod_path and not fn_mod.startswith(mod_path + "."):
                    continue

            funcs.append({
                "name": name,
                "import": f"from {mod_path} import {name}",
                "signature": _sig_str(obj),
                "description": _first_line(inspect.getdoc(obj)),
                "top_level": name in top_all,
            })

        # Also include classes in __all__ (like Recipe)
        for name, obj in inspect.getmembers(mod, inspect.isclass):
            if mod_path == "gis_utils" and name in top_all:
                funcs.append({
                    "name": name,
                    "import": f"from {mod_path} import {name}",
                    "signature": "(class)",
                    "description": _first_line(inspect.getdoc(obj)),
                    "top_level": True,
                })
            elif mod_path != "gis_utils" and not name.startswith("_"):
                obj_mod = getattr(obj, "__module__", "")
                if obj_mod == mod_path:
                    funcs.append({
                        "name": name,
                        "import": f"from {mod_path} import {name}",
                        "signature": "(class)",
                        "description": _first_line(inspect.getdoc(obj)),
                        "top_level": name in top_all,
                    })

        if funcs:
            # Sort: top-level first, then alphabetical
            funcs.sort(key=lambda f: (not f["top_level"], f["name"]))
            result[mod_path] = funcs

    return result


def _discover_recipes(project_dir: Path | None = None) -> list[dict[str, Any]]:
    """List all available recipes with metadata."""
    try:
        from gis_utils.recipes import list_recipes as _list
    except ImportError:
        return []

    recipes = _list(project_dir=project_dir)
    out = []
    for r in recipes:
        entry: dict[str, Any] = {
            "name": r.name,
            "description": r.description,
            "tags": r.tags,
            "multi_layer": r.is_multi_layer,
        }
        if r.is_multi_layer:
            entry["layers"] = r.layer_aliases()
        conn = r.connection
        if conn:
            entry["connection_type"] = conn.get("type", "")
            entry["url"] = conn.get("url", "")
        # Determine source: library vs project
        src_path = getattr(r, "_source_path", None)
        if src_path and project_dir and str(src_path).startswith(str(project_dir)):
            entry["source"] = "project"
        else:
            entry["source"] = "library"
        out.append(entry)
    return out


def _matches(entry: dict, term: str) -> bool:
    """Check if any text field in entry matches the search term."""
    term = term.lower()
    for key in ("name", "description", "import", "command"):
        val = entry.get(key, "")
        if isinstance(val, str) and term in val.lower():
            return True
    tags = entry.get("tags", [])
    if isinstance(tags, list):
        for t in tags:
            if term in str(t).lower():
                return True
    layers = entry.get("layers", [])
    if isinstance(layers, list):
        for l in layers:
            if term in str(l).lower():
                return True
    return False


def catalog(
    *,
    search: str | None = None,
    project_dir: str | Path | None = None,
    include_private: bool = False,
) -> dict[str, Any]:
    """Return structured catalog of all gis_utils public functions, recipes, and CLI commands.

    Args:
        search: Case-insensitive substring filter across names, modules, descriptions, tags.
        project_dir: Include project-local recipes from this directory's sources/ folder.
        include_private: If True, include _prefixed functions too (default False).

    Returns:
        Dict with keys: version, functions, recipes, cli.
    """
    proj = Path(project_dir) if project_dir else None

    # Version
    try:
        from importlib.metadata import version as pkg_version
        ver = pkg_version("gis-utils")
    except Exception:
        ver = "unknown"

    # Discover
    functions = _discover_functions(include_private=include_private)
    recipes = _discover_recipes(project_dir=proj)
    templates = _discover_templates()
    cli = list(_CLI_COMMANDS)

    # Filter
    if search:
        filtered_funcs = {}
        for mod, entries in functions.items():
            matched = [e for e in entries if _matches(e, search)]
            if matched:
                filtered_funcs[mod] = matched
        functions = filtered_funcs
        recipes = [r for r in recipes if _matches(r, search)]
        templates = [t for t in templates if _matches(t, search)]
        cli = [c for c in cli if _matches(c, search)]

    return {
        "version": ver,
        "functions": functions,
        "recipes": recipes,
        "templates": templates,
        "cli": cli,
    }


def _discover_templates() -> list[dict[str, Any]]:
    """List all registered workflow templates."""
    try:
        from gis_utils.templates import list_templates
        return list_templates()
    except ImportError:
        return []
