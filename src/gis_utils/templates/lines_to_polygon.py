"""lines_to_polygon — Convert (Multi)LineString features to a closed polygon.

Generic counterpart to ``dxf_lines_to_polygon`` — accepts any OGR-readable
vector input (Shapefile, GeoPackage, GeoJSON, …) containing LineString or
MultiLineString geometries and converts them into a single closed polygon
via the extend-and-polygonize approach.

Use when: you have a closed (or near-closed) line layer — e.g. a fence,
boundary, or buffer outline — and need a polygon to drive area
calculations or geometry differences.

Example workflow.yaml::

    - name: Build Aussenzaun
      template: lines_to_polygon
      params:
        input: Grundlagen/Linie.shp
        crs: "EPSG:25833"
        extend: 0
      output: Shapes/Aussenzaun.gpkg
"""

from __future__ import annotations

from pathlib import Path

from gis_utils.templates import register


@register(
    "lines_to_polygon",
    description="Convert (Multi)LineString features to a closed polygon (extend + polygonize)",
    params=["input", "crs", "extend", "snap_tolerance", "mode", "input_layer", "output_layer"],
)
def lines_to_polygon_template(
    params: dict, project_dir: Path, output_path: Path
) -> bool:
    """Read line geometries from any OGR source and emit a closed polygon.

    Params:
        input: Path to vector file with line geometries (relative to project root).
        crs: Target CRS (e.g. ``"EPSG:25833"``). Input is reprojected to this.
        extend (optional): Distance in metres to extend each line from both
            endpoints before polygonizing.  Default ``0``.
        snap_tolerance (optional): Snap endpoints within this distance before
            extending.  Default ``0``.
        mode (optional): ``"outer"`` (default) returns exterior ring only;
            ``"all"`` returns union of all polygonized cells.
        input_layer (optional): Layer name inside a multi-layer file (e.g. GPKG).
        output_layer (optional): Layer name to write inside the output GPKG.
            Defaults to the output file stem.
    """
    import geopandas as gpd
    from shapely.geometry import LineString, MultiLineString

    from gis_utils import lines_to_polygon

    input_path = (project_dir / params["input"]).resolve()
    crs = params["crs"]
    extend = params.get("extend", 0)
    snap_tolerance = params.get("snap_tolerance", 0)
    mode = params.get("mode", "outer")
    input_layer = params.get("input_layer")
    output_layer = params.get("output_layer", output_path.stem)

    gdf = (
        gpd.read_file(input_path, layer=input_layer)
        if input_layer
        else gpd.read_file(input_path)
    )
    gdf = gdf.to_crs(crs)

    # Flatten Multi-geometries; ignore null/empty
    lines: list[LineString] = []
    for geom in gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        if isinstance(geom, MultiLineString):
            lines.extend(geom.geoms)
        elif isinstance(geom, LineString):
            lines.append(geom)

    if not lines:
        raise ValueError(f"No LineString geometries in {input_path}")

    polygon = lines_to_polygon(
        lines, extend=extend, snap_tolerance=snap_tolerance, mode=mode
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_gdf = gpd.GeoDataFrame({"name": [output_layer]}, geometry=[polygon], crs=crs)
    if output_path.suffix.lower() == ".gpkg":
        out_gdf.to_file(output_path, driver="GPKG", layer=output_layer)
    else:
        out_gdf.to_file(output_path)

    print(
        f"[lines_to_polygon] {output_path}: 1 polygon, "
        f"area {polygon.area:,.0f} m², "
        f"({polygon.bounds[2]-polygon.bounds[0]:.0f}m x "
        f"{polygon.bounds[3]-polygon.bounds[1]:.0f}m)"
    )
    return True
