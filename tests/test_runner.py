"""Unit tests for the workflow runner core semantics.

Pure unit tests: they exercise dependency resolution and make-style
staleness against ``tmp_path`` files only — no QGIS, no network, no
recipe/template execution. See ``src/gis_utils/runner.py``.
"""

from __future__ import annotations

import os

import pytest

from gis_utils.runner import (
    _collect_deps,
    _collect_outputs,
    _resolve_extent,
    resolve_order,
    should_skip,
)


def _touch(path, mtime=None):
    """Create *path* (and parents) and optionally pin its mtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


# --------------------------------------------------------------------------
# resolve_order — topological sort
# --------------------------------------------------------------------------

def test_resolve_order_linear_chain():
    steps = [
        {"name": "C", "depends_on": ["B"]},
        {"name": "B", "depends_on": ["A"]},
        {"name": "A"},
    ]
    ordered = [s["name"] for s in resolve_order(steps)]
    assert ordered == ["A", "B", "C"]


def test_resolve_order_respects_definition_order_among_independent_steps():
    # Three independent (zero-degree) steps must come out in definition order,
    # not sorted alphabetically or by dict hashing.
    steps = [{"name": "second"}, {"name": "first"}, {"name": "third"}]
    ordered = [s["name"] for s in resolve_order(steps)]
    assert ordered == ["second", "first", "third"]


def test_resolve_order_diamond_dependency():
    #   A → B, A → C, (B,C) → D
    steps = [
        {"name": "A"},
        {"name": "B", "depends_on": ["A"]},
        {"name": "C", "depends_on": ["A"]},
        {"name": "D", "depends_on": ["B", "C"]},
    ]
    ordered = [s["name"] for s in resolve_order(steps)]
    assert ordered[0] == "A"
    assert ordered[-1] == "D"
    assert ordered.index("B") < ordered.index("D")
    assert ordered.index("C") < ordered.index("D")


def test_resolve_order_returns_full_step_dicts():
    steps = [{"name": "A", "script": "a.py"}, {"name": "B", "depends_on": ["A"]}]
    result = resolve_order(steps)
    assert result[0]["script"] == "a.py"


# --------------------------------------------------------------------------
# resolve_order — error cases
# --------------------------------------------------------------------------

def test_resolve_order_circular_dependency_raises():
    steps = [
        {"name": "A", "depends_on": ["B"]},
        {"name": "B", "depends_on": ["A"]},
    ]
    with pytest.raises(ValueError, match="Circular dependency"):
        resolve_order(steps)


def test_resolve_order_self_cycle_raises():
    steps = [{"name": "A", "depends_on": ["A"]}]
    with pytest.raises(ValueError, match="Circular dependency"):
        resolve_order(steps)


def test_resolve_order_unknown_dependency_raises():
    steps = [{"name": "A", "depends_on": ["does-not-exist"]}]
    with pytest.raises(ValueError, match="unknown step"):
        resolve_order(steps)


# --------------------------------------------------------------------------
# should_skip — make-style staleness
# --------------------------------------------------------------------------

def test_should_skip_run_always_never_skips(tmp_path):
    step = {"name": "s", "run": "always", "output": "out.gpkg"}
    _touch(tmp_path / "out.gpkg")
    assert should_skip(step, tmp_path) is False


def test_should_skip_no_outputs_never_skips(tmp_path):
    step = {"name": "s"}
    assert should_skip(step, tmp_path) is False


def test_should_skip_missing_output_runs(tmp_path):
    step = {"name": "s", "output": "out.gpkg"}
    assert should_skip(step, tmp_path) is False


def test_should_skip_outputs_exist_no_inputs_skips(tmp_path):
    step = {"name": "s", "output": "out.gpkg"}
    _touch(tmp_path / "out.gpkg")
    assert should_skip(step, tmp_path) is True


def test_should_skip_input_newer_than_output_runs(tmp_path):
    step = {"name": "s", "output": "out.gpkg", "inputs": ["src.shp"]}
    _touch(tmp_path / "out.gpkg", mtime=1000)
    _touch(tmp_path / "src.shp", mtime=2000)  # newer input → stale
    assert should_skip(step, tmp_path) is False


def test_should_skip_input_older_than_output_skips(tmp_path):
    step = {"name": "s", "output": "out.gpkg", "inputs": ["src.shp"]}
    _touch(tmp_path / "out.gpkg", mtime=2000)
    _touch(tmp_path / "src.shp", mtime=1000)  # older input → up to date
    assert should_skip(step, tmp_path) is True


def test_should_skip_missing_declared_input_does_not_force_rerun(tmp_path):
    # A declared input that does not exist can't make outputs stale
    # (_newest_mtime returns 0.0). Documented design choice.
    step = {"name": "s", "output": "out.gpkg", "inputs": ["ghost.shp"]}
    _touch(tmp_path / "out.gpkg", mtime=1000)
    assert should_skip(step, tmp_path) is True


def test_should_skip_edited_script_forces_rerun(tmp_path):
    # A step depends on its own script (make-style: target depends on recipe).
    step = {"name": "s", "output": "out.gpkg", "script": "scripts/s.py"}
    _touch(tmp_path / "out.gpkg", mtime=1000)
    _touch(tmp_path / "scripts" / "s.py", mtime=2000)
    assert should_skip(step, tmp_path) is False


def test_should_skip_recipe_input_boundary_is_an_input(tmp_path):
    step = {
        "name": "s",
        "recipe": "some_recipe",
        "output": "out.gpkg",
        "input_boundary": "aoi.shp",
    }
    _touch(tmp_path / "out.gpkg", mtime=1000)
    _touch(tmp_path / "aoi.shp", mtime=2000)  # newer boundary → stale
    assert should_skip(step, tmp_path) is False


def test_should_skip_input_boundary_ignored_without_recipe(tmp_path):
    # input_boundary only counts as an input for recipe steps (back-compat).
    step = {"name": "s", "output": "out.gpkg", "input_boundary": "aoi.shp"}
    _touch(tmp_path / "out.gpkg", mtime=1000)
    _touch(tmp_path / "aoi.shp", mtime=2000)
    assert should_skip(step, tmp_path) is True


def test_should_skip_multi_output_missing_one_runs(tmp_path):
    step = {"name": "s", "outputs": ["a.gpkg", "b.gpkg"]}
    _touch(tmp_path / "a.gpkg")
    # b.gpkg missing → not all outputs exist → run
    assert should_skip(step, tmp_path) is False


# --------------------------------------------------------------------------
# _collect_outputs
# --------------------------------------------------------------------------

def test_collect_outputs_prefers_outputs_list():
    step = {"outputs": ["a", "b"], "output": "c"}
    assert _collect_outputs(step) == ["a", "b"]


def test_collect_outputs_single_output_fallback():
    assert _collect_outputs({"output": "c"}) == ["c"]


def test_collect_outputs_multi_layer_recipe():
    step = {"output_dir": "Geodaten/", "layers": ["roads", "rails"]}
    assert _collect_outputs(step) == ["Geodaten/roads.gpkg", "Geodaten/rails.gpkg"]


def test_collect_outputs_empty():
    assert _collect_outputs({"name": "s"}) == []


# --------------------------------------------------------------------------
# _collect_deps — transitive dependency gathering
# --------------------------------------------------------------------------

def test_collect_deps_transitive():
    by_name = {
        "A": {"name": "A"},
        "B": {"name": "B", "depends_on": ["A"]},
        "C": {"name": "C", "depends_on": ["B"]},
    }
    assert _collect_deps("C", by_name) == {"A", "B", "C"}


def test_collect_deps_unknown_step_raises():
    with pytest.raises(ValueError, match="Unknown step"):
        _collect_deps("Z", {"A": {"name": "A"}})


# --------------------------------------------------------------------------
# _resolve_extent — missing input error case
# --------------------------------------------------------------------------

def test_resolve_extent_missing_input_boundary_raises(tmp_path):
    step = {"name": "s", "input_boundary": "missing_aoi.shp"}
    with pytest.raises(FileNotFoundError, match="input_boundary not found"):
        _resolve_extent(step, tmp_path)


def test_resolve_extent_buffer_without_crs_raises(tmp_path):
    _touch(tmp_path / "aoi.shp")
    step = {"name": "s", "input_boundary": "aoi.shp", "buffer_m": 100}
    with pytest.raises(ValueError, match="crs is required"):
        _resolve_extent(step, tmp_path)


def test_resolve_extent_no_boundary_returns_empty(tmp_path):
    assert _resolve_extent({"name": "s"}, tmp_path) == {}
