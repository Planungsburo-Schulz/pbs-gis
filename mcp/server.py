"""MCP server for pbs_gis — exposes catalog, recipes, templates as tools."""

import functools
import json
import sys
from pathlib import Path

from fastmcp import FastMCP

# The server runs under an interpreter that carries no pbs_gis install; the
# library is then served from this repository's own src/.
_SRC = Path(__file__).resolve().parent.parent / "src"


def _import_pbs_gis() -> ImportError | None:
    try:
        import pbs_gis  # noqa: F401
    except ImportError:
        if str(_SRC) not in sys.path:
            sys.path.insert(0, str(_SRC))
        try:
            import pbs_gis  # noqa: F401
        except ImportError as exc:
            return exc
    return None


_IMPORT_ERROR = _import_pbs_gis()


def _diagnosis(exc: ImportError) -> str:
    return json.dumps({
        "error": "pbs_gis is not importable in the MCP server process",
        "interpreter": sys.executable,
        "src_tried": str(_SRC),
        "src_exists": _SRC.is_dir(),
        "original_error": f"{type(exc).__name__}: {exc}",
    }, indent=2, ensure_ascii=False)


def _needs_pbs_gis(fn):
    """Turn an unresolvable pbs_gis import into a named diagnosis response."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if _IMPORT_ERROR is not None:
            return _diagnosis(_IMPORT_ERROR)
        try:
            return fn(*args, **kwargs)
        except ImportError as exc:
            return _diagnosis(exc)

    return wrapper


mcp = FastMCP("pbs-gis", instructions="GIS/CAD utility library discovery. Use these tools to find the right pbs_gis functions, recipes, and templates for a task.")


@mcp.tool()
@_needs_pbs_gis
def catalog(search: str = "", project_dir: str = "") -> str:
    """Search the pbs_gis API catalog — functions, recipes, templates, and CLI commands.

    Args:
        search: Filter by keyword (e.g., "dxf", "buffer", "alkis"). Empty returns everything.
        project_dir: Include project-local recipes from this directory. Empty uses library defaults only.
    """
    from pbs_gis.catalog import catalog as _catalog

    proj = project_dir or None
    result = _catalog(search=search or None, project_dir=proj)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
@_needs_pbs_gis
def list_recipes(search: str = "", project_dir: str = "") -> str:
    """List available data source recipes (WFS, WMS, ALKIS, OSM, etc.).

    Args:
        search: Filter by name, description, or tags. Empty returns all.
        project_dir: Include project-local recipes from sources/ directory.
    """
    from pbs_gis.recipes import list_recipes as _list_recipes

    proj = Path(project_dir) if project_dir else None
    recipes = _list_recipes(project_dir=proj, search=search or None)
    out = []
    for r in recipes:
        entry = {
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
        out.append(entry)
    return json.dumps(out, indent=2, default=str)


@mcp.tool()
@_needs_pbs_gis
def list_templates() -> str:
    """List available workflow templates (reusable processing patterns for workflow.yaml)."""
    from pbs_gis.templates import list_templates as _list_templates

    templates = _list_templates()
    return json.dumps(templates, indent=2, default=str)


@mcp.tool()
@_needs_pbs_gis
def check_recipe_layers(recipe_name: str) -> str:
    """Compare a multi-layer recipe's hardcoded layers against live WFS GetCapabilities.

    Args:
        recipe_name: Name of the recipe to check (e.g., "sh_alkis", "mv_bodengeologie").
    """
    from pbs_gis.recipes import load_recipe, check_recipe_layers as _check

    recipe = load_recipe(recipe_name)
    result = _check(recipe)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
@_needs_pbs_gis
def get_function_help(function_name: str) -> str:
    """Get detailed help for a specific pbs_gis function — full docstring, signature, module.

    Args:
        function_name: Function name (e.g., "extract_dxf_layers", "lines_to_polygon", "download").
    """
    import importlib
    import inspect

    from pbs_gis.catalog import _MODULES

    for mod_path in _MODULES:
        try:
            mod = importlib.import_module(mod_path)
        except ImportError:
            continue
        obj = getattr(mod, function_name, None)
        if obj and callable(obj):
            sig = str(inspect.signature(obj))
            doc = inspect.getdoc(obj) or "(no docstring)"
            return json.dumps({
                "name": function_name,
                "module": mod_path,
                "import": f"from {mod_path} import {function_name}",
                "signature": f"{function_name}{sig}",
                "docstring": doc,
            }, indent=2)

    return json.dumps({"error": f"Function '{function_name}' not found in pbs_gis"})


if __name__ == "__main__":
    mcp.run()
