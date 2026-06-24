"""point_buffer_union — Buffer points by attribute and union into a single polygon.

Loads point features, buffers each by a radius derived from an attribute
column (optionally multiplied by a factor), and unions all buffers into a
single polygon.  Useful for creating exclusion/influence zones around WEA,
BHKW, or other point features.

Output is a single polygon GeoPackage that can be styled as an exclusion
zone, Rotor-overlay, or influence area on maps.

Example workflow.yaml::

    - name: WEA 15x Höhe Buffer
      template: point_buffer_union
      params:
        input: Geodaten/WKA.gpkg
        crs: "EPSG:25832"
        buffer_col: HOEHE_M
        buffer_factor: 15
        default_buffer: 125
      output: output/wea_buffer_15x.gpkg
"""

from __future__ import annotations

from pathlib import Path

from gis_utils.templates import register


@register(
    "point_buffer_union",
    description="Buffer points by attribute × factor, union into single polygon",
    params=["input", "crs", "buffer_col", "buffer_factor", "default_buffer"],
)
def point_buffer_union(
    params: dict, project_dir: Path, output_path: Path
) -> bool:
    """Buffer point features and union into a single polygon.

    Params:
        input: Path to GeoPackage/shapefile with point geometry.
        crs: Projected CRS for buffering (e.g. ``"EPSG:25832"``).
        buffer_col: Column name containing the buffer radius value
            (e.g. ``"HOEHE_M"`` for turbine height).
        buffer_factor (optional): Multiply the column value by this factor
            (e.g. ``15`` for 15× height).  Default ``1.0``.
        default_buffer (optional): Fallback radius when the column value is
            missing or zero.  Default ``0`` (skip feature).
    """
    import geopandas as gpd
    from shapely.ops import unary_union

    input_path = project_dir / params["input"]
    crs = params["crs"]
    buffer_col = params["buffer_col"]
    buffer_factor = params.get("buffer_factor", 1.0)
    default_buffer = params.get("default_buffer", 0)

    gdf = gpd.read_file(input_path).to_crs(crs)

    buffers = []
    for _, row in gdf.iterrows():
        radius = row.get(buffer_col, default_buffer)
        if radius is None or radius <= 0:
            radius = default_buffer
        if radius <= 0:
            continue
        buffers.append(row.geometry.buffer(radius * buffer_factor))

    if not buffers:
        print("  No valid buffer geometries created")
        return False

    union_geom = unary_union(buffers)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = gpd.GeoDataFrame({"geometry": [union_geom]}, crs=crs)
    result.to_file(output_path, driver="GPKG")

    print(
        f"  Buffer union: {union_geom.area:.0f} m² from "
        f"{len(buffers)} points (factor={buffer_factor}×)"
    )
    return True
