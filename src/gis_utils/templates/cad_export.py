"""cad_export — Styled one-way export of vector layers to a DXF (Phase-4 P4-A).

Thin workflow wrapper over :func:`gis_utils.cad.export_layers`: turns a list of
layer specs and a style map from ``params:`` into a styled DXF at the step's
``output:`` path.  Reads GeoPackage/vector sources, writes DXF one-way (never
rewrites the sources — gis-safety).

Example workflow.yaml::

    - name: Export Planzeichnung DXF
      template: cad_export
      params:
        crs: "EPSG:25833"
        styles: gestaltung/cad_styles.yaml   # path (rel. to project) or inline map
        layers:
          - source: Geodaten/geltungsbereich.gpkg
            target_layer: Geltungsbereich
            style: geltungsbereich
          - source: Geodaten/baufeld.gpkg
            target_layer: Baufeld
            style: baufeld
        template_dxf: gestaltung/Vorlage.dxf   # optional title-block base
      output: Zeichnung/planzeichnung.dxf
"""

from __future__ import annotations

from pathlib import Path

from gis_utils.templates import register


@register(
    "cad_export",
    description="Export GeoPackage/vector layers to a styled DXF (gis_utils.cad.export_layers)",
    params=["layers", "styles", "crs", "template_dxf", "dxfversion"],
)
def cad_export_template(params: dict, project_dir: Path, output_path: Path) -> bool:
    """Build :class:`LayerSpec` objects from ``params`` and emit a styled DXF.

    Params:
        crs: Required project CRS (e.g. ``"EPSG:25833"``). Every source layer's
            CRS must match — a mismatch is a hard error (gis-safety).
        layers: List of specs, each ``{source, target_layer, style}`` plus
            optional ``layer`` (name inside a multi-layer GPKG) and
            ``label_field``. ``source`` is resolved relative to the project root.
        styles: Path to a ``cad_styles.yaml`` (resolved rel. to project) OR an
            inline ``{name: {...}}`` style map.
        template_dxf (optional): Base DXF (title block/frame) to draw on top of.
        dxfversion (optional): DXF version for a fresh document. Default ``R2010``.
    """
    from gis_utils.cad import LayerSpec, export_layers, parse_styles

    if output_path is None:
        raise ValueError("cad_export: an 'output:' DXF path is required")

    crs = params["crs"]  # required — no default (gis-safety)

    layer_entries = params.get("layers")
    if not layer_entries:
        raise ValueError("cad_export: 'layers' must list at least one layer spec")

    specs: list[LayerSpec] = []
    for entry in layer_entries:
        specs.append(
            LayerSpec(
                source=(project_dir / entry["source"]).resolve(),
                target_layer=entry["target_layer"],
                style=entry["style"],
                layer=entry.get("layer"),
                label_field=entry.get("label_field"),
            )
        )

    styles = params["styles"]
    if isinstance(styles, str):
        styles = (project_dir / styles).resolve()  # path → export_layers loads it
    elif isinstance(styles, dict):
        styles = parse_styles(styles)  # inline map → {name: Style}

    template_dxf = params.get("template_dxf")
    if template_dxf:
        template_dxf = (project_dir / template_dxf).resolve()

    output_path.parent.mkdir(parents=True, exist_ok=True)

    results = export_layers(
        specs,
        styles=styles,
        out_dxf=output_path,
        crs=crs,
        template_dxf=template_dxf,
        dxfversion=params.get("dxfversion", "R2010"),
    )

    total_geoms = sum(r.geometries for r in results)
    print(f"[cad_export] {output_path}: {len(results)} layers, {total_geoms} geometries")
    for r in results:
        for w in r.warnings:
            print(f"  [warn] {r.target_layer}: {w}")
    return True
