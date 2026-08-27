import geopandas as gpd
import pytest
from shapely.geometry import MultiPolygon, Polygon

from pbs_gis import close_slits, subtract_smaller_overlaps

CRS = "EPSG:25833"


def test_schlitz_in_einer_flaeche_schliesst_sich_und_verbindet_die_teile():
    """Zwei Stuecke einer Flaeche, 6 cm auseinander, werden eine Flaeche."""
    links = Polygon([(0, 0), (4.97, 0), (4.97, 10), (0, 10)])
    rechts = Polygon([(5.03, 0), (10, 0), (10, 10), (5.03, 10)])
    gdf = gpd.GeoDataFrame({"layer": ["A"]}, geometry=[MultiPolygon([links, rechts])], crs=CRS)

    out = close_slits(gdf, 0.2)

    assert out.geometry.iloc[0].geom_type == "Polygon"
    assert out.area.iloc[0] == pytest.approx(100.0, abs=0.01)


def test_breiter_spalt_bleibt_offen():
    """Ein Spalt breiter als max_width_m ist kein Schlitz und bleibt."""
    links = Polygon([(0, 0), (4.5, 0), (4.5, 10), (0, 10)])
    rechts = Polygon([(5.5, 0), (10, 0), (10, 10), (5.5, 10)])
    gdf = gpd.GeoDataFrame({"layer": ["A"]}, geometry=[MultiPolygon([links, rechts])], crs=CRS)

    out = close_slits(gdf, 0.2)

    assert out.area.iloc[0] == pytest.approx(90.0, abs=0.01)
    assert out.geometry.iloc[0].geom_type == "MultiPolygon"


def test_duennes_bauteil_ueberlebt_das_schliessen_des_grossen_nachbarn():
    """Eine 10 cm breite Rinne zwischen zwei Gruenstuecken wird nicht ueberwachsen.

    Das Gruen schliesst ueber die Rinne hinweg; die Aufloesung der Ueberlappung
    (kleinere gewinnt) gibt der Rinne ihren Platz zurueck.
    """
    links = Polygon([(0, 0), (4.95, 0), (4.95, 10), (0, 10)])
    rechts = Polygon([(5.05, 0), (10, 0), (10, 10), (5.05, 10)])
    rinne = Polygon([(4.95, 0), (5.05, 0), (5.05, 10), (4.95, 10)])
    gdf = gpd.GeoDataFrame({"layer": ["Gruen", "Rinne"]},
                           geometry=[MultiPolygon([links, rechts]), rinne], crs=CRS)

    out = subtract_smaller_overlaps(close_slits(gdf, 0.2))

    flaeche = dict(zip(out["layer"], out.area))
    assert flaeche["Rinne"] == pytest.approx(1.0, abs=0.001)
    assert flaeche["Gruen"] == pytest.approx(99.0, abs=0.01)
