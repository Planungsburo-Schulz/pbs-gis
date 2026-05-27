"""fetch_flurstuecke — Fetch ALKIS Flurstücke by Gemarkung / Flur / Nummer / OID.

Thin workflow wrapper around ``gis_utils.find_flurstuecke()``.

Use when: you need specific parcels (by nummer/oid) or whole-Gemarkung
scans as a workflow step, e.g. as the ``input`` to ``polygon_difference``
or ``clip_to_flurstuecke``.

Example workflow.yaml::

    - name: Fetch Flurstücke 9/27 + 9/79
      template: fetch_flurstuecke
      params:
        state: mv
        input_boundary: Grundlagen/Linie.shp
        buffer_m: 100
        nummern: ["9/27", "9/79"]
        crs: "EPSG:25833"
      output: Shapes/Flurstuecke.gpkg

Provide ONE of ``input_boundary`` / ``extent`` / ``gemarkung_schluessel``
/ ``gemeinde_schluessel`` to scope the WFS query — see
``find_flurstuecke`` for details.

NEVER add the full ALKIS Flurstück WFS layer to QGIS and filter by
attribute — at ~2 million features the QGIS-side attribute filter
effectively times out. This template (which calls find_flurstuecke
under the hood) is the supported path.
"""

from __future__ import annotations

from pathlib import Path

from gis_utils.templates import register

_PASSTHROUGH_KEYS = (
    "gemarkung",
    "flur",
    "nummern",
    "oids",
    "gemeinde",
    "gemarkung_schluessel",
    "gemeinde_schluessel",
    "extent",
    "buffer_m",
)


@register(
    "fetch_flurstuecke",
    description="Fetch ALKIS Flurstücke by Gemarkung/Flur/Nummer/OID via WFS (wraps find_flurstuecke)",
    params=[
        "state",
        "crs",
        "input_boundary",
        "extent",
        "buffer_m",
        "gemarkung",
        "flur",
        "nummern",
        "oids",
        "gemeinde",
        "gemarkung_schluessel",
        "gemeinde_schluessel",
        "output_layer",
    ],
)
def fetch_flurstuecke_template(
    params: dict, project_dir: Path, output_path: Path
) -> bool:
    """Resolve params, call ``find_flurstuecke``, write GPKG."""
    from gis_utils import find_flurstuecke

    if "state" not in params:
        raise ValueError("fetch_flurstuecke requires 'state' (e.g. 'mv', 'sh')")
    if "crs" not in params:
        raise ValueError("fetch_flurstuecke requires 'crs' (e.g. 'EPSG:25833')")

    kwargs: dict = {"state": params["state"], "crs": params["crs"]}

    # input_boundary is project-relative
    if "input_boundary" in params:
        kwargs["input_boundary"] = str((project_dir / params["input_boundary"]).resolve())

    for k in _PASSTHROUGH_KEYS:
        if k in params:
            kwargs[k] = params[k]

    output_layer = params.get("output_layer", output_path.stem)

    gdf = find_flurstuecke(**kwargs)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() == ".gpkg":
        gdf.to_file(output_path, driver="GPKG", layer=output_layer)
    else:
        gdf.to_file(output_path)
    print(f"[fetch_flurstuecke] {output_path}: {len(gdf)} Flurstück(e)")
    return True
