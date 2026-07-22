"""clip_to_flurstuecke — Clip a vector layer to the union of named Flurstücke.

Common pattern in PBS workflows: a layer (e.g. ``Ausgleichsflächen``) was
drawn against the open canvas without strict respect of parcel boundaries,
and the final artifact must be trimmed so only the parts on a permitted
set of Flurstücke remain. The template fetches those Flurstücke from the
ALKIS WFS by OID, takes their union, intersects the input layer, and
writes the result.

Example workflow.yaml::

    - name: Clip Ausgleichsflächen to allowed Flurstücke
      template: clip_to_flurstuecke
      params:
        input: Geodaten/Ausgleichsflächen.gpkg
        state: mv
        oids:
          - DEMVAL04000wCbubFL
          - DEMVAL04Z00004DqFL
        crs: "EPSG:25833"
        bbox_buffer_m: 500    # default; widens input extent for the WFS bbox query
        overwrite: true       # write back to the same file (in-place clip)
      run: always             # in-place mutations can't be inferred from output mtime
      output: Geodaten/Ausgleichsflächen.gpkg

If ``overwrite: false`` (default), ``output`` may differ from ``input``.
"""

from __future__ import annotations

from pathlib import Path

from pbs_gis.templates import register


@register(
    "clip_to_flurstuecke",
    description=(
        "Clip a vector layer to the union of Flurstücke identified by OID "
        "(fetched from ALKIS WFS); supports in-place overwrite"
    ),
    params=["input", "state", "oids", "crs", "bbox_buffer_m", "overwrite", "input_layer"],
)
def clip_to_flurstuecke(
    params: dict, project_dir: Path, output_path: Path | None
) -> bool:
    import geopandas as gpd

    from pbs_gis.alkis import find_flurstuecke

    input_path = (project_dir / params["input"]).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"input layer not found: {input_path}")

    state = params["state"]
    oids = params["oids"]
    if not oids:
        raise ValueError("clip_to_flurstuecke requires non-empty 'oids' list")
    crs = params.get("crs")
    if not crs:
        raise ValueError(
            "'crs' ist Pflicht (kein stiller Default — DATENKORREKTHEIT "
            "gefährliche Defaults); z. B. 'EPSG:25833'"
        )
    bbox_buffer_m = float(params.get("bbox_buffer_m", 500))
    overwrite = bool(params.get("overwrite", False))
    input_layer = params.get("input_layer")

    if overwrite and output_path and output_path.resolve() != input_path:
        raise ValueError(
            "overwrite=true but output ({}) differs from input ({})".format(
                output_path, input_path
            )
        )
    if not output_path:
        if not overwrite:
            raise ValueError("either output: or overwrite: true is required")
        output_path = input_path

    # Read input, derive bbox, fetch Flurstücke
    if input_layer:
        gdf = gpd.read_file(input_path, layer=input_layer)
    else:
        gdf = gpd.read_file(input_path)
    gdf = gdf.to_crs(crs)
    minx, miny, maxx, maxy = gdf.total_bounds
    extent = (
        minx - bbox_buffer_m, miny - bbox_buffer_m,
        maxx + bbox_buffer_m, maxy + bbox_buffer_m,
    )

    flurst = find_flurstuecke(
        state=state,
        oids=oids,
        extent=extent,
        crs=crs,
        project_dir=project_dir,
    )
    if len(flurst) != len(oids):
        raise SystemExit(
            f"[clip_to_flurstuecke] expected {len(oids)} Flurstücke matching "
            f"{oids}, got {len(flurst)} ({sorted(flurst['oid'].tolist()) if 'oid' in flurst.columns else '?'})"
        )

    allowed = flurst.geometry.union_all()

    before = len(gdf)
    gdf["geometry"] = gdf.geometry.intersection(allowed)
    gdf = gdf[~gdf.geometry.is_empty]
    gdf = gdf.explode(index_parts=False).reset_index(drop=True)

    # Write — preserve layer name if input was a GPKG with a named layer
    layer_name = input_layer or output_path.stem
    if output_path.suffix.lower() == ".gpkg":
        gdf.to_file(output_path, driver="GPKG", layer=layer_name)
    else:
        gdf.to_file(output_path)
    print(
        f"[clip_to_flurstuecke] {output_path}: {before} → {len(gdf)} feature(s) "
        f"after clip, area {gdf.geometry.area.sum():.1f} m²"
    )
    return True
