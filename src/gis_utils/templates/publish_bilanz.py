"""publish_bilanz — Area balance per category from a GPKG (Phase-4 P4-B.1).

Sums geometry areas per category from a vector layer and publishes a
``flaechenbilanz.yaml`` plus a Stand-Manifest (``…​.manifest.yaml``) next to it.
The balance structure follows the standortkonzept-FFPV seed workflow
(``pbs-bausteine doctypes/standortkonzept-ffpv/gis/workflow-vorlage.yaml``,
step "Flaechenbilanz publizieren" / ``scripts/bilanz.py``): per-category area
(m² + ha) and feature count, a total, plus a parameter echo (the criteria
thresholds) and the production date.  Categories come from ``params`` — kept
deliberately simple; the intelligence about *which* categories exist lives in the
upstream ``kategorien`` step that populated the ``category_field``.

Example workflow.yaml::

    - name: Flaechenbilanz publizieren
      template: publish_bilanz
      params:
        input: Geodaten/kategorien.gpkg
        category_field: kategorie
        crs: "EPSG:25833"
        categories: [ausschluss, restriktion, eignung]   # optional order/whitelist
        parameter:                                         # threshold echo
          mindestgroesse_ha: 5
          abstand_wohnen_innenbereich_m: 150
      output: intern/flaechenbilanz.yaml
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from gis_utils.templates import register


@register(
    "publish_bilanz",
    description="Area balance per category from a GPKG → flaechenbilanz.yaml + Stand-Manifest",
    params=["input", "input_layer", "category_field", "categories", "crs", "parameter"],
)
def publish_bilanz_template(params: dict, project_dir: Path, output_path: Path) -> bool:
    """Compute per-category area sums and publish ``flaechenbilanz.yaml`` + manifest.

    Params:
        input: Vector source (GeoPackage/…), resolved relative to project root.
        input_layer (optional): Layer name inside a multi-layer GPKG.
        category_field: Attribute column holding the category value.
        categories (optional): Explicit category order/whitelist. Categories not
            present get a zero row; present-but-unlisted categories are dropped
            from the report. Omit to report every category found, sorted.
        crs (optional): Reproject to this CRS before area computation. Areas
            require a *projected* CRS — a geographic CRS is a hard error.
        parameter (optional): Echo of the criteria thresholds (into both the
            balance and the manifest).
    """
    import geopandas as gpd

    from gis_utils.manifest import werkzeug_id, write_manifest

    if output_path is None:
        raise ValueError("publish_bilanz: an 'output:' YAML path is required")

    input_path = (project_dir / params["input"]).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"publish_bilanz: input not found: {input_path}")
    category_field = params["category_field"]
    only = params.get("categories")
    echo = params.get("parameter", {}) or {}
    crs = params.get("crs")

    input_layer = params.get("input_layer")
    gdf = gpd.read_file(input_path, layer=input_layer) if input_layer else gpd.read_file(input_path)

    if crs:
        gdf = gdf.to_crs(crs)
    if gdf.crs is None:
        raise ValueError(
            "publish_bilanz: input has no CRS; pass 'crs' so areas are meaningful"
        )
    if gdf.crs.is_geographic:
        raise ValueError(
            f"publish_bilanz: CRS {gdf.crs.to_string()} is geographic; areas need a "
            "projected CRS (pass 'crs', e.g. EPSG:25833)"
        )
    if category_field not in gdf.columns:
        raise ValueError(
            f"publish_bilanz: category_field '{category_field}' not in columns "
            f"{list(gdf.columns)}"
        )

    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    areas = gdf.geometry.area

    kategorien: dict[str, dict] = {}
    cats = list(only) if only else sorted(gdf[category_field].dropna().unique().tolist())
    for cat in cats:
        mask = gdf[category_field] == cat
        area = float(areas[mask].sum())
        kategorien[str(cat)] = {
            "flaeche_m2": round(area, 2),
            "flaeche_ha": round(area / 10_000.0, 4),
            "anzahl": int(mask.sum()),
        }

    reported_m2 = round(sum(k["flaeche_m2"] for k in kategorien.values()), 2)

    bilanz = {
        "schema_version": 1,
        "stand": date.today().isoformat(),
        "kategorie_feld": category_field,
        "parameter": dict(echo),
        "kategorien": kategorien,
        "summe": {
            "flaeche_m2": reported_m2,
            "flaeche_ha": round(reported_m2 / 10_000.0, 4),
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(bilanz, f, allow_unicode=True, sort_keys=False)

    write_manifest(
        output_path,
        parameter=dict(echo),
        quellen=[input_path],
        werkzeug=werkzeug_id("publish_bilanz"),
        basis=project_dir,
    )

    print(
        f"[publish_bilanz] {output_path}: {len(kategorien)} categories, "
        f"{reported_m2:,.0f} m² ({reported_m2 / 10_000.0:,.2f} ha)"
    )
    return True
