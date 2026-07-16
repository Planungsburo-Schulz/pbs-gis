"""Unit tests for the declarative ops/makro pipeline and the runner ops step."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Polygon

from pbs_gis.ops_pipeline import (
    BUILTIN_MAKROS,
    OpsPipelineError,
    apply_op,
    expand_ops,
    resolve_makros,
    run_ops,
)
from pbs_gis.runner import run_step

CRS = "EPSG:25833"


def _gdf(geoms, **cols):
    data = {"geometry": geoms}
    data.update(cols)
    return gpd.GeoDataFrame(data, crs=CRS)


# --- expand_ops ------------------------------------------------------------

def test_expand_ops_passes_plain_ops_through():
    ops = [{"op": "clean_line"}, {"op": "repair"}]
    assert expand_ops(ops, resolve_makros({})) == ops


def test_expand_ops_expands_makro_inline_preserving_order():
    makros = resolve_makros({"m": [{"op": "repair"}, {"op": "clean_line"}]})
    ops = [{"op": "remove_protrusions"}, {"makro": "m"}]
    expanded = expand_ops(ops, makros)
    assert expanded == [
        {"op": "remove_protrusions"},
        {"op": "repair"},
        {"op": "clean_line"},
    ]


def test_expand_ops_makro_reusable_multiple_times():
    makros = resolve_makros({"m": [{"op": "repair"}]})
    ops = [{"makro": "m"}, {"op": "clean_line"}, {"makro": "m"}]
    expanded = expand_ops(ops, makros)
    assert expanded == [{"op": "repair"}, {"op": "clean_line"}, {"op": "repair"}]


def test_expand_ops_unknown_makro_raises():
    with pytest.raises(OpsPipelineError, match="unknown makro"):
        expand_ops([{"makro": "nope"}], resolve_makros({}))


def test_expand_ops_makro_in_makro_raises():
    # A makro body that references another makro is rejected (no recursion).
    makros = resolve_makros({"outer": [{"makro": "saeuberung_standard"}]})
    with pytest.raises(OpsPipelineError, match="recursion"):
        expand_ops([{"makro": "outer"}], makros)


# --- resolve_makros / collision --------------------------------------------

def test_resolve_makros_merges_builtin_and_project():
    merged = resolve_makros({"custom": [{"op": "repair"}]})
    assert "saeuberung_standard" in merged
    assert "custom" in merged


def test_resolve_makros_collision_with_builtin_raises():
    with pytest.raises(OpsPipelineError, match="collides with a built-in makro"):
        resolve_makros({"saeuberung_standard": [{"op": "repair"}]})


# --- apply_op --------------------------------------------------------------

def test_apply_op_unknown_op_raises():
    gdf = _gdf([Polygon([(0, 0), (1, 0), (1, 1)])])
    with pytest.raises(OpsPipelineError, match="unknown op"):
        apply_op(gdf, {"op": "does_not_exist"})


def test_apply_op_missing_op_key_raises():
    gdf = _gdf([Polygon([(0, 0), (1, 0), (1, 1)])])
    with pytest.raises(OpsPipelineError, match="missing 'op'"):
        apply_op(gdf, {"min_segment_length": 0.5})


def test_apply_op_passes_params():
    line = LineString([(0, 0), (0.1, 0), (1, 0), (2, 0)])
    out = apply_op(_gdf([line]), {"op": "clean_line", "min_segment_length": 0.5})
    assert list(out.geometry.iloc[0].coords) == [(0, 0), (1, 0), (2, 0)]


# --- run_ops / saeuberung_standard end-to-end ------------------------------

def test_builtin_saeuberung_standard_content():
    # Sanity-lock the built-in chain (order matters for the cleaning result).
    assert [d["op"] for d in BUILTIN_MAKROS["saeuberung_standard"]] == [
        "remove_degenerate_spikes",
        "remove_protrusions",
        "remove_slivers_erosion",
        "repair",
    ]


def test_saeuberung_standard_end_to_end():
    # A 50x50 square with a zero-width spike on the top edge; the standard
    # cleaning stack drops the spike and returns a valid ~2500 m² polygon.
    poly = Polygon([(0, 0), (50, 0), (50, 50), (25, 50), (25, 65),
                    (25, 50), (0, 50)])
    gdf = _gdf([poly], name=["baufeld"])
    out = run_ops(gdf, [{"makro": "saeuberung_standard"}])
    assert len(out) == 1
    cleaned = out.geometry.iloc[0]
    assert cleaned.is_valid
    assert not cleaned.is_empty
    # Spike (top at y=65) gone; body stays ~50 high.
    assert cleaned.bounds[3] == pytest.approx(50.0, abs=0.3)
    assert cleaned.area == pytest.approx(2500.0, rel=0.05)


def test_run_ops_chain_of_plain_ops():
    line = LineString([(0, 0), (5, 0), (5, 0), (10, 0)])  # has a duplicate
    out = run_ops(_gdf([line]), [
        {"op": "clean_line", "tolerance": 0.01, "min_segment_length": 0.0},
    ])
    assert list(out.geometry.iloc[0].coords) == [(0, 0), (5, 0), (10, 0)]


# --- runner ops step (wiring) ----------------------------------------------

def test_run_step_ops_reads_and_writes(tmp_path):
    src = tmp_path / "raw.gpkg"
    poly = Polygon([(0, 0), (50, 0), (50, 50), (25, 50), (25, 65),
                    (25, 50), (0, 50)])
    _gdf([poly]).to_file(src, driver="GPKG")
    step = {
        "name": "clean",
        "ops": [{"makro": "saeuberung_standard"}],
        "input": "raw.gpkg",
        "output": "out.gpkg",
    }
    ok = run_step(step, tmp_path, makros={})
    assert ok is True
    out = gpd.read_file(tmp_path / "out.gpkg")
    assert len(out) == 1
    assert out.geometry.iloc[0].is_valid


def test_run_step_ops_expands_project_makro(tmp_path):
    src = tmp_path / "raw.gpkg"
    line = LineString([(0, 0), (0.1, 0), (1, 0), (2, 0)])
    _gdf([line]).to_file(src, driver="GPKG")
    step = {
        "name": "clean",
        "ops": [{"makro": "trassen_clean"}],
        "input": "raw.gpkg",
        "output": "out.gpkg",
    }
    makros = {"trassen_clean": [{"op": "clean_line", "min_segment_length": 0.5}]}
    assert run_step(step, tmp_path, makros=makros) is True
    out = gpd.read_file(tmp_path / "out.gpkg")
    assert list(out.geometry.iloc[0].coords) == [(0, 0), (1, 0), (2, 0)]


def test_run_step_ops_unknown_op_fails(tmp_path):
    src = tmp_path / "raw.gpkg"
    _gdf([Polygon([(0, 0), (1, 0), (1, 1)])]).to_file(src, driver="GPKG")
    step = {
        "name": "bad",
        "ops": [{"op": "no_such_op"}],
        "input": "raw.gpkg",
        "output": "out.gpkg",
    }
    # Loud error inside run_ops → step fails (returns False), no output written.
    assert run_step(step, tmp_path, makros={}) is False
    assert not (tmp_path / "out.gpkg").exists()


def test_run_step_ops_missing_input_fails(tmp_path):
    step = {
        "name": "bad",
        "ops": [{"op": "repair"}],
        "input": "ghost.gpkg",
        "output": "out.gpkg",
    }
    assert run_step(step, tmp_path, makros={}) is False
