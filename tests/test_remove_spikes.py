"""remove_spikes: Nadeln entfernen, echte spitze Ecken behalten."""

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from pbs_gis import remove_spikes, remove_spikes_geom

CRS = "EPSG:25833"


def mit_nadel() -> Polygon:
    """Ein Quadrat, aus dessen Oberkante eine 6 m lange Nadel ohne Breite ragt.

    Die beiden Fusspunkte liegen 1 mm auseinander; die Spitze schliesst
    0,003 m² ein. Genau diese Form hinterlaesst eine Vereinigung zwischen
    Raendern, die einander knapp verfehlen.
    """
    return Polygon([(0, 0), (10, 0), (10, 10),
                    (6.5005, 10), (6.5, 16), (6.4995, 10),
                    (0, 10)])


def test_nadel_faellt_weg_flaeche_bleibt():
    roh = mit_nadel()
    assert roh.is_valid, "Fixture selbst muss gueltig sein"

    sauber = remove_spikes_geom(roh)

    assert sauber.geom_type == "Polygon"
    assert len(sauber.exterior.coords) < len(roh.exterior.coords)
    assert sauber.bounds[3] == pytest.approx(10, abs=0.001), "Nadel ragt noch hinaus"
    assert sauber.area == pytest.approx(roh.area, abs=0.01)


def test_schlanker_sporn_mit_flaeche_bleibt():
    """Der Fall, an dem sich der Flaechenschutz beweisen muss.

    Derselbe 6-m-Fortsatz, aber 0,5 m breit: die Spitze misst 4,8° und
    schliesst 1,5 m² ein. Ein Kriterium nur ueber den Winkel wuerde ihn
    abschneiden — es ist Plangeometrie, kein Artefakt.
    """
    sporn = Polygon([(0, 0), (10, 0), (10, 10),
                     (6.5, 10), (6.5, 16), (6.0, 10),
                     (0, 10)])

    sauber = remove_spikes_geom(sporn)

    assert sauber.bounds[3] == pytest.approx(16), "Sporn wurde abgeschnitten"
    assert sauber.area == pytest.approx(sporn.area)
    assert len(sauber.exterior.coords) == len(sporn.exterior.coords)


def test_nadel_im_loch_faellt_auch_weg():
    aussen = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)])
    loch = [(5, 5), (15, 5), (15, 15),
            (10.0005, 15), (10, 9), (9.9995, 15),
            (5, 15)]
    roh = Polygon(aussen.exterior.coords, [loch])
    assert roh.is_valid, "Fixture selbst muss gueltig sein"

    sauber = remove_spikes_geom(roh)

    assert len(sauber.interiors[0].coords) < len(roh.interiors[0].coords)
    assert sauber.area == pytest.approx(roh.area, abs=0.01)


def test_gdf_behaelt_attribute_und_crs():
    gdf = gpd.GeoDataFrame({"layer": ["A"]}, geometry=[mit_nadel()], crs=CRS)

    out = remove_spikes(gdf)

    assert list(out["layer"]) == ["A"]
    assert out.crs == gdf.crs
    assert out.geometry.iloc[0].bounds[3] == pytest.approx(10, abs=0.001)


def test_entartetes_loch_faellt_weg():
    """Ein Innenring ohne Flaeche ist kein Loch, sondern eine Linie in der Flaeche.

    Er hat nur drei Punkte und liegt damit schon auf dem Boden, den die
    Nadelschleife nicht unterschreiten darf — sie kommt gar nicht an ihn heran.
    Genau diese Form zeichnet sich in QGIS als Trennlinie quer durch eine
    ungeteilte Flaeche.
    """
    aussen = [(0, 0), (20, 0), (20, 20), (0, 20)]
    strich = [(5, 5), (12, 12), (5, 5.0001)]
    roh = Polygon(aussen, [strich])

    sauber = remove_spikes_geom(roh)

    assert len(sauber.interiors) == 0, "entartetes Loch steht noch"
    assert sauber.area == pytest.approx(roh.area, abs=0.01)


def test_echtes_loch_bleibt_erhalten():
    aussen = [(0, 0), (20, 0), (20, 20), (0, 20)]
    loch = [(5, 5), (12, 5), (12, 12), (5, 12)]
    roh = Polygon(aussen, [loch])

    sauber = remove_spikes_geom(roh)

    assert len(sauber.interiors) == 1
    assert sauber.area == pytest.approx(roh.area)
