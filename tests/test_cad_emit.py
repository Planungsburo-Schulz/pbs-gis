"""Unit tests for the CAD emitter (``gis_utils.cad.emit``).

Covers the hard input contract (unknown style, missing source, CRS mismatch,
missing/no CRS) and an emit roundtrip: build small GeoPackages, export to DXF,
re-read via ezdxf, and assert layers/entity counts/colours.
"""

from __future__ import annotations

import ezdxf
import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, Polygon

from gis_utils.cad import ExportError, LayerSpec, export_layers, load_styles

CRS = "EPSG:25833"
FIXTURE = "tests/fixtures/cad_styles_georgendorf.yaml"


def _gpkg(tmp_path, name, geom, crs=CRS, extra=None):
    """Write a one-feature GeoPackage and return its path."""
    data = {"geometry": [geom]}
    if extra:
        data.update({k: [v] for k, v in extra.items()})
    gdf = gpd.GeoDataFrame(data, crs=crs)
    path = tmp_path / f"{name}.gpkg"
    gdf.to_file(path, driver="GPKG")
    return path


def _square(x0=0, y0=0, s=10):
    return Polygon([(x0, y0), (x0 + s, y0), (x0 + s, y0 + s), (x0, y0 + s)])


# --- contract --------------------------------------------------------------

def test_crs_required(tmp_path):
    src = _gpkg(tmp_path, "geltung", _square())
    with pytest.raises(ExportError):
        export_layers(
            [LayerSpec(src, "Geltungsbereich", "geltungsbereich")],
            styles=FIXTURE, out_dxf=tmp_path / "out.dxf", crs="",
        )


def test_unknown_style_raises(tmp_path):
    src = _gpkg(tmp_path, "geltung", _square())
    with pytest.raises(ExportError) as exc:
        export_layers(
            [LayerSpec(src, "X", "does_not_exist")],
            styles=FIXTURE, out_dxf=tmp_path / "out.dxf", crs=CRS,
        )
    assert "does_not_exist" in str(exc.value)


def test_missing_source_raises(tmp_path):
    with pytest.raises(ExportError):
        export_layers(
            [LayerSpec(tmp_path / "nope.gpkg", "X", "geltungsbereich")],
            styles=FIXTURE, out_dxf=tmp_path / "out.dxf", crs=CRS,
        )


def test_crs_mismatch_raises(tmp_path):
    # Source in a different CRS than the required project CRS.
    src = _gpkg(tmp_path, "geltung", _square(), crs="EPSG:25832")
    with pytest.raises(ExportError) as exc:
        export_layers(
            [LayerSpec(src, "Geltungsbereich", "geltungsbereich")],
            styles=FIXTURE, out_dxf=tmp_path / "out.dxf", crs=CRS,
        )
    assert "CRS" in str(exc.value)


def test_source_without_crs_raises(tmp_path):
    src = _gpkg(tmp_path, "nocrs", _square(), crs=None)
    with pytest.raises(ExportError):
        export_layers(
            [LayerSpec(src, "X", "geltungsbereich")],
            styles=FIXTURE, out_dxf=tmp_path / "out.dxf", crs=CRS,
        )


# --- emit roundtrip --------------------------------------------------------

def test_polygon_roundtrip(tmp_path):
    src = _gpkg(tmp_path, "geltung", _square())
    out = tmp_path / "out.dxf"
    results = export_layers(
        [LayerSpec(src, "Geltungsbereich", "geltungsbereich")],
        styles=FIXTURE, out_dxf=out, crs=CRS,
    )
    assert out.exists()
    assert results[0].features == 1
    assert results[0].geometries == 1  # exterior ring, no holes

    doc = ezdxf.readfile(str(out))
    assert "Geltungsbereich" in doc.layers
    layer = doc.layers.get("Geltungsbereich")
    assert layer.color == 10  # "red" → aci 10 (last-wins table; matches real DXF)
    assert int(layer.dxf.plot) == 0  # plot: false
    polylines = doc.modelspace().query('LWPOLYLINE[layer=="Geltungsbereich"]')
    assert len(polylines) == 1


def test_polygon_with_hatch_on_separate_layer(tmp_path):
    # Georgendorf muster: baufeld hatch carries layer_suffix "Schraffur", so the
    # outline stays on "Baufeld" and the hatch lands on "Baufeld Schraffur".
    src = _gpkg(tmp_path, "baufeld", _square())
    out = tmp_path / "out.dxf"
    results = export_layers(
        [LayerSpec(src, "Baufeld", "baufeld")],
        styles=FIXTURE, out_dxf=out, crs=CRS,
    )
    assert results[0].hatches == 1
    doc = ezdxf.readfile(str(out))
    assert "Baufeld Schraffur" in doc.layers
    assert len(doc.modelspace().query('HATCH[layer=="Baufeld"]')) == 0
    assert len(doc.modelspace().query('HATCH[layer=="Baufeld Schraffur"]')) == 1
    # The polygon outline stays on the geometry layer.
    assert len(doc.modelspace().query('LWPOLYLINE[layer=="Baufeld"]')) == 1


def test_line_and_point(tmp_path):
    line = _gpkg(tmp_path, "line", LineString([(0, 0), (10, 0), (10, 10)]))
    point = _gpkg(tmp_path, "pt", Point(5, 5))
    out = tmp_path / "out.dxf"
    export_layers(
        [
            LayerSpec(line, "Baugrenze", "baugrenze"),
            LayerSpec(point, "PunktL", "geltungsbereich"),
        ],
        styles=FIXTURE, out_dxf=out, crs=CRS,
    )
    doc = ezdxf.readfile(str(out))
    assert len(doc.modelspace().query('LWPOLYLINE[layer=="Baugrenze"]')) == 1
    assert len(doc.modelspace().query('POINT[layer=="PunktL"]')) == 1


def test_labels_written(tmp_path):
    src = _gpkg(tmp_path, "geltung", _square(), extra={"name": "GB-1"})
    out = tmp_path / "out.dxf"
    # baugrenze carries no text block → geltungsbereich would need one; use a
    # style with text via a small inline map instead.
    from gis_utils.cad.styles import parse_styles

    styles = parse_styles({
        "schema_version": 1,
        "styles": {"lbl": {
            "layer": {"color": "green"},
            "text": {"height": 2.0, "font": "Standard", "attachment": "MIDDLE_CENTER"},
        }},
    })
    results = export_layers(
        [LayerSpec(src, "Label", "lbl", label_field="name")],
        styles=styles, out_dxf=out, crs=CRS,
    )
    assert results[0].labels == 1
    doc = ezdxf.readfile(str(out))
    assert len(doc.modelspace().query('MTEXT[layer=="Label"]')) == 1


def test_template_content_kept(tmp_path):
    # A template DXF with pre-existing content: our layer is added on top and
    # the template's entities are retained.
    tpl = tmp_path / "tpl.dxf"
    tdoc = ezdxf.new("R2010")
    tdoc.modelspace().add_line((0, 0), (1, 1))
    tdoc.saveas(str(tpl))

    src = _gpkg(tmp_path, "geltung", _square())
    out = tmp_path / "out.dxf"
    export_layers(
        [LayerSpec(src, "Geltungsbereich", "geltungsbereich")],
        styles=FIXTURE, out_dxf=out, crs=CRS, template_dxf=tpl,
    )
    doc = ezdxf.readfile(str(out))
    assert len(doc.modelspace().query("LINE")) == 1  # template content kept
    assert "Geltungsbereich" in doc.layers


def test_reexport_is_idempotent_and_keeps_foreign(tmp_path):
    # First export, then draw a foreign entity into the DXF (as a human would in
    # AutoCAD), then re-export: our layers are rewritten, the foreign line stays.
    src = _gpkg(tmp_path, "geltung", _square())
    out = tmp_path / "out.dxf"
    export_layers(
        [LayerSpec(src, "Geltungsbereich", "geltungsbereich")],
        styles=FIXTURE, out_dxf=out, crs=CRS,
    )
    doc = ezdxf.readfile(str(out))
    doc.modelspace().add_line((0, 0), (99, 99), dxfattribs={"layer": "HandDrawn"})
    doc.saveas(str(out))

    # Re-export the same layer.
    export_layers(
        [LayerSpec(src, "Geltungsbereich", "geltungsbereich")],
        styles=FIXTURE, out_dxf=out, crs=CRS,
    )
    doc = ezdxf.readfile(str(out))
    # Exactly one of our polylines (not doubled), and the foreign line survives.
    assert len(doc.modelspace().query('LWPOLYLINE[layer=="Geltungsbereich"]')) == 1
    assert len(doc.modelspace().query('LINE[layer=="HandDrawn"]')) == 1


def test_invalid_lineweight_snaps_to_nearest(tmp_path):
    from gis_utils.cad.styles import parse_styles

    styles = parse_styles({
        "schema_version": 1,
        "styles": {"lw": {"layer": {"color": "green", "lineweight": 42}}},
    })
    src = _gpkg(tmp_path, "bg", _square())
    out = tmp_path / "out.dxf"
    results = export_layers(
        [LayerSpec(src, "LW", "lw")], styles=styles, out_dxf=out, crs=CRS,
    )
    assert any("lineweight" in w and "snapped" in w for w in results[0].warnings)
    doc = ezdxf.readfile(str(out))
    # 42 is invalid; nearest valid enum value is 40.
    assert doc.layers.get("LW").dxf.lineweight == 40


def test_invalid_linetype_falls_back(tmp_path):
    # A fresh document has no ACAD_ISO11W100 → warn + keep CONTINUOUS, no crash.
    src = _gpkg(tmp_path, "bg", _square())
    out = tmp_path / "out.dxf"
    results = export_layers(
        [LayerSpec(src, "Baugrenze", "baugrenze")],
        styles=FIXTURE, out_dxf=out, crs=CRS,
    )
    assert any("linetype" in w for w in results[0].warnings)
    doc = ezdxf.readfile(str(out))
    assert "Baugrenze" in doc.layers


def test_z_geometries_export(tmp_path):
    # Regression: 3D-Quellen (POLYGON Z / LINESTRING Z) brachen die
    # Koordinaten-Entpackung ("too many values to unpack (expected 2, got 3)").
    poly_z = Polygon([(0, 0, 5), (10, 0, 5), (10, 10, 5), (0, 10, 5)])
    line_z = LineString([(0, 0, 1), (5, 5, 2), (10, 5, 3)])
    out = tmp_path / "out.dxf"
    results = export_layers(
        [
            LayerSpec(_gpkg(tmp_path, "geltung_z", poly_z), "Geltungsbereich", "geltungsbereich"),
            LayerSpec(_gpkg(tmp_path, "grenze_z", line_z), "Baugrenze", "baugrenze"),
        ],
        styles=FIXTURE, out_dxf=out, crs=CRS,
    )
    assert out.exists()
    assert results[0].geometries == 1
    doc = ezdxf.readfile(str(out))
    assert len(doc.modelspace().query('LWPOLYLINE[layer=="Geltungsbereich"]')) == 1
    assert len(doc.modelspace().query('LWPOLYLINE[layer=="Baugrenze"]')) == 1
