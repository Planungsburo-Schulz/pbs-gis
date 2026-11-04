"""Export — die eingepasste Geometrie je Klasse als GeoPackage-Layer.

Instrumenten-Paar: der Rundweg (schreiben, mit geopandas zurücklesen) trifft
den auf die Eingabe angewandten Transform, und ein absichtlich falscher
Transform verfehlt dieselben Koordinaten — sonst prüft der Vergleich nichts.

Der Vergleich ist absolut (``rtol=0``): die relative Default-Toleranz von
``np.allclose`` ist bei Rechtswerten um 5,9 Mio. m rund 59 m breit und ließe
einen um 0,1° verdrehten Transform als Treffer durch.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest

from pbs_gis.einpassung import EinpassungError, apply_points, export_gpkg
from pbs_gis.georef import SimilarityTransform

T = SimilarityTransform(scale=1.0005, rotation_deg=-2.035, tx=394697.044, ty=5934685.239)
LAGEN = {1: np.zeros(2), 2: np.array([7.655, 343.936])}
KLASSE = {1: "anker", 2: "kette"}
LAYERS = {
    "leitung": {1: [np.array([[10.0, 20.0], [30.0, 25.0], [42.0, 60.0]])],
                2: [np.array([[5.0, 5.0], [6.0, 80.0]]),
                    np.array([[100.0, 100.0], [140.0, 90.0]])]},
    "katastersignatur": {1: [np.array([[0.0, 0.0], [50.0, 0.0]])],
                         2: []},
}


def _schreiben(tmp_path, **kw):
    args = dict(layers=LAYERS, transform=T, positions=LAGEN, lageklasse=KLASSE,
                crs="EPSG:25833")
    args.update(kw)
    path = tmp_path / "out.gpkg"
    return path, export_gpkg(path, **args)


def _soll(transform):
    """Erwartete Weltkoordinaten je (Layer, Blatt, Zug) — aus der Definition."""
    return {(name, p, i): apply_points(transform, P - LAGEN[p])
            for name, per in LAYERS.items() for p, chains in per.items()
            for i, P in enumerate(chains)}


def _gelesen(path):
    out = {}
    for name in LAYERS:
        gdf = gpd.read_file(path, layer=name)
        assert gdf.crs.to_epsg() == 25833
        for p, grp in gdf.groupby("blatt"):
            for i, geom in enumerate(grp.geometry):
                out[(name, int(p), i)] = np.asarray(geom.coords)
    return out


def test_rundweg_trifft_den_angewandten_transform(tmp_path):
    path, _ = _schreiben(tmp_path)
    soll, ist = _soll(T), _gelesen(path)
    assert sorted(ist) == sorted(soll)
    for key in soll:
        assert np.allclose(ist[key], soll[key], rtol=0, atol=1e-6)


def test_falscher_transform_verfehlt_den_vergleich(tmp_path):
    """Negativprobe: derselbe Vergleich gegen einen um 0,1° verdrehten Transform reißt."""
    path, _ = _schreiben(tmp_path)
    falsch = SimilarityTransform(scale=T.scale, rotation_deg=T.rotation_deg + 0.1,
                                 tx=T.tx, ty=T.ty)
    soll, ist = _soll(falsch), _gelesen(path)
    assert not all(np.allclose(ist[k], soll[k], rtol=0, atol=1e-6) for k in soll)


def test_attribute_je_zug(tmp_path):
    path, _ = _schreiben(tmp_path)
    gdf = gpd.read_file(path, layer="leitung")
    assert sorted(c for c in gdf.columns if c != "geometry") == ["blatt", "laenge_m",
                                                                "lageklasse"]
    assert dict(zip(gdf["blatt"], gdf["lageklasse"])) == {1: "anker", 2: "kette"}
    welt = np.asarray(gdf.geometry.iloc[0].coords)
    assert gdf["laenge_m"].iloc[0] == pytest.approx(
        float(np.hypot(*np.diff(welt, axis=0).T).sum()))


def test_ein_layer_je_klasse(tmp_path):
    path, gdfs = _schreiben(tmp_path)
    assert sorted(gpd.list_layers(path)["name"]) == ["katastersignatur", "leitung"]
    assert {n: len(g) for n, g in gdfs.items()} == {"leitung": 3, "katastersignatur": 1}


def test_vorhandene_datei_wird_nicht_ueberschrieben(tmp_path):
    path, _ = _schreiben(tmp_path)
    with pytest.raises(EinpassungError, match="existiert"):
        export_gpkg(path, layers=LAYERS, transform=T, positions=LAGEN,
                    lageklasse=KLASSE, crs="EPSG:25833")


def test_blatt_ohne_lage_wirft(tmp_path):
    with pytest.raises(EinpassungError, match="Blatt 2 .*Lage"):
        _schreiben(tmp_path, positions={1: LAGEN[1]})


def test_blatt_ohne_lageklasse_wirft(tmp_path):
    with pytest.raises(EinpassungError, match="Blatt 2 .*Lageklasse"):
        _schreiben(tmp_path, lageklasse={1: "anker"})


def test_unbekannte_lageklasse_wirft(tmp_path):
    with pytest.raises(EinpassungError, match="'verbund'"):
        _schreiben(tmp_path, lageklasse={1: "anker", 2: "verbund"})


def test_leerer_layer_wirft(tmp_path):
    with pytest.raises(EinpassungError, match="'leer'"):
        _schreiben(tmp_path, layers=dict(LAYERS, leer={1: [], 2: []}))
