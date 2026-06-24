"""polygon_difference — input minus one or more overlays (geometry difference).

Trivial workflow primitive: read polygon layers, subtract the union of
all overlays from the input, write the result. Equivalent to QGIS
Processing's ``native:difference`` but pure-Python (no QGIS dependency).

Single overlay::

    - name: Build Reptilienschutzzaun
      template: polygon_difference
      params:
        input: Geodaten/Flurstück for Reptilienschutzzaun.gpkg
        overlay: Geodaten/Remove from Reptilienschutzzaun.gpkg
        crs: "EPSG:25833"
      output: Geodaten/Reptilienschutzzaun.gpkg

Multiple overlays (union'd before subtraction)::

    - name: Build Ausgleichsfläche
      template: polygon_difference
      params:
        input: Shapes/Flurstuecke.gpkg
        overlay:
          - Shapes/Aussenzaun.gpkg
          - Shapes/Exclude Ausgleichsfläche.gpkg
        crs: "EPSG:25833"
        # overlay_layer: optional — pass a list of same length as `overlay`,
        # or a single string applied to every overlay file.
      output: Shapes/Ausgleichsflaeche.gpkg

The output retains the **input**'s attributes (overlays are treated as
a subtractive mask, not joined). Empty results are dropped; remaining
MultiPolygons are exploded to single Polygons (per gis_utils output
convention).
"""

from __future__ import annotations

from pathlib import Path

from gis_utils.templates import register


@register(
    "polygon_difference",
    description="Geometry difference: input layer minus overlay(s). Overlay accepts a list.",
    params=["input", "overlay", "crs", "input_layer", "overlay_layer", "output_layer"],
)
def polygon_difference(
    params: dict, project_dir: Path, output_path: Path | None
) -> bool:
    import geopandas as gpd
    from shapely import union_all

    if output_path is None:
        raise ValueError("polygon_difference requires an 'output:' path")

    input_path = (project_dir / params["input"]).resolve()
    crs = params.get("crs", "EPSG:25833")
    input_layer = params.get("input_layer")
    output_layer = params.get("output_layer", output_path.stem)

    # --- Normalize overlay & overlay_layer to parallel lists ---
    overlay_param = params["overlay"]
    overlay_paths: list[str] = (
        list(overlay_param) if isinstance(overlay_param, list) else [overlay_param]
    )

    overlay_layer_param = params.get("overlay_layer")
    if isinstance(overlay_layer_param, list):
        overlay_layers = overlay_layer_param
        if len(overlay_layers) != len(overlay_paths):
            raise ValueError(
                f"overlay_layer list length ({len(overlay_layers)}) must match "
                f"overlay list length ({len(overlay_paths)})"
            )
    elif overlay_layer_param is None:
        overlay_layers = [None] * len(overlay_paths)
    else:
        overlay_layers = [overlay_layer_param] * len(overlay_paths)

    # --- Read input ---
    inp = (
        gpd.read_file(input_path, layer=input_layer)
        if input_layer
        else gpd.read_file(input_path)
    )
    inp = inp.to_crs(crs)

    # --- Read & union every overlay ---
    overlay_geoms = []
    for ovl_rel, ovl_lyr in zip(overlay_paths, overlay_layers):
        ovl_path = (project_dir / ovl_rel).resolve()
        ovl = (
            gpd.read_file(ovl_path, layer=ovl_lyr)
            if ovl_lyr
            else gpd.read_file(ovl_path)
        )
        ovl = ovl.to_crs(crs)
        overlay_geoms.append(ovl.geometry.union_all())
    overlay_geom = union_all(overlay_geoms)

    # --- Subtract ---
    inp["geometry"] = inp.geometry.difference(overlay_geom)
    inp = inp[~inp.geometry.is_empty]
    inp = inp.explode(index_parts=False).reset_index(drop=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() == ".gpkg":
        inp.to_file(output_path, driver="GPKG", layer=output_layer)
    else:
        inp.to_file(output_path)
    print(
        f"[polygon_difference] {output_path}: {len(inp)} feature(s), "
        f"area {inp.geometry.area.sum():.1f} m² "
        f"(overlays merged: {len(overlay_paths)})"
    )
    return True
