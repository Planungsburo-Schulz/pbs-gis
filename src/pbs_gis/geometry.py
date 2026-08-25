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
