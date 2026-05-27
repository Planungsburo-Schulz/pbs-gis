"""
GIS area and intersection reporting with markdown output.

Generates area summary reports for polygon layers, optionally intersected
with cadastral parcels or categorized by attribute columns.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union
from shapely.validation import make_valid

from gis_utils.md_table import markdown_table


def intersection_areas(
    geom,
    parcels_gdf: gpd.GeoDataFrame,
    *,
    label_col: str = "label",
    min_area_m2: float = 0.01,
) -> pd.DataFrame:
    """
    Calculate intersection area between a geometry and each parcel.

    Args:
        geom: A Shapely geometry (typically a union of a layer's polygons).
        parcels_gdf: GeoDataFrame of parcels to intersect with.
            Must be in a projected CRS (meters) for accurate area calculation.
        label_col: Column in parcels_gdf to use as parcel label.
        min_area_m2: Ignore intersections smaller than this (filters noise).

    Returns:
        DataFrame with columns: label, area_m2, area_ha.
        Sorted by area descending.
    """
    if geom is None or geom.is_empty:
        return pd.DataFrame(columns=["label", "area_m2", "area_ha"])

    rows: list[dict[str, Any]] = []
    for idx, row in parcels_gdf.iterrows():
        parcel_geom = row.geometry
        if parcel_geom is None or not parcel_geom.intersects(geom):
            continue
        inter = parcel_geom.intersection(geom)
        area = inter.area
        if area < min_area_m2:
            continue
        label = row.get(label_col, f"parcel_{idx}") if label_col in parcels_gdf.columns else f"parcel_{idx}"
        rows.append({
            "label": str(label),
            "area_m2": round(area, 2),
            "area_ha": round(area / 10_000, 4),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("area_m2", ascending=False).reset_index(drop=True)
    return df


def area_by_category(
    target_gdf: gpd.GeoDataFrame,
    category_gdf: gpd.GeoDataFrame,
    category_col: str,
    *,
    min_area_m2: float = 1e-6,
) -> pd.DataFrame:
    """
    Calculate intersection areas between a target layer and categories.

    Useful for "area by biotope type", "area by land use class", etc.

    Args:
        target_gdf: GeoDataFrame of target polygons (e.g. project area).
        category_gdf: GeoDataFrame with category polygons (e.g. biotope types).
        category_col: Column in category_gdf that defines categories.
        min_area_m2: Filter out intersection fragments below this area.

    Returns:
        DataFrame with columns: category, area_m2, area_ha.
        Sorted by category.
    """
    # Ensure same CRS
    if target_gdf.crs and category_gdf.crs and target_gdf.crs != category_gdf.crs:
        category_gdf = category_gdf.to_crs(target_gdf.crs)

    target = target_gdf[target_gdf.geometry.notna()].copy()
    target["geometry"] = target.geometry.apply(lambda g: make_valid(g) if not g.is_valid else g)

    cats = category_gdf[[category_col, "geometry"]].copy()
    cats = cats[cats.geometry.notna()].copy()
    cats["geometry"] = cats.geometry.apply(lambda g: make_valid(g) if not g.is_valid else g)

    try:
        inter = gpd.overlay(target[["geometry"]], cats, how="intersection")
    except TypeError:
        inter = gpd.overlay(target[["geometry"]], cats, how="intersection")

    inter = inter[inter.geometry.notna()].copy()
    inter["area_m2"] = inter.geometry.area
    inter = inter[inter["area_m2"] > min_area_m2]

    result = (
        inter.groupby(category_col, dropna=False)["area_m2"]
        .sum()
        .reset_index()
        .rename(columns={category_col: "category"})
    )
    result["area_m2"] = result["area_m2"].round(0).astype(int)
    result["area_ha"] = (result["area_m2"] / 10_000).round(4)
    return result.sort_values("category").reset_index(drop=True)


def _auto_metrics_for(gdf: gpd.GeoDataFrame) -> list[str]:
    """Pick reasonable default metrics based on geometry type.

    Polygons → ['length', 'area']  (length = perimeter, from boundary)
    Lines    → ['length']
    Other    → []
    """
    if gdf.empty:
        return []
    gt = str(gdf.geom_type.iloc[0])
    if "Polygon" in gt:
        return ["length", "area"]
    if "Line" in gt:
        return ["length"]
    return []


def area_length_report(
    layers: dict[str, gpd.GeoDataFrame],
    *,
    crs: str,
    title: str = "Geometry Report",
    metrics_per_layer: dict[str, list[str]] | None = None,
    number_format: str = ",.1f",
) -> str:
    """Generate a markdown report combining area and length per layer.

    Polygon layers report both perimeter (length of boundary) and area;
    line layers report length. Override per-layer via ``metrics_per_layer``,
    e.g. ``{"Outer fence": ["length"]}`` to suppress area on a polygon.

    Args:
        layers: Dict of {layer_name: GeoDataFrame}.
        crs: Projected CRS for measurements (must use metres).
        title: Report title (rendered as h1).
        metrics_per_layer: Optional override of which metrics to compute per
            layer. Allowed metrics: ``"length"``, ``"area"``.
        number_format: Python format spec applied to numeric values.

    Returns:
        Markdown string ready to write to a file. Each layer becomes a
        bullet list of metrics; areas additionally include hectares.
    """
    metrics_per_layer = metrics_per_layer or {}
    out: list[str] = [f"# {title}", ""]

    for name, gdf in layers.items():
        out.append(f"## {name}")
        if gdf.empty:
            out.append("- (leer)")
            out.append("")
            continue
        gdf_proj = gdf.to_crs(crs) if gdf.crs and str(gdf.crs) != crs else gdf
        metrics = metrics_per_layer.get(name, _auto_metrics_for(gdf_proj))
        if not metrics:
            out.append("- (keine Metriken — Geometrie weder Linie noch Polygon)")
            out.append("")
            continue

        gt = str(gdf_proj.geom_type.iloc[0])
        for metric in metrics:
            if metric == "length":
                if "Polygon" in gt:
                    val = gdf_proj.geometry.boundary.length.sum()
                    label = "Länge (Umfang)"
                else:
                    val = gdf_proj.geometry.length.sum()
                    label = "Länge"
                out.append(f"- **{label}:** {val:{number_format}} m")
            elif metric == "area":
                area_m2 = gdf_proj.geometry.area.sum()
                out.append(
                    f"- **Fläche:** {area_m2:{number_format}} m² "
                    f"({area_m2/10_000:.3f} ha)"
                )
            else:
                raise ValueError(
                    f"Unknown metric {metric!r}; valid: 'length', 'area'"
                )
        out.append("")

    return "\n".join(out)


def area_report(
    layers: dict[str, gpd.GeoDataFrame],
    *,
    intersect_with: gpd.GeoDataFrame | None = None,
    intersect_label_col: str = "label",
    category_gdf: gpd.GeoDataFrame | None = None,
    category_col: str | None = None,
    crs: str,
    union_before_area: bool = True,
    title: str = "Area Report",
    number_format: str = ",.0f",
) -> str:
    """
    Generate a markdown area report for multiple polygon layers.

    Args:
        layers: Dict mapping layer names to GeoDataFrames.
        intersect_with: Optional parcels GeoDataFrame to intersect layers with.
        intersect_label_col: Column name for parcel labels.
        category_gdf: Optional GeoDataFrame for area-by-category breakdown.
        category_col: Column in category_gdf defining categories.
        crs: Projected CRS for area calculations (must use meters).
        union_before_area: Union each layer's geometries before calculating area
            (avoids double-counting overlapping polygons).
        title: Report title.
        number_format: Number format for markdown tables.

    Returns:
        Markdown string ready to write to a file.
    """
    sections: list[str] = [f"# {title}\n"]

    # Summary table
    summary_headers = ["Layer", "Area (m²)", "Area (ha)"]
    summary_rows: list[list[Any]] = []

    for name, gdf in layers.items():
        if gdf.empty:
            summary_rows.append([name, 0, 0.0])
            continue

        gdf_proj = gdf.to_crs(crs) if gdf.crs != crs else gdf

        if union_before_area:
            valid_geoms = [make_valid(g) for g in gdf_proj.geometry if g is not None]
            if not valid_geoms:
                summary_rows.append([name, 0, 0.0])
                continue
            union = unary_union(valid_geoms)
            area_m2 = union.area
        else:
            area_m2 = gdf_proj.geometry.area.sum()

        summary_rows.append([name, round(area_m2), round(area_m2 / 10_000, 4)])

    sections.append("## Summary\n")
    sections.append(markdown_table(summary_headers, summary_rows,
                                   number_format=number_format))

    # Intersection with parcels
    if intersect_with is not None:
        parcels_proj = intersect_with.to_crs(crs) if intersect_with.crs != crs else intersect_with

        for name, gdf in layers.items():
            if gdf.empty:
                continue
            gdf_proj = gdf.to_crs(crs) if gdf.crs != crs else gdf
            valid_geoms = [make_valid(g) for g in gdf_proj.geometry if g is not None]
            if not valid_geoms:
                continue
            union = unary_union(valid_geoms)

            df = intersection_areas(union, parcels_proj,
                                    label_col=intersect_label_col)
            if df.empty:
                continue

            sections.append(f"\n## {name} — Intersection\n")
            sections.append(markdown_table(
                ["Parcel", "Area (m²)", "Area (ha)"],
                df[["label", "area_m2", "area_ha"]].values.tolist(),
                number_format=number_format,
            ))

    # Category breakdown
    if category_gdf is not None and category_col is not None:
        cats_proj = category_gdf.to_crs(crs) if category_gdf.crs != crs else category_gdf

        for name, gdf in layers.items():
            if gdf.empty:
                continue
            gdf_proj = gdf.to_crs(crs) if gdf.crs != crs else gdf

            df = area_by_category(gdf_proj, cats_proj, category_col)
            if df.empty:
                continue

            sections.append(f"\n## {name} — by {category_col}\n")
            sections.append(markdown_table(
                [category_col, "Area (m²)", "Area (ha)"],
                df[["category", "area_m2", "area_ha"]].values.tolist(),
                number_format=number_format,
            ))

    return "\n".join(sections)
