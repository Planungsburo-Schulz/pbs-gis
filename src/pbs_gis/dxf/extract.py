"""
Extract geometry from DXF files into GeoDataFrames.

Handles all common entity types (LINE, LWPOLYLINE, POLYLINE, ARC, CIRCLE,
ELLIPSE, HATCH, POINT, TEXT, MTEXT) with proper bulge/arc interpolation
and recursive block (INSERT) processing with affine transforms.

Consolidates best approaches from multiple project scripts:
- Schwerin: ezdxf.math helpers for bulge, clean ring closure
- Winnert extract_layers_to_shp_fixed: adaptive arc segmentation, full entity coverage
- Winnert extract_everything: entity-to-LWPOLYLINE, HATCH edge handling
- K36 extract_circles_pappeln: circle-specific extraction with transforms
"""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf.math import bulge_center, bulge_radius
import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, MultiPolygon, Point, Polygon

from pbs_gis.dxf.read import read_cad


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _p2(p) -> tuple[float, float]:
    """Extract 2D coords from any ezdxf point-like object."""
    return (float(p[0]), float(p[1]))


def _arc_segment_count(theta: float, min_segments: int = 16) -> int:
    """Adaptive segment count based on arc angle. More segments for larger arcs."""
    return max(min_segments, int(abs(theta) * 32 / math.pi))


def interpolate_bulge_arc(
    start: tuple[float, float],
    end: tuple[float, float],
    bulge: float,
    num_points: int = 0,
) -> list[tuple[float, float]]:
    """
    Compute intermediate arc points between two vertices connected by a DXF bulge.

    Uses ezdxf.math.bulge_center/bulge_radius for robust center/radius calculation,
    then interpolates intermediate points along the arc.

    Args:
        start: Start vertex (x, y).
        end: End vertex (x, y).
        bulge: DXF bulge value. 0 = straight, positive = CCW, negative = CW.
        num_points: Number of intermediate points. 0 = auto-scale with arc size.

    Returns:
        List of intermediate (x, y) points (excludes start and end vertices).
        Empty list if bulge is effectively zero.
    """
    if abs(bulge) < 1e-12:
        return []

    try:
        center = bulge_center(start, end, bulge)
        radius = bulge_radius(start, end, bulge)
    except (ZeroDivisionError, ValueError):
        return []

    cx, cy = float(center.x), float(center.y)
    sa = math.atan2(start[1] - cy, start[0] - cx)
    span = 4.0 * math.atan(bulge)  # signed: positive=CCW, negative=CW

    if num_points <= 0:
        num_points = _arc_segment_count(span)

    return [
        (cx + radius * math.cos(sa + (k / num_points) * span),
         cy + radius * math.sin(sa + (k / num_points) * span))
        for k in range(1, num_points)
    ]


def lwpolyline_to_coords(
    entity,
    arc_points: int = 0,
) -> list[tuple[float, float]]:
    """
    Extract coordinate list from an LWPOLYLINE or 2D POLYLINE, interpolating bulge arcs.

    Args:
        entity: An ezdxf LWPOLYLINE or POLYLINE entity.
        arc_points: Points per arc segment. 0 = adaptive.

    Returns:
        List of (x, y) tuples. For closed entities, the last point equals the first.
    """
    entity_type = entity.dxftype()

    if entity_type == "LWPOLYLINE":
        raw = list(entity.get_points("xyb"))
        vertices = [_p2((p[0], p[1])) for p in raw]
        bulges = [float(p[2]) if len(p) > 2 else 0.0 for p in raw]
        is_closed = entity.closed
    elif entity_type == "POLYLINE":
        verts = list(entity.vertices)
        vertices = [_p2(v.dxf.location) for v in verts]
        bulges = [float(v.dxf.get("bulge", 0)) for v in verts]
        is_closed = entity.is_closed
    else:
        return []

    n = len(vertices)
    if n < 2:
        return list(vertices)

    out: list[tuple[float, float]] = [vertices[0]]
    loop_end = n if is_closed else n - 1

    for i in range(loop_end):
        s = vertices[i]
        e = vertices[(i + 1) % n]
        b = bulges[i]
        arc_pts = interpolate_bulge_arc(s, e, b, arc_points)
        out.extend(arc_pts)
        out.append(e)

    # Ensure exact closure for closed entities
    if is_closed and len(out) >= 3:
        if abs(out[-1][0] - out[0][0]) > 1e-9 or abs(out[-1][1] - out[0][1]) > 1e-9:
            out.append(out[0])
        else:
            out[-1] = out[0]  # exact match

    return out


def _arc_to_coords(entity, min_segments: int = 16) -> list[tuple[float, float]]:
    """Convert a DXF ARC entity to a list of points."""
    center = entity.dxf.center
    radius = entity.dxf.radius
    start_deg = entity.dxf.start_angle
    end_deg = entity.dxf.end_angle

    angle_range = end_deg - start_deg
    if angle_range < 0:
        angle_range += 360

    num_segments = max(min_segments, int(angle_range / 360 * 64))
    return [
        (center[0] + radius * math.cos(math.radians(start_deg + angle_range * i / num_segments)),
         center[1] + radius * math.sin(math.radians(start_deg + angle_range * i / num_segments)))
        for i in range(num_segments + 1)
    ]


def _circle_to_coords(entity, num_segments: int = 64) -> list[tuple[float, float]]:
    """Convert a DXF CIRCLE entity to a closed polygon coordinate list."""
    cx, cy = entity.dxf.center[0], entity.dxf.center[1]
    r = entity.dxf.radius
    pts = [
        (cx + r * math.cos(2 * math.pi * i / num_segments),
         cy + r * math.sin(2 * math.pi * i / num_segments))
        for i in range(num_segments)
    ]
    pts.append(pts[0])
    return pts


def _ellipse_to_coords(entity, num_segments: int = 64) -> list[tuple[float, float]]:
    """Convert a DXF ELLIPSE entity to a coordinate list."""
    center = entity.dxf.center
    major_axis = entity.dxf.major_axis
    ratio = entity.dxf.ratio
    start_param = entity.dxf.get("start_param", 0)
    end_param = entity.dxf.get("end_param", 2 * math.pi)

    major_len = math.sqrt(major_axis[0] ** 2 + major_axis[1] ** 2)
    major_angle = math.atan2(major_axis[1], major_axis[0])
    minor_len = major_len * ratio
    cos_a, sin_a = math.cos(major_angle), math.sin(major_angle)

    pts = []
    for i in range(num_segments):
        param = start_param + (end_param - start_param) * i / num_segments
        xl = major_len * math.cos(param)
        yl = minor_len * math.sin(param)
        pts.append((center[0] + xl * cos_a - yl * sin_a,
                     center[1] + xl * sin_a + yl * cos_a))

    if (end_param - start_param) >= (2 * math.pi - 0.01):
        pts.append(pts[0])
    return pts


def _hatch_to_coords(entity, tolerance: float = 0.1) -> list[tuple[float, float]] | None:
    """Extract first boundary path from a HATCH entity as coordinates.

    Uses ezdxf's path system which correctly handles all edge types
    (LineEdge, ArcEdge, EllipseEdge, SplineEdge) with proper arc
    direction and sweep calculations.
    """
    from ezdxf import path as ezdxf_path

    try:
        paths = list(ezdxf_path.from_hatch(entity))
    except Exception:
        paths = []

    if not paths:
        # Fallback: try direct vertex extraction
        for bpath in entity.paths:
            if hasattr(bpath, "vertices") and len(bpath.vertices) >= 3:
                return [_p2(v) for v in bpath.vertices]
        return None

    # Use the first (outermost) path
    pts = [(v.x, v.y) for v in paths[0].flattening(tolerance)]
    return pts if len(pts) >= 3 else None


# ---------------------------------------------------------------------------
# 3DSOLID / ACIS extraction
# ---------------------------------------------------------------------------

def _acis_get_transform(builder) -> np.ndarray:
    """Extract 4x4 world transform from ACIS SAB builder.

    Handles two SAB encoding formats:
    - Tag 20 (POSITION_VEC): separate vectors for rotation columns + translation
    - Tag 18 (string): space-separated "rx ry rz tx ty tz scale flags..."
    """
    for ent in builder.entities:
        if ent.name == "transform":
            # Try tag 20 (vector format) first
            vecs = [tok.value for tok in ent.data if tok.tag == 20]
            if len(vecs) >= 4:
                rx, ry, rz, t = vecs[0], vecs[1], vecs[2], vecs[3]
                return np.array([
                    [rx[0], ry[0], rz[0], t[0]],
                    [rx[1], ry[1], rz[1], t[1]],
                    [rx[2], ry[2], rz[2], t[2]],
                    [0,     0,     0,     1    ],
                ])

            # Try tag 18 (string format): "r00 r01 r02 r10 r11 r12 r20 r21 r22 tx ty tz ..."
            for tok in ent.data:
                if tok.tag == 18 and isinstance(tok.value, str):
                    parts = tok.value.split()
                    nums = []
                    for p in parts:
                        try:
                            nums.append(float(p))
                        except ValueError:
                            break
                    if len(nums) >= 12:
                        return np.array([
                            [nums[0], nums[3], nums[6], nums[9]],
                            [nums[1], nums[4], nums[7], nums[10]],
                            [nums[2], nums[5], nums[8], nums[11]],
                            [0,       0,       0,       1       ],
                        ])
    return np.eye(4)


def _acis_get_vertices(builder) -> list[tuple[float, float, float]]:
    """Extract vertex positions (LOCATION_VEC, tag 19) from ACIS point entities."""
    pts = []
    for ent in builder.entities:
        if ent.name == "point":
            for tok in ent.data:
                if tok.tag == 19:
                    pts.append(tok.value)
    return pts


def _solid3d_to_world_points(entity) -> list[tuple[float, float, float]]:
    """Convert a 3DSOLID entity to world-space 3D points.

    Parses the ACIS SAB body, extracts vertices, and applies the
    embedded transform matrix to get model-space coordinates.
    """
    from ezdxf.acis import sab as acis_sab
    builder = acis_sab.parse_sab(entity.sab)
    M = _acis_get_transform(builder)
    local_pts = _acis_get_vertices(builder)

    world_pts = []
    for p in local_pts:
        h = np.array([p[0], p[1], p[2], 1.0])
        w = M @ h
        world_pts.append((float(w[0]), float(w[1]), float(w[2])))
    return world_pts


def solid3d_to_circle(
    entity,
    diameter: float,
    *,
    vertex_index: int | str = -1,
    resolution: int = 64,
) -> tuple[Point, Polygon]:
    """Convert a cylindrical 3DSOLID to a 2D center point and circle polygon.

    For cylindrical/conical solids with 2 ACIS vertices (top/bottom of axis),
    projects to XY as the circle center.

    Args:
        entity: ezdxf 3DSOLID entity.
        diameter: Circle diameter in map units (meters).
        vertex_index: Which ACIS vertex to use as center.
                      "midpoint" = XY midpoint of all vertices.
                      int (default -1) = specific vertex index. Prefer this
                      when the drawing has a visible center cross — ACIS
                      vertices may not be symmetrically placed around the
                      geometric center, so midpoint can be off.
        resolution: Number of segments for the circle polygon.

    Returns:
        (center_point, circle_polygon) — both as Shapely geometries.
    """
    world_pts = _solid3d_to_world_points(entity)
    if not world_pts:
        raise ValueError("No ACIS vertices found in 3DSOLID")

    if vertex_index == "midpoint":
        cx = sum(p[0] for p in world_pts) / len(world_pts)
        cy = sum(p[1] for p in world_pts) / len(world_pts)
    else:
        pt = world_pts[vertex_index]
        cx, cy = pt[0], pt[1]

    center = Point(cx, cy)
    circle = center.buffer(diameter / 2.0, resolution=resolution)
    return center, circle


def _solid3d_to_2d_polygon(entity, *, bottom_face: bool = False) -> Polygon | None:
    """Convert a 3DSOLID to a 2D polygon by projecting vertices to XY plane.

    Note: does NOT apply GEODATA offset. Use extract_3dsolids() for
    georeferenced output, or apply offset manually.
    """
    world_pts_3d = _solid3d_to_world_points(entity)
    return _solid3d_to_2d_polygon_from_points(world_pts_3d, bottom_face=bottom_face)


def _solid3d_center_2d(entity) -> tuple[float, float] | None:
    """Get the 2D center point of a 3DSOLID (centroid of projected vertices).

    Note: does NOT apply GEODATA offset.
    """
    world_pts = _solid3d_to_world_points(entity)
    if not world_pts:
        return None
    xs = [p[0] for p in world_pts]
    ys = [p[1] for p in world_pts]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _get_geodata_offset(doc) -> tuple[float, float]:
    """Extract the XY offset from DXF GEODATA (design_point - reference_point).

    Many DXF files use a local coordinate system for 3D objects.
    The GEODATA entity maps local model-space coords to world CRS coords.
    Returns (dx, dy) offset to add to model-space coordinates.
    Returns (0, 0) if no GEODATA found or coords are already world-space.
    """
    for obj in doc.objects:
        if obj.dxftype() == "GEODATA":
            dp = obj.dxf.design_point
            rp = obj.dxf.reference_point
            return (dp[0] - rp[0], dp[1] - rp[1])
    return (0.0, 0.0)


def _needs_geodata_offset(world_pts_2d: list[tuple[float, float]], doc) -> bool:
    """Heuristic: check if points are in local coords (small) vs world coords (large).

    EPSG:25832 coords have X ~500k, Y ~5900k. If points are much smaller,
    they're likely in a local system and need the GEODATA offset.
    """
    if not world_pts_2d:
        return False
    xs = [p[0] for p in world_pts_2d]
    ys = [p[1] for p in world_pts_2d]
    # If max absolute value is < 100,000, probably local coords
    return max(abs(max(xs)), abs(max(ys)), abs(min(xs)), abs(min(ys))) < 100_000


def extract_3dsolids(
    dxf_path: str | Path,
    crs: str,
    *,
    layers: list[str] | None = None,
    bottom_face: bool = False,
) -> dict[str, gpd.GeoDataFrame]:
    """Extract 3DSOLID entities from DXF as 2D polygon GeoDataFrames.

    Each 3DSOLID is projected to 2D via convex hull of its ACIS vertices.
    Automatically applies GEODATA offset if coordinates are in local space.

    Args:
        dxf_path: Path to a DXF or DWG file (DWG needs the ODA File Converter,
            see :mod:`pbs_gis.dxf.read`).
        crs: Coordinate reference system (e.g. 'EPSG:25832').
        layers: List of layer names to extract. None = all layers with 3DSOLIDs.
        bottom_face: If True, use only bottom-Z vertices for each solid.

    Returns:
        Dict of {layer_name: GeoDataFrame} with Polygon geometries.
    """
    doc = read_cad(dxf_path)
    msp = doc.modelspace()
    geodata_offset = _get_geodata_offset(doc)

    layer_features: dict[str, list[dict]] = defaultdict(list)
    for entity in msp:
        if entity.dxftype() != "3DSOLID":
            continue
        layer = entity.dxf.layer
        if layers and layer not in layers:
            continue
        try:
            world_pts = _solid3d_to_world_points(entity)
            if not world_pts:
                continue

            # Apply GEODATA offset if needed
            pts_2d = [(p[0], p[1]) for p in world_pts]
            if _needs_geodata_offset(pts_2d, doc) and geodata_offset != (0.0, 0.0):
                dx, dy = geodata_offset
                world_pts = [(p[0] + dx, p[1] + dy, p[2]) for p in world_pts]

            poly = _solid3d_to_2d_polygon_from_points(world_pts, bottom_face=bottom_face)
            if poly is not None:
                from pbs_gis.geometry import repair_geometry
                poly = repair_geometry(poly, context=f"3DSOLID on {layer}")
            if poly is not None:
                feature = {"geometry": poly}
                zs = [p[2] for p in world_pts]
                feature["z_min"] = min(zs)
                feature["z_max"] = max(zs)
                feature["n_vertices"] = len(world_pts)
                # Store center for convenience
                centroid = poly.centroid
                feature["center_x"] = centroid.x
                feature["center_y"] = centroid.y
                layer_features[layer].append(feature)
        except Exception:
            continue

    result = {}
    for layer, features in layer_features.items():
        if features:
            gdf = gpd.GeoDataFrame(features, crs=crs)
            result[layer] = gdf
    return result


def _solid3d_to_2d_polygon_from_points(
    world_pts_3d: list[tuple[float, float, float]],
    *,
    bottom_face: bool = False,
) -> Polygon | None:
    """Build 2D polygon from world-space 3D points via convex hull."""
    from scipy.spatial import ConvexHull

    if len(world_pts_3d) < 3:
        return None

    if bottom_face:
        sorted_pts = sorted(world_pts_3d, key=lambda p: p[2])
        n_bottom = max(3, len(sorted_pts) // 2)
        pts_2d = [(p[0], p[1]) for p in sorted_pts[:n_bottom]]
    else:
        pts_2d = [(p[0], p[1]) for p in world_pts_3d]

    seen = set()
    unique = []
    for p in pts_2d:
        key = (round(p[0], 6), round(p[1], 6))
        if key not in seen:
            seen.add(key)
            unique.append(p)
    pts_2d = unique

    if len(pts_2d) < 3:
        return None

    pts_arr = np.array(pts_2d)
    try:
        hull = ConvexHull(pts_arr)
        ordered = [pts_2d[i] for i in hull.vertices]
        ordered.append(ordered[0])
        return Polygon(ordered)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------

def _extract_entity(
    entity,
    layer_name: str | None = None,
    circles_as_points: bool = False,
    arc_points: int = 0,
) -> tuple[list[tuple[float, float]] | None, str, dict[str, Any]]:
    """
    Extract geometry from a single DXF entity.

    Returns:
        (coords, geom_type, extra_attrs) or (None, "", {}) on failure.
        geom_type is one of: "Point", "LineString", "Polygon", "Hatch".
    """
    entity_type = entity.dxftype()
    extra: dict[str, Any] = {}

    try:
        if entity_type == "LINE":
            s, e = entity.dxf.start, entity.dxf.end
            return [_p2(s), _p2(e)], "LineString", extra

        if entity_type in ("LWPOLYLINE", "POLYLINE"):
            coords = lwpolyline_to_coords(entity, arc_points)
            if not coords:
                return None, "", extra
            is_closed = (entity.closed if entity_type == "LWPOLYLINE"
                         else entity.is_closed)
            gtype = "Polygon" if is_closed and len(coords) >= 4 else "LineString"
            return coords, gtype, extra

        if entity_type == "ARC":
            return _arc_to_coords(entity), "LineString", extra

        if entity_type == "CIRCLE":
            center = entity.dxf.center
            radius = entity.dxf.radius
            if circles_as_points:
                extra["radius"] = radius
                return [_p2(center)], "Point", extra
            return _circle_to_coords(entity), "Polygon", extra

        if entity_type == "ELLIPSE":
            center = entity.dxf.center
            major_axis = entity.dxf.major_axis
            ratio = entity.dxf.ratio
            coords = _ellipse_to_coords(entity)
            if circles_as_points:
                major_len = math.sqrt(major_axis[0] ** 2 + major_axis[1] ** 2)
                extra["major_axis"] = major_len
                extra["minor_axis"] = major_len * ratio
                return [_p2(center)], "Point", extra
            is_closed = len(coords) > 0 and coords[-1] == coords[0]
            return coords, "Polygon" if is_closed else "LineString", extra

        if entity_type == "HATCH":
            coords = _hatch_to_coords(entity)
            if coords:
                extra["is_hatch"] = True
                return coords, "Hatch", extra
            return None, "", extra

        if entity_type == "SOLID":
            # DXF SOLID (2D filled quad/triangle) — vertex order is 0,1,3,2
            v0 = _p2(entity.dxf.vtx0)
            v1 = _p2(entity.dxf.vtx1)
            v2 = _p2(entity.dxf.vtx2)
            v3 = _p2(entity.dxf.vtx3) if entity.dxf.hasattr("vtx3") else v2
            coords = [v0, v1, v3, v2, v0]
            return coords, "Polygon", extra

        if entity_type == "POINT":
            return [_p2(entity.dxf.location)], "Point", extra

        if entity_type in ("TEXT", "MTEXT"):
            return [_p2(entity.dxf.insert)], "Point", extra

    except Exception:
        pass

    return None, "", extra


# ---------------------------------------------------------------------------
# Transform helpers
# ---------------------------------------------------------------------------

def _apply_transform(
    points: list[tuple[float, float]],
    matrix: tuple[float, float, float, float, float],
) -> list[tuple[float, float]]:
    """Apply 2D affine transform: scale → rotate → translate."""
    x_off, y_off, rot, sx, sy = matrix
    cos_r, sin_r = math.cos(rot), math.sin(rot)
    out = []
    for x, y in points:
        xs, ys = x * sx, y * sy
        out.append((xs * cos_r - ys * sin_r + x_off,
                     xs * sin_r + ys * cos_r + y_off))
    return out


def _compose_transform(
    parent: tuple[float, float, float, float, float],
    insert_point: tuple[float, float, float],
    rotation_deg: float,
    x_scale: float,
    y_scale: float,
) -> tuple[float, float, float, float, float]:
    """Compose parent transform with an INSERT's local transform."""
    px, py, prot, psx, psy = parent
    cos_p, sin_p = math.cos(prot), math.sin(prot)
    ix, iy = insert_point[0], insert_point[1]
    tx = ix * psx * cos_p - iy * psy * sin_p + px
    ty = ix * psx * sin_p + iy * psy * cos_p + py
    return (tx, ty, prot + math.radians(rotation_deg), psx * x_scale, psy * y_scale)


IDENTITY_TRANSFORM = (0.0, 0.0, 0.0, 1.0, 1.0)


# ---------------------------------------------------------------------------
# Recursive block processing
# ---------------------------------------------------------------------------

def _process_block_recursive(
    entity,
    entity_layer: str,
    transform: tuple[float, float, float, float, float],
    doc: ezdxf.document.Drawing,
    *,
    exclude_layers: set[str],
    circles_as_points: bool,
    arc_points: int,
    depth: int = 0,
    max_depth: int = 10,
) -> list[tuple[str, dict[str, Any]]]:
    """Recursively extract geometry from an entity, handling nested INSERTs."""
    if depth > max_depth:
        return []

    if entity.dxftype() == "INSERT":
        block_name = entity.dxf.name
        if block_name not in doc.blocks:
            return []
        block = doc.blocks.get(block_name)

        child_transform = _compose_transform(
            transform,
            entity.dxf.insert,
            entity.dxf.get("rotation", 0),
            entity.dxf.get("xscale", 1),
            entity.dxf.get("yscale", 1),
        )

        features = []
        for child in block:
            child_layer = getattr(child.dxf, "layer", entity_layer)
            if child_layer in exclude_layers:
                continue
            features.extend(_process_block_recursive(
                child, child_layer, child_transform, doc,
                exclude_layers=exclude_layers,
                circles_as_points=circles_as_points,
                arc_points=arc_points,
                depth=depth + 1,
                max_depth=max_depth,
            ))
        return features

    # Regular entity — extract and transform
    coords, gtype, extra = _extract_entity(
        entity, entity_layer,
        circles_as_points=circles_as_points,
        arc_points=arc_points,
    )
    if not coords:
        return []

    transformed = _apply_transform(coords, transform)

    # Scale dimension attributes
    _, _, _, sx, sy = transform
    avg_scale = (abs(sx) + abs(sy)) / 2
    for key in ("radius", "major_axis", "minor_axis"):
        if key in extra:
            extra[key] *= avg_scale

    return [(entity_layer, _build_feature(transformed, gtype, entity.dxftype(), extra))]


def _build_feature(
    coords: list[tuple[float, float]],
    geom_type: str,
    entity_type: str,
    extra: dict[str, Any],
) -> dict[str, Any] | None:
    """Build a feature dict with Shapely geometry from coords and type."""
    from pbs_gis.geometry import repair_geometry

    try:
        if geom_type == "Point" and len(coords) == 1:
            geom = Point(coords[0])
        elif geom_type in ("Polygon", "Hatch") and len(coords) >= 3:
            geom = Polygon(coords)
            geom = repair_geometry(geom, context=entity_type)
            if geom is None:
                return None
            # If repair produced MultiPolygon, take the largest
            if geom.geom_type == "MultiPolygon":
                geom = max(geom.geoms, key=lambda g: g.area)
        elif geom_type == "LineString" and len(coords) >= 2:
            geom = LineString(coords)
        else:
            return None

        if geom.is_empty:
            return None

        feature: dict[str, Any] = {
            "geometry": geom,
            "entity_type": entity_type,
        }
        feature.update(extra)
        return feature
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_dxf_layers(
    dxf_path: str | Path,
    crs: str,
    *,
    layers: list[str] | None = None,
    exclude_layers: list[str] | None = None,
    arc_points: int = 0,
    circles_as_points: bool = False,
    process_blocks: bool = True,
    max_block_depth: int = 10,
) -> dict[str, dict[str, gpd.GeoDataFrame]]:
    """
    Extract all geometry from a DXF file, organized by layer and geometry type.

    Args:
        dxf_path: Path to a DXF or DWG file (DWG needs the ODA File Converter,
            see :mod:`pbs_gis.dxf.read`).
        crs: Coordinate reference system string (e.g. "EPSG:25833").
        layers: Only extract these layers. None = all layers.
        exclude_layers: Skip these layers.
        arc_points: Points per arc/bulge interpolation. 0 = adaptive.
        circles_as_points: If True, extract circles/ellipses as center Points
            with radius attributes instead of polygon approximations.
        process_blocks: If True, recursively extract geometry from INSERT
            (block reference) entities with proper affine transforms.
        max_block_depth: Maximum recursion depth for nested blocks.

    Returns:
        Nested dict: ``{layer_name: {geom_type: GeoDataFrame}}``.
        geom_type keys: ``"Point"``, ``"LineString"``, ``"Polygon"``, ``"Hatch"``.
        Each GeoDataFrame has columns: geometry, entity_type, plus any extras
        (radius, major_axis, minor_axis for circles/ellipses as points).
    """
    doc = read_cad(dxf_path)
    msp = doc.modelspace()
    exclude = set(exclude_layers or [])
    include = set(layers) if layers else None

    # Collect features: {layer: [feature_dict, ...]}
    layer_features: dict[str, list[dict[str, Any]]] = defaultdict(list)

    # 1. Process modelspace entities directly
    for entity in msp:
        layer = getattr(entity.dxf, "layer", None)
        if layer is None:
            continue
        if layer in exclude:
            continue
        if include and layer not in include:
            continue

        if entity.dxftype() == "INSERT" and process_blocks:
            results = _process_block_recursive(
                entity, layer, IDENTITY_TRANSFORM, doc,
                exclude_layers=exclude,
                circles_as_points=circles_as_points,
                arc_points=arc_points,
                max_depth=max_block_depth,
            )
            for feat_layer, feat in results:
                if feat is not None:
                    if include and feat_layer not in include:
                        continue
                    layer_features[feat_layer].append(feat)
        else:
            coords, gtype, extra = _extract_entity(
                entity, layer,
                circles_as_points=circles_as_points,
                arc_points=arc_points,
            )
            if coords:
                feat = _build_feature(coords, gtype, entity.dxftype(), extra)
                if feat is not None:
                    layer_features[layer].append(feat)

    # 2. Organize into {layer: {geom_type: GeoDataFrame}}
    result: dict[str, dict[str, gpd.GeoDataFrame]] = {}

    for layer, features in layer_features.items():
        by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for feat in features:
            geom = feat["geometry"]
            if geom.geom_type == "Point":
                by_type["Point"].append(feat)
            elif geom.geom_type == "LineString":
                by_type["LineString"].append(feat)
            elif geom.geom_type in ("Polygon", "MultiPolygon"):
                key = "Hatch" if feat.get("is_hatch") else "Polygon"
                by_type[key].append(feat)

        layer_gdfs: dict[str, gpd.GeoDataFrame] = {}
        for gtype, feats in by_type.items():
            gdf = gpd.GeoDataFrame(feats, crs=crs)
            gdf.drop(columns=["is_hatch"], errors="ignore", inplace=True)
            layer_gdfs[gtype] = gdf
        result[layer] = layer_gdfs

    return result


def extract_dxf_circles(
    dxf_path: str | Path,
    crs: str,
    *,
    layers: list[str] | None = None,
    process_blocks: bool = True,
    max_block_depth: int = 10,
) -> gpd.GeoDataFrame:
    """
    Extract circle centers as a Point GeoDataFrame with radius attribute.

    Convenience wrapper around extract_dxf_layers() for circle-specific extraction.

    Args:
        dxf_path: Path to DXF file.
        crs: Coordinate reference system string.
        layers: Only extract circles from these layers. None = all.
        process_blocks: Recurse into block references.
        max_block_depth: Maximum block nesting depth.

    Returns:
        GeoDataFrame with Point geometries and a ``radius`` column.
    """
    all_layers = extract_dxf_layers(
        dxf_path, crs,
        layers=layers,
        circles_as_points=True,
        process_blocks=process_blocks,
        max_block_depth=max_block_depth,
    )

    frames = []
    for layer_name, gdfs in all_layers.items():
        if "Point" in gdfs:
            gdf = gdfs["Point"].copy()
            if "radius" in gdf.columns:
                gdf["layer"] = layer_name
                frames.append(gdf)

    if not frames:
        return gpd.GeoDataFrame(columns=["geometry", "radius", "layer"], crs=crs)

    return gpd.GeoDataFrame(
        gpd.pd.concat(frames, ignore_index=True), crs=crs
    )


def save_layers_as_shapefiles(
    layers: dict[str, dict[str, gpd.GeoDataFrame]],
    output_dir: str | Path,
) -> list[Path]:
    """
    Write extract_dxf_layers() output to organized shapefiles.

    Creates subdirectories per geometry type (Point/, LineString/, Polygon/, Hatch/).

    Args:
        layers: Output from extract_dxf_layers().
        output_dir: Root output directory.

    Returns:
        List of written shapefile paths.
    """
    import re

    output_dir = Path(output_dir)
    written: list[Path] = []

    for layer_name, gdfs in layers.items():
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", layer_name).strip()
        if not safe_name:
            safe_name = "unnamed"

        for gtype, gdf in gdfs.items():
            if gdf.empty:
                continue
            subdir = output_dir / gtype
            subdir.mkdir(parents=True, exist_ok=True)
            path = subdir / f"{safe_name}.shp"
            gdf.to_file(path, driver="ESRI Shapefile")
            written.append(path)

    return written
