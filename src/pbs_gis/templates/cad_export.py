"""cad_export — Styled one-way export of vector layers to a DXF (Phase-4 P4-A).

Thin workflow wrapper over :func:`pbs_gis.cad.export_layers`: turns a list of
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
        texts:                                 # optional free texts (add_text)
          - text: "Plangebiet"
            position: [394700, 5934690]
            style: beschriftung                # a style carrying a `text` block
            layer: Beschriftung
        legend:                                # optional legend (add_legend)
          position: [394900, 5934800]
          entries: auto                        # or [Geltungsbereich, Baufeld]
      output: Zeichnung/planzeichnung.dxf
"""

from __future__ import annotations

from pathlib import Path

from pbs_gis.templates import register

_TEXT_KEYS = frozenset({"text", "position", "style", "layer"})
_LEGEND_KEYS = frozenset({"position", "entries"})


def _position(value, where: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"cad_export: {where}: 'position' must be [x, y], got {value!r}")
    return float(value[0]), float(value[1])


def _reject_unknown(entry: dict, allowed: frozenset, where: str) -> None:
    unknown = sorted(set(entry) - allowed)
    if unknown:
        raise ValueError(
            f"cad_export: {where}: unknown key(s) {unknown}; allowed: {sorted(allowed)}")


def _plan_annotations(texts, legend, specs, style_map):
    """Resolve ``texts`` and ``legend`` into concrete calls, or raise.

    Runs before the DXF is written: a bad entry must fail the step without
    leaving an unannotated DXF behind, which an ``auto`` re-run would take
    for up to date.

    Returns:
        ``(text_calls, legend_call)`` — a list of ``(text, position, kwargs)``
        for :func:`add_text` and ``(entries, position)`` for
        :func:`add_legend`, or ``None`` without a legend.
    """
    from pbs_gis.cad import LegendEntry

    text_calls = []
    for i, entry in enumerate(texts or []):
        where = f"texts[{i}]"
        _reject_unknown(entry, _TEXT_KEYS, where)
        kwargs = {}
        if entry.get("style") is not None:
            style = style_map.get(entry["style"])
            if style is None:
                raise ValueError(
                    f"cad_export: {where}: unknown style {entry['style']!r}. "
                    f"Known: {sorted(style_map)}")
            if style.text is None:
                raise ValueError(
                    f"cad_export: {where}: style {entry['style']!r} has no 'text' block")
            kwargs["style"] = style.text
        if entry.get("layer") is not None:
            kwargs["layer"] = entry["layer"]
        text_calls.append((entry["text"], _position(entry.get("position"), where), kwargs))

    if legend is None:
        return text_calls, None
    _reject_unknown(legend, _LEGEND_KEYS, "legend")
    if "entries" not in legend:
        raise ValueError("cad_export: legend: 'entries' is required (auto or a layer list)")
    by_layer = {s.target_layer: s for s in specs}
    if legend["entries"] == "auto":
        chosen = list(specs)
    else:
        unknown = [n for n in legend["entries"] if n not in by_layer]
        if unknown:
            raise ValueError(
                f"cad_export: legend: entries {unknown} are not exported layers "
                f"({list(by_layer)})")
        chosen = [by_layer[n] for n in legend["entries"]]
    entries = []
    for s in chosen:
        style = style_map.get(s.style)
        if style is None:
            raise ValueError(
                f"cad_export: legend: layer {s.target_layer!r} has unknown style "
                f"{s.style!r}. Known: {sorted(style_map)}")
        entries.append(LegendEntry(label=s.target_layer, style=style))
    return text_calls, (entries, _position(legend.get("position"), "legend"))


@register(
    "cad_export",
    description="Export GeoPackage/vector layers to a styled DXF (pbs_gis.cad.export_layers)",
    params=["layers", "styles", "crs", "template_dxf", "dxfversion", "texts", "legend"],
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
        texts (optional): List of ``{text, position: [x, y], style?, layer?}``,
            each drawn via :func:`pbs_gis.cad.add_text`. ``style`` names a style
            in ``styles`` whose ``text`` block formats the MTEXT; ``layer``
            defaults to ``"0"``.
        legend (optional): ``{position: [x, y], entries: auto | [layer names]}``,
            drawn via :func:`pbs_gis.cad.add_legend` — one row per exported
            layer (``auto``: all, in ``layers`` order), labelled with its
            ``target_layer`` and swatched with its style.

    Texts and legend are written into the DXF before the step returns, so a
    ``publiziert`` manifest hashes the final file. Without either parameter
    the DXF is exactly what :func:`pbs_gis.cad.export_layers` writes.
    """
    from pbs_gis.cad import LayerSpec, export_layers, load_styles, parse_styles

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

    texts, legend = params.get("texts"), params.get("legend")
    annotate = bool(texts) or legend is not None
    if annotate:
        style_map = load_styles(styles) if isinstance(styles, Path) else styles
        text_calls, legend_call = _plan_annotations(texts, legend, specs, style_map)

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

    if annotate:
        import ezdxf

        from pbs_gis.cad import add_legend, add_text

        doc = ezdxf.readfile(str(output_path))
        for text, position, kwargs in text_calls:
            add_text(doc, text, position, **kwargs)
        if legend_call is not None:
            add_legend(doc, legend_call[0], position=legend_call[1])
        doc.saveas(str(output_path))

    total_geoms = sum(r.geometries for r in results)
    print(f"[cad_export] {output_path}: {len(results)} layers, {total_geoms} geometries")
    for r in results:
        for w in r.warnings:
            print(f"  [warn] {r.target_layer}: {w}")
    return True
