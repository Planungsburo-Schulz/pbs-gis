"""``crs`` is a mandatory parameter for the geometry-building templates.

A silent ``crs`` default mis-locates legally binding areas (Ausgleichsfläche,
Zaun) when a project uses a different CRS than the former default.  Both
templates must now fail loudly instead.
"""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from pbs_gis.templates.clip_to_flurstuecke import clip_to_flurstuecke
from pbs_gis.templates.polygon_difference import polygon_difference

CRS = "EPSG:25833"


def _square_gpkg(path, crs=CRS):
    gdf = gpd.GeoDataFrame(
        {"geometry": [Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])]}, crs=crs
    )
    gdf.to_file(path, driver="GPKG")
    return path


def test_clip_to_flurstuecke_missing_crs_raises(tmp_path):
    inp = _square_gpkg(tmp_path / "input.gpkg")
    with pytest.raises(ValueError, match="'crs' ist Pflicht"):
        clip_to_flurstuecke(
            {"input": inp.name, "state": "mv", "oids": ["DEMVAL04000wCbubFL"]},
            project_dir=tmp_path,
            output_path=tmp_path / "out.gpkg",
        )


def test_polygon_difference_missing_crs_raises(tmp_path):
    with pytest.raises(ValueError, match="'crs' ist Pflicht"):
        polygon_difference(
            {"input": "input.gpkg", "overlay": "overlay.gpkg"},
            project_dir=tmp_path,
            output_path=tmp_path / "out.gpkg",
        )
