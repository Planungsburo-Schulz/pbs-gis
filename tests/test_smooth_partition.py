"""Tests für die Partitionsglättung (``pbs_gis.geometry.smooth_partition``).

Der Defekt, den sie behebt, ist unsichtbar, bis man die Summe bildet: geglättete
Nachbarflächen schrumpfen voneinander weg, und die Karte hat plötzlich Spalten,
die niemand gezeichnet hat. Die Tests prüfen deshalb Lücke und Überlappung,
nicht das Aussehen.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import Polygon

from pbs_gis import smooth_partition, smooth_polygons

CRS = "EPSG:25833"


def _zackige_grenze(y_mitte: float, breite: float, zacke: float = 0.5) -> list:
    """Punkte einer zitternden Trennlinie — wie eine Segmentgrenze."""
    rng = np.random.default_rng(2)
    return [(x, y_mitte + rng.uniform(-zacke, zacke))
            for x in np.arange(0, breite + 0.5, 0.5)]


@pytest.fixture()
def partition() -> tuple[gpd.GeoDataFrame, Polygon]:
    """Zwei Flächen, die sich eine zackige Grenze teilen — lückenlos."""
    b = 40.0
    grenze = _zackige_grenze(20.0, b)
    unten = Polygon([(0, 0), (b, 0)] + list(reversed(grenze)))
    oben = Polygon([(0, 40), (b, 40)] + grenze[::-1][::-1][::-1][::-1])
    oben = Polygon(grenze + [(b, 40), (0, 40)])
    gebiet = Polygon([(0, 0), (b, 0), (b, 40), (0, 40)])
    gdf = gpd.GeoDataFrame({"klasse": ["befestigt", "Grünfläche"]},
                           geometry=[unten, oben], crs=CRS)
    return gdf, gebiet


def test_eingabe_ist_lueckenlos(partition) -> None:
    """Prüft die Voraussetzung — sonst misst der eigentliche Test nichts."""
    gdf, gebiet = partition
    from shapely.ops import unary_union

    assert gebiet.difference(unary_union(list(gdf.geometry))).area < 0.01


def test_einzelglaettung_reisst_luecken(partition) -> None:
    """Der Defekt, um den es geht: derselbe Eingang, flächenweise geglättet."""
    gdf, gebiet = partition
    from shapely.ops import unary_union

    aus, _ = smooth_polygons(gdf, zacken_m=1.2, toleranz_m=0.4, runden=2)
    luecke = gebiet.difference(unary_union(list(aus.geometry))).area

    assert luecke > 5.0, f"Lücke nur {luecke} m² — Test misst den Defekt nicht"


def test_partitionsglaettung_haelt_dicht(partition) -> None:
    gdf, gebiet = partition

    aus, info = smooth_partition(gdf, "klasse", clip=gebiet, toleranz_m=0.4, runden=2)

    assert info["luecke_m2"] < 0.5
    assert info["ueberlappung_m2"] < 0.5


def test_klassen_bleiben_erhalten(partition) -> None:
    gdf, gebiet = partition

    aus, _ = smooth_partition(gdf, "klasse", clip=gebiet)

    assert set(aus["klasse"]) == {"befestigt", "Grünfläche"}


def test_zackigkeit_geht_deutlich_zurueck(partition) -> None:
    """Stützpunkte sind das falsche Mass — Rundung fügt welche HINZU. Gemessen
    wird der Richtungswechsel je Meter Rand, also das, was zackig aussieht."""
    gdf, gebiet = partition

    # Die Toleranz muss über der Zackenamplitude liegen (hier 0,5 m), sonst
    # kann kein Verfahren die Zacke von einer echten Ecke unterscheiden.
    aus, info = smooth_partition(gdf, "klasse", clip=gebiet, toleranz_m=1.0, runden=2)

    assert info["zackigkeit_nachher"] < info["zackigkeit_vorher"] / 3


def test_flaechenverhaeltnis_bleibt(partition) -> None:
    """Die Grenze darf sich glätten, aber nicht wandern."""
    gdf, gebiet = partition
    vorher = gdf.set_index("klasse").area

    aus, _ = smooth_partition(gdf, "klasse", clip=gebiet, toleranz_m=0.4, runden=2)
    nachher = aus.dissolve(by="klasse").area

    for k in vorher.index:
        assert nachher[k] == pytest.approx(vorher[k], rel=0.05)


def test_leerer_rahmen() -> None:
    leer = gpd.GeoDataFrame({"klasse": []}, geometry=[], crs=CRS)

    aus, info = smooth_partition(leer, "klasse")

    assert len(aus) == 0
