"""``wfs.download``: ein von Hand übergebener ``extent`` erreicht den Dienst in
der Projektion der Verbindung, auf jedem Weg, der eine BBOX sendet (OGR-WFS,
KVP-GetFeature, Stored Query). Offline — der Dienst ist ein Stub, geprüft wird,
was gesendet würde.

Erwartungswerte aus der Definition von EPSG:5650: dieselbe UTM-Zone 33N wie
EPSG:25833, nur mit Zonen-Präfix im Rechtswert (False Easting 33 500 000
statt 500 000) — Rechtswert + 33 000 000, Hochwert unverändert."""

from __future__ import annotations

import urllib.parse

import geopandas as gpd
import pytest
import yaml
from shapely.geometry import Point, box

from pbs_gis import wfs
from pbs_gis.recipes import load_recipe

RUEGEN_25833 = (395000.0, 6030000.0, 405000.0, 6040000.0)
RUEGEN_5650 = (33395000.0, 6030000.0, 33405000.0, 6040000.0)


def _recipe(tmp_path, **extra):
    data = {
        "name": "stub_5650",
        "connection": {"type": "wfs", "url": "https://stub.invalid/wfs",
                       "version": "1.1.0", "crs": "EPSG:5650", "layer": "t2_nsg_f"},
    }
    for key, value in extra.items():
        if key in ("bbox_kvp", "bbox_stored_query"):
            data["connection"][key] = value
        else:
            data[key] = value
    (tmp_path / "sources").mkdir(exist_ok=True)
    (tmp_path / "sources" / "stub_5650.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return load_recipe("stub_5650", project_dir=tmp_path)


@pytest.fixture
def gesendet(monkeypatch):
    """Fängt jeden read_file-Aufruf an den Dienst ab und liefert ein Feature in
    EPSG:5650; lokale Dateien (input_boundary) liest das echte read_file."""
    calls = []
    echt = gpd.read_file

    def stub(source, **kwargs):
        if not str(source).startswith(("WFS:", "https://stub.invalid")):
            return echt(source, **kwargs)
        calls.append((str(source), kwargs))
        return gpd.GeoDataFrame({"geometry": [Point(33400000, 6035000)]}, crs="EPSG:5650")

    monkeypatch.setattr(wfs.gpd, "read_file", stub)
    return calls


def _download(tmp_path, recipe, **kw):
    return wfs.download(None, "", recipe=recipe, cache_dir=tmp_path / "cache",
                        no_cache=True, **kw)


def _query(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


def _floats(text):
    return tuple(float(v) for v in text.split(",")[:4])


def test_ogr_pfad_sendet_extent_in_verbindungs_projektion(tmp_path, gesendet):
    recipe = _recipe(tmp_path, ausgabe_crs="EPSG:25833")
    _download(tmp_path, recipe, extent=RUEGEN_25833)
    (source, kwargs), = gesendet
    assert source == "WFS:https://stub.invalid/wfs"
    assert kwargs["bbox"] == pytest.approx(RUEGEN_5650, abs=0.01)


def test_kvp_pfad_sendet_extent_in_verbindungs_projektion(tmp_path, gesendet):
    recipe = _recipe(tmp_path, ausgabe_crs="EPSG:25833", bbox_kvp=True)
    _download(tmp_path, recipe, extent=RUEGEN_25833)
    q = _query(gesendet[0][0])
    assert q["SRSNAME"] == "EPSG:5650"
    assert q["BBOX"].endswith(",EPSG:5650")
    assert _floats(q["BBOX"]) == pytest.approx(RUEGEN_5650, abs=0.01)


def test_stored_query_pfad_sendet_extent_in_verbindungs_projektion(tmp_path, gesendet):
    recipe = _recipe(tmp_path, ausgabe_crs="EPSG:25833",
                     bbox_stored_query={"id": "bbox_sq"})
    _download(tmp_path, recipe, extent=RUEGEN_25833)
    q = _query(gesendet[0][0])
    assert q["CRS"] == "EPSG:5650"
    sent = tuple(float(q[k]) for k in ("x1", "y1", "x2", "y2"))
    assert sent == pytest.approx(RUEGEN_5650, abs=0.01)


def test_extent_crs_explizit_hebt_den_standard_auf(tmp_path, gesendet):
    recipe = _recipe(tmp_path, ausgabe_crs="EPSG:25833")
    _download(tmp_path, recipe, extent=RUEGEN_5650, extent_crs="EPSG:5650")
    assert gesendet[0][1]["bbox"] == pytest.approx(RUEGEN_5650, abs=0.01)


def test_ohne_ausgabe_crs_gilt_der_extent_in_der_verbindungs_projektion(tmp_path, gesendet):
    recipe = _recipe(tmp_path)
    _download(tmp_path, recipe, extent=RUEGEN_5650)
    assert gesendet[0][1]["bbox"] == RUEGEN_5650


def test_uebergebenes_crs_bleibt_projektion_des_extents(tmp_path, gesendet):
    # Aufrufer wie alkis.find_flurstuecke reichen crs= und den extent in diesem crs
    recipe = _recipe(tmp_path, bbox_kvp=True)
    _download(tmp_path, recipe, extent=RUEGEN_25833, crs="EPSG:25833")
    q = _query(gesendet[0][0])
    assert q["SRSNAME"] == "EPSG:25833"
    assert _floats(q["BBOX"]) == pytest.approx(RUEGEN_25833, abs=0.01)


def test_input_boundary_wird_nur_einmal_umgerechnet(tmp_path, gesendet):
    grenze = tmp_path / "grenze.gpkg"
    gpd.GeoDataFrame({"geometry": [box(*RUEGEN_25833)]}, crs="EPSG:25833").to_file(
        grenze, driver="GPKG")
    recipe = _recipe(tmp_path, ausgabe_crs="EPSG:25833")
    _download(tmp_path, recipe, input_boundary=grenze)
    assert gesendet[0][1]["bbox"] == pytest.approx(RUEGEN_5650, abs=0.01)
