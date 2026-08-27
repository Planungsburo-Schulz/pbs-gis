"""weld_fragments: ein Belag, ein Polygon — Bruchstueck ans Geschwister."""

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from pbs_gis import weld_fragments

CRS = "EPSG:25833"


def gross() -> Polygon:
    return Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])


def punktberuehrung() -> Polygon:
    """Beruehrt `gross` in genau EINEM Punkt (10, 10) — als ein Polygon ungueltig."""
    return Polygon([(10, 10), (10.1, 10.1), (10.2, 10.0)])


def test_punktberuehrung_wird_verschweisst():
    gdf = gpd.GeoDataFrame(
        {"belag": ["A", "A"]}, geometry=[gross(), punktberuehrung()], crs=CRS)
    assert len(gdf) == 2

    out, info = weld_fragments(gdf, "belag")

    assert len(out) == 1, "Bruchstueck steht noch daneben"
    assert out.geometry.iloc[0].geom_type == "Polygon"
    assert info["verschweisst"] == 1


def test_knapp_getrenntes_bruchstueck_wird_verschweisst():
    daneben = Polygon([(10.05, 4), (10.3, 4), (10.3, 4.3), (10.05, 4.3)])
    gdf = gpd.GeoDataFrame(
        {"belag": ["A", "A"]}, geometry=[gross(), daneben], crs=CRS)

    out, info = weld_fragments(gdf, "belag", tolerance_m=0.1)

    assert len(out) == 1
    assert info["verschweisst"] == 1


def test_fremder_belag_wird_nie_verschweisst():
    """Hier wandert kein Material ueber eine Belagsgrenze."""
    gdf = gpd.GeoDataFrame(
        {"belag": ["A", "B"]}, geometry=[gross(), punktberuehrung()], crs=CRS)

    out, info = weld_fragments(gdf, "belag")

    assert len(out) == 2
    assert info["verschweisst"] == 0
    assert info["uebrig"] == 1


def test_echte_flaeche_bleibt_eigenstaendig():
    """Nur Bruchstuecke wandern — eine richtige Flaeche behaelt ihre Zeile.

    Die zweite Flaeche ist KLEINER als die erste und beruehrt sie in einem
    Punkt: an allem ausser ihrer Groesse ein Verschweiss-Kandidat. Genau das
    macht sie zum Pruefstein fuer die Schwelle — ohne sie wuerden 5 m² Belag
    in die Nachbarflaeche gezogen.
    """
    zweite = Polygon([(10, 10), (15, 10), (15, 11), (10, 11)])
    assert zweite.area == pytest.approx(5.0)
    gdf = gpd.GeoDataFrame(
        {"belag": ["A", "A"]}, geometry=[gross(), zweite], crs=CRS)

    out, info = weld_fragments(gdf, "belag", max_fragment_m2=0.1)

    assert len(out) == 2, "eine 5 m² grosse Flaeche wurde verschweisst"
    assert info["verschweisst"] == 0


def test_flaeche_bleibt_erhalten():
    gdf = gpd.GeoDataFrame(
        {"belag": ["A", "A"]}, geometry=[gross(), punktberuehrung()], crs=CRS)
    vorher = gdf.area.sum()

    out, info = weld_fragments(gdf, "belag")

    # Die Bruecke fuegt hoechstens ihre Kreisflaeche hinzu (r = 1 cm).
    assert out.area.sum() == pytest.approx(vorher, abs=0.001)
    assert info["zugefuegt_m2"] < 0.001


def test_bruecke_greift_nicht_in_die_nachbarflaeche():
    """Die Bruecke fuellt nur, was niemandem gehoert.

    Ungekuerzt macht ihre Scheibe aus einer ueberlappungsfreien Karte eine mit
    Ueberlappung — winzig und trotzdem ein gebrochenes Versprechen.
    """
    # 2 mm ueber dem Beruehrungspunkt (10, 10) — die 1-cm-Scheibe der Bruecke
    # reicht dort hinein, ungekuerzt.
    nachbar = Polygon([(9.99, 10.002), (10.0, 10.002), (10.0, 10.5), (9.99, 10.5)])
    gdf = gpd.GeoDataFrame(
        {"belag": ["A", "A", "B"]},
        geometry=[gross(), punktberuehrung(), nachbar],
        crs=CRS,
    )

    out, info = weld_fragments(gdf, "belag")

    ueberlappung = 0.0
    for a in range(len(out)):
        for b in range(a + 1, len(out)):
            s = out.geometry.iloc[a].intersection(out.geometry.iloc[b])
            if not s.is_empty:
                ueberlappung += s.area
    assert ueberlappung == pytest.approx(0.0, abs=1e-9), "Bruecke ragt in den Nachbarn"
