"""Tests for Stand-Manifest emission (``pbs_gis.manifest``) and the runner's
``publiziert:`` binding (Phase-4 P4-B.1).

Covers: the written manifest's structure and hashes, error cases (missing
artefact/source), the runner ``_maybe_write_manifest`` hook, an end-to-end
``run_workflow`` run that emits a manifest for a published step, and — when the
pbs-projekt sibling checkout is reachable — a strict validation of a real
emission against the authoritative ``pbs_projekt.schema.ManifestDatei`` model
(the Abgleich).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

from pbs_gis.manifest import (
    MANIFEST_SCHEMA_VERSION,
    sha256_file,
    werkzeug_id,
    write_manifest,
)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# --- write_manifest --------------------------------------------------------

def test_write_manifest_structure_and_hashes(tmp_path):
    art = _write(tmp_path / "flaechenbilanz.yaml", "summe: 42\n")
    q1 = _write(tmp_path / "kategorien.gpkg", "not really a gpkg but bytes")
    manifest_path = write_manifest(
        art,
        parameter={"mindestgroesse_ha": 5},
        quellen=[q1],
        werkzeug=werkzeug_id("publish_bilanz"),
        basis=tmp_path,
    )

    assert manifest_path == tmp_path / "flaechenbilanz.yaml.manifest.yaml"
    m = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))

    assert set(m) == {
        "schema_version", "stand", "artefakt", "hash",
        "parameter", "quellen", "werkzeug",
    }
    assert m["schema_version"] == MANIFEST_SCHEMA_VERSION == 1
    assert m["stand"] == date.today().isoformat()
    assert m["artefakt"] == "flaechenbilanz.yaml"  # bare name echo
    assert m["hash"] == _sha(art.read_bytes())
    assert m["parameter"] == {"mindestgroesse_ha": 5}
    assert m["quellen"] == [{"pfad": "kategorien.gpkg", "hash": _sha(q1.read_bytes())}]
    assert m["werkzeug"].startswith("pbs_gis/") and "publish_bilanz" in m["werkzeug"]


def test_write_manifest_quellen_relative_to_basis(tmp_path):
    art = _write(tmp_path / "out.dxf", "x")
    sub = tmp_path / "Geodaten"
    sub.mkdir()
    q = _write(sub / "src.gpkg", "y")
    manifest_path = write_manifest(
        art, parameter={}, quellen=[q], werkzeug="w", basis=tmp_path
    )
    m = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert m["quellen"][0]["pfad"] == "Geodaten/src.gpkg"  # portable, not absolute


def test_write_manifest_missing_artefakt_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        write_manifest(tmp_path / "nope.yaml", parameter={}, quellen=[], werkzeug="w")


def test_write_manifest_missing_quelle_raises(tmp_path):
    art = _write(tmp_path / "a.yaml", "x")
    with pytest.raises(FileNotFoundError):
        write_manifest(art, parameter={}, quellen=[tmp_path / "gone.gpkg"], werkzeug="w")


def _beistell(quelle: Path, inhalt) -> Path:
    p = quelle.with_name(quelle.name + ".quellstand.json")
    p.write_text(inhalt if isinstance(inhalt, str) else json.dumps(inhalt), encoding="utf-8")
    return p


def test_write_manifest_liest_beistelldatei(tmp_path):
    art = _write(tmp_path / "plan.dxf", "x")
    mit = _write(tmp_path / "flst.gpkg", "a")
    ohne_stand = _write(tmp_path / "geb.gpkg", "b")
    ohne = _write(tmp_path / "eigen.gpkg", "c")
    _beistell(mit, {"quell_stand": "2026-07-09", "dienst": "https://d.invalid/wfs",
                    "abgerufen": "2026-10-21T10:00:00+02:00"})
    _beistell(ohne_stand, {"quell_stand": None, "dienst": "https://d.invalid/wfs",
                           "abgerufen": None})
    manifest_path = write_manifest(
        art, parameter={}, quellen=[mit, ohne_stand, ohne], werkzeug="w", basis=tmp_path
    )
    q = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))["quellen"]
    assert q[0] == {"pfad": "flst.gpkg", "hash": _sha(b"a"),
                    "quell_stand": "2026-07-09", "dienst": "https://d.invalid/wfs"}
    assert q[1] == {"pfad": "geb.gpkg", "hash": _sha(b"b"),
                    "quell_stand": None, "dienst": "https://d.invalid/wfs"}
    assert q[2] == {"pfad": "eigen.gpkg", "hash": _sha(b"c")}  # keine Beistell-Datei: kein Feld


def test_manifest_aus_wfs_download_traegt_quell_stand(tmp_path, monkeypatch):
    """Die Kante zwischen Schreiber (wfs.download) und Leser (write_manifest):
    dieselbe Beistell-Datei, ohne Absprache über den Namen hinaus."""
    import geopandas as gpd
    from shapely.geometry import Point

    from pbs_gis import wfs

    monkeypatch.setattr(wfs.gpd, "read_file", lambda source, **kw: gpd.GeoDataFrame(
        {"geometry": [Point(400000, 6035000)]}, crs="EPSG:25833"))
    monkeypatch.setattr(wfs, "_ermittle_quell_stand", lambda dienst, layer, version: "2026-07-09")
    out = tmp_path / "flst.gpkg"
    wfs.download("https://d.invalid/wfs?x=1", "adv:AX_Flurstueck", crs="EPSG:25833",
                 cache_dir=tmp_path / "cache", output_path=out)
    art = _write(tmp_path / "plan.dxf", "x")
    m = yaml.safe_load(write_manifest(art, parameter={}, quellen=[out], werkzeug="w",
                                      basis=tmp_path).read_text(encoding="utf-8"))
    assert m["quellen"][0]["quell_stand"] == "2026-07-09"
    assert m["quellen"][0]["dienst"] == "https://d.invalid/wfs#adv:AX_Flurstueck"


@pytest.mark.parametrize("inhalt", [
    "{kein json",
    {"quell_stand": "2026-07-09", "abgerufen": None},  # dienst fehlt
    ["kein", "objekt"],
])
def test_write_manifest_unlesbare_beistelldatei_bricht(tmp_path, inhalt):
    art = _write(tmp_path / "plan.dxf", "x")
    q = _write(tmp_path / "flst.gpkg", "a")
    beistell = _beistell(q, inhalt)
    with pytest.raises(ValueError, match=str(beistell)):
        write_manifest(art, parameter={}, quellen=[q], werkzeug="w")
    assert not (tmp_path / "plan.dxf.manifest.yaml").exists()


def test_sha256_file_matches(tmp_path):
    f = _write(tmp_path / "f.bin", "hello world")
    assert sha256_file(f) == _sha(b"hello world")


# --- runner binding (publiziert flag) --------------------------------------

def test_maybe_write_manifest_hook(tmp_path):
    from pbs_gis.runner import _maybe_write_manifest

    (tmp_path / "Geodaten").mkdir()
    out = _write(tmp_path / "Geodaten" / "result.gpkg", "data")
    src = _write(tmp_path / "Grundlagen_src.gpkg", "input")

    step = {
        "name": "Publish result",
        "template": "some_template",
        "output": "Geodaten/result.gpkg",
        "inputs": ["Grundlagen_src.gpkg"],
        "publiziert": True,
        "manifest_parameter": {"crs": "EPSG:25833"},
    }
    _maybe_write_manifest(step, tmp_path)

    manifest = out.with_name("result.gpkg.manifest.yaml")
    assert manifest.exists()
    m = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    assert m["hash"] == _sha(out.read_bytes())
    assert m["parameter"] == {"crs": "EPSG:25833"}
    assert m["quellen"][0]["pfad"] == "Grundlagen_src.gpkg"
    assert "some_template" in m["werkzeug"]


def test_maybe_write_manifest_no_flag_no_manifest(tmp_path):
    from pbs_gis.runner import _maybe_write_manifest

    out = _write(tmp_path / "result.gpkg", "data")
    step = {"name": "Plain", "output": "result.gpkg"}  # no publiziert
    _maybe_write_manifest(step, tmp_path)
    assert not out.with_name("result.gpkg.manifest.yaml").exists()


def test_run_workflow_publiziert_emits_manifest(tmp_path):
    """End-to-end: a published cad_export step writes its DXF *and* a manifest."""
    import geopandas as gpd
    from shapely.geometry import Polygon

    from pbs_gis.runner import run_workflow

    (tmp_path / "Geodaten").mkdir()
    square = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    gpd.GeoDataFrame({"geometry": [square]}, crs="EPSG:25833").to_file(
        tmp_path / "Geodaten" / "geltung.gpkg", driver="GPKG"
    )
    styles = _write(
        tmp_path / "cad_styles.yaml",
        "schema_version: 1\nstyles:\n  geltungsbereich:\n    layer:\n      color: red\n",
    )

    workflow = {
        "project": {"name": "manifest-e2e"},
        "steps": [
            {
                "name": "Export DXF",
                "template": "cad_export",
                "params": {
                    "crs": "EPSG:25833",
                    "styles": "cad_styles.yaml",
                    "layers": [
                        {
                            "source": "Geodaten/geltung.gpkg",
                            "target_layer": "Geltungsbereich",
                            "style": "geltungsbereich",
                        }
                    ],
                },
                "output": "Zeichnung/plan.dxf",
                "inputs": ["Geodaten/geltung.gpkg"],
                "publiziert": True,
                "manifest_parameter": {"dxfversion": "R2010"},
            }
        ],
    }
    (tmp_path / "workflow.yaml").write_text(
        yaml.safe_dump(workflow, allow_unicode=True), encoding="utf-8"
    )

    assert run_workflow(tmp_path) is True

    dxf = tmp_path / "Zeichnung" / "plan.dxf"
    manifest = dxf.with_name("plan.dxf.manifest.yaml")
    assert dxf.exists()
    assert manifest.exists()
    m = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    assert m["hash"] == sha256_file(dxf)
    assert m["artefakt"] == "plan.dxf"
    assert m["quellen"][0]["pfad"] == "Geodaten/geltung.gpkg"
    assert m["parameter"] == {"dxfversion": "R2010"}


# --- Abgleich against the authoritative pbs-projekt model -------------------

def _load_manifest_datei_model():
    """Import ``pbs_projekt.schema.ManifestDatei`` from a sibling checkout.

    Route chosen (reported to the operator): IMPORT-BY-PATH, test-only. The
    runtime module ``pbs_gis.manifest`` has NO pbs-projekt dependency; only
    this test reaches into the sibling repo to validate a real emission against
    the authority. Skips (never fails) when pbs-projekt or pydantic is absent,
    so pbs_gis remains standalone-testable.
    """
    src = os.environ.get("PBS_PROJEKT_SRC") or str(
        Path("~/dev/Planungsbüro-Schulz/pbs-projekt/src").expanduser()
    )
    if not Path(src).is_dir():
        pytest.skip(f"pbs-projekt src not found ({src}); Abgleich skipped")
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        from pbs_projekt.schema import ManifestDatei  # type: ignore
    except Exception as e:  # pydantic missing / import error
        pytest.skip(f"pbs_projekt not importable ({e}); Abgleich skipped")
    return ManifestDatei


def test_emission_validates_against_pbs_projekt_manifestdatei(tmp_path):
    ManifestDatei = _load_manifest_datei_model()

    art = _write(tmp_path / "planstand.dxf", "dxf-bytes")
    q = _write(tmp_path / "Geodaten_src.gpkg", "src-bytes")
    manifest_path = write_manifest(
        art,
        parameter={"crs": "EPSG:25833", "mindestgroesse_ha": 5},
        quellen=[q],
        werkzeug=werkzeug_id("cad_export"),
        basis=tmp_path,
    )
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))

    # Strict parse: extra="forbid" + schema_version must equal the authority's.
    model = ManifestDatei(**data)
    assert model.schema_version == MANIFEST_SCHEMA_VERSION
    assert model.hash == sha256_file(art)
    assert model.artefakt == "planstand.dxf"
    assert model.quellen[0].pfad == "Geodaten_src.gpkg"
    assert str(model.stand) == date.today().isoformat()


def test_emission_mit_beistelldateien_validiert_gegen_manifestdatei(tmp_path):
    ManifestDatei = _load_manifest_datei_model()

    art = _write(tmp_path / "planstand.dxf", "dxf-bytes")
    mit = _write(tmp_path / "flst.gpkg", "a")
    ohne_stand = _write(tmp_path / "geb.gpkg", "b")
    ohne = _write(tmp_path / "eigen.gpkg", "c")
    _beistell(mit, {"quell_stand": "2026-07-09", "dienst": "https://d.invalid/wfs",
                    "abgerufen": "2026-10-21T10:00:00+02:00"})
    _beistell(ohne_stand, {"quell_stand": None, "dienst": "https://d.invalid/wfs",
                           "abgerufen": None})
    manifest_path = write_manifest(
        art, parameter={}, quellen=[mit, ohne_stand, ohne],
        werkzeug=werkzeug_id("cad_export"), basis=tmp_path,
    )
    model = ManifestDatei(**yaml.safe_load(manifest_path.read_text(encoding="utf-8")))
    assert str(model.quellen[0].quell_stand) == "2026-07-09"
    assert model.quellen[0].dienst == "https://d.invalid/wfs"
    assert model.quellen[1].quell_stand is None
    assert model.quellen[1].dienst == "https://d.invalid/wfs"
    assert model.quellen[2].quell_stand is None and model.quellen[2].dienst is None
