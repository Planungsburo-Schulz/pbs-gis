"""Tests for the Phase-4 workflow templates ``cad_export`` and ``publish_bilanz``.

``cad_export``: fixture GeoPackage → styled DXF, re-read via ezdxf.
``publish_bilanz``: fixture GeoPackage with a category field → flaechenbilanz.yaml
plus a Stand-Manifest whose artefact hash matches the written YAML.
"""

from __future__ import annotations

from pathlib import Path

import ezdxf
import geopandas as gpd
import pytest
import yaml
from shapely.geometry import Polygon

from pbs_gis.manifest import sha256_file
from pbs_gis.templates import get_template

CRS = "EPSG:25833"
STYLES_FIXTURE = Path(__file__).parent / "fixtures" / "cad_styles_georgendorf.yaml"


def _square(x0=0, y0=0, s=10):
    return Polygon([(x0, y0), (x0 + s, y0), (x0 + s, y0 + s), (x0, y0 + s)])


# --- cad_export ------------------------------------------------------------

def test_cad_export_registered():
    assert get_template("cad_export") is not None


def test_cad_export_end_to_end(tmp_path):
    (tmp_path / "Geodaten").mkdir()
    gpd.GeoDataFrame({"geometry": [_square()]}, crs=CRS).to_file(
        tmp_path / "Geodaten" / "geltung.gpkg", driver="GPKG"
    )
    gpd.GeoDataFrame({"geometry": [_square(20, 0, 5)]}, crs=CRS).to_file(
        tmp_path / "Geodaten" / "baufeld.gpkg", driver="GPKG"
    )
    # Copy the packaged style fixture next to the project so the template can
    # resolve it relative to project_dir.
    styles_src = Path(STYLES_FIXTURE).resolve()
    (tmp_path / "cad_styles.yaml").write_text(
        styles_src.read_text(encoding="utf-8"), encoding="utf-8"
    )

    params = {
        "crs": CRS,
        "styles": "cad_styles.yaml",
        "layers": [
            {"source": "Geodaten/geltung.gpkg", "target_layer": "Geltungsbereich",
             "style": "geltungsbereich"},
            {"source": "Geodaten/baufeld.gpkg", "target_layer": "Baufeld",
             "style": "baufeld"},
        ],
    }
    out = tmp_path / "Zeichnung" / "plan.dxf"
    ok = get_template("cad_export")(params, tmp_path, out)

    assert ok is True
    assert out.exists()
    doc = ezdxf.readfile(str(out))
    layer_names = {ly.dxf.name for ly in doc.layers}
    assert "Geltungsbereich" in layer_names
    assert "Baufeld" in layer_names
    # geometries actually landed in the target layers
    ent_layers = {e.dxf.layer for e in doc.modelspace()}
    assert "Geltungsbereich" in ent_layers


def test_cad_export_inline_styles(tmp_path):
    (tmp_path / "Geodaten").mkdir()
    gpd.GeoDataFrame({"geometry": [_square()]}, crs=CRS).to_file(
        tmp_path / "Geodaten" / "geltung.gpkg", driver="GPKG"
    )
    params = {
        "crs": CRS,
        "styles": {"schema_version": 1, "styles": {"g": {"layer": {"color": "red"}}}},
        "layers": [
            {"source": "Geodaten/geltung.gpkg", "target_layer": "G", "style": "g"},
        ],
    }
    out = tmp_path / "plan.dxf"
    assert get_template("cad_export")(params, tmp_path, out) is True
    assert out.exists()


_STYLES_MIT_TEXT = {
    "schema_version": 1,
    "styles": {
        "g": {"layer": {"color": "red"}},
        "b": {"layer": {"color": "blue"}},
        "beschriftung": {"text": {"height": 3.5}},
    },
}


def _zwei_layer_projekt(tmp_path):
    (tmp_path / "Geodaten").mkdir()
    gpd.GeoDataFrame({"geometry": [_square()]}, crs=CRS).to_file(
        tmp_path / "Geodaten" / "geltung.gpkg", driver="GPKG")
    gpd.GeoDataFrame({"geometry": [_square(20, 0, 5)]}, crs=CRS).to_file(
        tmp_path / "Geodaten" / "baufeld.gpkg", driver="GPKG")
    return [
        {"source": "Geodaten/geltung.gpkg", "target_layer": "Geltungsbereich", "style": "g"},
        {"source": "Geodaten/baufeld.gpkg", "target_layer": "Baufeld", "style": "b"},
    ]


def _mtexte(doc, layer=None):
    return [e for e in doc.modelspace().query("MTEXT")
            if layer is None or e.dxf.layer == layer]


def test_cad_export_texte_und_legende_im_dxf_und_im_manifest(tmp_path):
    """Freitexte und Legende landen im DXF, BEVOR das Manifest es hasht."""
    from pbs_gis.runner import run_workflow

    layers = _zwei_layer_projekt(tmp_path)
    step = {
        "name": "Planzeichnung",
        "template": "cad_export",
        "params": {
            "crs": CRS, "styles": _STYLES_MIT_TEXT, "layers": layers,
            "texts": [
                {"text": "Plangebiet Nord", "position": [5, 12],
                 "style": "beschriftung", "layer": "Beschriftung"},
                {"text": "ohne Stil", "position": [30, 2]},
            ],
            "legend": {"position": [50, 40], "entries": "auto"},
        },
        "output": "Zeichnung/plan.dxf",
        "publiziert": True,
    }
    (tmp_path / "workflow.yaml").write_text(
        yaml.safe_dump({"project": {"name": "t"}, "steps": [step]}, allow_unicode=True),
        encoding="utf-8")
    assert run_workflow(tmp_path) is True

    out = tmp_path / "Zeichnung" / "plan.dxf"
    doc = ezdxf.readfile(str(out))
    (frei,) = [m for m in _mtexte(doc) if m.text == "Plangebiet Nord"]
    assert frei.dxf.layer == "Beschriftung"
    assert tuple(frei.dxf.insert)[:2] == (5.0, 12.0)
    assert frei.dxf.char_height == pytest.approx(3.5)
    (ohne,) = [m for m in _mtexte(doc) if m.text == "ohne Stil"]
    assert ohne.dxf.layer == "0"
    assert [m.text for m in _mtexte(doc, "Legend")] == ["Geltungsbereich", "Baufeld"]

    m = yaml.safe_load(out.with_name("plan.dxf.manifest.yaml").read_text(encoding="utf-8"))
    assert m["hash"] == sha256_file(out)


def test_cad_export_legende_mit_layerliste_in_ihrer_reihenfolge(tmp_path):
    layers = _zwei_layer_projekt(tmp_path)
    params = {"crs": CRS, "styles": _STYLES_MIT_TEXT, "layers": layers,
              "legend": {"position": [0, 0], "entries": ["Baufeld"]}}
    out = tmp_path / "plan.dxf"
    assert get_template("cad_export")(params, tmp_path, out) is True
    doc = ezdxf.readfile(str(out))
    assert [m.text for m in _mtexte(doc, "Legend")] == ["Baufeld"]


def test_cad_export_legende_mit_eigener_beschriftung_je_ebene(tmp_path):
    """Die Planzeichenerklärung nennt das Planzeichen mit Rechtsgrundlage, nicht den Ebenen-Namen."""
    layers = _zwei_layer_projekt(tmp_path)
    params = {"crs": CRS, "styles": _STYLES_MIT_TEXT, "layers": layers,
              "legend": {"position": [0, 0], "entries": [
                  {"layer": "Geltungsbereich",
                   "label": "Grenze des räumlichen Geltungsbereichs (§ 9 Abs. 7 BauGB)"},
                  "Baufeld"]}}
    out = tmp_path / "plan.dxf"
    assert get_template("cad_export")(params, tmp_path, out) is True
    assert [m.text for m in _mtexte(ezdxf.readfile(str(out)), "Legend")] == [
        "Grenze des räumlichen Geltungsbereichs (§ 9 Abs. 7 BauGB)", "Baufeld"]


def test_cad_export_wiederholter_export_verdoppelt_texte_und_legende_nicht(tmp_path):
    layers = _zwei_layer_projekt(tmp_path)
    params = {"crs": CRS, "styles": _STYLES_MIT_TEXT, "layers": layers,
              "texts": [{"text": "Plangebiet Nord", "position": [5, 12]}],
              "legend": {"position": [50, 40], "entries": "auto"}}
    out = tmp_path / "plan.dxf"
    for _ in range(2):
        assert get_template("cad_export")(params, tmp_path, out) is True
    doc = ezdxf.readfile(str(out))
    assert [m.text for m in _mtexte(doc)].count("Plangebiet Nord") == 1
    assert [m.text for m in _mtexte(doc, "Legend")] == ["Geltungsbereich", "Baufeld"]


def test_cad_export_ohne_texte_und_legende_byte_gleich_dem_reinen_export(tmp_path, monkeypatch):
    """Ohne die neuen Parameter schreibt die Vorlage genau, was export_layers schreibt."""
    from pbs_gis.cad import LayerSpec, export_layers, parse_styles

    monkeypatch.setattr(ezdxf.options, "write_fixed_meta_data_for_testing", True)
    layers = _zwei_layer_projekt(tmp_path)
    out = tmp_path / "vorlage.dxf"
    assert get_template("cad_export")(
        {"crs": CRS, "styles": _STYLES_MIT_TEXT, "layers": layers}, tmp_path, out) is True
    direkt = tmp_path / "direkt.dxf"
    export_layers([LayerSpec(source=tmp_path / e["source"], target_layer=e["target_layer"],
                             style=e["style"]) for e in layers],
                  styles=parse_styles(_STYLES_MIT_TEXT), out_dxf=direkt, crs=CRS)
    assert out.read_bytes() == direkt.read_bytes()


@pytest.mark.parametrize("params, match", [
    ({"texts": [{"text": "x", "position": [0, 0], "style": "fehlt"}]}, "fehlt"),
    ({"texts": [{"text": "x", "position": [0, 0], "style": "g"}]}, "text"),
    ({"texts": [{"text": "x", "position": [0, 0], "farbe": "rot"}]}, "farbe"),
    ({"texts": [{"text": "x", "position": [0]}]}, "position"),
    ({"legend": {"position": [0, 0], "entries": ["Unbekannt"]}}, "Unbekannt"),
    ({"legend": {"position": [0, 0]}}, "entries"),
    ({"legend": {"position": [0, 0], "entries": [{"layer": "Unbekannt", "label": "x"}]}},
     "Unbekannt"),
    ({"legend": {"position": [0, 0], "entries": [{"layer": "Baufeld", "farbe": "x"}]}},
     "farbe"),
])
def test_cad_export_texte_und_legende_werfen_laut(tmp_path, params, match):
    layers = _zwei_layer_projekt(tmp_path)
    with pytest.raises(ValueError, match=match):
        get_template("cad_export")(
            {"crs": CRS, "styles": _STYLES_MIT_TEXT, "layers": layers, **params},
            tmp_path, tmp_path / "o.dxf")
    # Kein unbeschriftetes DXF bleibt liegen, das ein auto-Lauf für aktuell hielte.
    assert not (tmp_path / "o.dxf").exists()


def _vorlage_mit_block(tmp_path, name="Baum erhalten"):
    """Template-DXF, das einen Block definiert — die Planzeichen kommen aus der Vorlage."""
    doc = ezdxf.new("R2018")
    doc.blocks.new(name).add_circle((0, 0), 1.0)
    p = tmp_path / "vorlage.dxf"
    doc.saveas(str(p))
    return "vorlage.dxf"


def _punkt_projekt(tmp_path):
    from shapely.geometry import Point

    _zwei_layer_projekt(tmp_path)
    gpd.GeoDataFrame({"geometry": [Point(3, 4), Point(7, 8)]}, crs=CRS).to_file(
        tmp_path / "Geodaten" / "baeume.gpkg", driver="GPKG")
    return {"source": "Geodaten/baeume.gpkg", "target_layer": "Baeume", "style": "g",
            "point_block": "Baum erhalten", "point_block_scale": 2.0}


def _inserts(doc, layer=None):
    return [e for e in doc.modelspace().query("INSERT")
            if layer is None or e.dxf.layer == layer]


def test_cad_export_punkt_block_setzt_je_punkt_einen_block_statt_eines_points(tmp_path):
    spec = _punkt_projekt(tmp_path)
    params = {"crs": CRS, "styles": _STYLES_MIT_TEXT, "layers": [spec],
              "template_dxf": _vorlage_mit_block(tmp_path)}
    out = tmp_path / "plan.dxf"
    assert get_template("cad_export")(params, tmp_path, out) is True
    doc = ezdxf.readfile(str(out))
    refs = _inserts(doc, "Baeume")
    assert sorted((r.dxf.insert.x, r.dxf.insert.y) for r in refs) == [(3, 4), (7, 8)]
    assert {r.dxf.name for r in refs} == {"Baum erhalten"}
    assert {r.dxf.xscale for r in refs} == {2.0}
    assert not [e for e in doc.modelspace().query("POINT") if e.dxf.layer == "Baeume"]


def test_cad_export_freie_bloecke_landen_an_position_und_ebene(tmp_path):
    layers = _zwei_layer_projekt(tmp_path)
    params = {"crs": CRS, "styles": _STYLES_MIT_TEXT, "layers": layers,
              "template_dxf": _vorlage_mit_block(tmp_path, "Nordpfeil"),
              "blocks": [{"block": "Nordpfeil", "position": [50, 60], "layer": "Planzeichen",
                          "scale": 3.0, "rotation": 15.0}]}
    out = tmp_path / "plan.dxf"
    assert get_template("cad_export")(params, tmp_path, out) is True
    (ref,) = _inserts(ezdxf.readfile(str(out)), "Planzeichen")
    assert (ref.dxf.name, ref.dxf.insert.x, ref.dxf.insert.y) == ("Nordpfeil", 50, 60)
    assert (ref.dxf.xscale, ref.dxf.rotation) == (3.0, 15.0)


def test_cad_export_bloecke_verdoppeln_sich_beim_wiederholten_export_nicht(tmp_path):
    spec = _punkt_projekt(tmp_path)
    params = {"crs": CRS, "styles": _STYLES_MIT_TEXT, "layers": [spec],
              "template_dxf": _vorlage_mit_block(tmp_path),
              "blocks": [{"block": "Baum erhalten", "position": [0, 0]}]}
    out = tmp_path / "plan.dxf"
    for _ in range(2):
        assert get_template("cad_export")(params, tmp_path, out) is True
    assert len(_inserts(ezdxf.readfile(str(out)))) == 3


@pytest.mark.parametrize("bad, match", [
    ({"layers_patch": {"point_block": "Fehlt"}}, "Fehlt"),
    ({"blocks": [{"block": "Fehlt", "position": [0, 0]}]}, "Fehlt"),
    ({"blocks": [{"block": "Baum erhalten", "position": [0, 0], "farbe": "rot"}]}, "farbe"),
    ({"blocks": [{"block": "Baum erhalten"}]}, "position"),
])
def test_cad_export_bloecke_werfen_laut_ohne_dxf(tmp_path, bad, match):
    spec = {**_punkt_projekt(tmp_path), **bad.pop("layers_patch", {})}
    params = {"crs": CRS, "styles": _STYLES_MIT_TEXT, "layers": [spec],
              "template_dxf": _vorlage_mit_block(tmp_path), **bad}
    with pytest.raises(ValueError, match=match):
        get_template("cad_export")(params, tmp_path, tmp_path / "o.dxf")
    assert not (tmp_path / "o.dxf").exists()


def test_cad_export_label_field_ohne_text_stil_bricht_laut(tmp_path):
    """Eine Beschriftung, die der Stil nicht setzen kann, fehlt sonst still im Plan."""
    layers = _zwei_layer_projekt(tmp_path)
    layers[0]["label_field"] = "name"
    with pytest.raises(ValueError, match="label_field"):
        get_template("cad_export")(
            {"crs": CRS, "styles": _STYLES_MIT_TEXT, "layers": layers}, tmp_path,
            tmp_path / "o.dxf")
    assert not (tmp_path / "o.dxf").exists()


def test_cad_export_missing_layers_raises(tmp_path):
    with pytest.raises(ValueError):
        get_template("cad_export")({"crs": CRS, "styles": {}, "layers": []},
                                   tmp_path, tmp_path / "o.dxf")


# --- publish_bilanz --------------------------------------------------------

def test_publish_bilanz_registered():
    assert get_template("publish_bilanz") is not None


def test_publish_bilanz_yaml_and_manifest(tmp_path):
    (tmp_path / "Geodaten").mkdir()
    # 3 features, 2 categories. Areas: eignung 100+100=200, restriktion 25.
    gdf = gpd.GeoDataFrame(
        {
            "kategorie": ["eignung", "eignung", "restriktion"],
            "geometry": [_square(0, 0, 10), _square(20, 0, 10), _square(40, 0, 5)],
        },
        crs=CRS,
    )
    src = tmp_path / "Geodaten" / "kategorien.gpkg"
    gdf.to_file(src, driver="GPKG")

    params = {
        "input": "Geodaten/kategorien.gpkg",
        "category_field": "kategorie",
        "crs": CRS,
        "categories": ["ausschluss", "restriktion", "eignung"],
        "parameter": {"mindestgroesse_ha": 5},
    }
    out = tmp_path / "intern" / "flaechenbilanz.yaml"
    ok = get_template("publish_bilanz")(params, tmp_path, out)

    assert ok is True
    assert out.exists()
    bilanz = yaml.safe_load(out.read_text(encoding="utf-8"))

    assert bilanz["kategorie_feld"] == "kategorie"
    assert bilanz["parameter"] == {"mindestgroesse_ha": 5}
    kat = bilanz["kategorien"]
    # order/whitelist honoured; absent category → zero row
    assert list(kat.keys()) == ["ausschluss", "restriktion", "eignung"]
    assert kat["ausschluss"] == {"flaeche_m2": 0.0, "flaeche_ha": 0.0, "anzahl": 0}
    assert kat["eignung"]["flaeche_m2"] == pytest.approx(200.0)
    assert kat["eignung"]["anzahl"] == 2
    assert kat["restriktion"]["flaeche_m2"] == pytest.approx(25.0)
    assert bilanz["summe"]["flaeche_m2"] == pytest.approx(225.0)

    # Manifest emitted next to the YAML, artefact hash matches the written file.
    manifest = out.with_name("flaechenbilanz.yaml.manifest.yaml")
    assert manifest.exists()
    m = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    assert m["hash"] == sha256_file(out)
    assert m["artefakt"] == "flaechenbilanz.yaml"
    assert m["parameter"] == {"mindestgroesse_ha": 5}
    assert m["quellen"][0]["pfad"] == "Geodaten/kategorien.gpkg"
    assert m["quellen"][0]["hash"] == sha256_file(src)
    assert "publish_bilanz" in m["werkzeug"]


def test_publish_bilanz_all_categories_when_unlisted(tmp_path):
    (tmp_path / "Geodaten").mkdir()
    gdf = gpd.GeoDataFrame(
        {"kat": ["a", "b"], "geometry": [_square(0, 0, 10), _square(20, 0, 10)]},
        crs=CRS,
    )
    gdf.to_file(tmp_path / "Geodaten" / "k.gpkg", driver="GPKG")
    params = {"input": "Geodaten/k.gpkg", "category_field": "kat", "crs": CRS}
    out = tmp_path / "bilanz.yaml"
    assert get_template("publish_bilanz")(params, tmp_path, out) is True
    bilanz = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert set(bilanz["kategorien"]) == {"a", "b"}


def test_publish_bilanz_geographic_crs_raises(tmp_path):
    gdf = gpd.GeoDataFrame(
        {"kat": ["a"], "geometry": [_square()]}, crs="EPSG:4326"
    )
    gdf.to_file(tmp_path / "k.gpkg", driver="GPKG")
    params = {"input": "k.gpkg", "category_field": "kat"}  # no reprojection
    with pytest.raises(ValueError, match="geographic"):
        get_template("publish_bilanz")(params, tmp_path, tmp_path / "b.yaml")
