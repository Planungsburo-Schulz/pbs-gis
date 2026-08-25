"""Unit tests for the commercial-basemap check (``pbs_gis.basemap``).

The check exists because the failure is quiet: a commercial layer added once as a
quick backdrop stays in the project file and travels through every later map. So
the assertions cover both halves — it finds the tile host whatever the layer is
called, and it stays silent on official services.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from pbs_gis.basemap import (declared_exception, find_commercial_basemaps,
                             official_aerial_recipes, scan_text)

GOOGLE_SRC = ("crs=EPSG:3857&format&type=xyz&url=https://mt1.google.com/vt/"
              "lyrs%3Ds%26x%3D%7Bx%7D%26y%3D%7By%7D%26z%3D%7Bz%7D&zmax=20&zmin=0")
DOP_SRC = ("contextualWMSLegend=0&crs=EPSG:25833&dpiMode=7&format=image/jpeg"
           "&layers=mv_dop&styles&url=https://www.geodaten-mv.de/dienste/adv_dop")


def _project_xml(*layers: tuple[str, str]) -> str:
    body = "".join(
        f"<maplayer><layername>{name}</layername>"
        f"<datasource>{src}</datasource></maplayer>"
        for name, src in layers
    )
    return f"<qgis version='3.44'><projectlayers>{body}</projectlayers></qgis>"


def _write_qgs(path: Path, *layers: tuple[str, str]) -> Path:
    path.write_text(_project_xml(*layers))
    return path


def test_finds_google_tiles(tmp_path: Path) -> None:
    p = _write_qgs(tmp_path / "Karte.qgs", ("Google Satellite", GOOGLE_SRC))

    hits = find_commercial_basemaps(tmp_path)

    assert len(hits) == 1
    assert hits[0].provider == "Google"
    assert hits[0].layer_name == "Google Satellite"
    assert hits[0].file == p


def test_matches_the_host_not_the_layer_name(tmp_path: Path) -> None:
    """A renamed layer is the case worth catching — 'Luftbild' pointing at Google
    reads as official to every later reader."""
    _write_qgs(tmp_path / "Karte.qgs", ("Luftbild", GOOGLE_SRC))

    hits = find_commercial_basemaps(tmp_path)

    assert [h.provider for h in hits] == ["Google"]


def test_official_wms_is_not_a_hit(tmp_path: Path) -> None:
    """The silence half: a check that flagged everything would be discounted."""
    _write_qgs(tmp_path / "Karte.qgs", ("DOP20 MV", DOP_SRC))

    assert find_commercial_basemaps(tmp_path) == []


def test_reads_a_zipped_qgz(tmp_path: Path) -> None:
    inner = _project_xml(("Bing", "type=xyz&url=https://ecn.t0.tiles.virtualearth.net/x"))
    qgz = tmp_path / "Karte.qgz"
    with zipfile.ZipFile(qgz, "w") as zf:
        zf.writestr("Karte.qgs", inner)

    hits = find_commercial_basemaps(tmp_path)

    assert [h.provider for h in hits] == ["Bing"]


def test_several_providers_in_one_project(tmp_path: Path) -> None:
    _write_qgs(tmp_path / "Karte.qgs",
               ("Sat", GOOGLE_SRC),
               ("Esri", "type=xyz&url=https://services.arcgisonline.com/tile/x"),
               ("DOP", DOP_SRC))

    assert sorted(h.provider for h in find_commercial_basemaps(tmp_path)) == ["Esri", "Google"]


def test_a_broken_project_file_does_not_stop_the_scan(tmp_path: Path) -> None:
    (tmp_path / "kaputt.qgz").write_bytes(b"not a zip")
    _write_qgs(tmp_path / "Karte.qgs", ("Sat", GOOGLE_SRC))

    assert len(find_commercial_basemaps(tmp_path)) == 1


def test_layer_without_datasource_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "Karte.qgs").write_text(
        "<qgis><projectlayers><maplayer><layername>X</layername></maplayer>"
        "</projectlayers></qgis>")

    assert find_commercial_basemaps(tmp_path) == []


# --- declared exception ------------------------------------------------------

def test_exception_needs_a_reason(tmp_path: Path) -> None:
    (tmp_path / "workflow.yaml").write_text(
        'project:\n  name: X\n  basemap_exception: ""\n')

    assert declared_exception(tmp_path) is None


def test_exception_with_reason_is_returned(tmp_path: Path) -> None:
    (tmp_path / "workflow.yaml").write_text(
        'project:\n  name: X\n  basemap_exception: "nur interne Orientierung"\n')

    assert declared_exception(tmp_path) == "nur interne Orientierung"


def test_no_workflow_file_is_no_exception(tmp_path: Path) -> None:
    assert declared_exception(tmp_path) is None


def test_official_recipes_offer_an_alternative() -> None:
    """The check has to name what to use instead, or it only scolds."""
    names = [n for n, _ in official_aerial_recipes()]

    assert "mv_dop" in names
    assert len(names) >= 2
