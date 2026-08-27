"""to_single_part: eine Zeile, ein Polygon — und kein stiller Flächenverlust."""

import geopandas as gpd
import pytest
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Point, Polygon

from pbs_gis import to_single_part

CRS = "EPSG:25833"


def quadrat(x: float, groesse: float = 1.0) -> Polygon:
    return Polygon([(x, 0), (x + groesse, 0), (x + groesse, groesse), (x, groesse)])


def test_multipolygon_wird_zerlegt_attribute_bleiben():
    gdf = gpd.GeoDataFrame(
        {"layer": ["A", "B"]},
        geometry=[MultiPolygon([quadrat(0), quadrat(3)]), quadrat(6)],
        crs=CRS,
    )

    out = to_single_part(gdf)

    assert set(out.geometry.geom_type) == {"Polygon"}, "Multipolygon hat überlebt"
    assert len(out) == 3
    assert sorted(out["layer"]) == ["A", "A", "B"]


def test_flaechenlose_reste_fallen_weg_flaeche_bleibt():
    """Eine Differenzbildung hinterlässt Linien und Punkte in einer Collection.

    Sie tragen die Attribute einer echten Fläche und würden in jeder Objektzahl
    mitzählen, ohne zu einer Summe beizutragen.
    """
    gdf = gpd.GeoDataFrame(
        {"layer": ["A"]},
        geometry=[
            GeometryCollection(
                [quadrat(0), LineString([(9, 9), (10, 10)]), Point(20, 20)]
            )
        ],
        crs=CRS,
    )

    out = to_single_part(gdf)

    assert list(out.geometry.geom_type) == ["Polygon"]
    assert out.area.sum() == pytest.approx(1.0)


def test_flaechensumme_ueberlebt_die_zerlegung():
    """Der Fehler, der hier drohte: ein Teil fällt weg und die Bilanz sinkt still."""
    gdf = gpd.GeoDataFrame(
        {"layer": ["A", "B"]},
        geometry=[MultiPolygon([quadrat(0), quadrat(3, 2.0)]), quadrat(9, 3.0)],
        crs=CRS,
    )

    out = to_single_part(gdf)

    assert out.area.sum() == pytest.approx(gdf.area.sum())
    assert out.crs == gdf.crs


def test_leere_eingabe_bleibt_leer():
    gdf = gpd.GeoDataFrame({"layer": []}, geometry=[], crs=CRS)

    assert len(to_single_part(gdf)) == 0
