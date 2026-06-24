"""buffer_zones — Concentric ring buffer zones around a source geometry.

Produces ring polygons at named distance bands around a source (point, line,
or polygon) layer.  Optionally intersects the rings with a target layer and
writes a per-zone area report.

Typical use case: planning analyses where a project area's relationship to
infrastructure (Autobahn, Bahnstrecke, Hochspannungsleitung, Gewässer) is
defined by distance bands — e.g. § 35 Abs. 1 Nr. 8 BauGB privileging zone of
200 m around BAB, with sub-band 0–110 m.

Two source modes
----------------

**Mode A — trust polygon source.**  Use a polygon directly (e.g. ATKIS
``AX_Strassenverkehr`` or a manually traced BAB outline)::

    - name: BAB Pufferzonen Wölzow
      template: buffer_zones
      params:
        source: Geodaten/A24_Verkehrsflaeche.gpkg
        crs: "EPSG:25833"
        zones:
          - {name: "0-110m", outer_m: 110}
          - {name: "110-200m", inner_m: 110, outer_m: 200}
        target: Geodaten/Projektfläche.gpkg        # optional
        report_csv: area_by_bab_zone.csv       # optional
      output: Geodaten/bab_pufferzonen.gpkg

**Mode B — line source + measured / regulatory extension.**  Use the road
axis (e.g. ATKIS ``AX_Strassenachse``) plus the half-width to the legal
reference edge (e.g. Fahrbahnkante per StVO).  The source is buffered
outward by ``source_extend_m`` *before* the zones are computed.  This is
the right path when the polygon data underestimates the real surface
(common in MV ATKIS where ``AX_Strassenverkehr`` is narrower than the
visible road)::

    params:
      source: Geodaten/A24_Mittellinie.gpkg
      crs: "EPSG:25833"
      source_extend_m: 9.5     # measured DOP distance Mittellinie → Fahrbahnkante
      # OR (alternative if the value isn't measured):
      # lanes_per_direction: 2  # RAA-2008 RQ 31 derives 9.5m for BAB
      # road_type: bab          # default; only "bab" supported for derivation
      zones: [...]

If both ``source_extend_m`` and ``lanes_per_direction`` are given, the
explicit ``source_extend_m`` wins.  Lane-count derivation uses the
RAA-2008 BAB formula: ``2.0 m + N × 3.75 m`` (half-Mittelstreifen + N
Fahrstreifen à 3.75 m).

Output GPKG contains
--------------------

- Layer ``zones`` — the ring polygons (one row per zone).
- Layer ``zones_target_intersection`` — only present when ``target`` is given.
  One row per zone × target intersection.

Each layer carries ``name``, ``inner_m``, ``outer_m``, ``area_m2`` columns.
"""

from __future__ import annotations

from pathlib import Path

from gis_utils.templates import register


# RAA-2008 BAB regulatory geometry (Regelquerschnitt RQ 31 et seq.)
# Half-Mittelstreifen (2.0 m) + N Fahrstreifen × 3.75 m gives the distance
# from the BAB axis to the Fahrbahnkante.  RQ 31 (N=2) → 9.5 m;
# RQ 36 (N=3) → 13.25 m.
_BAB_HALF_MITTELSTREIFEN_M = 2.0
_BAB_FAHRSTREIFEN_M = 3.75


def _resolve_source_extend(params: dict) -> float:
    """Decide ``source_extend_m`` from explicit value or lane-count derivation.

    Priority:
      1. Explicit ``source_extend_m`` (caller's measurement / known half-width).
      2. ``lanes_per_direction`` + ``road_type`` (default ``bab``) →
         RAA-2008 formula for BAB; raises for other road types.
      3. Default ``0.0`` (no extension).
    """
    if "source_extend_m" in params and params["source_extend_m"] is not None:
        return float(params["source_extend_m"])
    lanes = params.get("lanes_per_direction")
    if lanes is None:
        return 0.0
    road_type = str(params.get("road_type", "bab")).lower()
    if road_type == "bab":
        return _BAB_HALF_MITTELSTREIFEN_M + int(lanes) * _BAB_FAHRSTREIFEN_M
    raise ValueError(
        f"buffer_zones: lanes_per_direction derivation only supported for "
        f"road_type='bab' (got '{road_type}'). Provide source_extend_m "
        f"explicitly for other road types."
    )


@register(
    "buffer_zones",
    description=(
        "Concentric ring buffer zones around a source layer (point/line/polygon); "
        "optional target intersection + area report; supports line+extension "
        "mode for road-axis sources"
    ),
    params=[
        "source", "crs", "zones",
        "source_extend_m", "lanes_per_direction", "road_type",
        "target", "report_csv",
    ],
)
def buffer_zones_template(
    params: dict, project_dir: Path, output_path: Path
) -> bool:
    """Generate concentric buffer ring zones around a source layer.

    Params:
        source: Path to source layer (Shapefile / GeoPackage / GeoJSON).
            Geometries are unioned into one geometry before buffering.
        crs: Projected CRS in metres (e.g. ``"EPSG:25833"``).
        zones: List of zone definitions, each a dict with:
            - ``name`` (str): zone label.
            - ``outer_m`` (float): outer distance in CRS units.
            - ``inner_m`` (float, optional, default 0): inner distance.
        target (optional): Path to target layer.  If given, the rings are
            intersected with the target and a second GPKG layer
            ``zones_target_intersection`` is written.
        report_csv (optional): Path to CSV file for the area report.
            Written when the rings produce non-empty geometries.
            Columns: ``Zone | Ring (m²) | Ring (ha)``, plus
            ``Target ∩ (m²) | Target ∩ (ha)`` when ``target`` is given.
    """
    import geopandas as gpd
    import pandas as pd
    from shapely.ops import unary_union

    from gis_utils import buffer_ring_zones, markdown_table

    source_path = project_dir / params["source"]
    crs = params["crs"]
    zones_cfg = params["zones"]
    target_param = params.get("target")
    report_csv = params.get("report_csv")

    if not source_path.exists():
        print(f"  [ERROR] source not found: {source_path}")
        return False

    src_gdf = gpd.read_file(source_path)
    if src_gdf.empty:
        print(f"  [ERROR] source has no features: {source_path}")
        return False
    if src_gdf.crs is None:
        src_gdf = src_gdf.set_crs(crs)
    elif str(src_gdf.crs) != crs:
        src_gdf = src_gdf.to_crs(crs)

    source_geom = unary_union(src_gdf.geometry.values)
    if source_geom is None or source_geom.is_empty:
        print(f"  [ERROR] source geometry is empty after union")
        return False

    # Mode B: extend source by measured / regulatory half-width
    extend_m = _resolve_source_extend(params)
    if extend_m > 0:
        source_geom = source_geom.buffer(extend_m)
        print(f"  Source extended by {extend_m:.2f} m before zone computation")

    rings = buffer_ring_zones(source_geom, zones_cfg)
    if not rings:
        print(f"  [ERROR] no non-empty ring zones produced")
        return False

    zone_records = [
        {
            "name": meta["name"],
            "inner_m": meta["inner_m"],
            "outer_m": meta["outer_m"],
            "area_m2": meta["area_m2"],
            "geometry": geom,
        }
        for meta, geom in rings
    ]
    zones_gdf = gpd.GeoDataFrame(zone_records, crs=crs)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Always write GPKG (multi-layer support); single-layer fallback for .shp.
    out_ext = output_path.suffix.lower()
    if out_ext in (".gpkg", ".geopackage"):
        zones_gdf.to_file(output_path, driver="GPKG", layer="zones")
    else:
        zones_gdf.to_file(output_path)
    print(f"  Zones: {len(zones_gdf)} ring polygons → {output_path}")

    # --- Intersection with target ---
    intersection_gdf = None
    if target_param:
        target_path = project_dir / target_param
        if not target_path.exists():
            print(f"  [WARN] target not found, skipping intersection: {target_path}")
        else:
            tgt_gdf = gpd.read_file(target_path)
            if tgt_gdf.crs is None:
                tgt_gdf = tgt_gdf.set_crs(crs)
            elif str(tgt_gdf.crs) != crs:
                tgt_gdf = tgt_gdf.to_crs(crs)
            tgt_union = unary_union(tgt_gdf.geometry.values)

            int_records = []
            for meta, ring in rings:
                inter = ring.intersection(tgt_union)
                if inter.is_empty:
                    continue
                int_records.append({
                    "name": meta["name"],
                    "inner_m": meta["inner_m"],
                    "outer_m": meta["outer_m"],
                    "area_m2": float(inter.area),
                    "geometry": inter,
                })
            intersection_gdf = gpd.GeoDataFrame(int_records, crs=crs)

            if not intersection_gdf.empty and out_ext in (".gpkg", ".geopackage"):
                intersection_gdf.to_file(
                    output_path, driver="GPKG",
                    layer="zones_target_intersection",
                )
                print(
                    f"  Intersection: {len(intersection_gdf)} polygons "
                    f"(layer 'zones_target_intersection')"
                )

    # --- Report ---
    csv_rows = []
    md_rows = []
    if intersection_gdf is None or intersection_gdf.empty:
        for meta, _ in rings:
            m2 = round(meta["area_m2"])
            ha = round(meta["area_m2"] / 10_000, 4)
            csv_rows.append([meta["name"], m2, ha])
            md_rows.append([meta["name"], f"{m2:,}", f"{ha:.4f}"])
        headers = ["Zone", "Ring (m²)", "Ring (ha)"]
    else:
        int_by_zone = {
            r["name"]: r["area_m2"]
            for _, r in intersection_gdf.iterrows()
        }
        for meta, _ in rings:
            tarea = int_by_zone.get(meta["name"], 0.0)
            ring_m2 = round(meta["area_m2"])
            ring_ha = round(meta["area_m2"] / 10_000, 4)
            tgt_m2 = round(tarea)
            tgt_ha = round(tarea / 10_000, 4)
            csv_rows.append([meta["name"], ring_m2, ring_ha, tgt_m2, tgt_ha])
            md_rows.append([
                meta["name"],
                f"{ring_m2:,}", f"{ring_ha:.4f}",
                f"{tgt_m2:,}", f"{tgt_ha:.4f}",
            ])
        headers = [
            "Zone", "Ring (m²)", "Ring (ha)",
            "Target ∩ (m²)", "Target ∩ (ha)",
        ]

    print()
    print(markdown_table(headers, md_rows))

    if report_csv:
        csv_path = project_dir / report_csv
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(csv_rows, columns=headers).to_csv(csv_path, index=False)
        print(f"  Report: {csv_path}")
        # Markdown sibling report — same name, .md extension
        md_path = csv_path.with_suffix(".md")
        md_body = (
            f"# Flächenbilanz Pufferzonen ({source_path.stem})\n\n"
            f"{markdown_table(headers, md_rows)}\n"
        )
        md_path.write_text(md_body, encoding="utf-8")
        print(f"  Report (md): {md_path}")

    return True
