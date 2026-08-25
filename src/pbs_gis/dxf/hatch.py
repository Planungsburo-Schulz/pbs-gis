"""
Read HATCH entities as areas — the form a planner draws surfaces in.

A design drawing carries its surfaces as hatches, one layer per material, and the
layer name is what says which material it is. Their areas are what a quantity
take-off is checked against, so the geometry has to be the whole hatch: every
boundary path, arcs and splines flattened, and inner paths subtracted as holes.

Taking only the first boundary path — the cheaper reading — returns a plausible
polygon for every hatch and a wrong area for each one that has a hole, which is
invisible in the result. Hence this reader rather than the coordinate-level
extraction in :mod:`pbs_gis.dxf.extract`.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from shapely.geometry import Polygon
from shapely.ops import unary_union

from pbs_gis.dxf.read import CadReadError, read_cad

# Chord error when flattening arcs and splines. 2 cm is far below the drawing
# accuracy of a design plan and keeps a 100 m arc within a square centimetre.
DEFAULT_FLATTENING = 0.02

# UTM zone prefixes CAD drawings carry in X (33 for EPSG:25833, 32 for 25832).
_ZONE_SHIFT = {"25833": 33_000_000, "25832": 32_000_000}


def extract_hatch_areas(
    dxf_path: str | Path,
    *,
    crs: str,
    layers: list[str] | None = None,
    strip_zone: bool = False,
    flattening: float = DEFAULT_FLATTENING,
    dissolve: bool = False,
) -> gpd.GeoDataFrame:
    """Read HATCH entities as polygons, one row per hatch.

    Args:
        dxf_path: Source ``.dxf``/``.dwg``.
        crs: CRS of the drawing's coordinates, e.g. ``"EPSG:25833"``.
        layers: Layer names to read. ``None`` reads every layer holding hatches.
        strip_zone: Subtract the UTM zone prefix from X, as CAD drawings carry it.
        flattening: Chord error in metres when flattening arcs and splines.
        dissolve: Return one row per layer (hatches unioned) instead of one per
            hatch. Areas that touch are merged, so the layer total no longer
            double-counts an overlap.

    Returns:
        GeoDataFrame with columns ``layer``, ``area_m2``, ``geometry``.

    Raises:
        CadReadError: A requested layer holds no hatch — a silently empty result
            is indistinguishable from a misspelt layer name.
    """
    from ezdxf import path as ezpath

    doc = read_cad(dxf_path)

    shift = 0
    if strip_zone:
        code = crs.split(":")[-1]
        if code not in _ZONE_SHIFT:
            raise CadReadError(
                f"strip_zone: no zone prefix known for {crs} "
                f"(known: {', '.join('EPSG:' + c for c in _ZONE_SHIFT)})"
            )
        shift = _ZONE_SHIFT[code]

    wanted = set(layers) if layers else None
    rows: list[tuple[str, Polygon]] = []
    seen_layers: set[str] = set()

    for entity in doc.modelspace():
        if entity.dxftype() != "HATCH":
            continue
        if wanted is not None and entity.dxf.layer not in wanted:
            continue

        geom = _hatch_to_polygon(entity, ezpath, shift, flattening)
        if geom is None:
            continue
        seen_layers.add(entity.dxf.layer)
        rows.append((entity.dxf.layer, geom))

    if wanted is not None:
        empty = sorted(wanted - seen_layers)
        if empty:
            raise CadReadError(
                "No hatch found on layer(s): " + ", ".join(repr(e) for e in empty)
            )

    gdf = gpd.GeoDataFrame(
        {"layer": [r[0] for r in rows]},
        geometry=[r[1] for r in rows],
        crs=crs,
    )
    if dissolve and not gdf.empty:
        gdf = gdf.dissolve(by="layer", as_index=False)
    gdf["area_m2"] = gdf.area
    return gdf[["layer", "area_m2", "geometry"]]


def _hatch_to_polygon(entity, ezpath, shift: int, flattening: float) -> Polygon | None:
    """Build one polygon from all boundary paths of *entity*, holes subtracted.

    Which path is a hole is decided by containment rather than by the entity's
    own path-type flags: CAD writes those inconsistently, while containment is a
    property of the geometry that arrived.
    """
    rings = []
    for boundary in ezpath.from_hatch(entity):
        points = [(v.x - shift, v.y) for v in boundary.flattening(flattening)]
        if len(points) < 4:
            continue
        ring = Polygon(points)
        if not ring.is_valid:
            ring = ring.buffer(0)
        if ring.is_empty or ring.area <= 0:
            continue
        rings.append(ring)

    if not rings:
        return None

    rings.sort(key=lambda g: g.area, reverse=True)
    outer, holes = rings[0], []
    for ring in rings[1:]:
        if ring.within(outer):
            holes.append(ring)
        else:
            outer = unary_union([outer, ring])

    return outer.difference(unary_union(holes)) if holes else outer
