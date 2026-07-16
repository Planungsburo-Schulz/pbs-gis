"""
CAD emitter: styled one-way export of GeoPackage/vector layers to DXF.

Public API:

* :func:`export_layers` + :class:`LayerSpec` — the emit entry point.
* :func:`load_styles` / :class:`Style` — the strict ``cad_styles.yaml`` schema.
* :func:`resolve_color` — ACI/RGB colour resolution from the packaged table.

New input contract (Phase-4 R1): GeoPackage sources + a strict style map in,
DXF out; no ``project_settings`` coupling, no sync. This cut adds the annotate
layer on top of the emitter core: legend (:func:`add_legend`), block/text
insertion (:func:`insert_block`, :func:`add_text`), block arrays along a path
(:func:`insert_block_array`) and paperspace viewports (:func:`add_viewport`,
:func:`add_viewport_for_bbox`). Manifest emission stays out of scope (p4a-3).
"""

from pbs_gis.cad.annotate import AnnotateError, add_text, insert_block
from pbs_gis.cad.path_array import PathArrayResult, insert_block_array
from pbs_gis.cad.colors import ColorError, normalize_transparency, resolve_color
from pbs_gis.cad.emit import (
    CAD_APP_ID,
    ExportError,
    LayerResult,
    LayerSpec,
    export_layers,
)
from pbs_gis.cad.legend import (
    LegendEntry,
    LegendResult,
    LegendStyle,
    add_legend,
)
from pbs_gis.cad.styles import (
    SCHEMA_VERSION,
    EntityStyle,
    HatchStyle,
    LayerStyle,
    Style,
    StyleError,
    TextStyle,
    load_styles,
    parse_styles,
)
from pbs_gis.cad.viewport import (
    ViewportResult,
    add_viewport,
    add_viewport_for_bbox,
)

__all__ = [
    "export_layers",
    "LayerSpec",
    "LayerResult",
    "ExportError",
    "CAD_APP_ID",
    "load_styles",
    "parse_styles",
    "Style",
    "LayerStyle",
    "HatchStyle",
    "TextStyle",
    "EntityStyle",
    "StyleError",
    "SCHEMA_VERSION",
    "resolve_color",
    "normalize_transparency",
    "ColorError",
    # annotate
    "add_legend",
    "LegendEntry",
    "LegendStyle",
    "LegendResult",
    "insert_block",
    "insert_block_array",
    "PathArrayResult",
    "add_text",
    "AnnotateError",
    "add_viewport",
    "add_viewport_for_bbox",
    "ViewportResult",
]
