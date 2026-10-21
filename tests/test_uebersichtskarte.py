"""Übersichtskarte (``pbs_gis.uebersichtskarte``): Selbstprobe, und die Quelle
wird gelesen statt angenommen — Projektion, Ebene und Feature-Zahl."""

import geopandas as gpd
import pytest
from PIL import Image
from shapely.geometry import Polygon

from pbs_gis.uebersichtskarte import erzeuge, feature_count, main, selbstprobe


def _quadrat(x0, y0, s=300):
    return Polygon([(x0, y0), (x0 + s, y0), (x0 + s, y0 + s), (x0, y0 + s)])


def _gpkg(pfad, ebenen):
    """``ebenen``: {Ebenenname: (EPSG, Anzahl Quadrate)}."""
    for name, (epsg, n) in ebenen.items():
        gpd.GeoDataFrame(
            {"geometry": [_quadrat(400000 + 500 * i, 5900000) for i in range(n)]},
            crs=f"EPSG:{epsg}",
        ).to_file(pfad, layer=name, driver="GPKG")
    return pfad


def _leeres_raster(wms, layer, epsg, bbox, groesse):
    return Image.new("RGBA", groesse, (255, 255, 255, 255))


def _erzeuge(quelle, ausgabe, **kw):
    return erzeuge(quelle, ausgabe, "T", "U", stand="2000-01-01",
                   hole_karte=_leeres_raster, **kw)


def test_selbstprobe_pass():
    assert selbstprobe() is True


def test_ohne_epsg_exit_2(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        main(["--quelle", str(tmp_path / "fehlt.gpkg"), "--ausgabe", str(tmp_path / "out"),
              "--titel", "T", "--untertitel", "U"])
    assert e.value.code == 2
    assert "--epsg" in capsys.readouterr().err


def test_quelle_in_anderer_projektion_bricht_ab(tmp_path):
    quelle = _gpkg(tmp_path / "q.gpkg", {"geltungsbereich": (25832, 1)})
    with pytest.raises(SystemExit) as e:
        _erzeuge(quelle, tmp_path / "out", epsg=25833)
    assert "25832" in str(e.value) and "25833" in str(e.value)
    assert not (tmp_path / "out").exists()


def test_zwei_ebenen_ohne_ebene_bricht_ab(tmp_path):
    quelle = _gpkg(tmp_path / "q.gpkg", {"umring_a": (25833, 1), "umring_b": (25833, 1)})
    with pytest.raises(SystemExit) as e:
        _erzeuge(quelle, tmp_path / "out", epsg=25833)
    assert "umring_a" in str(e.value) and "umring_b" in str(e.value)


def test_zwei_ebenen_mit_ebene(tmp_path):
    quelle = _gpkg(tmp_path / "q.gpkg", {"umring_a": (25833, 2), "umring_b": (25833, 1)})
    pfade = _erzeuge(quelle, tmp_path / "out", epsg=25833, ebene="umring_b")
    assert feature_count(pfade[2]) == 1
    assert feature_count(pfade[3]) == 1


def test_umbenannte_einzelebene(tmp_path):
    quelle = _gpkg(tmp_path / "q.gpkg", {"umring": (25833, 1)})
    pfade = _erzeuge(quelle, tmp_path / "out", epsg=25833)
    assert all(p.is_file() for p in pfade)
    assert feature_count(pfade[2]) > 0


def test_zwei_features_brechen_ab(tmp_path):
    quelle = _gpkg(tmp_path / "q.gpkg", {"geltungsbereich": (25833, 2)})
    with pytest.raises(SystemExit) as e:
        _erzeuge(quelle, tmp_path / "out", epsg=25833)
    assert "2" in str(e.value)
    assert not (tmp_path / "out").exists()


def test_dateinamen_und_liesmich_tragen_die_uebergebene_projektion(tmp_path):
    quelle = _gpkg(tmp_path / "q.gpkg", {"geltungsbereich": (25832, 1)})
    pdf, png, dxf, gpkg, man = _erzeuge(quelle, tmp_path / "out", epsg=25832)
    assert dxf.name == "2000-01-01_Geltungsbereich_EPSG25832.dxf"
    assert gpkg.name == "2000-01-01_Geltungsbereich_EPSG25832.gpkg"
    text = man.read_text(encoding="utf-8")
    assert "(EPSG:25832)" in text
    assert "- Erzeugt mit: `python -m pbs_gis.uebersichtskarte`" in text
    assert "intern/abgeleitet/uebersichtskarte.py" not in text
