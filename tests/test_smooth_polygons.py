"""Tests für die Umriss-Glättung (``pbs_gis.geometry.smooth_polygons``).

Der Zweck ist eine zeichenbare Kante, der Preis ist Fläche. Beides wird geprüft:
die Zacken müssen weg sein UND die Fläche darf nur wenig wandern — eine Glättung,
die 10 % Fläche kostet, hat die Bilanz verändert, für die sie gemacht wurde.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import Polygon

from pbs_gis import smooth_polygons

CRS = "EPSG:25833"


def _zackiges_quadrat(seite: float = 40.0, zacke: float = 0.4, schritt: float = 0.5) -> Polygon:
    """Quadrat, dessen Ränder im Sägezahnmuster zittern — wie eine Segmentgrenze."""
    rng = np.random.default_rng(0)
    punkte = []
    n = int(seite / schritt)
    for i in range(n):      # unten
        punkte.append((i * schritt, rng.uniform(-zacke, zacke)))
    for i in range(n):      # rechts
        punkte.append((seite + rng.uniform(-zacke, zacke), i * schritt))
    for i in range(n):      # oben
        punkte.append((seite - i * schritt, seite + rng.uniform(-zacke, zacke)))
    for i in range(n):      # links
        punkte.append((rng.uniform(-zacke, zacke), seite - i * schritt))
    return Polygon(punkte)


@pytest.fixture()
def zackig() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"klasse": ["Grünfläche"]},
                            geometry=[_zackiges_quadrat()], crs=CRS)


def test_stuetzpunkte_gehen_deutlich_zurueck(zackig: gpd.GeoDataFrame) -> None:
    aus, info = smooth_polygons(zackig, zacken_m=1.0, toleranz_m=0.5, runden=2)

    assert info["stuetzpunkte_vorher"] > 300
    assert info["stuetzpunkte_nachher"] < info["stuetzpunkte_vorher"] / 3


def test_flaeche_bleibt_nahezu_erhalten(zackig: gpd.GeoDataFrame) -> None:
    """Der eigentliche Prüfstein: eine Glättung, die Fläche frisst, verfälscht
    die Bilanz, für die sie gemacht wurde."""
    aus, info = smooth_polygons(zackig, zacken_m=1.0, toleranz_m=0.5, runden=2)

    assert abs(info["flaechenaenderung_pct"]) < 3.0


def test_form_bleibt_erhalten(zackig: gpd.GeoDataFrame) -> None:
    aus, _ = smooth_polygons(zackig, zacken_m=1.0, toleranz_m=0.5, runden=2)
    original = zackig.geometry.iloc[0]
    geglaettet = aus.geometry.iloc[0]

    # Die geglättete Fläche liegt im Wesentlichen über der originalen
    assert geglaettet.intersection(original).area / original.area > 0.95


def test_runden_null_laesst_ecken_scharf(zackig: gpd.GeoDataFrame) -> None:
    scharf, i_scharf = smooth_polygons(zackig, zacken_m=1.0, toleranz_m=0.5, runden=0)
    rund, i_rund = smooth_polygons(zackig, zacken_m=1.0, toleranz_m=0.5, runden=3)

    assert i_rund["stuetzpunkte_nachher"] > i_scharf["stuetzpunkte_nachher"]


def test_attribute_bleiben(zackig: gpd.GeoDataFrame) -> None:
    aus, _ = smooth_polygons(zackig, zacken_m=1.0)

    assert list(aus["klasse"]) == ["Grünfläche"]


def test_leerer_rahmen_geht_durch() -> None:
    leer = gpd.GeoDataFrame({"klasse": []}, geometry=[], crs=CRS)

    aus, info = smooth_polygons(leer)

    assert len(aus) == 0
    assert info["flaechenaenderung_pct"] == 0.0


def test_splitter_fallen_weg() -> None:
    gdf = gpd.GeoDataFrame(
        {"klasse": ["a", "b"]},
        geometry=[Polygon([(0, 0), (30, 0), (30, 30), (0, 30)]),
                  Polygon([(100, 100), (100.5, 100), (100.5, 100.5), (100, 100.5)])],
        crs=CRS)

    aus, _ = smooth_polygons(gdf, zacken_m=0.8, min_flaeche_m2=2.0)

    assert list(aus["klasse"]) == ["a"]
