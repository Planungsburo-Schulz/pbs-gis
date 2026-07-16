"""
Vector operations ported from Python-ACAD-Tools ``src/operations/*``.

Phase-4 R2: **re-implemented to the pbs_gis convention** — every operation
takes and returns a :class:`geopandas.GeoDataFrame` (or a plain geometry where
that is the natural unit), with no ``all_layers`` dict, ``project_settings``,
``operation`` config dict, or file I/O. File loading/saving stays in the
runner/template layer. Behaviour follows the reference algorithms; the coupling
is stripped.

Ops that pbs_gis already covers under the geopandas/shapely API (buffer,
plain dissolve, intersection, difference) are **not** re-added here — the
library fassung wins (R2). ``lagefaktor`` is deliberately **not** ported: the
reference is a ~520-line Ausgleich/GRZ scoring engine bound to a specific config
schema, parcel layers and Excel-protocol output, producing legally relevant
numbers; a faithful gdf-in/out API needs its own design pass and is reported as
a gap rather than best-guessed here.

Families:

* Polygon cleaning — :func:`remove_slivers_erosion`,
  :func:`remove_degenerate_spikes`, :func:`remove_protrusions`,
  :func:`simplify_slivers`. Distinct algorithms (erosion/dilation, vertex
  cleaning, convex-body protrusion removal, Douglas-Peucker) kept as separate
  functions rather than merged — they clean different defects.
* Line cleaning — :func:`clean_line` (consecutive-duplicate and short-segment
  removal on LineString/MultiLineString, optional Douglas-Peucker).
* Construction — :func:`connect_points`.
* Reconstruction — :func:`dissolve_by_majority_intersection`.
* Filtering — :func:`filter_by_column`, :func:`filter_by_intersection`.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
from shapely.geometry import (
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.ops import unary_union

# shapely buffer style codes (kept explicit so intent is legible).
_JOIN_MITRE = 2
_CAP_FLAT = 2


# ---------------------------------------------------------------------------
# Polygon cleaning family
# ---------------------------------------------------------------------------


def remove_slivers_erosion(
    gdf: gpd.GeoDataFrame,
    erosion_distance: float = 0.1,
    *,
    mitre_limit: float = 5.0,
) -> gpd.GeoDataFrame:
    """Remove thin slivers via erosion then dilation (mitre joins keep corners).

    Each geometry is eroded by *erosion_distance* (a negative buffer) — which
    deletes features/parts thinner than ``2 * erosion_distance`` — then dilated
    back by the same amount. Mitre joins preserve sharp corners. Attributes are
    kept; geometries that vanish under erosion are dropped.

    Args:
        gdf: Polygon GeoDataFrame (projected CRS with metric units).
        erosion_distance: Half the minimum feature thickness to keep.
        mitre_limit: Mitre limit for corner preservation.

    Returns:
        Cleaned GeoDataFrame (same columns, CRS preserved).
    """
    if gdf.empty:
        return gdf.copy()
    kept = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        eroded = geom.buffer(-erosion_distance, join_style=_JOIN_MITRE,
                             cap_style=_CAP_FLAT, mitre_limit=mitre_limit)
        if eroded.is_empty:
            continue
        restored = eroded.buffer(erosion_distance, join_style=_JOIN_MITRE,
                                 cap_style=_CAP_FLAT, mitre_limit=mitre_limit)
        if restored.is_empty:
            continue
        new_row = row.copy()
        new_row.geometry = restored
        kept.append(new_row)
    if not kept:
        return gpd.GeoDataFrame(columns=gdf.columns, crs=gdf.crs)
    return gpd.GeoDataFrame(kept, crs=gdf.crs).reset_index(drop=True)


def simplify_slivers(
    gdf: gpd.GeoDataFrame,
    tolerance: float = 0.1,
    *,
    preserve_topology: bool = True,
    min_area_threshold: float = 10.0,
) -> gpd.GeoDataFrame:
    """Conservative sliver removal via Douglas-Peucker simplification.

    Simplifies each geometry with *tolerance* and drops results whose area falls
    below *min_area_threshold*. On simplification error the original geometry is
    kept.

    Args:
        gdf: Polygon GeoDataFrame.
        tolerance: Simplification tolerance (CRS units).
        preserve_topology: Passed to shapely ``simplify``.
        min_area_threshold: Drop features smaller than this after simplifying.

    Returns:
        Simplified GeoDataFrame.
    """
    if gdf.empty:
        return gdf.copy()
    kept = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        try:
            simplified = geom.simplify(tolerance, preserve_topology=preserve_topology)
        except Exception:
            kept.append(row.copy())
            continue
        if (simplified is not None and not simplified.is_empty
                and simplified.is_valid and simplified.area >= min_area_threshold):
            new_row = row.copy()
            new_row.geometry = simplified
            kept.append(new_row)
    if not kept:
        return gpd.GeoDataFrame(columns=gdf.columns, crs=gdf.crs)
    return gpd.GeoDataFrame(kept, crs=gdf.crs).reset_index(drop=True)


def remove_degenerate_spikes(
    gdf: gpd.GeoDataFrame,
    *,
    tolerance: float = 0.01,
    simplify_tolerance: float = 0.05,
    min_spike_length: float = 0.1,
) -> gpd.GeoDataFrame:
    """Remove zero-width spikes (edges that go out and return on the same path).

    Cleans consecutive-duplicate and collinear vertices, then repairs topology,
    merges near-duplicate vertices, simplifies, and strips thin protrusions via
    a small erosion/dilation. Follows the reference multi-step approach. On a
    per-feature failure the original geometry is kept.

    Args:
        gdf: Polygon GeoDataFrame.
        tolerance: Vertex-merge / collinearity threshold (CRS units).
        simplify_tolerance: Douglas-Peucker tolerance for redundant points.
        min_spike_length: Minimum spike length targeted by the erosion pass.

    Returns:
        Cleaned GeoDataFrame.
    """
    if gdf.empty:
        return gdf.copy()
    kept = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        try:
            if isinstance(geom, Polygon):
                cleaned = _despike_polygon(geom, tolerance, simplify_tolerance, min_spike_length)
            elif isinstance(geom, MultiPolygon):
                parts = [_despike_polygon(p, tolerance, simplify_tolerance, min_spike_length)
                         for p in geom.geoms]
                parts = [p for p in parts if p is not None and not p.is_empty]
                cleaned = MultiPolygon(parts) if parts else None
            else:
                cleaned = geom
        except Exception:
            cleaned = geom
        if cleaned is not None and not cleaned.is_empty and cleaned.is_valid:
            new_row = row.copy()
            new_row.geometry = cleaned
            kept.append(new_row)
    if not kept:
        return gpd.GeoDataFrame(columns=gdf.columns, crs=gdf.crs)
    return gpd.GeoDataFrame(kept, crs=gdf.crs).reset_index(drop=True)


def remove_protrusions(
    gdf: gpd.GeoDataFrame,
    *,
    protrusion_threshold: float = 2.0,
    min_protrusion_length: float = 3.0,
    buffer_distance: float = 1.0,
) -> gpd.GeoDataFrame:
    """Remove thin protrusions while preserving the main polygon body.

    For each polygon the "main body" is found by erosion then dilation; areas
    sticking out beyond it that are thin (high aspect ratio) and long enough are
    subtracted. A protrusion removal that would cost more than 50% of the area
    is skipped (original kept). Follows the reference conservative approach.

    Args:
        gdf: Polygon GeoDataFrame.
        protrusion_threshold: Reserved for API parity with the reference
            (main-body detection is driven by *buffer_distance*).
        min_protrusion_length: Minimum protrusion length to remove.
        buffer_distance: Erosion/dilation distance for main-body detection.

    Returns:
        Cleaned GeoDataFrame.
    """
    if gdf.empty:
        return gdf.copy()
    kept = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        if isinstance(geom, Polygon):
            cleaned = _deprotrude_polygon(geom, min_protrusion_length, buffer_distance)
        elif isinstance(geom, MultiPolygon):
            parts = [_deprotrude_polygon(p, min_protrusion_length, buffer_distance)
                     for p in geom.geoms]
            parts = [p for p in parts if p is not None and not p.is_empty]
            cleaned = MultiPolygon(parts) if parts else None
        else:
            cleaned = geom
        if cleaned is not None and not cleaned.is_empty:
            new_row = row.copy()
            new_row.geometry = cleaned
            kept.append(new_row)
    if not kept:
        return gpd.GeoDataFrame(columns=gdf.columns, crs=gdf.crs)
    return gpd.GeoDataFrame(kept, crs=gdf.crs).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Line cleaning family
# ---------------------------------------------------------------------------


def clean_line(
    gdf: gpd.GeoDataFrame,
    *,
    tolerance: float = 0.01,
    min_segment_length: float = 0.5,
    simplify_tolerance: float = 0.0,
) -> gpd.GeoDataFrame:
    """Clean LineString geometries: drop near-duplicate vertices and short segments.

    Ported from the reference ``cleanLine`` operation. Each line is cleaned in
    three passes:

    1. **Near-duplicate vertices** closer than *tolerance* to the previously
       kept vertex are removed.
    2. **Short segments** are removed: walking from the start, a vertex is
       dropped when its distance to the last kept vertex is below
       *min_segment_length*. The final vertex is always kept, so endpoints —
       and the closing vertex of a closed ring — survive.
    3. **Optional** Douglas-Peucker simplification when *simplify_tolerance*
       is greater than zero (disabled by default).

    ``LineString`` and ``MultiLineString`` are cleaned part-by-part; a
    non-line geometry is passed through unchanged. Lines that collapse to
    fewer than two vertices, or become empty/invalid, are dropped. On a
    per-feature error the original geometry is kept. Attributes and CRS are
    preserved.

    Args:
        gdf: GeoDataFrame of (Multi)LineString geometries (projected CRS with
            metric units for the metric thresholds to make sense).
        tolerance: Vertex-merge threshold (CRS units); vertices closer than
            this to their predecessor are treated as duplicates.
        min_segment_length: Segments shorter than this are removed.
        simplify_tolerance: Douglas-Peucker tolerance; ``0`` disables it.

    Returns:
        Cleaned GeoDataFrame (same columns, CRS preserved).
    """
    if gdf.empty:
        return gdf.copy()
    kept = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        try:
            cleaned = _clean_line_geometry(
                geom, tolerance, min_segment_length, simplify_tolerance
            )
        except Exception:
            cleaned = geom
        if cleaned is not None and not cleaned.is_empty and cleaned.is_valid:
            new_row = row.copy()
            new_row.geometry = cleaned
            kept.append(new_row)
    if not kept:
        return gpd.GeoDataFrame(columns=gdf.columns, crs=gdf.crs)
    return gpd.GeoDataFrame(kept, crs=gdf.crs).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def connect_points(
    gdf: gpd.GeoDataFrame,
    *,
    max_distance: float | None = None,
) -> gpd.GeoDataFrame:
    """Connect points into a nearest-neighbour path (or one path per cluster).

    Collects all Point/MultiPoint vertices and links them by a greedy
    nearest-neighbour walk. With *max_distance* the points are first clustered
    (single-link within *max_distance*) and each multi-point cluster becomes its
    own LineString; without it a single path connects all points.

    Args:
        gdf: Point/MultiPoint GeoDataFrame.
        max_distance: Optional single-link clustering distance (CRS units).

    Returns:
        GeoDataFrame of LineString(s) in the input CRS (empty if < 2 points).
    """
    pts: list[tuple[float, float]] = []
    for geom in gdf.geometry:
        if isinstance(geom, Point):
            pts.append((geom.x, geom.y))
        elif isinstance(geom, MultiPoint):
            pts.extend((p.x, p.y) for p in geom.geoms)
    if len(pts) < 2:
        return gpd.GeoDataFrame(geometry=[], crs=gdf.crs)

    arr = np.array(pts)
    if max_distance is None:
        line = LineString(_nn_order(arr, list(range(len(arr)))))
        return gpd.GeoDataFrame(geometry=[line], crs=gdf.crs)

    lines = []
    for group in _cluster_indices(arr, max_distance):
        if len(group) > 1:
            lines.append(LineString(_nn_order(arr, group)))
    return gpd.GeoDataFrame(geometry=lines, crs=gdf.crs)


# ---------------------------------------------------------------------------
# Reconstruction
# ---------------------------------------------------------------------------


def dissolve_by_majority_intersection(
    source_gdf: gpd.GeoDataFrame,
    reference_gdf: gpd.GeoDataFrame,
    *,
    transfer_attributes: list[str] | tuple[str, ...] = (),
    threshold: float = 50.0,
) -> gpd.GeoDataFrame:
    """Reconstruct coarse polygons from fine ones by majority overlap.

    For each *reference* polygon, all *source* polygons with at least
    *threshold* percent of their own area inside it are dissolved into one
    high-detail polygon; requested reference attributes are transferred. Typical
    use: rebuild Gemeinde boundaries from Gemarkung polygons.

    Args:
        source_gdf: High-detail source polygons.
        reference_gdf: Low-detail reference polygons.
        transfer_attributes: Reference columns to copy onto each result.
        threshold: Minimum overlap percentage (of source area) to match.

    Returns:
        GeoDataFrame of reconstructed polygons in the source CRS.

    Raises:
        ValueError: if either input lacks geometry.
    """
    if source_gdf.empty or reference_gdf.empty:
        return gpd.GeoDataFrame(geometry=[], crs=source_gdf.crs)

    if source_gdf.crs is not None and reference_gdf.crs is not None \
            and source_gdf.crs != reference_gdf.crs:
        reference_gdf = reference_gdf.to_crs(source_gdf.crs)

    features = []
    for _, ref in reference_gdf.iterrows():
        ref_geom = ref.geometry
        if ref_geom is None or ref_geom.is_empty:
            continue
        matched = []
        for idx, src in source_gdf.iterrows():
            src_geom = src.geometry
            if src_geom is None or src_geom.is_empty or not src_geom.intersects(ref_geom):
                continue
            src_area = src_geom.area
            if src_area <= 0:
                continue
            overlap_pct = src_geom.intersection(ref_geom).area / src_area * 100.0
            if overlap_pct >= threshold:
                matched.append(idx)
        if not matched:
            continue
        dissolved = unary_union(source_gdf.loc[matched].geometry.tolist())
        attrs = {a: ref[a] for a in transfer_attributes if a in ref.index}
        attrs["geometry"] = dissolved
        features.append(attrs)

    if not features:
        return gpd.GeoDataFrame(geometry=[], crs=source_gdf.crs)
    return gpd.GeoDataFrame(features, crs=source_gdf.crs)


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

_OPERATORS = frozenset({"eq", "neq", "gt", "gte", "lt", "lte", "in", "contains"})


def filter_by_column(
    gdf: gpd.GeoDataFrame,
    column: str,
    value,
    *,
    operator: str = "eq",
    case_sensitive: bool = True,
) -> gpd.GeoDataFrame:
    """Filter features by a column value.

    Args:
        gdf: Input GeoDataFrame.
        column: Column to filter on (must exist).
        value: Comparison value (a list for ``in``).
        operator: One of ``eq, neq, gt, gte, lt, lte, in, contains``.
        case_sensitive: For string ``contains`` only.

    Returns:
        Filtered GeoDataFrame (copy, index reset).

    Raises:
        KeyError: if *column* is absent.
        ValueError: for an unknown operator.
    """
    if column not in gdf.columns:
        raise KeyError(f"column {column!r} not in GeoDataFrame")
    if operator not in _OPERATORS:
        raise ValueError(f"unknown operator {operator!r}; allowed: {sorted(_OPERATORS)}")

    col = gdf[column]
    if operator == "eq":
        mask = col == value
    elif operator == "neq":
        mask = col != value
    elif operator == "gt":
        mask = col > value
    elif operator == "gte":
        mask = col >= value
    elif operator == "lt":
        mask = col < value
    elif operator == "lte":
        mask = col <= value
    elif operator == "in":
        mask = col.isin(value if isinstance(value, list) else [value])
    else:  # contains
        if not case_sensitive and isinstance(value, str):
            mask = col.str.lower().str.contains(value.lower())
        else:
            mask = col.str.contains(value)
    return gdf[mask].reset_index(drop=True)


def filter_by_intersection(
    gdf: gpd.GeoDataFrame,
    other_gdf: gpd.GeoDataFrame,
    *,
    predicate: str = "intersects",
    buffer: float = 0.0,
) -> gpd.GeoDataFrame:
    """Keep features that spatially relate to *other_gdf*.

    Features are kept when their geometry satisfies *predicate* against the
    (optionally buffered) union of *other_gdf*. A small negative *buffer*
    reproduces the reference's edge-on-edge tolerance.

    Args:
        gdf: Features to filter.
        other_gdf: Filter geometry (union taken).
        predicate: Shapely predicate: ``intersects``, ``within``, ``contains``,
            ``crosses``, ``touches``, ``overlaps``.
        buffer: Buffer applied to the filter union before testing (CRS units;
            may be negative).

    Returns:
        Filtered GeoDataFrame (copy, index reset).

    Raises:
        ValueError: for an unknown predicate.
    """
    predicates = {"intersects", "within", "contains", "crosses", "touches", "overlaps"}
    if predicate not in predicates:
        raise ValueError(f"unknown predicate {predicate!r}; allowed: {sorted(predicates)}")
    if gdf.empty or other_gdf.empty:
        return gpd.GeoDataFrame(columns=gdf.columns, crs=gdf.crs)

    filt = unary_union(other_gdf.geometry.tolist())
    if buffer:
        filt = filt.buffer(buffer)
    test = getattr(gdf.geometry, predicate)
    return gdf[test(filt)].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Internal helpers (polygon cleaning + point ordering)
# ---------------------------------------------------------------------------


def _clean_line_geometry(geom, tolerance: float, min_segment_length: float,
                         simplify_tolerance: float):
    """Clean one (Multi)LineString; pass non-line geometries through."""
    if isinstance(geom, LineString):
        return _clean_one_line(geom, tolerance, min_segment_length, simplify_tolerance)
    if isinstance(geom, MultiLineString):
        parts = [_clean_one_line(g, tolerance, min_segment_length, simplify_tolerance)
                 for g in geom.geoms]
        parts = [p for p in parts if p is not None and not p.is_empty]
        return MultiLineString(parts) if parts else None
    return geom


def _clean_one_line(line: LineString, tolerance: float, min_segment_length: float,
                    simplify_tolerance: float) -> LineString | None:
    if not isinstance(line, LineString) or line.is_empty:
        return line
    coords = list(line.coords)
    if len(coords) < 2:
        return line
    coords = _drop_consecutive_close(coords, tolerance)
    if len(coords) < 2:
        return None
    coords = _drop_short_segments(coords, min_segment_length)
    if len(coords) < 2:
        return None
    cleaned = LineString(coords)
    if simplify_tolerance > 0:
        simplified = cleaned.simplify(simplify_tolerance, preserve_topology=False)
        if not simplified.is_empty and simplified.is_valid:
            cleaned = simplified
    return cleaned


def _drop_consecutive_close(coords: list, tolerance: float) -> list:
    """Drop vertices within *tolerance* of the previously kept vertex."""
    out = [coords[0]]
    for c in coords[1:]:
        if Point(c).distance(Point(out[-1])) > tolerance:
            out.append(c)
    return out


def _drop_short_segments(coords: list, min_segment_length: float) -> list:
    """Drop vertices that would form a segment shorter than *min_segment_length*.

    The first and last vertices are always kept (endpoints / ring closure). If
    everything between collapses, fall back to the two endpoints.
    """
    if len(coords) < 3 or min_segment_length <= 0:
        return coords
    out = [coords[0]]
    last = len(coords) - 1
    for i in range(1, len(coords)):
        if i == last:
            out.append(coords[i])
            continue
        if Point(coords[i]).distance(Point(out[-1])) < min_segment_length:
            continue
        out.append(coords[i])
    if len(out) < 2:
        return [coords[0], coords[-1]]
    return out


def _nn_order(arr: np.ndarray, indices: list[int]) -> list[tuple[float, float]]:
    """Greedy nearest-neighbour ordering of ``arr[indices]`` from the first point."""
    from scipy.spatial.distance import cdist

    local = arr[indices]
    path = [0]
    remaining = list(range(1, len(local)))
    while remaining:
        cur = local[path[-1]].reshape(1, -1)
        dists = cdist(cur, local[remaining])[0]
        nxt = remaining[int(np.argmin(dists))]
        path.append(nxt)
        remaining.remove(nxt)
    return [tuple(local[i]) for i in path]


def _cluster_indices(arr: np.ndarray, max_distance: float) -> list[list[int]]:
    """Single-link clustering: indices grouped if within *max_distance* (chained)."""
    from scipy.spatial.distance import cdist

    remaining = set(range(len(arr)))
    groups: list[list[int]] = []
    while remaining:
        group = [remaining.pop()]
        changed = True
        while changed:
            changed = False
            current = arr[group]
            to_add = set()
            for idx in remaining:
                if np.min(cdist(arr[idx].reshape(1, -1), current)) <= max_distance:
                    to_add.add(idx)
            if to_add:
                group.extend(to_add)
                remaining -= to_add
                changed = True
        groups.append(group)
    return groups


def _ring_without_consecutive_dups(coords: list) -> list:
    out = [coords[0]]
    for c in coords[1:]:
        if c != out[-1]:
            out.append(c)
    if len(out) >= 3 and out[0] != out[-1]:
        out.append(out[0])
    return out


def _ring_without_collinear(coords: list, tolerance: float) -> list:
    out = [coords[0]]
    for i in range(1, len(coords) - 1):
        prev = np.array(out[-1])
        cur = np.array(coords[i])
        nxt = np.array(coords[i + 1])
        v1 = cur - prev
        v2 = nxt - cur
        n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
        if n1 > 0 and n2 > 0:
            cross = abs(v1[0] * v2[1] - v1[1] * v2[0]) / (n1 + n2)
            if cross < tolerance:
                continue
        out.append(tuple(cur))
    if len(out) >= 3:
        out.append(out[0])
    return out


def _merge_close_ring(coords: list, tolerance: float) -> list:
    out = [coords[0]]
    for c in coords[1:]:
        if Point(c).distance(Point(out[-1])) > tolerance:
            out.append(c)
    if len(out) >= 3 and out[0] != out[-1]:
        out.append(out[0])
    return out


def _clean_ring(coords: list, tolerance: float) -> list | None:
    """Consecutive-dup + collinear cleaning of one ring; None if too few points."""
    coords = _ring_without_consecutive_dups(list(coords))
    if len(coords) < 4:
        return None
    coords = _ring_without_collinear(coords, tolerance)
    if len(coords) < 4:
        return None
    return coords


def _despike_polygon(polygon: Polygon, tolerance: float,
                     simplify_tolerance: float, min_spike_length: float) -> Polygon | None:
    if not isinstance(polygon, Polygon) or polygon.is_empty:
        return polygon
    exterior = _clean_ring(list(polygon.exterior.coords), tolerance)
    if exterior is None:
        return polygon
    interiors = []
    for interior in polygon.interiors:
        ring = _clean_ring(list(interior.coords), tolerance)
        if ring is not None:
            interiors.append(ring)
    cleaned = Polygon(exterior, interiors)

    cleaned = cleaned.buffer(0)
    if cleaned.is_empty or not cleaned.is_valid:
        return polygon
    if isinstance(cleaned, MultiPolygon):
        cleaned = max(cleaned.geoms, key=lambda p: p.area)

    ext = _merge_close_ring(list(cleaned.exterior.coords), tolerance)
    if len(ext) < 4:
        return polygon
    ints = []
    for interior in cleaned.interiors:
        ring = _merge_close_ring(list(interior.coords), tolerance)
        if len(ring) >= 4:
            ints.append(ring)
    cleaned = Polygon(ext, ints)

    cleaned = cleaned.simplify(simplify_tolerance, preserve_topology=True)
    if cleaned.is_empty or not cleaned.is_valid:
        return polygon

    buffer_amount = min(min_spike_length / 2, 0.1)
    eroded = cleaned.buffer(-buffer_amount)
    if not eroded.is_empty:
        restored = eroded.buffer(buffer_amount)
        if restored.is_valid and not restored.is_empty:
            if isinstance(restored, MultiPolygon):
                restored = max(restored.geoms, key=lambda p: p.area)
            cleaned = restored

    cleaned = cleaned.buffer(0)
    if isinstance(cleaned, MultiPolygon):
        cleaned = max(cleaned.geoms, key=lambda p: p.area)
    if cleaned.is_empty or not cleaned.is_valid:
        return polygon
    return cleaned


def _deprotrude_polygon(polygon: Polygon, min_protrusion_length: float,
                        buffer_distance: float) -> Polygon | None:
    if not isinstance(polygon, Polygon) or polygon.is_empty:
        return polygon
    try:
        main_body = polygon.buffer(-buffer_distance)
        if main_body.is_empty:
            return polygon
        main_body = main_body.buffer(buffer_distance)

        protrusions = polygon.difference(main_body)
        if protrusions.is_empty:
            return polygon

        significant = []
        if isinstance(protrusions, MultiPolygon):
            significant = [p for p in protrusions.geoms
                           if _is_significant_protrusion(p, min_protrusion_length)]
        elif isinstance(protrusions, Polygon):
            if _is_significant_protrusion(protrusions, min_protrusion_length):
                significant = [protrusions]
        if not significant:
            return polygon

        cleaned = polygon.difference(unary_union(significant))
        if cleaned.is_empty or not cleaned.is_valid:
            return polygon
        if isinstance(cleaned, MultiPolygon):
            cleaned = max(cleaned.geoms, key=lambda p: p.area)
        elif not isinstance(cleaned, Polygon):
            return polygon

        if polygon.area > 0 and (polygon.area - cleaned.area) / polygon.area > 0.5:
            return polygon
        return cleaned
    except Exception:
        return polygon


def _is_significant_protrusion(protrusion, min_length: float) -> bool:
    if not isinstance(protrusion, Polygon) or protrusion.is_empty or protrusion.area <= 0:
        return False
    minx, miny, maxx, maxy = protrusion.bounds
    width, height = maxx - minx, maxy - miny
    if width <= 0 or height <= 0:
        return False
    aspect = max(width, height) / min(width, height)
    max_dim = max(width, height)
    if protrusion.area < 1.0:
        return True
    if protrusion.area < 10.0 and aspect > 1.5:
        return True
    return aspect > 3.0 and max_dim >= min_length
