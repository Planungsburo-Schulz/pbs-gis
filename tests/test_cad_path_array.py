"""Unit tests for the CAD path-array capability (block references along a line)."""

from __future__ import annotations

import pytest
from shapely.geometry import LineString, MultiLineString

from pbs_gis.cad import AnnotateError, insert_block_array
from pbs_gis.cad.emit import _purge_emitted
from pbs_gis.dxf.document import new_dxf_document


def _doc_with_block(name="sym"):
    doc = new_dxf_document()
    block = doc.blocks.new(name=name)
    block.add_circle((0, 0), radius=0.5)
    return doc


def test_places_blocks_at_even_spacing():
    doc = _doc_with_block()
    line = LineString([(0, 0), (10, 0)])
    result = insert_block_array(doc, line, "sym", spacing=2.0, layer="Sym")
    # offset 0, spacing 2, endpoint included → 0,2,4,6,8,10 = 6 inserts.
    assert result.inserts == 6
    inserts = doc.modelspace().query('INSERT[layer=="Sym"]')
    assert len(inserts) == 6
    xs = sorted(round(e.dxf.insert.x, 6) for e in inserts)
    assert xs == [0, 2, 4, 6, 8, 10]
    assert all(e.dxf.insert.y == 0 for e in inserts)


def test_offset_shifts_first_block():
    doc = _doc_with_block()
    line = LineString([(0, 0), (10, 0)])
    result = insert_block_array(doc, line, "sym", spacing=5.0, offset=1.0)
    xs = sorted(round(r.dxf.insert.x, 6) for r in result.references)
    # 1 and 6; the next grid position (11) overshoots the 10m line, so no
    # forced endpoint block.
    assert xs == [1, 6]


def test_fixed_rotation_by_default():
    doc = _doc_with_block()
    line = LineString([(0, 0), (0, 10)])  # vertical line
    result = insert_block_array(doc, line, "sym", spacing=5.0, rotation=30.0)
    assert all(r.dxf.rotation == 30.0 for r in result.references)


def test_align_to_path_follows_tangent():
    doc = _doc_with_block()
    # L-shape: horizontal (angle 0) then vertical (angle 90).
    line = LineString([(0, 0), (10, 0), (10, 10)])
    result = insert_block_array(doc, line, "sym", spacing=2.0, align_to_path=True)
    by_pos = {(round(r.dxf.insert.x, 3), round(r.dxf.insert.y, 3)): r.dxf.rotation
              for r in result.references}
    # A block on the horizontal leg has rotation ~0.
    assert by_pos[(4.0, 0.0)] == pytest.approx(0.0, abs=1e-6)
    # A block on the vertical leg has rotation ~90.
    assert by_pos[(10.0, 4.0)] == pytest.approx(90.0, abs=1e-6)


def test_align_to_path_adds_base_rotation():
    doc = _doc_with_block()
    line = LineString([(0, 0), (10, 0)])  # tangent 0
    result = insert_block_array(doc, line, "sym", spacing=5.0,
                                align_to_path=True, rotation=15.0)
    assert all(r.dxf.rotation == pytest.approx(15.0) for r in result.references)


def test_multilinestring_places_on_all_parts():
    doc = _doc_with_block()
    mls = MultiLineString([
        LineString([(0, 0), (10, 0)]),
        LineString([(0, 20), (10, 20)]),
    ])
    result = insert_block_array(doc, mls, "sym", spacing=5.0)
    # each part: 0,5,10 = 3 → 6 total.
    assert result.inserts == 6
    ys = {round(r.dxf.insert.y, 3) for r in result.references}
    assert ys == {0.0, 20.0}


def test_unknown_block_raises():
    doc = new_dxf_document()
    with pytest.raises(AnnotateError):
        insert_block_array(doc, LineString([(0, 0), (10, 0)]), "nope", spacing=2.0)


def test_non_positive_spacing_raises():
    doc = _doc_with_block()
    with pytest.raises(ValueError):
        insert_block_array(doc, LineString([(0, 0), (10, 0)]), "sym", spacing=0.0)


def test_negative_offset_raises():
    doc = _doc_with_block()
    with pytest.raises(ValueError):
        insert_block_array(doc, LineString([(0, 0), (10, 0)]), "sym",
                           spacing=2.0, offset=-1.0)


def test_non_line_geometry_raises():
    doc = _doc_with_block()
    from shapely.geometry import Point
    with pytest.raises(ValueError):
        insert_block_array(doc, Point(0, 0), "sym", spacing=2.0)


def test_inserts_are_provenance_tagged():
    doc = _doc_with_block()
    line = LineString([(0, 0), (10, 0)])
    result = insert_block_array(doc, line, "sym", spacing=5.0)
    assert result.inserts == 3
    removed = _purge_emitted(doc)
    assert removed == 3
    assert len(doc.modelspace().query("INSERT")) == 0
