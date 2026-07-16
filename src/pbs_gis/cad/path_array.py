"""Emit block references at regular spacing along a line (path array).

New rewrite (Phase-4 Nachzügler) of the placement core of Python-ACAD-Tools'
``path_array.py``. The reference grew a large decision surface — matplotlib
debug visuals, overlap rejection, source-constraint checking, per-vertex angle
optimisation — all bound to its ``all_layers`` model. None of that belongs in a
one-way emitter, so this keeps only the placement walk:

* :func:`insert_block_array` — reference a block **already defined** in the
  document at even *spacing* along a LineString/MultiLineString, optionally
  starting at an *offset* and rotating each reference to follow the path
  tangent.

Placement reuses :func:`pbs_gis.cad.annotate.insert_block`, so every emitted
INSERT carries the emitter provenance and an idempotent re-export (via
``emit._purge_emitted``) clears them — no separate cleanup path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely.geometry import LineString, MultiLineString

from pbs_gis.cad.annotate import AnnotateError, insert_block


@dataclass
class PathArrayResult:
    """Tally of a path-array emission (for verification/reporting)."""

    block: str
    layer: str
    inserts: int = 0
    references: list = field(default_factory=list)


def insert_block_array(
    doc,
    line: LineString | MultiLineString,
    block: str,
    *,
    spacing: float,
    layer: str = "0",
    offset: float = 0.0,
    scale: float = 1.0,
    rotation: float = 0.0,
    align_to_path: bool = False,
    space: str = "model",
) -> PathArrayResult:
    """
    Insert references to *block* at even *spacing* along *line*.

    The first reference is placed *offset* metres from the line's start, then
    every *spacing* metres along the path as long as the position stays within
    the line (the end point itself is used only when it falls on the grid). A
    ``MultiLineString`` is walked part by part (the offset restarts on each
    part).

    Args:
        doc: ezdxf document. *block* must already be defined in ``doc.blocks``
            (typically via a template DXF) — an unknown name is a hard error,
            never a silent no-op.
        line: A shapely ``LineString`` or ``MultiLineString`` (coordinates in
            the drawing's CRS; DXF is CRS-less).
        block: Block definition name to reference.
        spacing: Distance between consecutive references (CRS units, > 0).
        layer: Target layer (created if missing).
        offset: Distance along the path before the first reference (>= 0).
        scale: Uniform block scale.
        rotation: Base rotation in degrees. With *align_to_path* it is added on
            top of the path-tangent angle; otherwise it is used verbatim.
        align_to_path: If true, each reference is rotated to the tangent of the
            segment it sits on (plus *rotation*).
        space: ``"model"`` or ``"paper"``.

    Returns:
        A :class:`PathArrayResult` with the count and the created INSERT
        entities, in placement order.

    Raises:
        AnnotateError: if *block* is not defined in the document.
        ValueError: for a non-positive *spacing*, a negative *offset*, or a
            geometry that is not a (Multi)LineString.
    """
    if spacing <= 0:
        raise ValueError(f"spacing must be > 0, got {spacing!r}")
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset!r}")
    if not block or block not in doc.blocks:
        available = [b.name for b in doc.blocks][:10]
        raise AnnotateError(
            f"Block {block!r} not defined in document; provide it via a template. "
            f"Known (first 10): {available}"
        )

    if isinstance(line, LineString):
        parts = [line]
    elif isinstance(line, MultiLineString):
        parts = list(line.geoms)
    else:
        raise ValueError(
            f"line must be a LineString or MultiLineString, got {type(line).__name__}"
        )

    result = PathArrayResult(block=block, layer=layer)
    for part in parts:
        if part.is_empty or part.length == 0:
            continue
        for dist in _positions(part.length, spacing, offset):
            point = part.interpolate(dist)
            rot = rotation
            if align_to_path:
                rot = _segment_angle(part, dist) + rotation
            ref = insert_block(
                doc, block, (point.x, point.y),
                layer=layer, scale=scale, rotation=rot, space=space,
            )
            result.references.append(ref)
            result.inserts += 1
    return result


def _positions(total: float, spacing: float, offset: float) -> list[float]:
    """Distances offset, offset+spacing, … up to *total* (endpoint included)."""
    positions = []
    dist = offset
    while dist <= total + 1e-9:
        positions.append(min(dist, total))
        dist += spacing
    return positions


def _segment_angle(line: LineString, distance: float) -> float:
    """Tangent angle (degrees) of the segment containing *distance* along *line*."""
    coords = list(line.coords)
    acc = 0.0
    last = len(coords) - 2
    for i in range(len(coords) - 1):
        seg_len = math.dist(coords[i][:2], coords[i + 1][:2])
        if acc + seg_len >= distance or i == last:
            dx = coords[i + 1][0] - coords[i][0]
            dy = coords[i + 1][1] - coords[i][1]
            return math.degrees(math.atan2(dy, dx))
        acc += seg_len
    return 0.0
