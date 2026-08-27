"""close_gaps: Ritzen einer Partition schliessen, ohne Fläche zu erfinden."""

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from pbs_gis import close_gaps

CRS = "EPSG:25833"


def rechteck(x0: float, x1: float, y0: float = 0.0, y1: float = 10.0) -> Polygon:
    return Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def test_ritze_zwischen_zwei_flaechen_wird_geschlossen():
    """Der Regelfall: 1 cm Spalt über 10 m, den kein Flächenfilter fängt."""
    gdf = gpd.GeoDataFrame(
        {"layer": ["A", "B"]},
        geometry=[rechteck(0, 5), rechteck(5.01, 10)],
        crs=CRS,
    )
    bereich = rechteck(0, 10)

    out, info = close_gaps(gdf, bereich)

    assert info["geschlossen"] == 1
    assert info["geschlossen_m2"] == pytest.approx(0.1)
    assert info["offen"] == 0
    assert out.area.sum() == pytest.approx(bereich.area)


def test_ritze_geht_an_den_laengeren_rand():
    """Zwei Nachbarn, ungleich lange gemeinsame Grenze — der längere bekommt sie."""
    gdf = gpd.GeoDataFrame(
        {"layer": ["kurz", "lang"]},
        geometry=[rechteck(0, 5, 0, 2), rechteck(0, 5, 2.01, 10)],
        crs=CRS,
    )
    # Die Ritze läuft über die volle Breite; beide Nachbarn grenzen gleich lang an,
    # also wird der Fall über eine verkürzte untere Fläche entschieden.
    gdf.loc[0, "geometry"] = rechteck(0, 3, 0, 2)
    bereich = rechteck(0, 5, 0, 10)

    out, info = close_gaps(gdf, bereich)

    assert info["geschlossen"] >= 1
    gewachsen = out.loc[out["layer"] == "lang", "geometry"].iloc[0].area
    assert gewachsen > rechteck(0, 5, 2.01, 10).area


def test_echtes_loch_bleibt_offen():
    """Eine breite Lücke ist ein Ort ohne Fläche — sie zu schliessen erfände eine."""
    gdf = gpd.GeoDataFrame(
        {"layer": ["A", "B"]},
        geometry=[rechteck(0, 4), rechteck(6, 10)],
        crs=CRS,
    )
    bereich = rechteck(0, 10)

    out, info = close_gaps(gdf, bereich, max_width_m=1.0)

    assert info["geschlossen"] == 0
    assert info["offen"] == 1
    assert info["offen_m2"] == pytest.approx(20.0)
    assert out.area.sum() == pytest.approx(gdf.area.sum())


def test_flaeche_wird_nie_erfunden_oder_verloren():
    """Die Summe wächst genau um das Geschlossene — nicht mehr, nicht weniger."""
    gdf = gpd.GeoDataFrame(
        {"layer": ["A", "B", "C"]},
        geometry=[rechteck(0, 3), rechteck(3.02, 6), rechteck(6.05, 10)],
        crs=CRS,
    )
    bereich = rechteck(0, 10)
    vorher = gdf.area.sum()

    out, info = close_gaps(gdf, bereich)

    assert out.area.sum() == pytest.approx(vorher + info["geschlossen_m2"])


def test_leere_eingabe():
    gdf = gpd.GeoDataFrame({"layer": []}, geometry=[], crs=CRS)

    out, info = close_gaps(gdf, rechteck(0, 10))

    assert len(out) == 0
    assert info["geschlossen"] == 0


def test_geometrycollection_beendet_den_lauf_nicht():
    """Eine geschnittene Fläche kann als GeometryCollection ankommen.

    Deren `.boundary` ist None; ohne Absicherung stirbt die Nachbarsuche daran.
    Der Nachbar wird dann übergangen, die Ritze bleibt offen — gemeldet, nicht
    verschwiegen.
    """
    from shapely.geometry import GeometryCollection, LineString

    gdf = gpd.GeoDataFrame(
        {"layer": ["A", "B"]},
        geometry=[
            GeometryCollection([rechteck(0, 5), LineString([(5, 0), (5, 10)])]),
            rechteck(5.01, 10),
        ],
        crs=CRS,
    )

    out, info = close_gaps(gdf, rechteck(0, 10))

    assert len(out) == 2
    assert info["geschlossen"] + info["offen"] == 1
