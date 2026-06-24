---
name: buffer-zones-workflow
description: This skill should be used when the user asks to "create buffer zones", "Pufferzonen", "Privilegierungsanalyse", "BAB Pufferzone", "200m Streifen Autobahn", "Schutzabstand Bahn", "buffer around motorway/railway/Hochspannungsleitung/Gewässer", "§35 Abs 1 Nr 8 BauGB Privilegierung", or any task involving distance-band analyses around linear infrastructure (Autobahn, Bundesstraße, Bahnstrecke, Hochspannung, Gewässer) — typically for §35 BauGB privileging or Schutzabstand verification.
license: MIT
---

## Infrastructure Buffer-Zone Analysis

A recurring planning task: split a project area into distance bands relative to nearby infrastructure (BAB, Bundesstraße, Bahnstrecke, etc.) and report areas per band.  Common driver: §35 Abs. 1 Nr. 8 BauGB privileging (200 m strip, often sub-banded 0–110 m / 110–200 m).

This skill captures the workflow + decision tree so we don't re-derive it each time.

### Required clarifications (ask in ONE round, before implementing)

1. **Infrastructure type + identifier** → recipe + classification filter
   - BAB → `recipe: mv_atkis` / `sh_atkis`, `widmung: "1301"`, `bezeichnung: "<A24/A20/...>"`
   - Bundesstraße → same, `widmung: "1303"`, `bezeichnung: "<B5/...>"`
   - Landesstraße → `widmung: "1305"`
   - Bahn → `recipe: mv_atkis`, layer `bahnverkehrsflaeche` or `bahnstrecke`, `bahnkategorie` filter

2. **Zone definitions** → `zones:` list
   - Default for §35 BauGB: `[{name: "0-110m", outer_m: 110}, {name: "110-200m", inner_m: 110, outer_m: 200}]`
   - User may want different bands

3. **Project layers for breakdown** → which target(s)?
   - Minimum: `Projektfläche.gpkg` for the §35-required total
   - Often also: `Modulflächen.shp`, `Wege.shp`, sometimes `Baufeld Umgrenzung.shp`, `Ausgleichsfläche.shp`
   - Multi-layer breakdown → use a project-side script (not the template's single-target intersection)

4. **Source mode** — polygon vs. line+extend
   - Polygon mode: when ATKIS `AX_Strassenverkehr` (or equivalent) covers the full visible road on DOP — rarely the case in MV
   - Line+extend mode (default for BAB/Bahn in MV): use the `AX_Strassenachse` / `AX_Bahnstrecke` axis with a known/measured half-width to the legal reference edge
   - **Always ask the user to verify on DOP** before trusting polygon mode

5. **Extension value** (only for line+extend mode) → `source_extend_m` OR `lanes_per_direction`
   - `source_extend_m` (priority 1): user's DOP measurement axis-line → Fahrbahnkante
   - `lanes_per_direction` (priority 2): RAA-2008 derivation, only valid for `road_type: bab`
   - Both given → explicit `source_extend_m` wins
   - **Always offer to compute from regulatory standard** if user has no measurement

### Bundesland → recipe mapping

| Bundesland | Recipe | Status |
|---|---|---|
| Mecklenburg-Vorpommern | `mv_atkis` | Implemented |
| Schleswig-Holstein | `sh_atkis` | Not yet implemented; create analogous recipe pointing at `https://service.gdi-sh.de/...` (verify endpoint via GeoPortal-SH) |
| Other | Create new `<bundesland>_atkis` recipe | Each Bundesland has its own ATKIS WFS endpoint |

### Implementation pattern (workflow.yaml)

```yaml
- name: <Infrastruktur> Mittellinie aus ATKIS
  recipe: mv_atkis
  layers: [strassenachse]    # uses guide_only cascade
  filter:
    widmung: "1301"
    bezeichnung: A24
  input_boundary: Geodaten/Projektfläche.gpkg
  crs: "EPSG:25833"
  output_dir: Geodaten/ATKIS
  outputs:
    - Geodaten/ATKIS/strassenachse.gpkg

- name: <Infrastruktur>-Pufferzonen
  template: buffer_zones
  params:
    source: Geodaten/ATKIS/strassenachse.gpkg
    crs: "EPSG:25833"
    source_extend_m: 9.5      # measured OR RAA-2008 RQ derivation (see below)
    # Alternative if no measurement:
    # lanes_per_direction: 2
    # road_type: bab
    zones:
      - {name: "0-110m", outer_m: 110}
      - {name: "110-200m", inner_m: 110, outer_m: 200}
    target: Geodaten/Projektfläche.gpkg
    report_csv: area_by_<infra>_zone.csv
  output: Geodaten/<infra>_pufferzonen.gpkg
```

For multi-layer breakdown (Modulflächen × Zone, Wege × Zone, etc.), add a project-side script `scripts/calculate_areas_by_zone.py` reading the zones GPKG and intersecting with each layer.

### Regulatory defaults (BAB, RAA-2008 RQ)

| Querschnitt | Lanes/Dir | Mittellinie → Fahrbahnkante |
|---|---|---|
| RQ 31 | 2 | **9.5 m** |
| RQ 36 | 3 | **13.25 m** |
| DDR-Standard 2-spurig | 2 | ~8.5 m (older A24, A20) |

Formula (BAB only): `2.0 m + N × 3.75 m` where N = lanes per direction.  Half-Mittelstreifen + N Fahrstreifen × 3.75 m.

### Legal reference

- **BauGB §35 Abs. 1 Nr. 8 lit. b bb)**: distance "gemessen vom äußeren Rand der **Fahrbahn**"
- **StVO §2 Abs. 1**: "Standstreifen ist nicht Bestandteil der Fahrbahn"
- → "Äußerer Rand der Fahrbahn" = lane edge (Fahrbahnbegrenzungslinie auf DOP) — **Standstreifen NOT included**
- Therefore: ATKIS Achslinie + halbe Fahrstreifenbreite is the BauGB-correct reference
- Note: EEG/Clearingstelle interpretation differs (uses "befestigte Fahrbahn" → includes Standstreifen) — does NOT apply to BauGB

### User-side verification (always prompt for this)

After implementation, ask user to load in QGIS on DOP background:
- `Geodaten/ATKIS/strassenachse.gpkg` — should sit in the middle of the visible BAB
- `Geodaten/<infra>_pufferzonen.gpkg` Layer `zones` — inner edge should sit on the **white Fahrbahnbegrenzungslinie** (lane edge), NOT at the visible road outer edge

If inner edge is visibly wrong → adjust `source_extend_m`.

### Discovery via MCP

```
mcp__gis-utils__list_recipes(search="atkis")        # find available ATKIS recipes
mcp__gis-utils__list_templates()                    # confirm buffer_zones present
mcp__gis-utils__check_recipe_layers("mv_atkis")     # verify recipe layers against live WFS
```

### Common mistakes (avoid)

- **Trusting `AX_Strassenverkehr` polygons in MV** — they're often narrower than the visible Fahrbahn, missing Standstreifen and sometimes lanes
- **Forgetting `source_extend_m`** when using a line source — zones will start at the centerline instead of the Fahrbahnkante
- **Adding `+Standstreifen-width`** to source_extend_m for BauGB analyses — Standstreifen is NOT part of Fahrbahn per StVO; only add Standstreifen-width for EEG-context analyses
- **Computing `lanes_per_direction` for non-BAB without `source_extend_m`** — formula is BAB-specific
