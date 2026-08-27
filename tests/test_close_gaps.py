"""close_gaps: Ritzen einer Partition schliessen, ohne Fläche zu erfinden."""

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from pbs_gis import close_gaps

CRS = "EPSG:25833"


def rechteck(x0: float, x1: float, y0: float = 0.0, y1: float = 10.0) -> Polygon:
    return Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def test_ritze_zwischen_zwei_flaechen_wird_geschlossen():
    """Der Regelfall: 1 cm Spalt über 10 m, den kein Flächenfilter fängt.

    Die Ritze grenzt an BEIDE Flächen, wird also geteilt: jede bekommt die
    Hälfte, und die Naht läuft auf der Mittellinie.
    """
    gdf = gpd.GeoDataFrame(
        {"layer": ["A", "B"]},
        geometry=[rechteck(0, 5), rechteck(5.01, 10)],
        crs=CRS,
    )
    bereich = rechteck(0, 10)

    out, info = close_gaps(gdf, bereich, split_between_neighbours=True)

    assert info["geschlossen_m2"] == pytest.approx(0.1)
    assert info["offen"] == 0
    assert out.area.sum() == pytest.approx(bereich.area)
    assert out.area.iloc[0] == pytest.approx(out.area.iloc[1], rel=0.02), \
        "gleich lange Grenzen, also gleiche Hälften"


def test_lange_ritze_geht_stueckweise_an_den_jeweiligen_nachbarn():
    """Der Fall, für den geteilt wird.

    Eine 30 m lange Ritze läuft an ihrer oberen Hälfte an A, an ihrer unteren
    an B. Ganz an den längeren Rand vergeben, trüge sie über ihre volle Länge
    das falsche Material.
    """
    links_oben = Polygon([(0, 15), (5, 15), (5, 30), (0, 30)])
    links_unten = Polygon([(0, 0), (5, 0), (5, 15), (0, 15)])
    rechts = Polygon([(5.02, 0), (10, 0), (10, 30), (5.02, 30)])
    gdf = gpd.GeoDataFrame(
        {"layer": ["oben", "unten", "rechts"]},
        geometry=[links_oben, links_unten, rechts],
        crs=CRS,
    )
    bereich = Polygon([(0, 0), (10, 0), (10, 30), (0, 30)])

    out, info = close_gaps(gdf, bereich, split_between_neighbours=True)

    assert out.area.sum() == pytest.approx(bereich.area)
    flaeche = dict(zip(out["layer"], out.area))
    # Beide linken Flächen wachsen — ganz-an-einen hätte nur eine wachsen lassen.
    assert flaeche["oben"] > links_oben.area, "obere Hälfte hat nichts bekommen"
    assert flaeche["unten"] > links_unten.area, "untere Hälfte hat nichts bekommen"
    assert flaeche["oben"] == pytest.approx(flaeche["unten"], rel=0.05)


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


def test_schnitt_durch_eine_flaeche_geht_ganz_an_sie():
    """Eine Luecke mit DERSELBEN Klasse auf beiden Seiten trennt nicht, sie schneidet.

    Der Streifen laeuft ueber die volle Breite zwischen zwei Gruenflaechen-
    Stuecken hindurch; auf seinem rechten Drittel liegt unten eine andere
    Klasse. Geteilt bekaeme er dort deren Material — und die zerschnittene
    Gruenflaeche bliebe zerschnitten.
    """
    oben = Polygon([(0, 6), (30, 6), (30, 10), (0, 10)])
    unten = Polygon([(0, 0), (20, 0), (20, 5.7), (0, 5.7)])
    fremd = Polygon([(20, 0), (30, 0), (30, 5.7), (20, 5.7)])
    gdf = gpd.GeoDataFrame(
        {"belag": ["Gruen", "Gruen", "Decke"]},
        geometry=[oben, unten, fremd],
        crs=CRS,
    )
    bereich = Polygon([(0, 0), (30, 0), (30, 10), (0, 10)])

    out, info = close_gaps(gdf, bereich, split_between_neighbours=True,
                           class_column="belag")

    flaeche = {}
    for b, a in zip(out["belag"], out.area):
        flaeche[b] = flaeche.get(b, 0) + a
    # Der ganze Streifen (30 x 0,3 m) gehoert der zerschnittenen Gruenflaeche.
    assert flaeche["Gruen"] == pytest.approx(oben.area + unten.area + 9.0, abs=0.01)
    assert flaeche["Decke"] == pytest.approx(fremd.area, abs=0.01), \
        "die fremde Klasse hat einen Teil des Schnitts bekommen"
