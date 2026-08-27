"""
Geometry utilities for GIS workflows.

Provides common operations on Shapely geometries and GeoPandas GeoDataFrames:
polygon hole removal, geometry repair, set operations, and loading helpers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import math

import numpy as np
import geopandas as gpd
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import polygonize, unary_union
from shapely.validation import make_valid as _shapely_make_valid

# UTM zone prefixes: zone 32 → 32xxxxxx, zone 33 → 33xxxxxx
_KNOWN_ZONE_PREFIXES = {32, 33}


def remove_inner_rings(geom) -> Any:
    """
    Remove all inner rings (holes) from a geometry.

    Works on Polygon, MultiPolygon, and GeometryCollection. Other geometry
    types and None/empty values pass through unchanged.

    Args:
        geom: A Shapely geometry.

    Returns:
        Geometry with holes removed.
    """
    if geom is None or geom.is_empty:
        return geom
    if geom.geom_type == "Polygon":
        return Polygon(geom.exterior) if geom.interiors else geom
    if geom.geom_type == "MultiPolygon":
        return MultiPolygon([remove_inner_rings(p) for p in geom.geoms])
    if geom.geom_type == "GeometryCollection":
        from shapely.geometry import GeometryCollection
        return GeometryCollection([remove_inner_rings(g) for g in geom.geoms])
    return geom


def repair_geometry(geom, *, context: str = "") -> Any:
    """Validate and repair a single geometry, extracting polygons from compound results.

    Handles all known edge cases from DXF conversion:
    - Self-intersecting polygons (from opposing arc directions in hatches)
    - make_valid() returning GeometryCollection (Polygon + LineString artifacts)
    - make_valid() returning MultiPolygon from self-intersections
    - Empty geometries

    Prints a warning when repair is needed so issues are caught early.

    Args:
        geom: A Shapely geometry.
        context: Optional string for the warning message (e.g. layer/entity info).

    Returns:
        Repaired geometry (Polygon preferred), or None if unrecoverable.
    """
    if geom is None or geom.is_empty:
        return None

    if geom.is_valid:
        return geom

    ctx = f" ({context})" if context else ""
    print(f"[geometry] Warning: invalid geometry{ctx}, repairing...", flush=True)

    repaired = _shapely_make_valid(geom)
    if repaired.is_empty:
        print(f"[geometry] Warning: repair produced empty geometry{ctx}", flush=True)
        return None

    # Extract polygon(s) from compound results
    if repaired.geom_type in ("MultiPolygon", "GeometryCollection"):
        polys = [g for g in repaired.geoms if g.geom_type == "Polygon" and g.area > 0]
        if not polys:
            return repaired  # no polygons, return as-is (might be lines/points)
        if len(polys) == 1:
            return polys[0]
        # Multiple polygons: return as MultiPolygon
        return MultiPolygon(polys)

    return repaired


def make_valid_gdf(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Attempt to fix all invalid geometries in a GeoDataFrame.

    Non-destructive: returns a copy. Silently returns the original on error.

    Args:
        gdf: Input GeoDataFrame.

    Returns:
        GeoDataFrame with repaired geometries.
    """
    try:
        out = gdf.copy()
        out["geometry"] = out.geometry.make_valid()
        return out
    except Exception:
        return gdf


def subtract_geometries(
    base_gdf: gpd.GeoDataFrame,
    subtract_gdf: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """
    Compute set difference: base_gdf minus the union of subtract_gdf.

    Auto-reprojects subtract_gdf if CRS differs. Results contain only
    Polygon geometries (MultiPolygons are exploded).

    Args:
        base_gdf: GeoDataFrame to subtract from.
        subtract_gdf: GeoDataFrame whose union is subtracted.

    Returns:
        GeoDataFrame with Polygon geometries in base_gdf's CRS.
    """
    if subtract_gdf.empty:
        return base_gdf.copy()

    if base_gdf.crs and subtract_gdf.crs and base_gdf.crs != subtract_gdf.crs:
        subtract_gdf = subtract_gdf.to_crs(base_gdf.crs)

    sub_geoms = [_shapely_make_valid(g) for g in subtract_gdf.geometry if g is not None]
    sub_union = unary_union(sub_geoms)

    if sub_union.is_empty:
        return base_gdf.copy()

    result_geoms: list[Polygon] = []
    for _, row in base_gdf.iterrows():
        if row.geometry is None:
            continue
        geom = _shapely_make_valid(row.geometry)
        diff = geom.difference(sub_union)
        if diff.is_empty:
            continue
        for part in _extract_polygons(diff):
            result_geoms.append(part)

    if not result_geoms:
        return gpd.GeoDataFrame(columns=base_gdf.columns, crs=base_gdf.crs)

    return gpd.GeoDataFrame(geometry=result_geoms, crs=base_gdf.crs)


def subtract_smaller_overlaps(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Where polygons overlap, subtract the smaller from the larger.

    Processes polygons in descending area order. For each polygon, subtracts
    all smaller intersecting polygons from it.

    Args:
        gdf: GeoDataFrame with polygon geometries.

    Returns:
        GeoDataFrame with non-overlapping polygons.
    """
    geoms = list(gdf.geometry)
    areas = [g.area if g is not None else 0 for g in geoms]
    n = len(geoms)
    by_area = sorted(range(n), key=lambda i: -areas[i])

    for j in by_area:
        if geoms[j] is None or geoms[j].is_empty:
            continue
        for i in range(n):
            if i == j or geoms[i] is None or geoms[i].is_empty:
                continue
            if areas[i] >= areas[j]:
                continue
            if not geoms[i].intersects(geoms[j]):
                continue
            try:
                new_j = geoms[j].difference(geoms[i])
                if new_j.is_empty:
                    geoms[j] = None
                    break
                geoms[j] = new_j
            except Exception:
                pass

    out = gdf.copy()
    out["geometry"] = geoms
    out = out[out.geometry.notna() & ~out.geometry.is_empty].copy()
    return out.reset_index(drop=True)


def to_single_part(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    One row per polygon: split multi-part geometries, drop area-less fragments.

    A set difference can leave a line or a point behind inside a
    GeometryCollection where two polygons touched. Such a fragment carries no
    area but does carry the attributes of a real surface, so it would be
    counted as one in any feature tally while contributing nothing to a sum.
    It is removed here; the row count before and after is what shows it
    happened.

    Args:
        gdf: GeoDataFrame with polygon geometries, possibly multi-part.

    Returns:
        GeoDataFrame of single Polygon geometries, attributes carried over.
    """
    if gdf.empty:
        return gdf.reset_index(drop=True)

    parts = gdf.explode(index_parts=False, ignore_index=True)
    parts = parts[parts.geometry.notna() & ~parts.geometry.is_empty]
    parts = parts[parts.geometry.geom_type == "Polygon"]
    return parts.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Line geometry operations
# ---------------------------------------------------------------------------


def extend_line(
    line: LineString,
    distance: float,
    *,
    start: bool = True,
    end: bool = True,
) -> LineString:
    """Extend a LineString from one or both endpoints in the direction of the line.

    Args:
        line: Input LineString.
        distance: Extension distance (in CRS units, typically metres).
        start: Extend from the start (first vertex) of the line.
        end: Extend from the end (last vertex) of the line.

    Returns:
        New LineString with extended endpoint(s).
    """
    coords = list(line.coords)

    if start and len(coords) >= 2:
        dx = coords[0][0] - coords[1][0]
        dy = coords[0][1] - coords[1][1]
        d = np.sqrt(dx**2 + dy**2)
        if d > 0:
            coords = [
                (coords[0][0] + dx / d * distance,
                 coords[0][1] + dy / d * distance)
            ] + coords

    if end and len(coords) >= 2:
        dx = coords[-1][0] - coords[-2][0]
        dy = coords[-1][1] - coords[-2][1]
        d = np.sqrt(dx**2 + dy**2)
        if d > 0:
            coords = coords + [
                (coords[-1][0] + dx / d * distance,
                 coords[-1][1] + dy / d * distance)
            ]

    return LineString(coords)


def snap_endpoints(
    lines: list[LineString],
    tolerance: float,
) -> list[LineString]:
    """Snap LineString endpoints that are within tolerance of each other.

    Clusters nearby endpoints and replaces them with the cluster centroid,
    closing small gaps between nearly-connected lines.

    Args:
        lines: List of LineString geometries.
        tolerance: Maximum distance (CRS units) for snapping endpoints together.

    Returns:
        New list of LineStrings with snapped endpoints.
    """
    from collections import defaultdict
    from scipy.spatial import cKDTree

    if not lines:
        return []

    # Collect all endpoints
    endpoints = []
    for line in lines:
        c = list(line.coords)
        endpoints.append(c[0])
        endpoints.append(c[-1])

    pts = np.array(endpoints)
    tree = cKDTree(pts)
    groups = tree.query_ball_tree(tree, r=tolerance)

    # Union-find clustering
    parent = list(range(len(pts)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        pi, pj = find(i), find(j)
        if pi != pj:
            parent[pi] = pj

    for i, neighbors in enumerate(groups):
        for j in neighbors:
            union(i, j)

    # Compute cluster centroids
    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(len(pts)):
        clusters[find(i)].append(i)

    snap_map: dict[tuple, tuple] = {}
    for members in clusters.values():
        if len(members) > 1:
            centroid = tuple(pts[members].mean(axis=0))
            for m in members:
                snap_map[tuple(pts[m])] = centroid

    # Rebuild lines with snapped endpoints
    result = []
    for line in lines:
        coords = list(line.coords)
        start = snap_map.get(coords[0], coords[0])
        end = snap_map.get(coords[-1], coords[-1])
        result.append(LineString([start] + coords[1:-1] + [end]))

    return result


def lines_to_polygon(
    lines: list[LineString],
    *,
    extend: float = 0,
    snap_tolerance: float = 0,
    mode: str = "outer",
) -> Polygon:
    """Convert disconnected lines into a closed polygon.

    Pipeline: snap endpoints → extend lines → node at intersections →
    polygonize → union → take exterior ring.

    Args:
        lines: List of LineString geometries.
        extend: Distance to extend each line from both endpoints (CRS units).
            Set to 0 to skip extension.
        snap_tolerance: Snap endpoints within this distance before extending.
            Set to 0 to skip snapping.
        mode: ``"outer"`` returns only the exterior ring (no holes).
            ``"all"`` returns the union of all polygonized cells.

    Returns:
        Polygon formed from the line network.

    Raises:
        RuntimeError: If no closed polygons can be formed from the lines.
    """
    work = list(lines)

    if snap_tolerance > 0:
        work = snap_endpoints(work, snap_tolerance)

    if extend > 0:
        work = [extend_line(l, extend) for l in work]

    # Node at intersections and polygonize
    noded = unary_union(work)
    polys = list(polygonize(noded))

    if not polys:
        raise RuntimeError(
            "Could not form any polygons from lines — they may not "
            "intersect even after extension"
        )

    union_poly = unary_union(polys)

    if mode == "outer":
        if union_poly.geom_type == "Polygon":
            return Polygon(union_poly.exterior)
        # MultiPolygon: fill holes in each part, re-union, take exterior
        filled = unary_union(
            [Polygon(p.exterior) for p in union_poly.geoms]
        )
        if filled.geom_type == "Polygon":
            return Polygon(filled.exterior)
        return Polygon(max(filled.geoms, key=lambda g: g.area).exterior)

    # mode == "all"
    if union_poly.geom_type == "Polygon":
        return Polygon(union_poly.exterior)
    return unary_union([Polygon(p.exterior) for p in union_poly.geoms])


def load_and_union(
    path: str | Path,
    crs: str | None = None,
) -> tuple[Any | None, gpd.GeoDataFrame | None]:
    """
    Load a shapefile and union all geometries to a single shape.

    Useful for avoiding double-counting overlapping polygons in area calculations.

    Args:
        path: Path to shapefile (or any format geopandas can read).
        crs: Reproject to this CRS. None = keep original.

    Returns:
        Tuple of (unioned_geometry, geodataframe), or (None, None) on error.
    """
    path = Path(path)
    if not path.exists():
        return None, None

    gdf = gpd.read_file(path)
    if gdf.empty:
        return None, None

    if crs:
        gdf = gdf.to_crs(crs)

    union_geom = gdf.union_all()
    return union_geom, gdf


def find_column(gdf: gpd.GeoDataFrame, candidates: list[str]) -> str | None:
    """
    Find the first column name from a list of candidates that exists in a GeoDataFrame.

    Useful for handling shapefiles with varying column naming conventions.

    Args:
        gdf: GeoDataFrame to search.
        candidates: Column name candidates in priority order.

    Returns:
        First matching column name, or None.
    """
    for c in candidates:
        if c in gdf.columns:
            return c
    return None


def morphological_filter(
    gdf: gpd.GeoDataFrame,
    min_area_ha: float = 0.5,
    buffer_distance: float = 10.0,
    remove_holes: bool = True,
) -> gpd.GeoDataFrame:
    """
    Clean polygon data using buffer-dissolve-buffer morphological filtering.

    Merges nearby polygons by buffering outward, dissolving, then buffering
    back inward. Removes small fragments and optionally fills holes.

    Args:
        gdf: GeoDataFrame with polygon geometries (must be in projected CRS with meters).
        min_area_ha: Remove polygons smaller than this (hectares).
        buffer_distance: Buffer distance in meters for the morphological operation.
        remove_holes: If True, remove inner rings from result polygons.

    Returns:
        Filtered GeoDataFrame with area_m2 and area_ha columns.
    """
    if gdf.empty:
        return gdf.copy()

    # Step 1: Size filter
    gdf = gdf.copy()
    gdf["area_ha"] = gdf.geometry.area / 10_000
    filtered = gdf[gdf["area_ha"] >= min_area_ha].copy()
    if filtered.empty:
        return filtered

    # Step 2: Positive buffer (sharp corners) → dissolve → negative buffer
    buffered = filtered.geometry.buffer(buffer_distance, cap_style=3, join_style=2)
    dissolved = gpd.GeoDataFrame(geometry=buffered, crs=filtered.crs).dissolve()
    if len(dissolved) == 1:
        dissolved = dissolved.explode(index_parts=False)
    dissolved = dissolved.reset_index(drop=True)

    shrunk = dissolved.geometry.buffer(-buffer_distance, cap_style=3, join_style=2)
    shrunk = shrunk[~shrunk.is_empty]
    result = gpd.GeoDataFrame(geometry=shrunk, crs=filtered.crs)
    result = result.explode(index_parts=False).reset_index(drop=True)
    result = result[result.geometry.is_valid & ~result.geometry.is_empty]

    # Step 3: Recalculate areas, re-filter
    result["area_m2"] = result.geometry.area
    result["area_ha"] = result["area_m2"] / 10_000
    result = result[result["area_ha"] >= min_area_ha].copy()

    # Step 4: Remove holes
    if remove_holes:
        result["geometry"] = result.geometry.apply(remove_inner_rings)
        result["area_m2"] = result.geometry.area
        result["area_ha"] = result["area_m2"] / 10_000

    return result.reset_index(drop=True)


def distance_to_nearest(
    gdf: gpd.GeoDataFrame,
    reference_gdf: gpd.GeoDataFrame,
    column_name: str = "dist_nearest",
) -> gpd.GeoDataFrame:
    """
    Add a column with the distance to the nearest feature in a reference layer.

    Both GeoDataFrames must be in the same projected CRS (meters).

    Args:
        gdf: GeoDataFrame to add distances to.
        reference_gdf: Reference features to measure distance to.
        column_name: Name of the new distance column.

    Returns:
        Copy of gdf with the distance column added (meters, rounded to 1 decimal).
    """
    if reference_gdf.empty:
        result = gdf.copy()
        result[column_name] = None
        return result

    # Ensure same CRS
    if gdf.crs and reference_gdf.crs and gdf.crs != reference_gdf.crs:
        reference_gdf = reference_gdf.to_crs(gdf.crs)

    ref_geoms = reference_gdf.geometry.tolist()
    result = gdf.copy()
    distances = []
    for geom in result.geometry:
        if geom is None or geom.is_empty:
            distances.append(None)
            continue
        d = min(geom.distance(ref) for ref in ref_geoms)
        distances.append(round(d, 1))
    result[column_name] = distances
    return result


# ---------------------------------------------------------------------------
# Direction classification
# ---------------------------------------------------------------------------

# 8-zone labels ordered by angle (starting at east = 0°, going counter-clockwise).
# Each zone is 45° wide, centered on its axis.
_DIRECTIONS_8 = [
    "right",       #    0°: -22.5° to  +22.5°
    "up-right",    #   45°: +22.5° to  +67.5°
    "up",          #   90°: +67.5° to +112.5°
    "up-left",     #  135°: +112.5° to +157.5°
    "left",        #  180°: +157.5° to +202.5° (i.e. |angle| >= 157.5°)
    "down-left",   # -135°: -157.5° to -112.5°
    "down",        #  -90°: -112.5° to  -67.5°
    "down-right",  #  -45°:  -67.5° to  -22.5°
]

# 4-zone labels: same order, each zone is 90° wide.
_DIRECTIONS_4 = ["right", "up", "left", "down"]

# Map each 4-zone name to which 8-zone names it contains.
_ZONES_4_TO_8 = {
    "right": {"right", "up-right", "down-right"},
    "up": {"up", "up-right", "up-left"},
    "left": {"left", "up-left", "down-left"},
    "down": {"down", "down-right", "down-left"},
}


def classify_direction(dx: float, dy: float, *, zones: int = 4) -> str:
    """Classify a 2D vector into a direction zone.

    Args:
        dx: X component of the vector (positive = east).
        dy: Y component of the vector (positive = north).
        zones: Number of zones — ``4`` for cardinal directions (right, up,
            left, down; 90° each) or ``8`` to include diagonals (up-right,
            up-left, down-right, down-left; 45° each).

    Returns:
        Direction label. With ``zones=4``: one of ``"right"``, ``"up"``,
        ``"left"``, ``"down"``. With ``zones=8``: also ``"up-right"``,
        ``"up-left"``, ``"down-right"``, ``"down-left"``.

    Raises:
        ValueError: If dx and dy are both zero, or zones is not 4 or 8.
    """
    if dx == 0 and dy == 0:
        raise ValueError("Cannot classify direction of a zero-length vector")
    if zones not in (4, 8):
        raise ValueError(f"zones must be 4 or 8, got {zones}")

    angle = math.atan2(dy, dx)  # radians, -pi to +pi
    n = zones
    sector_size = 2 * math.pi / n
    # Shift angle so sector 0 ("right") is centered at 0
    index = int((angle + sector_size / 2) / sector_size) % n
    labels = _DIRECTIONS_8 if zones == 8 else _DIRECTIONS_4
    return labels[index]


def filter_lines_by_direction(
    gdf: gpd.GeoDataFrame,
    directions: str | list[str],
    *,
    zones: int = 4,
    select: str = "shortest",
    min_length: float = 0.1,
) -> gpd.GeoDataFrame:
    """Filter LineStrings by their direction zone, optionally picking one per zone.

    Each line's direction is determined by the vector from its first to its
    last coordinate. Lines shorter than *min_length* are excluded.

    Args:
        gdf: GeoDataFrame with LineString geometries.
        directions: One or more direction names to keep. Valid names depend
            on *zones*: with ``zones=4`` use ``"right"``, ``"up"``, ``"left"``,
            ``"down"``; with ``zones=8`` also ``"up-right"`` etc.
            A 4-zone name used with ``zones=8`` matches the corresponding
            three 8-zone sectors (e.g. ``"right"`` matches ``"right"``,
            ``"up-right"``, ``"down-right"``).
        zones: ``4`` or ``8`` — passed to :func:`classify_direction`.
        select: ``"shortest"`` returns only the shortest line per requested
            direction. ``"all"`` returns every line in the requested zones.
        min_length: Minimum line length (CRS units) to consider.

    Returns:
        Filtered GeoDataFrame. When *select* is ``"shortest"``, contains at
        most one feature per requested direction.
    """
    if isinstance(directions, str):
        directions = [directions]

    valid_names = set(_DIRECTIONS_8 if zones == 8 else _DIRECTIONS_4)
    # Expand 4-zone names when using 8 zones
    resolved: set[str] = set()
    for d in directions:
        if d in valid_names:
            resolved.add(d)
        elif zones == 8 and d in _ZONES_4_TO_8:
            resolved.update(_ZONES_4_TO_8[d])
        else:
            raise ValueError(
                f"Unknown direction {d!r} for zones={zones}, "
                f"valid: {sorted(valid_names)}"
            )

    # Classify each line
    records = []
    for idx, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty or geom.length < min_length:
            continue
        c0, c1 = geom.coords[0], geom.coords[-1]
        d = classify_direction(c1[0] - c0[0], c1[1] - c0[1], zones=zones)
        if d in resolved:
            records.append((idx, d, geom.length))

    if not records:
        return gpd.GeoDataFrame(columns=gdf.columns, crs=gdf.crs)

    if select == "all":
        keep = [idx for idx, _, _ in records]
    elif select == "shortest":
        best: dict[str, tuple] = {}
        for idx, d, length in records:
            if d not in best or length < best[d][1]:
                best[d] = (idx, length)
        keep = [idx for idx, _ in best.values()]
    else:
        raise ValueError(f"select must be 'shortest' or 'all', got {select!r}")

    return gdf.loc[keep].reset_index(drop=True)


def points_with_buffers(
    data: list[dict],
    crs: str,
    x_col: str = "x",
    y_col: str = "y",
    buffer_col: str | None = None,
    buffer_factor: float = 1.0,
    default_buffer: float = 0.0,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame | None]:
    """
    Create a point GeoDataFrame from coordinate data, optionally with buffer union.

    Args:
        data: List of dicts with coordinate and attribute columns.
        crs: Coordinate reference system for the output.
        x_col: Column name for X/easting coordinate.
        y_col: Column name for Y/northing coordinate.
        buffer_col: Column name containing buffer radius values.
            If None, no buffer GeoDataFrame is created.
        buffer_factor: Multiply buffer values by this factor (e.g. 15 for 15x height).
        default_buffer: Default buffer radius when buffer_col value is missing/zero.

    Returns:
        Tuple of (points_gdf, buffer_union_gdf). buffer_union_gdf is None if
        buffer_col is None. Buffer GeoDataFrame contains a single unioned polygon.
    """
    from shapely.geometry import Point

    geometries = []
    attributes = []
    for row in data:
        x = row.get(x_col)
        y = row.get(y_col)
        if x is None or y is None:
            continue
        try:
            geometries.append(Point(float(x), float(y)))
        except (ValueError, TypeError):
            continue
        attributes.append({k: v for k, v in row.items() if k not in (x_col, y_col)})

    if not geometries:
        empty = gpd.GeoDataFrame(columns=["geometry"], crs=crs)
        return empty, None

    points_gdf = gpd.GeoDataFrame(attributes, geometry=geometries, crs=crs)

    if buffer_col is None:
        return points_gdf, None

    # Create buffer union
    buffers = []
    for i, row in points_gdf.iterrows():
        r = row.get(buffer_col, default_buffer)
        if r is None or r <= 0:
            r = default_buffer
        if r <= 0:
            continue
        buffers.append(points_gdf.geometry.iloc[i] .buffer(r * buffer_factor))

    if not buffers:
        return points_gdf, None

    buffer_union = unary_union(buffers)
    buffer_gdf = gpd.GeoDataFrame(geometry=[buffer_union], crs=crs)
    return points_gdf, buffer_gdf


def strip_utm_zone_prefix(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Strip the leading UTM zone digit(s) from X coordinates in a GeoDataFrame.

    CAD/DXF files sometimes store UTM coordinates with the zone number prefixed
    to the easting (e.g. 33266881 instead of 266881 for zone 33, or 32548000
    instead of 548000 for zone 32). This function detects and removes that prefix.

    Auto-detects the zone prefix (32 or 33) from the data. Raises ValueError
    if X coordinates don't have a known zone prefix.

    Args:
        gdf: GeoDataFrame with projected UTM coordinates.

    Returns:
        Copy of gdf with corrected X coordinates.
    """
    if gdf.empty:
        return gdf.copy()

    sample_x = gdf.geometry.iloc[0].centroid.x
    prefix = int(str(int(sample_x))[:2])
    if prefix not in _KNOWN_ZONE_PREFIXES:
        raise ValueError(
            f"X coordinate {sample_x:.0f} does not start with a known UTM zone "
            f"prefix ({_KNOWN_ZONE_PREFIXES}). No stripping needed?"
        )

    shift = prefix * 1_000_000
    out = gdf.copy()
    out["geometry"] = out.geometry.apply(
        lambda geom: _shift_x(geom, -shift)
    )
    print(
        f"[geometry] Stripped zone prefix {prefix} from X coordinates "
        f"(shift: -{shift})",
        flush=True,
    )
    return out


def _shift_x(geom, dx: float):
    """Shift all X coordinates of a geometry by dx."""
    from shapely import transform as _transform

    def _apply(coords):
        coords = np.array(coords, dtype=float)
        coords[:, 0] += dx
        return coords

    return _transform(geom, _apply)


def buffer_ring_zones(
    source_geom,
    zones: list[dict],
) -> list[tuple[dict, Any]]:
    """Concentric ring polygons by distance bands around a source geometry.

    For each zone, computes::

        ring = source.buffer(outer_m).difference(source.buffer(inner_m))

    For polygon sources this naturally excludes the source itself when
    ``inner_m=0`` (because ``Polygon.buffer(0)`` returns the polygon).  For
    line sources the source is implicitly contained in the inner band
    (because ``LineString.buffer(0)`` is empty).

    Args:
        source_geom: Any Shapely geometry — typically a polygon (e.g. road
            traffic surface) or line (e.g. railway centerline).  May be a
            collection.
        zones: List of zone definitions, each a dict with keys:
            - ``name`` (str): zone label.
            - ``outer_m`` (float): outer distance in CRS units (typically
              metres for projected CRS).
            - ``inner_m`` (float, optional, default 0): inner distance.

    Returns:
        List of ``(zone_meta, ring_polygon)`` tuples, ordered as input.
        Empty rings are dropped.  ``zone_meta`` is the input zone dict plus
        ``area_m2`` (assumes a metric projected CRS).

    Raises:
        ValueError: if ``outer_m <= 0`` or ``inner_m >= outer_m``.
    """
    out: list[tuple[dict, Any]] = []
    if source_geom is None or source_geom.is_empty:
        return out
    for z in zones:
        name = z.get("name", "")
        outer_m = float(z["outer_m"])
        inner_m = float(z.get("inner_m", 0))
        if outer_m <= 0:
            raise ValueError(f"zone '{name}': outer_m must be > 0 (got {outer_m})")
        if inner_m < 0:
            raise ValueError(f"zone '{name}': inner_m must be >= 0 (got {inner_m})")
        if inner_m >= outer_m:
            raise ValueError(
                f"zone '{name}': inner_m ({inner_m}) must be < outer_m ({outer_m})"
            )
        outer = source_geom.buffer(outer_m)
        inner = source_geom.buffer(inner_m)
        ring = outer.difference(inner)
        if ring.is_empty:
            continue
        meta = {**z, "inner_m": inner_m, "outer_m": outer_m,
                "area_m2": float(ring.area)}
        out.append((meta, ring))
    return out


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_polygons(geom) -> list[Polygon]:
    """Extract Polygon parts from any geometry type."""
    if geom.geom_type == "Polygon" and not geom.is_empty:
        return [geom]
    if geom.geom_type == "MultiPolygon":
        return [p for p in geom.geoms if not p.is_empty]
    if geom.geom_type == "GeometryCollection":
        out: list[Polygon] = []
        for g in geom.geoms:
            if g.geom_type == "Polygon" and not g.is_empty:
                out.append(g)
        return out
    return []


def _zackigkeit(geom) -> float:
    """Richtungswechsel je Meter Rand — das Mass für „sieht zackig aus".

    Stützpunktzahl allein taugt nicht: Rundung fügt Punkte HINZU, um eine Kurve
    zu bilden, und macht das Ergebnis trotzdem ruhiger.
    """
    import math

    teile = geom.geoms if hasattr(geom, "geoms") else [geom]
    winkel_summe = 0.0
    laenge = 0.0
    for t in teile:
        if t.geom_type != "Polygon":
            continue
        for ring in [t.exterior, *t.interiors]:
            c = list(ring.coords)
            laenge += ring.length
            for i in range(1, len(c) - 1):
                (x0, y0), (x1, y1), (x2, y2) = c[i - 1], c[i], c[i + 1]
                a1 = math.atan2(y1 - y0, x1 - x0)
                a2 = math.atan2(y2 - y1, x2 - x1)
                d = abs((a2 - a1 + math.pi) % (2 * math.pi) - math.pi)
                winkel_summe += d
    return winkel_summe / laenge if laenge else 0.0


def _zackigkeit_gesamt(geoms) -> float:
    """Richtungswechsel je Meter über ALLE Ränder zusammen.

    Als Mittel der Einzelwerte ist die Kennzahl unbrauchbar: ein Splitter hat
    winzige Randlänge, sein Quotient explodiert und beherrscht den Mittelwert
    (gemessen: 6192 statt 3,4). Summe durch Summe bleibt stabil.
    """
    import math

    winkel, laenge = 0.0, 0.0
    for geom in geoms:
        if geom is None or geom.is_empty:
            continue
        for t in (geom.geoms if hasattr(geom, "geoms") else [geom]):
            if t.geom_type != "Polygon":
                continue
            for ring in [t.exterior, *t.interiors]:
                c = list(ring.coords)
                laenge += ring.length
                for i in range(1, len(c) - 1):
                    (x0, y0), (x1, y1), (x2, y2) = c[i - 1], c[i], c[i + 1]
                    a1 = math.atan2(y1 - y0, x1 - x0)
                    a2 = math.atan2(y2 - y1, x2 - x1)
                    winkel += abs((a2 - a1 + math.pi) % (2 * math.pi) - math.pi)
    return winkel / laenge if laenge else 0.0


def _chaikin_ring(coords: list[tuple[float, float]], runden: int) -> list[tuple[float, float]]:
    """Chaikins Eckenabschnitt auf einem geschlossenen Ring."""
    punkte = list(coords[:-1]) if coords[0] == coords[-1] else list(coords)
    for _ in range(runden):
        neu = []
        n = len(punkte)
        for i in range(n):
            (x0, y0), (x1, y1) = punkte[i], punkte[(i + 1) % n]
            neu.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
            neu.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
        punkte = neu
    return punkte + [punkte[0]]


def smooth_polygons(
    gdf,
    *,
    zacken_m: float = 0.8,
    toleranz_m: float = 0.4,
    runden: int = 2,
    min_flaeche_m2: float = 2.0,
):
    """Flächenumrisse in eine zeichenbare Form bringen.

    Automatisch gewonnene Flächen — aus Rasterklassifikation, Segmentierung oder
    Digitalisierung am Bildschirm — haben Ränder aus hunderten kurzer Stücke mit
    ständigen Richtungswechseln. Fachlich sind sie richtig, in einem Plan sind
    sie unbrauchbar: keine dieser Zacken entspricht einer Kante im Gelände, und
    ein Leser hält sie für Aussagen.

    Drei Schritte, in dieser Reihenfolge:

    1. **Zacken kappen** über Schliessen und Öffnen im Vektorraum
       (``buffer(+d).buffer(−2d).buffer(+d)``). Entfernt Ausbuchtungen und
       Einkerbungen unterhalb der Zackenbreite.
    2. **Stützpunkte ausdünnen** (Douglas-Peucker) — was danach bleibt, sind
       Richtungswechsel, die etwas bedeuten.
    3. **Ecken runden** (Chaikin) — aus dem Polygonzug wird eine Linie, die man
       zeichnen würde.

    Die Fläche ändert sich dabei; wie stark, steht im Rückgabewert, denn eine
    Glättung, die für eine Bilanz zu viel kostet, muss auffallen und nicht
    geglaubt werden.

    Args:
        gdf: GeoDataFrame mit Polygonen in einem metrischen CRS.
        zacken_m: Breite der Zacken, die verschwinden sollen.
        toleranz_m: Douglas-Peucker-Toleranz.
        runden: Chaikin-Durchläufe; 0 lässt die Ecken scharf.
        min_flaeche_m2: Was danach kleiner ist, fällt weg.

    Returns:
        ``(GeoDataFrame, info)`` mit ``flaeche_vorher_m2``, ``flaeche_nachher_m2``,
        ``flaechenaenderung_pct`` und der Stützpunktzahl vorher/nachher.
    """
    import numpy as np
    from shapely.geometry import MultiPolygon, Polygon

    if gdf.empty:
        return gdf.copy(), {"flaeche_vorher_m2": 0.0, "flaeche_nachher_m2": 0.0,
                            "flaechenaenderung_pct": 0.0}

    def stuetzpunkte(g):
        if g is None or g.is_empty:
            return 0
        teile = g.geoms if hasattr(g, "geoms") else [g]
        return sum(len(t.exterior.coords) + sum(len(r.coords) for r in t.interiors)
                   for t in teile if t.geom_type == "Polygon")

    vorher_flaeche = float(gdf.area.sum())
    vorher_punkte = int(sum(stuetzpunkte(g) for g in gdf.geometry))

    d = zacken_m / 2.0
    geglaettet = []
    for geom in gdf.geometry:
        g = geom.buffer(d, join_style=1).buffer(-2 * d, join_style=1).buffer(d, join_style=1)
        if g.is_empty:
            geglaettet.append(g)
            continue
        g = g.simplify(toleranz_m, preserve_topology=True)
        if runden > 0:
            teile = []
            for t in (g.geoms if hasattr(g, "geoms") else [g]):
                if t.geom_type != "Polygon" or t.is_empty:
                    continue
                aussen = _chaikin_ring(list(t.exterior.coords), runden)
                innen = [_chaikin_ring(list(r.coords), runden) for r in t.interiors
                         if len(r.coords) > 4]
                p = Polygon(aussen, innen)
                if not p.is_valid:
                    p = p.buffer(0)
                if not p.is_empty:
                    teile.append(p)
            g = teile[0] if len(teile) == 1 else (MultiPolygon(teile) if teile else g)
        geglaettet.append(g)

    aus = gdf.copy()
    aus["geometry"] = geglaettet
    aus = aus[~aus.geometry.is_empty & ~aus.geometry.isna()]
    aus = aus[aus.area >= min_flaeche_m2]

    nachher_flaeche = float(aus.area.sum())
    info = {
        "flaeche_vorher_m2": vorher_flaeche,
        "flaeche_nachher_m2": nachher_flaeche,
        "flaechenaenderung_pct": (100.0 * (nachher_flaeche - vorher_flaeche) / vorher_flaeche)
        if vorher_flaeche else 0.0,
        "stuetzpunkte_vorher": vorher_punkte,
        "stuetzpunkte_nachher": int(sum(stuetzpunkte(g) for g in aus.geometry)),
    }
    return aus.reset_index(drop=True), info


def _chaikin_linie(coords: list[tuple[float, float]], runden: int) -> list[tuple[float, float]]:
    """Chaikin auf einer offenen Linie; Anfang und Ende bleiben, wo sie sind.

    Die Endpunkte sind Knoten des Netzes — verschieben sie sich, reissen die
    Nachbarflächen auf, und genau das soll die Partitionsglättung verhindern.
    """
    punkte = list(coords)
    if len(punkte) < 3:
        return punkte
    for _ in range(runden):
        neu = [punkte[0]]
        for i in range(len(punkte) - 1):
            (x0, y0), (x1, y1) = punkte[i], punkte[i + 1]
            neu.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
            neu.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
        neu.append(punkte[-1])
        punkte = neu
    return punkte


def smooth_partition(
    gdf,
    klasse_spalte: str,
    *,
    clip=None,
    toleranz_m: float = 0.4,
    runden: int = 2,
    min_flaeche_m2: float = 3.0,
    raster_m: float | None = None,
):
    """Eine flächendeckende Klassenkarte glätten, ohne Lücken zu reissen.

    :func:`smooth_polygons` behandelt jede Fläche für sich — bei einer
    PARTITION, in der die Flächen aneinandergrenzen, schrumpfen benachbarte
    Ränder dabei voneinander weg. Gemessen an einer realen Bestandskarte: 274 m²
    Lücke in 119 Spalten von rund 0,45 m Breite, dazu 73 m² Überlappung, obwohl
    die Eingabe lückenlos war. Im GIS ist so eine Karte kaputt: Flächen summieren
    sich nicht mehr auf ihr Gebiet, und jede Verschneidung erbt die Spalten.

    Deshalb wird hier nicht die Fläche geglättet, sondern das GRENZNETZ: alle
    Ränder werden zu einem Liniennetz vereinigt (dabei an jeder Kreuzung
    aufgetrennt), jede Kante für sich geglättet — mit festgehaltenen Endpunkten,
    damit die Knoten bleiben —, und aus dem geglätteten Netz werden die Flächen
    neu gebildet. Was zusammengehörte, grenzt danach wieder exakt aneinander.

    Args:
        gdf: GeoDataFrame mit aneinandergrenzenden Polygonen.
        klasse_spalte: Spalte, die die Klasse trägt; sie wird über den
            Repräsentativpunkt der neuen Flächen übernommen.
        clip: Gebiet, das die Partition ausfüllt (dessen Rand ist Teil des
            Netzes). ``None`` nimmt die Aussenhülle der Eingabe.
        toleranz_m: Douglas-Peucker-Toleranz je Kante.
        runden: Chaikin-Durchläufe je Kante.
        min_flaeche_m2: Splitter darunter werden dem grössten Nachbarn
            zugeschlagen statt weggeworfen — wegwerfen risse wieder Lücken.
        raster_m: Gitterweite, auf die die Eingabe vor der Netzbildung
            einrastet. ``None`` nimmt die halbe Toleranz. Ohne diesen Schritt
            zerfällt das Netz einer Rasterklassifikation in Mikrokanten, an
            denen keine Vereinfachung greift.

    Returns:
        ``(GeoDataFrame, info)`` mit ``luecke_m2``, ``ueberlappung_m2`` und den
        Stützpunktzahlen. Bei einer sauberen Partition ist beides nahe null.
    """
    import geopandas as gpd
    import numpy as np
    from shapely.geometry import LineString, MultiLineString
    from shapely.ops import linemerge, polygonize, unary_union

    if gdf.empty:
        return gdf.copy(), {"luecke_m2": 0.0, "ueberlappung_m2": 0.0}

    if raster_m is None:
        raster_m = toleranz_m / 2

    # Vor jeder Veränderung messen: sonst weist die Kennzahl die Wirkung des
    # Einrastens nicht aus und das Verfahren schönt sich selbst.
    zackig_vorher = _zackigkeit_gesamt(list(gdf.geometry))

    # Entartete Flächen aussortieren: aus Verschmelzen und Explodieren fallen
    # Splitter ohne Fläche, deren Rand keine Linie mehr ist (gemessen: 59 von
    # 108). Sie tragen nichts bei und lassen das Verketten der Kanten abbrechen.
    gdf = gdf[gdf.geometry.notna()].copy()
    gdf["geometry"] = gdf.geometry.buffer(0)
    gdf = gdf[(gdf.geom_type.isin(["Polygon", "MultiPolygon"]))
              & (gdf.area > 1e-6) & gdf.geometry.is_valid]
    if gdf.empty:
        leer = gdf.copy()
        return leer, {"luecke_m2": 0.0, "ueberlappung_m2": 0.0}

    # Auf ein Gitter einrasten, BEVOR das Grenznetz gebildet wird. Ohne das
    # berühren sich die Flächen an unzähligen Pixelecken, das Netz zerfällt in
    # Mikrokanten (gemessen: 4.467 Kanten, Median 0,26 m und drei Punkte) und
    # keine Vereinfachung kann dort greifen — die Endpunkte sind fixiert.
    if raster_m and raster_m > 0:
        from shapely import set_precision

        gdf["geometry"] = [set_precision(g, raster_m) for g in gdf.geometry]
        gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.is_valid & (gdf.area > 1e-6)]
        if gdf.empty:
            return gdf.copy(), {"luecke_m2": 0.0, "ueberlappung_m2": 0.0}

    gebiet = clip if clip is not None else unary_union(list(gdf.geometry))

    def punkte(g):
        teile = g.geoms if hasattr(g, "geoms") else [g]
        return sum(len(t.exterior.coords) for t in teile if t.geom_type == "Polygon")

    vorher_punkte = int(sum(punkte(g) for g in gdf.geometry))

    # Vereinigung trennt das Netz an jeder Kreuzung auf — die Knoten, die
    # danach festgehalten werden.
    netz = unary_union([g.boundary for g in gdf.geometry] + [gebiet.boundary])
    # unary_union zerlegt in Einzelstrecken — gemessen: 86 Zwei-Punkt-Segmente
    # für zwei Flächen. Darauf greift weder Ausdünnen noch Runden, beides
    # braucht mindestens drei Punkte. linemerge verkettet sie wieder zu Zügen
    # und bricht nur an echten Verzweigungen, also genau an den Knoten.
    # Reale Ränder liefern gelegentlich entartete Stücke (ein Punkt, Nulllänge);
    # linemerge bricht daran ab. Aussortieren, statt den Lauf zu verlieren.
    roh = list(netz.geoms) if isinstance(netz, MultiLineString) else [netz]
    brauchbar = [k for k in roh
                 if k.geom_type == "LineString" and len(k.coords) > 1 and k.length > 0]
    if not brauchbar:
        return gdf.copy(), {"luecke_m2": float(gebiet.area), "ueberlappung_m2": 0.0}
    verkettet = linemerge(brauchbar)
    kanten = (list(verkettet.geoms) if isinstance(verkettet, MultiLineString)
              else [verkettet])

    # Der Gebietsrand ist gegeben und wird nicht geglättet: gerundet schneidet
    # er Ecken ab, und die Partition passt dann nicht mehr in ihr Gebiet —
    # gemessen 125 m² Lücke an einem 40-m-Quadrat, allein aus den vier Ecken.
    # Eng gefasst: mit der Toleranz als Puffer gilt jede Kante, die zufällig
    # nahe am Gebietsrand verläuft, als Randkante und bleibt ungeglättet —
    # gemessen blieb die Zackigkeit dadurch fast unverändert.
    rand = gebiet.boundary.buffer(0.01)

    geglaettet = []
    for kante in kanten:
        if kante.geom_type != "LineString" or len(kante.coords) < 3:
            geglaettet.append(kante)
            continue
        if kante.within(rand):
            geglaettet.append(kante)
            continue
        vereinfacht = kante.simplify(toleranz_m, preserve_topology=True)
        rund = _chaikin_linie(list(vereinfacht.coords), runden) if runden else list(vereinfacht.coords)
        if len(rund) >= 2:
            geglaettet.append(LineString(rund))

    flaechen = [f for f in polygonize(unary_union(geglaettet)) if not f.is_empty]
    if not flaechen:
        return gdf.copy(), {"luecke_m2": float(gebiet.area), "ueberlappung_m2": 0.0}

    # Klasse aus der Eingabe übernehmen: über den Punkt, der garantiert INNEN
    # liegt — ein Schwerpunkt kann bei c-förmigen Flächen ausserhalb landen.
    treffer = gpd.GeoDataFrame(geometry=[f.representative_point() for f in flaechen],
                               crs=gdf.crs)
    attribute = [s for s in gdf.columns if s != "geometry"]
    zuordnung = gpd.sjoin(treffer, gdf[attribute + ["geometry"]],
                          how="left", predicate="within")
    zuordnung = zuordnung[~zuordnung.index.duplicated(keep="first")]

    aus = gpd.GeoDataFrame(
        {s: zuordnung[s].to_numpy() for s in attribute},
        geometry=flaechen, crs=gdf.crs)
    ohne = aus[aus[klasse_spalte].isna()]
    aus = aus[aus[klasse_spalte].notna()]
    if len(ohne) and len(aus):
        # Wegwerfen risse genau die Lücke auf, die diese Funktion vermeiden soll
        nachbar = gpd.sjoin_nearest(ohne[["geometry"]], aus, how="left")
        nachbar = nachbar[~nachbar.index.duplicated(keep="first")]
        ohne = ohne.copy()
        for spalte in [s for s in aus.columns if s != "geometry"]:
            quelle_spalte = spalte + "_right" if spalte + "_right" in nachbar else spalte
            if quelle_spalte in nachbar:
                ohne[spalte] = nachbar[quelle_spalte].to_numpy()
        aus = gpd.GeoDataFrame(__import__("pandas").concat([aus, ohne]), crs=gdf.crs)
    if clip is not None:
        aus["geometry"] = aus.geometry.intersection(gebiet)
        aus = aus[~aus.geometry.is_empty]

    # Splitter dem grössten Nachbarn zuschlagen, statt sie zu verwerfen
    klein = aus[aus.area < min_flaeche_m2]
    gross = aus[aus.area >= min_flaeche_m2]
    if len(klein) and len(gross):
        nachbar = gpd.sjoin_nearest(klein[["geometry"]], gross[[klasse_spalte, "geometry"]],
                                    how="left")
        nachbar = nachbar[~nachbar.index.duplicated(keep="first")]
        klein = klein.copy()
        klein[klasse_spalte] = nachbar[klasse_spalte + "_right"].to_numpy() \
            if klasse_spalte + "_right" in nachbar else nachbar[klasse_spalte].to_numpy()
        aus = gpd.GeoDataFrame(__import__("pandas").concat([gross, klein]), crs=gdf.crs)

    schluessel = [s for s in aus.columns if s != "geometry"]
    aus = aus.dissolve(by=schluessel, as_index=False).explode(index_parts=False)
    # Der Zuschnitt aufs Gebiet kann Linien und Punkte hinterlassen, wo eine
    # Fläche den Rand nur berührt. Sie tragen null Fläche, bleiben aber im Layer
    # und lassen jede Weiterverarbeitung stolpern, die Polygone erwartet —
    # gemessen: 36 solcher Reste in einer Karte mit 70 echten Flächen.
    aus = aus[aus.geom_type.isin(["Polygon", "MultiPolygon"])]
    aus = aus[~aus.geometry.is_empty & (aus.area > 0)].reset_index(drop=True)

    vereinigt = unary_union(list(aus.geometry))
    info = {
        "luecke_m2": float(gebiet.difference(vereinigt).area),
        "ueberlappung_m2": float(aus.area.sum() - vereinigt.area),
        "stuetzpunkte_vorher": vorher_punkte,
        "stuetzpunkte_nachher": int(sum(punkte(g) for g in aus.geometry)),
        "zackigkeit_vorher": zackig_vorher,
        "zackigkeit_nachher": _zackigkeit_gesamt(list(aus.geometry)),
        "flaechen": int(len(aus)),
    }
    return aus, info


def _richtung(linie, s: float, ds: float = 1.0):
    """Einheitsvektor der Linienrichtung an der Bogenlänge *s*."""
    import math

    a = linie.interpolate(max(s - ds / 2, 0))
    b = linie.interpolate(min(s + ds / 2, linie.length))
    dx, dy = b.x - a.x, b.y - a.y
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 0 else None


def _parallele_abschnitte(kanten_linie, grenzen, *, naehe_m: float,
                          max_winkel_grad: float, schritt_m: float = 2.0,
                          min_laenge_m: float = 4.0):
    """Abschnitte einer Kante, die nah UND richtungsgleich zu einer Grenze laufen.

    Nähe allein genügt nicht: eine Kante, die eine Klassengrenze KREUZT, ist an
    der Kreuzung ebenso nah wie eine, die sie begleitet — und wird ohne diese
    Prüfung genauso als Ersatz behandelt. Genau daran kollabierte die Übernahme
    an einer realen Karte.
    """
    import math

    from shapely.geometry import LineString, MultiLineString
    from shapely.ops import unary_union

    linien = (list(kanten_linie.geoms) if isinstance(kanten_linie, MultiLineString)
              else [kanten_linie])
    cos_grenze = math.cos(math.radians(max_winkel_grad))
    treffer = []

    for linie in linien:
        if linie.geom_type != "LineString" or linie.length < schritt_m:
            continue
        lauf = []
        s = 0.0
        while s <= linie.length:
            p = linie.interpolate(s)
            if p.distance(grenzen) <= naehe_m:
                r_kante = _richtung(linie, s, schritt_m)
                t = grenzen.project(p)
                r_grenze = _richtung(grenzen, t, schritt_m) if hasattr(grenzen, "project") else None
                if r_kante and r_grenze:
                    # Betrag: Gegenrichtung ist ebenso parallel
                    kos = abs(r_kante[0] * r_grenze[0] + r_kante[1] * r_grenze[1])
                    if kos >= cos_grenze:
                        lauf.append(s)
                        s += schritt_m
                        continue
            if len(lauf) >= 2 and (lauf[-1] - lauf[0]) >= min_laenge_m:
                treffer.append(_teilstueck(linie, lauf[0], lauf[-1]))
            lauf = []
            s += schritt_m
        if len(lauf) >= 2 and (lauf[-1] - lauf[0]) >= min_laenge_m:
            treffer.append(_teilstueck(linie, lauf[0], lauf[-1]))

    return unary_union(treffer) if treffer else LineString([])


def _teilstueck(linie, s0: float, s1: float):
    """Abschnitt einer Linie zwischen zwei Bogenlängen."""
    from shapely.geometry import LineString

    punkte = [linie.interpolate(s0)]
    for x, y in linie.coords:
        from shapely.geometry import Point

        s = linie.project(Point(x, y))
        if s0 < s < s1:
            punkte.append(Point(x, y))
    punkte.append(linie.interpolate(s1))
    return LineString([(p.x, p.y) for p in punkte])


def partition_along(
    gdf,
    klasse_spalte: str,
    kanten,
    *,
    clip=None,
    naehe_m: float = 1.5,
    max_winkel_grad: float = 25.0,
    min_laenge_m: float = 4.0,
    min_anteil: float = 0.5,
):
    """Klassengrenzen auf gemessene Kanten legen, statt sie im Bild zu lassen.

    Eine Bildklassifikation zieht ihre Grenzen dort, wo die Farbe wechselt —
    gemessen an einer realen Karte im Median 0,91 m neben der Kante, die der
    Vermesser aufgenommen hat. Für ein Dokument, das geprüft wird, ist der
    Unterschied nicht die Genauigkeit allein: eine Grenze auf einer Vermessungs-
    kante ist BEGRÜNDBAR, eine auf einer Farbschwelle nicht.

    STAND UND GEMESSENE GRENZE. Die Richtungsprüfung ist eingebaut: nur
    Kantenabschnitte, die über ``min_laenge_m`` hinweg innerhalb ``naehe_m`` und
    im Winkel unter ``max_winkel_grad`` zur Grenze verlaufen, gelten als Ersatz.
    Ohne sie genügte Nähe — und eine KREUZENDE Kante ist an der Kreuzung ebenso
    nah wie eine begleitende; an einer realen Karte kollabierte die Klassifikation
    dadurch auf 72 statt 2.580 m² Grünfläche. Mit ihr bleibt der Kollaps aus.

    WAS NOCH FEHLT: das Ersetzen selbst ist auf fragmentierten Karten weiterhin
    nicht flächentreu. An derselben Karte (54 Flächen, 148 m parallele Kanten
    gegen 131 m entfernte Grenze — eine gesunde Bilanz) sprang die Grünfläche
    dennoch um +1.764 m², weil beim Neubilden Flächen verschmelzen, deren
    Trennung nicht durch eine Kante ersetzt wird. Bis das geklärt ist, gilt:
    einsetzbar auf Karten mit wenigen grossen Flächen und wirklich
    grenzführenden Kanten, und das Ergebnis IMMER gegen die Flächenbilanz der
    Eingabe prüfen — die Lückenprüfung allein meldete hier sauber 0,00 m².

    Statt die Grenzen zu verschieben (was Lücken reisst, sobald zwei Nachbarn
    verschieden weit springen), werden die gemessenen Kanten ins Grenznetz
    aufgenommen und die Flächen daraus neu gebildet. Jede neue Teilfläche
    bekommt die Klasse, die sie flächenmässig beherrscht; anschliessend
    verschmelzen gleiche Nachbarn, wodurch alle Schnitte verschwinden, die keine
    Klassengrenze sind.

    Args:
        gdf: Klassenkarte (Polygone) in einem metrischen CRS.
        klasse_spalte: Spalte mit der Klasse.
        kanten: GeoDataFrame/GeoSeries mit Referenzlinien (Vermessungskanten).
        clip: Gebiet, das die Partition ausfüllt.
        naehe_m: Höchstabstand zwischen Kante und Klassengrenze.
        max_winkel_grad: Höchster Richtungsunterschied. Nähe allein genügt
            nicht — eine Kante, die eine Grenze KREUZT, ist an der Kreuzung
            ebenso nah wie eine, die sie begleitet.
        min_laenge_m: Ein paralleler Abschnitt muss mindestens so lang sein.
            Kürzere Zufallstreffer ersetzen keine Grenze.
        min_anteil: Mindest-Flächenanteil, den die Mehrheitsklasse in einer
            neuen Teilfläche haben muss. Darunter gilt sie als uneindeutig und
            behält die Klasse der grössten überlappenden Ausgangsfläche.

    Returns:
        ``(GeoDataFrame, info)`` mit ``kanten_genutzt_m``, ``luecke_m2`` und der
        Zahl der Flächen vorher/nachher.
    """
    import geopandas as gpd
    import numpy as np
    import pandas as pd
    from shapely.geometry import MultiLineString
    from shapely.ops import linemerge, polygonize, unary_union

    if gdf.empty:
        return gdf.copy(), {"kanten_genutzt_m": 0.0, "luecke_m2": 0.0}

    gebiet = clip if clip is not None else unary_union(list(gdf.geometry))
    alle_raender = unary_union([g.boundary for g in gdf.geometry])
    # Nur die INNEREN Grenzen zählen: der Aussenrand ist durch das Gebiet
    # gegeben und keine Klassengrenze. Zählt man ihn mit, gilt jede Kante, die
    # das Gebiet quert, als grenznah und zerschneidet Flächen ohne Grund.
    klassengrenzen = alle_raender.difference(gebiet.boundary.buffer(0.05))

    kanten_geom = (list(kanten.geometry) if hasattr(kanten, "geometry")
                   else list(kanten))
    # Verketten, bevor abgeschritten wird: nach dem Zuschnitt liegen die Kanten
    # als Mikrostücke vor, viele kürzer als die Schrittweite — abgeschritten
    # ergäbe das nirgends einen zusammenhängenden parallelen Abschnitt.
    roh = unary_union(kanten_geom).intersection(gebiet)
    stuecke_k = [k for k in (roh.geoms if hasattr(roh, "geoms") else [roh])
                 if k.geom_type == "LineString" and len(k.coords) > 1 and k.length > 0]
    kanten_linie = linemerge(stuecke_k) if stuecke_k else roh
    nah = _parallele_abschnitte(kanten_linie, klassengrenzen, naehe_m=naehe_m,
                                max_winkel_grad=max_winkel_grad,
                                min_laenge_m=min_laenge_m)
    genutzt = nah.length

    # Wo eine gemessene Kante einspringt, wird die alte Klassengrenze ENTFERNT —
    # sonst bleiben beide im Netz stehen, der Streifen dazwischen wird eine
    # eigene Fläche und behält per Mehrheit seine alte Klasse. Die Grenze wandert
    # dann gar nicht, obwohl die Kante aufgenommen wurde.
    # Nur der Grenzabschnitt neben einem parallelen Kantenstück fällt weg — mit
    # dem vollen Nähe-Puffer verschwand auch Grenze, die keine Kante ersetzt.
    # Flache Endkappe: ein runder Puffer ragt über die Enden des parallelen
    # Abschnitts hinaus und reisst dort ein Loch ins Netz, durch das die
    # Nachbarflächen verschmelzen — gemessen sprang die Grünfläche dadurch um
    # +1.764 m².
    ersetzt = (alle_raender.difference(nah.buffer(naehe_m * 1.05, cap_style=2))
               if not nah.is_empty else alle_raender)
    netz = unary_union([ersetzt, gebiet.boundary, nah])
    stuecke = [k for k in (netz.geoms if hasattr(netz, "geoms") else [netz])
               if k.geom_type == "LineString" and len(k.coords) > 1 and k.length > 0]
    neu = [f for f in polygonize(unary_union(stuecke)) if not f.is_empty and f.area > 0]
    if not neu:
        return gdf.copy(), {"kanten_genutzt_m": float(genutzt),
                            "luecke_m2": float(gebiet.area)}

    # Klasse über den FLÄCHENANTEIL, nicht über einen Punkt: eine neue Teilfläche
    # kann über zwei alte reichen, und dann entscheidet ein Punkt willkürlich.
    neu_gdf = gpd.GeoDataFrame(geometry=neu, crs=gdf.crs)
    neu_gdf["neu_id"] = range(len(neu_gdf))
    neu_gdf["neu_flaeche"] = neu_gdf.area
    schnitt = gpd.overlay(neu_gdf, gdf[[klasse_spalte, "geometry"]],
                          how="intersection", keep_geom_type=True)
    schnitt["anteil"] = schnitt.area
    gruppe = (schnitt.groupby(["neu_id", klasse_spalte])["anteil"].sum()
              .reset_index().sort_values("anteil", ascending=False))
    beste = gruppe.drop_duplicates("neu_id").set_index("neu_id")

    neu_gdf[klasse_spalte] = neu_gdf["neu_id"].map(beste[klasse_spalte])
    neu_gdf["_anteil"] = (neu_gdf["neu_id"].map(beste["anteil"])
                          / neu_gdf["neu_flaeche"])
    neu_gdf = neu_gdf[neu_gdf[klasse_spalte].notna()]

    aus = neu_gdf[[klasse_spalte, "geometry"]].dissolve(
        by=klasse_spalte, as_index=False).explode(index_parts=False)
    aus = aus[aus.geom_type.isin(["Polygon", "MultiPolygon"])]
    aus = aus[~aus.geometry.is_empty & (aus.area > 0)].reset_index(drop=True)

    vereinigt = unary_union(list(aus.geometry))
    info = {
        "kanten_genutzt_m": float(genutzt),
        "kanten_gesamt_m": float(unary_union(kanten_geom).intersection(gebiet).length),
        "luecke_m2": float(gebiet.difference(vereinigt).area),
        "flaechen_vorher": int(len(gdf)),
        "flaechen_nachher": int(len(aus)),
        "uneindeutig": int((neu_gdf["_anteil"] < min_anteil).sum()),
    }
    return aus, info


def absorb_small(
    gdf,
    klasse_spalte: str,
    *,
    min_flaeche_m2: float = 30.0,
    nur_klassen: list[str] | None = None,
):
    """Kleinstflächen dem Nachbarn zuschlagen, mit dem sie die längste Grenze teilen.

    Eine Klassifikation lässt Splitter zurück, die keine Aussage sind, sondern
    Rauschen — besonders in der Grauzone um eine Schwelle. Sie einzeln zu
    entscheiden ist Handarbeit ohne Erkenntnis; wo eine solche Fläche fast
    vollständig von einer Klasse umgeben ist, ist die Zuordnung Topologie und
    keine Interpretation.

    Der Nachbar wird über die LÄNGE der gemeinsamen Grenze bestimmt, nicht über
    die Entfernung: eine Fläche kann an einer Ecke an etwas anderes stossen als
    an ihrer ganzen langen Seite.

    Args:
        gdf: Klassenkarte.
        klasse_spalte: Spalte mit der Klasse.
        min_flaeche_m2: Flächen darunter werden aufgelöst.
        nur_klassen: Nur diese Klassen auflösen (etwa ``["unsicher"]``).
            ``None`` behandelt alle.

    Returns:
        ``(GeoDataFrame, info)`` mit ``aufgeloest``, ``aufgeloest_m2`` und
        ``uebrig`` — Kleinstflächen ohne Nachbarn bleiben stehen.
    """
    import geopandas as gpd
    import pandas as pd

    if gdf.empty:
        return gdf.copy(), {"aufgeloest": 0, "aufgeloest_m2": 0.0, "uebrig": 0}

    arbeit = gdf.reset_index(drop=True).copy()
    klein = arbeit[(arbeit.area < min_flaeche_m2)
                   & (arbeit[klasse_spalte].isin(nur_klassen) if nur_klassen else True)]
    if klein.empty:
        return arbeit, {"aufgeloest": 0, "aufgeloest_m2": 0.0, "uebrig": 0}

    gross = arbeit.drop(index=klein.index)
    aufgeloest, flaeche, uebrig = 0, 0.0, 0
    for i, zeile in klein.iterrows():
        beruehrt = gross[gross.geometry.intersects(zeile.geometry.buffer(0.01))]
        if beruehrt.empty:
            uebrig += 1
            continue
        laengen = beruehrt.geometry.apply(
            lambda g: g.buffer(0.01).intersection(zeile.geometry.buffer(0.01)).area)
        ziel = laengen.idxmax()
        arbeit.at[i, klasse_spalte] = gross.at[ziel, klasse_spalte]
        aufgeloest += 1
        flaeche += float(zeile.geometry.area)

    schluessel = [s for s in arbeit.columns if s != "geometry"]
    aus = arbeit.dissolve(by=schluessel, as_index=False).explode(index_parts=False)
    aus = aus[aus.geom_type.isin(["Polygon", "MultiPolygon"])].reset_index(drop=True)
    return aus, {"aufgeloest": aufgeloest, "aufgeloest_m2": flaeche, "uebrig": uebrig}
