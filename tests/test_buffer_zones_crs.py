"""A CRS-less buffer source must be an explicit assumption, not a silent stamp.

Stamping a naive layer with the working ``crs`` presumes its coordinates
already sit in that frame — a mis-location if they don't.  The template now
raises unless ``source_crs_assume`` declares the real source CRS.
"""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from pbs_gis.templates.buffer_zones import buffer_zones_template

CRS = "EPSG:25833"


def _write_source(path, crs):
    gdf = gpd.GeoDataFrame(
        {"geometry": [Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])]}, crs=crs
    )
    gdf.to_file(path, driver="GPKG")
    return path


def _params(**extra):
    p = {"source": "source.gpkg", "crs": CRS, "zones": [{"name": "0-50m", "outer_m": 50}]}
    p.update(extra)
    return p


def test_crsless_source_without_assume_raises(tmp_path):
    _write_source(tmp_path / "source.gpkg", crs=None)
    with pytest.raises(ValueError, match="trägt kein CRS"):
        buffer_zones_template(_params(), tmp_path, tmp_path / "out.gpkg")


def test_crsless_source_with_assume_stamps_and_completes(tmp_path, capsys):
    _write_source(tmp_path / "source.gpkg", crs=None)
    out = tmp_path / "out.gpkg"
    ok = buffer_zones_template(_params(source_crs_assume=CRS), tmp_path, out)
    assert ok is True
    assert out.exists()
    assert "CRS-Annahme" in capsys.readouterr().out


def test_source_with_crs_still_works(tmp_path):
    _write_source(tmp_path / "source.gpkg", crs=CRS)
    ok = buffer_zones_template(_params(), tmp_path, tmp_path / "out.gpkg")
    assert ok is True
