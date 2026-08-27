import geopandas as gpd
import pytest
from shapely.geometry import Polygon
from shapely.ops import unary_union

from pbs_gis import transfer_spikes

CRS = "EPSG:25833"


def test_nadel_geht_an_den_nachbarn_partition_bleibt_exakt():
    """A hat eine 2 m lange Nadel (Spitze ~3°), die in B hineinragt."""
    a = Polygon([(0, 0), (10, 0), (10, 5), (12, 5.05), (10, 5.1), (10, 10), (0, 10)])
    b = Polygon([(10, 0), (20, 0), (20, 10), (10, 10), (10, 5.1), (12, 5.05), (10, 5)])
    gdf = gpd.GeoDataFrame({"layer": ["A", "B"]}, geometry=[a, b], crs=CRS)
    vorher = unary_union([a, b])

    out, info = transfer_spikes(gdf, max_area_m2=0.15, max_angle_deg=5.0)

    assert info["uebergeben"] == 1
    fl = dict(zip(out["layer"], out.area))
    assert fl["A"] == pytest.approx(100.0, abs=1e-6)
    assert fl["B"] == pytest.approx(100.0, abs=1e-6)
    ga, gb = out.geometry.iloc[0], out.geometry.iloc[1]
    assert ga.intersection(gb).area == pytest.approx(0.0, abs=1e-9)
    assert unary_union([ga, gb]).symmetric_difference(vorher).area == pytest.approx(0.0, abs=1e-9)
    from pbs_gis.geometry import _nadel_dreiecke
    assert _nadel_dreiecke(ga, 0.15, 5.0) == [] and _nadel_dreiecke(gb, 0.15, 5.0) == []
    assert ga.simplify(0).equals(Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]))


def test_echte_spitze_ecke_bleibt():
    """Ein spitzer Keil mit echter Flaeche ist Plangeometrie, keine Nadel."""
    a = Polygon([(0, 0), (10, 0), (10, 5), (30, 6), (10, 7), (10, 10), (0, 10)])
    b = Polygon([(10, 0), (40, 0), (40, 10), (10, 10), (10, 7), (30, 6), (10, 5)])
    gdf = gpd.GeoDataFrame({"layer": ["A", "B"]}, geometry=[a, b], crs=CRS)

    out, info = transfer_spikes(gdf, max_area_m2=0.15, max_angle_deg=5.0)

    assert info["uebergeben"] == 0
    assert out.area.iloc[0] == pytest.approx(a.area)


def test_nadel_ins_leere_bleibt_stehen():
    """Ohne Nachbarn an den Schenkeln riesse die Uebergabe ein Loch — also bleibt sie."""
    a = Polygon([(0, 0), (10, 0), (10, 5), (12, 5.05), (10, 5.1), (10, 10), (0, 10)])
    gdf = gpd.GeoDataFrame({"layer": ["A"]}, geometry=[a], crs=CRS)

    out, info = transfer_spikes(gdf)

    assert info["uebergeben"] == 0 and info["geblieben"] == 1
    assert out.area.iloc[0] == pytest.approx(a.area)


def test_kerbe_hinein_wird_von_der_flaeche_geschlossen():
    """Spiegelfall: B ragt als Nadel in A, aber nur A traegt die spitze Ecke.

    A hat die 3°-Kerbe; B fuellt sie mit einer stumpferen Spitze aus. Die
    Kerbe wird von A geschlossen, B gibt das Dreieck ab — Partition exakt.
    """
    a = Polygon([(0, 0), (10, 0), (10, 5), (8, 5.05), (10, 5.1), (10, 10), (0, 10)])
    b = Polygon([(10, 0), (20, 0), (20, 10), (10, 10), (10, 5.1), (8, 5.05), (10, 5)])
    gdf = gpd.GeoDataFrame({"layer": ["A", "B"]}, geometry=[a, b], crs=CRS)
    vorher = unary_union([a, b])

    out, info = transfer_spikes(gdf, max_area_m2=0.15, max_angle_deg=5.0)

    fl = dict(zip(out["layer"], out.area))
    assert fl["A"] == pytest.approx(100.0, abs=1e-6)
    assert fl["B"] == pytest.approx(100.0, abs=1e-6)
    ga, gb = out.geometry.iloc[0], out.geometry.iloc[1]
    assert ga.intersection(gb).area == pytest.approx(0.0, abs=1e-9)
    assert unary_union([ga, gb]).symmetric_difference(vorher).area == pytest.approx(0.0, abs=1e-9)
