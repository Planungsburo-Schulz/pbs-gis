"""Reporting functions must reject CRS-less layers, not measure them raw.

``area_report`` already breaks loudly on a naive layer (``to_crs`` on naive
geometry).  ``area_length_report`` silently measured it, and ``area_by_category``
silently overlaid it — both now fail like ``area_report``.
"""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from pbs_gis.reporting import area_by_category, area_length_report

CRS = "EPSG:25833"


def _square_gdf(crs, s=10, **cols):
    return gpd.GeoDataFrame(
        {**cols, "geometry": [Polygon([(0, 0), (s, 0), (s, s), (0, s)])]}, crs=crs
    )


def test_area_length_report_crsless_raises():
    gdf = _square_gdf(crs=None)
    with pytest.raises(ValueError):
        area_length_report({"Fläche": gdf}, crs=CRS)


def test_area_length_report_with_crs_reports():
    gdf = _square_gdf(crs=CRS)
    md = area_length_report({"Fläche": gdf}, crs=CRS)
    assert "## Fläche" in md
    assert "Fläche:" in md  # area metric line for a polygon


def test_area_by_category_crsless_side_raises():
    target = _square_gdf(crs=CRS)
    cats = _square_gdf(crs=None, kat=["A"])
    with pytest.raises(ValueError, match="CRS auf beiden Layern"):
        area_by_category(target, cats, "kat")


def test_area_by_category_with_crs_reports():
    target = _square_gdf(crs=CRS, s=10)
    cats = _square_gdf(crs=CRS, s=10, kat=["A"])
    df = area_by_category(target, cats, "kat")
    assert list(df["category"]) == ["A"]
    assert df["area_m2"].iloc[0] == 100
