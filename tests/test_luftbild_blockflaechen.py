"""Tests für die Bestandsermittlung aus Luftbild und Vermessungssymbolen.

Beide Bausteine haben denselben stillen Fehlerfall: ein plausibles Ergebnis mit
falscher Fläche. Beim Luftbild eine Schwelle, die im nächsten Bild nicht mehr
passt; bei den Blöcken ein angenommener statt gemessener Basisradius. Die Tests
prüfen deshalb Flächenwerte, nicht bloss die Existenz von Geometrie.
"""

from __future__ import annotations

from pathlib import Path

import ezdxf
import numpy as np
import pytest

from pbs_gis.dxf.blockflaechen import block_basisradius, block_circles
from pbs_gis.dxf.read import CadReadError
from pbs_gis.luftbild import excess_green, vegetationsflaechen, vegetationsschwelle

CRS = "EPSG:25833"


# --- Luftbild ----------------------------------------------------------------

def _rgb_bild(pfad: Path, aufloesung: float = 0.2) -> Path:
    """20 x 20 m: linke Hälfte Vegetation, rechte Hälfte Asphalt."""
    import rasterio
    from rasterio.transform import from_origin

    n = int(20 / aufloesung)
    bild = np.zeros((3, n, n), np.uint8)
    bild[:, :, : n // 2] = np.array([70, 120, 60], np.uint8)[:, None, None]   # Grün
    bild[:, :, n // 2 :] = np.array([130, 130, 128], np.uint8)[:, None, None]  # Belag

    with rasterio.open(pfad, "w", driver="GTiff", height=n, width=n, count=3,
                       dtype="uint8", crs=CRS,
                       transform=from_origin(300000, 5900000, aufloesung, aufloesung)) as dst:
        dst.write(bild)
    return pfad


def test_excess_green_trennt_die_haelften() -> None:
    rgb = np.zeros((4, 4, 3), np.uint8)
    rgb[:, :2] = [70, 120, 60]
    rgb[:, 2:] = [130, 130, 128]

    exg = excess_green(rgb)

    assert exg[:, :2].mean() > 0.1
    assert exg[:, 2:].mean() < 0.02


def test_schwelle_kommt_aus_dem_bild(tmp_path: Path) -> None:
    """Eine feste Konstante träfe ein anderes Bildjahr nicht — die Schwelle muss
    zwischen den beiden Verteilungen liegen, nicht auf einem gemerkten Wert."""
    rgb = np.zeros((10, 10, 3), np.uint8)
    rgb[:, :5] = [70, 120, 60]
    rgb[:, 5:] = [130, 130, 128]
    exg = excess_green(rgb)

    s = vegetationsschwelle(exg)

    assert exg[:, 5:].mean() < s < exg[:, :5].mean()


def test_flaechen_stimmen(tmp_path: Path) -> None:
    tif = _rgb_bild(tmp_path / "dop.tif")

    gdf, info = vegetationsflaechen(tif, glaettung_m=0.4, min_flaeche_m2=1.0)

    je_klasse = gdf.groupby("klasse")["flaeche_m2"].sum()
    assert je_klasse["Vegetation"] == pytest.approx(200.0, rel=0.05)
    assert je_klasse["befestigt"] == pytest.approx(200.0, rel=0.05)


def test_uebergebene_schwelle_wird_verwendet(tmp_path: Path) -> None:
    """Reproduzierbarkeit: derselbe Wert muss dasselbe Ergebnis liefern."""
    tif = _rgb_bild(tmp_path / "dop.tif")
    _, info = vegetationsflaechen(tif)
    s = info["schwelle"]

    a, ia = vegetationsflaechen(tif, schwelle=s)
    b, ib = vegetationsflaechen(tif, schwelle=s)

    assert ia["schwelle"] == ib["schwelle"] == s
    assert a["flaeche_m2"].sum() == pytest.approx(b["flaeche_m2"].sum())


def test_graustufenbild_wird_abgewiesen(tmp_path: Path) -> None:
    import rasterio
    from rasterio.transform import from_origin

    p = tmp_path / "grau.tif"
    with rasterio.open(p, "w", driver="GTiff", height=8, width=8, count=1,
                       dtype="uint8", crs=CRS,
                       transform=from_origin(300000, 5900000, 0.2, 0.2)) as dst:
        dst.write(np.full((1, 8, 8), 120, np.uint8))

    with pytest.raises(ValueError, match="RGB erwartet"):
        vegetationsflaechen(p)


# --- Blockflächen ------------------------------------------------------------

def _dxf_mit_kronen(pfad: Path, basisradius: float, skalen: list[float]) -> Path:
    doc = ezdxf.new("R2010")
    blk = doc.blocks.new(name="BAUMKR")
    blk.add_circle((0, 0), radius=basisradius)
    msp = doc.modelspace()
    for i, s in enumerate(skalen):
        msp.add_blockref("BAUMKR", (100 + i * 60, 200),
                         dxfattribs={"xscale": s, "yscale": s})
    doc.saveas(pfad)
    return pfad


def test_basisradius_wird_gemessen(tmp_path: Path) -> None:
    """Ein angenommener Einheitskreis halbiert oder verdoppelt jede Fläche."""
    p = _dxf_mit_kronen(tmp_path / "b.dxf", basisradius=0.5, skalen=[8.0])
    import ezdxf as ez

    assert block_basisradius(ez.readfile(p), "BAUMKR") == pytest.approx(0.5, abs=0.01)


def test_kreisflaechen_aus_skalierung(tmp_path: Path) -> None:
    p = _dxf_mit_kronen(tmp_path / "b.dxf", basisradius=1.0, skalen=[8.0, 4.0])

    gdf = block_circles(p, "BAUMKR", crs=CRS)

    assert sorted(round(r, 2) for r in gdf["radius_m"]) == [4.0, 8.0]
    assert gdf["flaeche_m2"].max() == pytest.approx(np.pi * 64, rel=0.01)


def test_halber_basisradius_halbiert_den_radius(tmp_path: Path) -> None:
    p = _dxf_mit_kronen(tmp_path / "b.dxf", basisradius=0.5, skalen=[8.0])

    gdf = block_circles(p, "BAUMKR", crs=CRS)

    assert gdf["radius_m"].iloc[0] == pytest.approx(4.0, abs=0.05)


def test_zonenpraefix_wird_abgezogen(tmp_path: Path) -> None:
    doc = ezdxf.new("R2010")
    blk = doc.blocks.new(name="BAUMKR")
    blk.add_circle((0, 0), radius=1.0)
    doc.modelspace().add_blockref("BAUMKR", (33_000_000 + 500, 5_900_000),
                                  dxfattribs={"xscale": 3.0, "yscale": 3.0})
    p = tmp_path / "zone.dxf"
    doc.saveas(p)

    gdf = block_circles(p, "BAUMKR", crs=CRS, strip_zone_shift=33_000_000)

    assert gdf.total_bounds[0] == pytest.approx(497.0, abs=0.5)


def test_min_radius_filtert(tmp_path: Path) -> None:
    p = _dxf_mit_kronen(tmp_path / "b.dxf", basisradius=1.0, skalen=[8.0, 0.5])

    gdf = block_circles(p, "BAUMKR", crs=CRS, min_radius_m=1.0)

    assert len(gdf) == 1


def test_unbekannter_block_wirft(tmp_path: Path) -> None:
    p = _dxf_mit_kronen(tmp_path / "b.dxf", basisradius=1.0, skalen=[2.0])

    with pytest.raises(CadReadError, match="BAUMKRONE"):
        block_circles(p, "BAUMKRONE", crs=CRS)


def test_block_ohne_einfuegung_wirft(tmp_path: Path) -> None:
    """Leeres Ergebnis und Tippfehler wären sonst nicht zu unterscheiden."""
    doc = ezdxf.new("R2010")
    blk = doc.blocks.new(name="BAUMKR")
    blk.add_circle((0, 0), radius=1.0)
    p = tmp_path / "leer.dxf"
    doc.saveas(p)

    with pytest.raises(CadReadError, match="Keine Einfügung"):
        block_circles(p, "BAUMKR", crs=CRS)


def test_schwelle_traegt_auch_schwaches_gruen(tmp_path: Path) -> None:
    """Der eigentliche Grund für Otsu: trockener Rasen im Spätsommer hat einen
    viel schwächeren Index als saftiger. Eine gemerkte Konstante (etwa 0,05)
    zählt ihn als befestigt und meldet dabei ein plausibles Ergebnis."""
    import rasterio
    from rasterio.transform import from_origin

    n = 100
    bild = np.zeros((3, n, n), np.uint8)
    bild[:, :, : n // 2] = np.array([120, 126, 118], np.uint8)[:, None, None]  # matt
    bild[:, :, n // 2 :] = np.array([132, 132, 132], np.uint8)[:, None, None]  # Belag
    p = tmp_path / "trocken.tif"
    with rasterio.open(p, "w", driver="GTiff", height=n, width=n, count=3,
                       dtype="uint8", crs=CRS,
                       transform=from_origin(300000, 5900000, 0.2, 0.2)) as dst:
        dst.write(bild)

    gdf, info = vegetationsflaechen(p, glaettung_m=0.4, min_flaeche_m2=1.0)

    assert info["schwelle"] < 0.05, "Otsu muss unter die gemerkte Konstante gehen"
    je_klasse = gdf.groupby("klasse")["flaeche_m2"].sum()
    assert je_klasse["Vegetation"] == pytest.approx(200.0, rel=0.05)


def test_glaettungsverlust_wird_ausgewiesen(tmp_path: Path) -> None:
    """Ein grosszuegiger Radius kostet echte Flaeche — an einem realen DOP20
    waren es 37 %. Wer den Wert nicht sieht, haelt das Ergebnis fuer die
    Vegetation und nicht fuer ihren geglaetteten Rest."""
    import rasterio
    from rasterio.transform import from_origin

    # 20 x 20 m mit einem 0,6 m schmalen Rasenstreifen: schmaler als ein
    # grosszuegiges Strukturelement, breiter als ein sparsames.
    n, aufl = 100, 0.2
    bild = np.full((3, n, n), 132, np.uint8)
    bild[:, :, 40:43] = np.array([70, 120, 60], np.uint8)[:, None, None]
    p = tmp_path / "streifen.tif"
    with rasterio.open(p, "w", driver="GTiff", height=n, width=n, count=3,
                       dtype="uint8", crs=CRS,
                       transform=from_origin(300000, 5900000, aufl, aufl)) as dst:
        dst.write(bild)

    schmal, i_schmal = vegetationsflaechen(p, glaettung_m=0.2, min_flaeche_m2=0.5)
    breit, i_breit = vegetationsflaechen(p, glaettung_m=1.5, min_flaeche_m2=0.5)

    assert i_schmal["glaettungsverlust_pct"] < 20
    assert i_breit["glaettungsverlust_pct"] > 80
    assert i_breit["vegetation_roh_m2"] == pytest.approx(i_schmal["vegetation_roh_m2"])
