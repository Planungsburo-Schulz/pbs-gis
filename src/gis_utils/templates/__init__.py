"""Built-in workflow templates for common GIS processing patterns.

Templates are Python functions that compose gis_utils library functions into
reusable processing steps.  They are invoked by the workflow runner via
``template:`` steps in workflow.yaml.

Each template lives in its own file under this package.  To add a new
template, create a new ``.py`` file, import ``register`` from here, and
decorate your handler function.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_TEMPLATES: dict[str, Callable] = {}


def register(name: str, *, description: str = "", params: list[str] | None = None):
    """Decorator to register a template handler.

    Args:
        name: Template name used in workflow.yaml ``template:`` field.
        description: One-line description shown in catalog output.
        params: List of parameter names accepted via ``params:`` in workflow.yaml.
    """

    def decorator(fn: Callable) -> Callable:
        fn._template_name = name
        fn._template_description = description
        fn._template_params = params or []
        _TEMPLATES[name] = fn
        return fn

    return decorator


def get_template(name: str) -> Callable:
    """Look up a template by name.

    Raises:
        KeyError: If *name* is not a registered template.
    """
    if name not in _TEMPLATES:
        raise KeyError(
            f"Unknown template '{name}'. "
            f"Available: {', '.join(sorted(_TEMPLATES))}"
        )
    return _TEMPLATES[name]


def list_templates() -> list[dict[str, Any]]:
    """Return metadata about all registered templates."""
    result = []
    for name, fn in sorted(_TEMPLATES.items()):
        result.append(
            {
                "name": name,
                "description": getattr(fn, "_template_description", ""),
                "params": getattr(fn, "_template_params", []),
            }
        )
    return result


# ---------------------------------------------------------------------------
# Auto-import all template modules so their @register decorators run.
# ---------------------------------------------------------------------------

from gis_utils.templates import (  # noqa: E402, F401
    apply_qml_style,
    buffer_zones,
    cad_export,
    clip_to_flurstuecke,
    concentric_point_buffers,
    distance_lines_to_nearest,
    dxf_extract_layers,
    dxf_lines_to_polygon,
    dxf_verification,
    fetch_flurstuecke,
    layout_from_qpt,
    lines_to_polygon,
    point_buffer_union,
    polygon_difference,
    publish_bilanz,
)
