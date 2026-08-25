"""Tests für Kantenübernahme und Splitterauflösung.

Beide Werkzeuge verändern eine fertige Klassenkarte, und beide könnten sie dabei
kaputtmachen: die Kantenübernahme durch Lücken, die Splitterauflösung durch
Flächen, die sie verschluckt. Die Tests prüfen genau das mit.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from pbs_gis import absorb_small, partition_along

CRS = "EPSG:25833"


@pytest.fixture()
def karte_und_kante():
    """Klassengrenze bei y=20, die gemessene Kante liegt 0,8 m daneben."""
    b = 40.0
    unten = Polygon([(0, 0), (b, 0), (b, 20), (0, 20)])
    oben = Polygon([(0, 20), (b, 20), (b, 40), (0, 40)])
    gebiet = Polygon([(0, 0), (b, 0), (b, 40), (0, 40)])
    gdf = gpd.GeoDataFrame({"klasse": ["befestigt", "Grünfläche"]},
                           geometry=[unten, oben], crs=CRS)
    kante = gpd.GeoDataFrame(geometry=[LineString([(0, 20.8), (b, 20.8)])], crs=CRS)
    return gdf, kante, gebiet


def test_grenze_wandert_auf_die_gemessene_kante(karte_und_kante) -> None:
    gdf, kante, gebiet = karte_und_kante

    aus, info = partition_along(gdf, "klasse", kante, clip=gebiet, naehe_m=1.5)

    fest = aus.loc[aus["klasse"] == "befestigt"].union_all()
    # Die befestigte Fläche reicht jetzt bis zur gemessenen Kante bei 20,8
    assert fest.bounds[3] == pytest.approx(20.8, abs=0.05)
    assert info["kanten_genutzt_m"] == pytest.approx(40.0, rel=0.05)


def test_kantenuebernahme_reisst_keine_luecken(karte_und_kante) -> None:
    gdf, kante, gebiet = karte_und_kante

    aus, info = partition_along(gdf, "klasse", kante, clip=gebiet)

    assert info["luecke_m2"] < 0.01


def test_weit_entfernte_kanten_zerschneiden_nicht(karte_und_kante) -> None:
    """Eine Kante mitten in einer Fläche ist keine Klassengrenze — sie darf die
    Fläche nicht zerteilen."""
    gdf, _, gebiet = karte_und_kante
    mittendrin = gpd.GeoDataFrame(geometry=[LineString([(0, 8), (40, 8)])], crs=CRS)

    aus, info = partition_along(gdf, "klasse", mittendrin, clip=gebiet, naehe_m=1.5)

    assert info["kanten_genutzt_m"] == pytest.approx(0.0, abs=0.1)
    assert len(aus) == 2


def test_klasse_kommt_aus_dem_flaechenanteil(karte_und_kante) -> None:
    """Über einen Punkt entschieden wäre die Zuordnung willkürlich, sobald eine
    neue Teilfläche über zwei alte reicht."""
    gdf, kante, gebiet = karte_und_kante

    aus, _ = partition_along(gdf, "klasse", kante, clip=gebiet)

    assert set(aus["klasse"]) == {"befestigt", "Grünfläche"}
    gesamt = unary_union(list(aus.geometry))
    assert gesamt.area == pytest.approx(gebiet.area, rel=0.001)


# --- Splitterauflösung -------------------------------------------------------

@pytest.fixture()
def mit_splittern() -> gpd.GeoDataFrame:
    gross_a = Polygon([(0, 0), (40, 0), (40, 20), (0, 20)])
    loch = [(5, 30), (8, 30), (8, 33), (5, 33)]
    # gross_b MIT Loch, damit winzig nicht darin liegt — sonst überlappen sich
    # zwei Features und jede Flächensumme im Test wäre falsch.
    gross_b = Polygon([(0, 22), (40, 22), (40, 40), (0, 40)], [loch])
    splitter = Polygon([(0, 20), (40, 20), (40, 22), (0, 22)])   # 80 m², dazwischen
    winzig = Polygon(loch)                                       # 9 m², im Loch von B
    return gpd.GeoDataFrame(
        {"klasse": ["befestigt", "Grünfläche", "unsicher", "unsicher"]},
        geometry=[gross_a, gross_b, splitter, winzig], crs=CRS)


def test_winzige_flaeche_geht_an_den_umgebenden_nachbarn(mit_splittern) -> None:
    aus, info = absorb_small(mit_splittern, "klasse", min_flaeche_m2=30.0,
                             nur_klassen=["unsicher"])

    assert info["aufgeloest"] == 1
    assert "unsicher" in set(aus["klasse"])          # der 80-m²-Streifen bleibt
    gruen = aus.loc[aus["klasse"] == "Grünfläche"].area.sum()
    # 18 x 40 mit Loch, plus das gefüllte Loch = wieder die volle Fläche
    assert gruen == pytest.approx(18 * 40, rel=0.01)


def test_flaeche_geht_nicht_verloren(mit_splittern) -> None:
    """Der Fehler, der hier drohen würde: Splitter verschwinden lassen statt
    zuordnen — die Karte hätte danach Löcher."""
    vorher = mit_splittern.area.sum()

    aus, _ = absorb_small(mit_splittern, "klasse", min_flaeche_m2=30.0)

    assert aus.area.sum() == pytest.approx(vorher, rel=0.001)


def test_grosse_flaechen_bleiben_unangetastet(mit_splittern) -> None:
    aus, info = absorb_small(mit_splittern, "klasse", min_flaeche_m2=5.0)

    assert info["aufgeloest"] == 0
    assert len(aus) == 4


def test_nur_genannte_klassen_werden_aufgeloest(mit_splittern) -> None:
    aus, info = absorb_small(mit_splittern, "klasse", min_flaeche_m2=1000.0,
                             nur_klassen=["unsicher"])

    # Auch bei riesiger Schwelle bleiben befestigt und Grünfläche stehen
    assert {"befestigt", "Grünfläche"} <= set(aus["klasse"])


def test_kreuzende_kante_wird_nicht_uebernommen(karte_und_kante) -> None:
    """Der Kern der Richtungsprüfung: eine Kante, die die Klassengrenze KREUZT,
    ist an der Kreuzung genauso nah wie eine, die sie begleitet. Ohne die
    Prüfung wurde sie als Ersatz behandelt — und riss die Karte auseinander."""
    gdf, _, gebiet = karte_und_kante
    quer = gpd.GeoDataFrame(geometry=[LineString([(20, 0), (20, 40)])], crs=CRS)

    aus, info = partition_along(gdf, "klasse", quer, clip=gebiet,
                                naehe_m=1.5, max_winkel_grad=25)

    assert info["kanten_genutzt_m"] == pytest.approx(0.0, abs=0.5)
    assert len(aus) == 2
    assert info["luecke_m2"] < 0.01


def test_zu_kurzer_paralleler_abschnitt_zaehlt_nicht(karte_und_kante) -> None:
    gdf, _, gebiet = karte_und_kante
    kurz = gpd.GeoDataFrame(geometry=[LineString([(10, 20.8), (12, 20.8)])], crs=CRS)

    aus, info = partition_along(gdf, "klasse", kurz, clip=gebiet,
                                naehe_m=1.5, min_laenge_m=4.0)

    assert info["kanten_genutzt_m"] == pytest.approx(0.0, abs=0.5)
