"""dxf_hatch_areas — Read HATCH surfaces from a DXF into a GeoPackage.

A design drawing carries its surfaces as hatches, one layer per material. This
step turns them into polygons with their layer name and area, so the drawing's
own surfaces can be measured and checked against a quantity take-off.

Whole hatches are read: every boundary path, arcs flattened, inner paths
subtracted as holes.

Use when: You need the planner's surfaces as GIS geometry — for an area balance,
a sealing/unsealing comparison, or a map that colours by material.

Example workflow.yaml::

    - name: Materialflächen
      template: dxf_hatch_areas
      params:
        dxf: Grundlagen/Plan/Planung/X_Entwurf_SRF.dxf
        crs: "EPSG:25833"
        strip_zone: true
        dissolve: true
      output: Geodaten/materialflaechen.gpkg
      run: always
"""

from __future__ import annotations

from pathlib import Path

from pbs_gis.templates import register


@register(
    "dxf_hatch_areas",
    description="Read HATCH surfaces from a DXF into a GeoPackage (layer, area_m2)",
    params=["dxf", "crs", "layers", "strip_zone", "dissolve", "flattening"],
)
def dxf_hatch_areas(params: dict, project_dir: Path, output_path: Path) -> bool:
    """Write the drawing's hatch surfaces as a GeoPackage.

    Params:
        dxf: Path to the source ``.dxf``/``.dwg`` (relative to project root).
        crs: CRS of the drawing's coordinates, e.g. ``"EPSG:25833"``.
        layers (optional): Layer names to read.  Default: every layer with hatches.
        strip_zone (optional): Subtract the UTM zone prefix from X.  Default false.
        dissolve (optional): One row per layer instead of one per hatch.
            Default false.
        flattening (optional): Chord error in metres for arcs.  Default 0.02.
    """
    from pbs_gis.dxf.hatch import DEFAULT_FLATTENING, extract_hatch_areas

    gdf = extract_hatch_areas(
        project_dir / params["dxf"],
        crs=params["crs"],
        layers=params.get("layers"),
        strip_zone=params.get("strip_zone", False),
        flattening=params.get("flattening", DEFAULT_FLATTENING),
        dissolve=params.get("dissolve", False),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(output_path, driver="GPKG")

    print(f"  {len(gdf)} Fläche(n) auf {gdf['layer'].nunique()} Layer(n), "
          f"{gdf['area_m2'].sum():,.1f} m² -> {output_path}")
    return True
