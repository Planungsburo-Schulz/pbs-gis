"""
CAD emitter: write GeoPackage layers to a DXF, styled by a strict style map.

New input contract (Phase-4 R1: neu geschrieben mit Referenz, no
``project_settings`` / ``all_layers`` coupling): the caller passes a list of
:class:`LayerSpec` (source vector + target DXF layer + style name), a style map
(or ``cad_styles.yaml`` path), a required project ``crs``, an output DXF path,
and an optional template DXF. One-way only — this module writes DXF, it never
reads a DXF back or syncs.

Low-level ezdxf operations (layer properties, hatch creation, transparency,
text styles, drawing header) are salvaged from Python-ACAD-Tools/src/dxf_utils.py
and decoupled from that project's ``ProjectLoader``: colour resolution goes
through :mod:`gis_utils.cad.colors` instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
import geopandas as gpd
from ezdxf import colors as ezcolors
from pyproj import CRS

from gis_utils.cad.colors import normalize_transparency, resolve_color
from gis_utils.cad.styles import (
    ATTACHMENT_CODES,
    EntityStyle,
    HatchStyle,
    LayerStyle,
    Style,
    TextStyle,
    load_styles,
)
from gis_utils.dxf.document import new_dxf_document

# Provenance marker on emitted entities (salvage-aligned with the reference's
# XDATA ownership tag), so a later step can recognise emitter-created entities.
CAD_APP_ID = "GIS_UTILS_CAD"

# ezdxf accepts only a fixed set of DXF lineweight enum values; anything else
# raises on assignment. We validate against ezdxf's own list and warn+skip.
try:  # pragma: no cover - import shape depends on ezdxf version
    from ezdxf.lldxf.const import VALID_DXF_LINEWEIGHTS as _VALID_LINEWEIGHTS
except Exception:  # pragma: no cover
    _VALID_LINEWEIGHTS = (
        0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90,
        100, 106, 120, 140, 158, 200, 211,
    )


class ExportError(ValueError):
    """Raised for an invalid export request (bad spec, CRS mismatch, …)."""


@dataclass
class LayerSpec:
    """
    One source-layer → DXF-layer mapping.

    Attributes:
        source: Path to a GeoPackage (canonical) or any geopandas-readable
            vector file. Inputs received as Shapefile are read as-is (never
            rewritten) per the gis-safety output convention.
        target_layer: DXF layer name to create/populate.
        style: Style name to look up in the style map.
        layer: Layer name inside a multi-layer GeoPackage. ``None`` reads the
            file's default (single) layer.
        label_field: Attribute column to render as a per-feature text label at
            the geometry centroid. Requires the style to carry a ``text`` block.
    """

    source: str | Path
    target_layer: str
    style: str
    layer: str | None = None
    label_field: str | None = None


@dataclass
class LayerResult:
    """Per-layer emit tally (for verification/reporting)."""

    target_layer: str
    features: int = 0
    geometries: int = 0
    hatches: int = 0
    labels: int = 0
    warnings: list[str] = field(default_factory=list)


def _warn(results: list[str], msg: str) -> None:
    print(f"  [cad] WARNING: {msg}")
    results.append(msg)


def _normalize_lineweight(value: int, warnings: list[str]) -> int | None:
    """Coerce a lineweight to the nearest valid ezdxf enum value.

    ezdxf only accepts a fixed set of DXF lineweight enum values (hundredths of
    a millimetre) plus the special sentinels ``-1`` (BYLAYER), ``-2`` (BYBLOCK)
    and ``-3`` (DEFAULT). An exact match is used verbatim; anything else is
    snapped to the closest valid positive value and a warning is recorded
    (documented behaviour — never a silent drop).
    """
    if value in _VALID_LINEWEIGHTS or value in (-1, -2, -3):
        return value
    nearest = min(_VALID_LINEWEIGHTS, key=lambda v: abs(v - value))
    _warn(warnings, f"lineweight {value} not a valid DXF value; snapped to {nearest}")
    return nearest


def _set_layer_properties(layer, ls: LayerStyle, warnings: list[str]) -> None:
    """Apply a :class:`LayerStyle` to an ezdxf layer table entry.

    Salvaged from ``dxf_utils.update_layer_properties``, decoupled from
    ``name_to_aci`` (colour resolution now lives in :mod:`colors`).
    """
    if ls.color is not None:
        color = resolve_color(ls.color)
        if isinstance(color, tuple):
            layer.rgb = color
        else:
            layer.color = color
    if ls.linetype is not None:
        if ls.linetype in layer.doc.linetypes:
            layer.dxf.linetype = ls.linetype
        else:
            _warn(warnings, f"linetype {ls.linetype!r} not in document; keeping CONTINUOUS")
    if ls.lineweight is not None:
        lw = _normalize_lineweight(ls.lineweight, warnings)
        if lw is not None:
            layer.dxf.lineweight = lw
    if ls.transparency is not None:
        t = normalize_transparency(ls.transparency)
        if t is not None:
            layer.transparency = t
    if ls.plot is not None:
        layer.dxf.plot = int(ls.plot)
    if ls.locked is not None:
        layer.lock() if ls.locked else layer.unlock()
    if ls.frozen is not None:
        layer.freeze() if ls.frozen else layer.thaw()
    if ls.is_on is not None:
        layer.on = bool(ls.is_on)


def _ensure_text_style(doc, ts: TextStyle) -> str:
    """Ensure the text style (font) exists; return the style name to reference.

    Salvaged from ``dxf_utils.ensure_text_style_exists``. ``font`` names an
    ezdxf text style; if absent it is created with the given height.
    """
    name = ts.font
    if name not in doc.styles:
        try:
            style = doc.styles.new(name)
            style.dxf.font = name
            style.dxf.height = 0.0
            style.dxf.width = 1.0
        except Exception:
            return "Standard"
    return name


def _attach_provenance(entity, doc) -> None:
    """Tag an emitted entity with the emitter's app id (light XDATA)."""
    try:
        entity.set_xdata(CAD_APP_ID, [(1000, CAD_APP_ID)])
    except Exception:
        pass


def _has_provenance(entity) -> bool:
    """True if *entity* carries this emitter's provenance XDATA."""
    try:
        return entity.has_xdata(CAD_APP_ID)
    except Exception:
        return False


def _purge_emitted(doc) -> int:
    """Delete every entity previously written by this emitter (idempotent re-export).

    Identifies emitter-owned entities by their :data:`CAD_APP_ID` XDATA
    provenance and removes them from modelspace, so a re-export rewrites our
    own layers cleanly while leaving foreign entities (hand-drawn in AutoCAD,
    template content) untouched — even where they share a layer with ours.
    Returns the number of entities removed.
    """
    msp = doc.modelspace()
    doomed = [e for e in msp if _has_provenance(e)]
    for entity in doomed:
        msp.delete_entity(entity)
    return len(doomed)


def _apply_entity_style(entity, es: EntityStyle | None) -> None:
    """Apply per-entity style bits (linetype scale/generation)."""
    if es is None:
        return
    if es.linetype_scale is not None:
        entity.dxf.ltscale = float(es.linetype_scale)
    if es.linetype_generation is not None and entity.dxftype() in ("LWPOLYLINE", "POLYLINE"):
        from ezdxf.lldxf.const import LWPOLYLINE_PLINEGEN

        if es.linetype_generation:
            entity.dxf.flags |= LWPOLYLINE_PLINEGEN
        else:
            entity.dxf.flags &= ~LWPOLYLINE_PLINEGEN


def _hatch_layer_name(geom_layer: str, hs: HatchStyle) -> str:
    """Target layer for a hatch: the geometry layer, or a suffixed sibling."""
    if hs.layer_suffix:
        return f"{geom_layer} {hs.layer_suffix}"
    return geom_layer


def _add_hatch(msp, rings: list[list[tuple[float, float]]], hs: HatchStyle,
               geom_layer: str, doc) -> object | None:
    """Create a hatch over closed boundary *rings*.

    Salvaged from ``dxf_utils.create_hatch`` (boundaries first, then pattern),
    decoupled from ``project_loader``. When the style carries a
    ``layer_suffix`` the hatch lands on a separate ``"<geom_layer> <suffix>"``
    layer (created on demand), leaving the geometry layer for outlines only.
    """
    if not rings:
        return None
    layer_name = _hatch_layer_name(geom_layer, hs)
    if layer_name not in doc.layers:
        doc.layers.add(layer_name)
    hatch = msp.add_hatch(dxfattribs={"layer": layer_name})
    hatch.dxf.elevation = (0, 0, 0)
    for ring in rings:
        if len(ring) >= 3:
            hatch.paths.add_polyline_path(ring, is_closed=True)

    pattern = (hs.pattern or "SOLID").upper()
    if pattern != "SOLID":
        try:
            hatch.set_pattern_fill(pattern, scale=hs.scale, angle=hs.angle)
        except ezdxf.DXFValueError:
            hatch.set_solid_fill()
    else:
        hatch.set_solid_fill()

    if hs.color is not None:
        color = resolve_color(hs.color)
        if isinstance(color, tuple):
            hatch.rgb = color
        else:
            hatch.dxf.color = color
    else:
        hatch.dxf.color = ezdxf.const.BYLAYER

    t = normalize_transparency(hs.transparency)
    if t is not None:
        hatch.dxf.transparency = ezcolors.float2transparency(t)
    return hatch


def _polygon_rings(geom) -> list[list[tuple[float, float]]]:
    """Exterior + interior rings of a (Multi)Polygon as coordinate lists."""
    rings: list[list[tuple[float, float]]] = []
    if geom.geom_type == "Polygon":
        rings.append([(x, y) for x, y, *_ in geom.exterior.coords])
        for interior in geom.interiors:
            rings.append([(x, y) for x, y, *_ in interior.coords])
    elif geom.geom_type == "MultiPolygon":
        for part in geom.geoms:
            rings.extend(_polygon_rings(part))
    return rings


def _write_geometry(msp, geom, layer_name: str, style: Style, doc,
                    result: LayerResult) -> None:
    """Write one Shapely geometry as DXF entities on *layer_name*, styled."""
    if geom is None or geom.is_empty:
        return
    gtype = geom.geom_type
    close = bool(style.entity.close) if (style.entity and style.entity.close) else False

    if gtype == "Point":
        e = msp.add_point((geom.x, geom.y), dxfattribs={"layer": layer_name})
        _attach_provenance(e, doc)
        result.geometries += 1
    elif gtype in ("LineString", "LinearRing"):
        pts = [(x, y) for x, y, *_ in geom.coords]
        if len(pts) >= 2:
            e = msp.add_lwpolyline(pts, format="xy", close=close,
                                   dxfattribs={"layer": layer_name})
            _apply_entity_style(e, style.entity)
            _attach_provenance(e, doc)
            result.geometries += 1
    elif gtype == "Polygon":
        rings = _polygon_rings(geom)
        for ring in rings:
            if len(ring) >= 2:
                e = msp.add_lwpolyline(ring, format="xy", close=True,
                                       dxfattribs={"layer": layer_name})
                _apply_entity_style(e, style.entity)
                _attach_provenance(e, doc)
                result.geometries += 1
        if style.hatch is not None:
            h = _add_hatch(msp, rings, style.hatch, layer_name, doc)
            if h is not None:
                _attach_provenance(h, doc)
                result.hatches += 1
    elif hasattr(geom, "geoms"):
        for part in geom.geoms:
            _write_geometry(msp, part, layer_name, style, doc, result)


def _write_label(msp, geom, text: str, layer_name: str, ts: TextStyle,
                 style_name: str, doc, result: LayerResult) -> None:
    """Write a single MTEXT label at the geometry centroid."""
    if not text:
        return
    try:
        centroid = geom.centroid
    except Exception:
        return
    attribs = {
        "layer": layer_name,
        "style": style_name,
        "char_height": ts.height,
        "insert": (centroid.x, centroid.y),
    }
    if ts.max_width:
        attribs["width"] = ts.max_width
    mtext = msp.add_mtext(str(text), dxfattribs=attribs)
    if ts.color is not None:
        color = resolve_color(ts.color)
        if isinstance(color, tuple):
            mtext.rgb = color
        else:
            mtext.dxf.color = color
    if ts.attachment is not None:
        mtext.dxf.attachment_point = ATTACHMENT_CODES[ts.attachment]
    if ts.rotation is not None:
        mtext.dxf.rotation = float(ts.rotation)
    _attach_provenance(mtext, doc)
    result.labels += 1


def export_layers(
    specs: list[LayerSpec],
    *,
    styles: dict[str, Style] | str | Path,
    out_dxf: str | Path,
    crs: str,
    template_dxf: str | Path | None = None,
    dxfversion: str = "R2010",
) -> list[LayerResult]:
    """
    Export GeoPackage/vector layers to a styled DXF.

    Args:
        specs: Source→target layer mappings.
        styles: A ``{name: Style}`` map, or a path to a ``cad_styles.yaml`` file.
        out_dxf: Output DXF path (parents created).
        crs: Required project CRS (e.g. ``"EPSG:25833"``). Every source layer's
            CRS must match — a mismatch is a hard error, never a silent
            reproject (gis-safety). DXF itself is CRS-less; coordinates are
            written verbatim in this CRS.
        template_dxf: Optional template DXF opened as the base; its content is
            kept and our layers are added on top (title block, frame, linetypes).
            ``None`` → if *out_dxf* already exists it is reopened and only the
            previously emitted (provenance-tagged) entities are purged before
            rewriting (idempotent re-export, foreign content preserved);
            otherwise a fresh document via :func:`new_dxf_document`.
        dxfversion: DXF version for a fresh document (ignored when a template is
            given).

    Returns:
        One :class:`LayerResult` per spec, in input order.

    Raises:
        ExportError: on an unknown style, missing source, or CRS mismatch.
        FileNotFoundError: on a missing template or style file.
    """
    if not crs:
        raise ExportError("crs is required (no default — see gis-safety CRS rule)")

    style_map = load_styles(styles) if isinstance(styles, (str, Path)) else styles

    out_dxf = Path(out_dxf)

    if template_dxf is not None:
        template_dxf = Path(template_dxf)
        if not template_dxf.exists():
            raise FileNotFoundError(f"Template DXF not found: {template_dxf}")
        doc = ezdxf.readfile(str(template_dxf))
    elif out_dxf.exists():
        # Idempotent re-export: reopen the existing target, strip only what we
        # wrote last time (by provenance), and rewrite — foreign layers/entities
        # (hand-drawn in AutoCAD) survive across re-exports.
        doc = ezdxf.readfile(str(out_dxf))
        _purge_emitted(doc)
    else:
        doc = new_dxf_document(dxfversion)

    try:
        doc.appids.new(CAD_APP_ID)
    except Exception:
        pass

    msp = doc.modelspace()
    results: list[LayerResult] = []

    for spec in specs:
        result = LayerResult(target_layer=spec.target_layer)
        results.append(result)

        style = style_map.get(spec.style)
        if style is None:
            raise ExportError(
                f"Layer {spec.target_layer!r}: unknown style {spec.style!r}. "
                f"Known: {sorted(style_map)}"
            )

        source = Path(spec.source)
        if not source.exists():
            raise ExportError(f"Layer {spec.target_layer!r}: source not found: {source}")

        read_kwargs = {"layer": spec.layer} if spec.layer else {}
        gdf = gpd.read_file(source, **read_kwargs)

        if gdf.crs is None:
            raise ExportError(
                f"Layer {spec.target_layer!r}: source {source} has no CRS; "
                f"cannot verify against {crs}"
            )
        if gdf.crs != CRS.from_user_input(crs):
            raise ExportError(
                f"Layer {spec.target_layer!r}: source CRS {gdf.crs.to_string()!r} "
                f"!= required {crs!r}. Reconcile explicitly; no silent reproject."
            )

        result.features = len(gdf)

        # Create / fetch the target layer and apply its style.
        if spec.target_layer in doc.layers:
            layer = doc.layers.get(spec.target_layer)
        else:
            layer = doc.layers.add(spec.target_layer)
        if style.layer is not None:
            _set_layer_properties(layer, style.layer, result.warnings)

        text_style_name = None
        if spec.label_field is not None and style.text is not None:
            text_style_name = _ensure_text_style(doc, style.text)

        for _, row in gdf.iterrows():
            geom = row.geometry
            _write_geometry(msp, geom, spec.target_layer, style, doc, result)
            if text_style_name is not None:
                value = row.get(spec.label_field)
                if value is not None and str(value).strip():
                    _write_label(msp, geom, str(value), spec.target_layer,
                                 style.text, text_style_name, doc, result)

    out_dxf.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(out_dxf))
    return results
