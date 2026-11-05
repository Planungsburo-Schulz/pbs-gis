"""Unit tests for the workflow runner core semantics.

Pure unit tests: they exercise dependency resolution and make-style
staleness against ``tmp_path`` files only — no QGIS, no network, no
recipe/template execution. See ``src/pbs_gis/runner.py``.
"""

from __future__ import annotations

import os

import pytest

from pbs_gis.runner import (
    _collect_deps,
    _collect_outputs,
    _resolve_extent,
    plan_runs,
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


# --------------------------------------------------------------------------
# plan_runs / --dry-run — the plan shows what a real run would run
# --------------------------------------------------------------------------

def _schreib_skript(path, ausgabe, mtime):
    """A step script that writes *ausgabe* (relative to the project) when run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "from pathlib import Path\n"
        f"p = Path({ausgabe!r}); p.parent.mkdir(parents=True, exist_ok=True)\n"
        "p.write_text('neu', encoding='utf-8')\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def _zwei_schritte(tmp_path, *, a_veraltet=True, b_inputs=("Geodaten/a.txt",), dritter=False):
    """A schreibt Geodaten/a.txt, B liest es und schreibt Geodaten/b.txt.

    Mit ``a_veraltet`` ist A's Skript neuer als seine Ausgabe (A läuft nach
    eigener Regel); B ist für sich frisch: Ausgabe neuer als Eingabe und Skript.
    """
    import yaml

    _schreib_skript(tmp_path / "scripts" / "a.py", "Geodaten/a.txt",
                    3000 if a_veraltet else 1000)
    _touch(tmp_path / "Geodaten" / "a.txt", 2000)
    _schreib_skript(tmp_path / "scripts" / "b.py", "Geodaten/b.txt", 1000)
    _touch(tmp_path / "Geodaten" / "b.txt", 4000)
    steps = [
        {"name": "A", "script": "scripts/a.py", "output": "Geodaten/a.txt"},
        {"name": "B", "script": "scripts/b.py", "inputs": list(b_inputs),
         "output": "Geodaten/b.txt"},
    ]
    if dritter:
        _schreib_skript(tmp_path / "scripts" / "c.py", "Geodaten/c.txt", 1000)
        _touch(tmp_path / "Geodaten" / "c.txt", 5000)
        steps.append({"name": "C", "script": "scripts/c.py",
                      "inputs": ["Geodaten/b.txt"], "output": "Geodaten/c.txt"})
    (tmp_path / "workflow.yaml").write_text(
        yaml.safe_dump({"project": {"name": "t"}, "steps": steps}), encoding="utf-8")


def _dry_run_status(tmp_path, capsys):
    import re

    from pbs_gis.runner import run_workflow

    assert run_workflow(tmp_path, dry_run=True) is True
    return dict(re.findall(r"\] (\S+) \[([^\]]+)\]", capsys.readouterr().out))


def test_dry_run_zeigt_den_konsumenten_eines_laufenden_schritts_als_laufend(tmp_path, capsys):
    """A läuft, B konsumiert A's Ausgabe und ist sonst frisch -> B läuft im Plan."""
    _zwei_schritte(tmp_path)
    status = _dry_run_status(tmp_path, capsys)
    assert status["A"] == "auto"
    assert status["B"] == "auto — input from: A"


def test_dry_run_plan_stimmt_mit_dem_echten_lauf_ueberein(tmp_path, capsys):
    """Gegenprobe am echten Lauf: B läuft dort tatsächlich, weil A's Ausgabe neu ist."""
    from pbs_gis.runner import run_workflow

    _zwei_schritte(tmp_path)
    plan = _dry_run_status(tmp_path, capsys)
    assert run_workflow(tmp_path) is True
    lauf = capsys.readouterr().out
    for name in ("A", "B"):
        gelaufen = f"{name} — done" in lauf
        assert gelaufen == (not plan[name].startswith("SKIP")), (name, plan[name])
        assert gelaufen
    assert (tmp_path / "Geodaten" / "b.txt").read_text(encoding="utf-8") == "neu"


def test_dry_run_kaskade_ist_transitiv(tmp_path, capsys):
    _zwei_schritte(tmp_path, dritter=True)
    status = _dry_run_status(tmp_path, capsys)
    assert status["C"] == "auto — input from: B"


def test_dry_run_frischer_vorlaeufer_laesst_den_konsumenten_aus(tmp_path, capsys):
    """Negativprobe: läuft A nicht, bleibt B übersprungen."""
    _zwei_schritte(tmp_path, a_veraltet=False)
    status = _dry_run_status(tmp_path, capsys)
    assert status["A"] == "SKIP (outputs exist)"
    assert status["B"] == "SKIP (outputs exist)"


def test_dry_run_verzeichnis_eingabe_sieht_die_ausgabe_darin(tmp_path, capsys):
    """Ein Verzeichnis als Eingabe enthält A's Ausgabe — der echte Lauf sähe sie."""
    _zwei_schritte(tmp_path, b_inputs=("Geodaten",))
    # b.txt liegt selbst in Geodaten/ und ist neuer als alles andere dort.
    status = _dry_run_status(tmp_path, capsys)
    assert status["B"] == "auto — input from: A"


def test_params_aenderung_in_workflow_yaml_loest_keinen_neulauf_aus(tmp_path, capsys):
    """Die Params eines Schritts sind keine Eingabe der Frische-Regel.

    Ein geänderter ``layers:``-Eintrag in workflow.yaml (Datei neuer als die
    Ausgabe) lässt einen frischen Schritt übersprungen — im echten Lauf wie im
    Plan.
    """
    import yaml

    from pbs_gis.runner import run_workflow

    out = _touch(tmp_path / "Zeichnung" / "plan.dxf", 2000)
    _touch(tmp_path / "Geodaten" / "g.gpkg", 1000)
    step = {"name": "DXF", "template": "cad_export", "inputs": ["Geodaten/g.gpkg"],
            "params": {"crs": "EPSG:25833", "styles": {}, "layers": [
                {"source": "Geodaten/g.gpkg", "target_layer": "Neu", "style": "g"}]},
            "output": "Zeichnung/plan.dxf"}
    wf = tmp_path / "workflow.yaml"
    wf.write_text(yaml.safe_dump({"project": {"name": "t"}, "steps": [step]}),
                  encoding="utf-8")
    assert wf.stat().st_mtime > out.stat().st_mtime
    assert _dry_run_status(tmp_path, capsys)["DXF"] == "SKIP (outputs exist)"
    assert run_workflow(tmp_path) is True
    assert "DXF — skipped" in capsys.readouterr().out
    assert out.stat().st_mtime == 2000


# --- depends_on makes the upstream step's outputs an input ---------------------

def _bilanz_kette():
    erzeuger = {"name": "erzeuger", "script": "s.py", "outputs": ["kat.gpkg"]}
    bilanz = {"name": "bilanz", "template": "publish_bilanz",
              "output": "bilanz.yaml", "depends_on": ["erzeuger"]}
    return erzeuger, bilanz


def test_should_skip_depends_on_upstream_output_newer_runs(tmp_path):
    # A step naming only its producer (no ``inputs:``) still reads the
    # producer's outputs; a rewritten output must make it stale.
    erzeuger, bilanz = _bilanz_kette()
    _touch(tmp_path / "bilanz.yaml", mtime=1000)
    _touch(tmp_path / "kat.gpkg", mtime=2000)
    by_name = {s["name"]: s for s in (erzeuger, bilanz)}
    assert should_skip(bilanz, tmp_path, by_name) is False


def test_should_skip_depends_on_upstream_output_older_skips(tmp_path):
    erzeuger, bilanz = _bilanz_kette()
    _touch(tmp_path / "bilanz.yaml", mtime=2000)
    _touch(tmp_path / "kat.gpkg", mtime=1000)
    by_name = {s["name"]: s for s in (erzeuger, bilanz)}
    assert should_skip(bilanz, tmp_path, by_name) is True


def test_plan_runs_depends_on_cascades_without_declared_inputs(tmp_path):
    erzeuger, bilanz = _bilanz_kette()
    _touch(tmp_path / "s.py", mtime=3000)          # edited script → erzeuger runs
    _touch(tmp_path / "kat.gpkg", mtime=2000)
    _touch(tmp_path / "bilanz.yaml", mtime=2500)   # bilanz newer than kat today
    plan = plan_runs([erzeuger, bilanz], tmp_path)
    assert plan == {"erzeuger": [], "bilanz": ["erzeuger"]}
