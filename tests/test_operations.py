"""Unit tests for ported vector operations (``pbs_gis.operations``)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, Polygon

from shapely.geometry import MultiLineString

from pbs_gis.operations import (
    clean_line,
    connect_points,
    dissolve_by_majority_intersection,
    filter_by_column,
    filter_by_intersection,
    remove_degenerate_spikes,
    remove_protrusions,
    remove_slivers_erosion,
    simplify_slivers,
)

CRS = "EPSG:25833"


def _gdf(geoms, crs=CRS, **cols):
    data = {"geometry": geoms}
    data.update(cols)
    return gpd.GeoDataFrame(data, crs=crs)


def _square(x0=0, y0=0, s=10):
    return Polygon([(x0, y0), (x0 + s, y0), (x0 + s, y0 + s), (x0, y0 + s)])


# --- cleaning family -------------------------------------------------------

def test_remove_slivers_erosion_drops_thin():
    # A fat square plus a 0.1m-thin sliver strip. Erosion 0.2 removes the strip.
    fat = _square(s=10)
    sliver = Polygon([(20, 0), (20.1, 0), (20.1, 10), (20, 10)])
    gdf = _gdf([fat, sliver])
    out = remove_slivers_erosion(gdf, erosion_distance=0.2)
    assert len(out) == 1
    assert out.crs == gdf.crs
    assert out.geometry.iloc[0].area == pytest.approx(fat.area, rel=0.02)


def test_remove_slivers_preserves_attributes():
    gdf = _gdf([_square(s=10)], name=["A"])
    out = remove_slivers_erosion(gdf, erosion_distance=0.1)
    assert "name" in out.columns
    assert out["name"].iloc[0] == "A"


def test_simplify_slivers_drops_small_and_simplifies():
    big = _square(s=10)
    tiny = _square(x0=100, y0=100, s=1)  # area 1 < threshold 10
    gdf = _gdf([big, tiny])
    out = simplify_slivers(gdf, tolerance=0.1, min_area_threshold=10.0)
    assert len(out) == 1


def test_remove_degenerate_spikes_removes_zero_width_spike():
    # A 10x10 square whose top edge shoots a zero-width spike up to y=15 and back
    # (…(5,10)->(5,15)->(5,10)…). The spike carries no area; cleaning must drop it.
    poly = Polygon([(0, 0), (10, 0), (10, 10), (5, 10), (5, 15),
                    (5, 10), (0, 10)])
    gdf = _gdf([poly])
    out = remove_degenerate_spikes(gdf)
    assert len(out) == 1
    cleaned = out.geometry.iloc[0]
    assert cleaned.is_valid
    # The spike (top at y=15) is gone; the body top stays at ~y=10.
    assert cleaned.bounds[3] == pytest.approx(10.0, abs=0.2)
    assert cleaned.area == pytest.approx(100.0, rel=0.02)


def test_remove_protrusions_strips_thin_spike():
    # A 20x20 body with a thin 1x10 protrusion sticking out the right side.
    body = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)])
    spike = Polygon([(20, 9), (30, 9), (30, 10), (20, 10)])
    poly = body.union(spike)
    gdf = _gdf([poly])
    out = remove_protrusions(gdf, min_protrusion_length=3.0, buffer_distance=1.0)
    assert len(out) == 1
    assert out.geometry.iloc[0].area < poly.area
    assert out.geometry.iloc[0].area == pytest.approx(body.area, rel=0.1)


# --- clean_line ------------------------------------------------------------

def test_clean_line_removes_short_segment_vertex():
    # A near-collinear midpoint 0.1m from the start creates a 0.1m segment;
    # min_segment_length=0.5 drops it, keeping start, the 1m vertex, and the end.
    line = LineString([(0, 0), (0.1, 0), (1, 0), (2, 0)])
    out = clean_line(_gdf([line]), min_segment_length=0.5)
    assert len(out) == 1
    coords = list(out.geometry.iloc[0].coords)
    assert coords == [(0, 0), (1, 0), (2, 0)]
    assert out.crs == CRS


def test_clean_line_removes_consecutive_duplicate():
    line = LineString([(0, 0), (5, 0), (5, 0), (10, 0)])
    out = clean_line(_gdf([line]), tolerance=0.01, min_segment_length=0.0)
    coords = list(out.geometry.iloc[0].coords)
    assert coords == [(0, 0), (5, 0), (10, 0)]


def test_clean_line_preserves_attributes():
    line = LineString([(0, 0), (10, 0)])
    out = clean_line(_gdf([line], name=["trasse"]))
    assert out["name"].iloc[0] == "trasse"


def test_clean_line_keeps_closed_ring_closed():
    # A closed square ring with a duplicated corner vertex.
    ring = LineString([(0, 0), (10, 0), (10, 0), (10, 10), (0, 10), (0, 0)])
    out = clean_line(_gdf([ring]), tolerance=0.01, min_segment_length=0.1)
    geom = out.geometry.iloc[0]
    assert geom.is_valid
    assert geom.is_closed
    assert list(geom.coords)[0] == list(geom.coords)[-1]
    # Duplicate corner removed → 5 coords (4 corners + closing point).
    assert len(geom.coords) == 5


def test_clean_line_handles_multilinestring():
    a = LineString([(0, 0), (5, 0), (5, 0), (10, 0)])  # has a duplicate
    b = LineString([(0, 10), (10, 10)])
    out = clean_line(_gdf([MultiLineString([a, b])]), tolerance=0.01,
                     min_segment_length=0.0)
    assert len(out) == 1
    geom = out.geometry.iloc[0]
    assert isinstance(geom, MultiLineString)
    assert len(geom.geoms) == 2
    assert len(geom.geoms[0].coords) == 3  # duplicate dropped in first part


def test_clean_line_drops_degenerate_line():
    # Both vertices within tolerance collapse to one point → feature dropped.
    line = LineString([(0, 0), (0.001, 0)])
    out = clean_line(_gdf([line]), tolerance=0.01)
    assert out.empty


def test_clean_line_simplify_removes_collinear():
    line = LineString([(0, 0), (5, 0), (10, 0)])
    out = clean_line(_gdf([line]), min_segment_length=0.0, simplify_tolerance=0.1)
    assert list(out.geometry.iloc[0].coords) == [(0, 0), (10, 0)]


def test_clean_line_empty_gdf():
    out = clean_line(gpd.GeoDataFrame(geometry=[], crs=CRS))
    assert out.empty


# --- connect_points --------------------------------------------------------

def test_connect_points_single_path():
    pts = [Point(0, 0), Point(1, 0), Point(2, 0)]
    out = connect_points(_gdf(pts))
    assert len(out) == 1
    assert isinstance(out.geometry.iloc[0], LineString)
    assert len(out.geometry.iloc[0].coords) == 3


def test_connect_points_too_few():
    out = connect_points(_gdf([Point(0, 0)]))
    assert out.empty


def test_connect_points_clusters_by_distance():
    # Two clusters 100m apart; maxDistance 5 keeps them separate.
    pts = [Point(0, 0), Point(1, 0), Point(100, 0), Point(101, 0)]
    out = connect_points(_gdf(pts), max_distance=5)
    assert len(out) == 2


# --- dissolve_by_majority_intersection -------------------------------------

def test_dissolve_by_majority_reconstructs():
    # Two source cells fully inside reference A, one inside reference B.
    src = _gdf([
        _square(0, 0, 10),
        _square(10, 0, 10),
        _square(100, 0, 10),
    ])
    ref = _gdf([
        Polygon([(0, 0), (20, 0), (20, 10), (0, 10)]),      # covers first two
        Polygon([(100, 0), (110, 0), (110, 10), (100, 10)]),  # covers third
    ], name=["A", "B"])
    out = dissolve_by_majority_intersection(src, ref, transfer_attributes=["name"])
    assert len(out) == 2
    assert set(out["name"]) == {"A", "B"}
    a_geom = out[out["name"] == "A"].geometry.iloc[0]
    assert a_geom.area == pytest.approx(200.0, rel=0.01)


def test_dissolve_by_majority_threshold_excludes_minority():
    # Source cell only 25% inside reference → excluded at default 50% threshold.
    src = _gdf([_square(0, 0, 10)])
    ref = _gdf([Polygon([(0, 0), (5, 0), (5, 5), (0, 5)])])  # 25 of 100 = 25%
    out = dissolve_by_majority_intersection(src, ref)
    assert out.empty


# --- filters ---------------------------------------------------------------

def test_filter_by_column_operators():
    gdf = _gdf([Point(0, 0), Point(1, 1), Point(2, 2)], kat=["a", "b", "a"])
    assert len(filter_by_column(gdf, "kat", "a")) == 2
    assert len(filter_by_column(gdf, "kat", "a", operator="neq")) == 1
    assert len(filter_by_column(gdf, "kat", ["a", "b"], operator="in")) == 3


def test_filter_by_column_unknown_column_raises():
    gdf = _gdf([Point(0, 0)])
    with pytest.raises(KeyError):
        filter_by_column(gdf, "nope", 1)


def test_filter_by_intersection_keeps_overlapping():
    gdf = _gdf([_square(0, 0, 5), _square(100, 100, 5)])
    other = _gdf([_square(0, 0, 10)])
    out = filter_by_intersection(gdf, other)
    assert len(out) == 1


def test_filter_by_intersection_unknown_predicate_raises():
    gdf = _gdf([_square(0, 0, 5)])
    with pytest.raises(ValueError):
        filter_by_intersection(gdf, gdf, predicate="bogus")
