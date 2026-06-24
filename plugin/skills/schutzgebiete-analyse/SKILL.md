---
name: schutzgebiete-analyse
description: This skill should be used when the user asks to "Schutzgebiete prüfen", "Schutzgebiete-Analyse", "Schutzgebiete-Distanz", "Naturschutzcheck", "Natura 2000 prüfen", "FFH-Vorprüfung Daten", "NSG / SPA / LSG / Biotopverbund / Wasserschutzgebiet check", "Schutzabstand Schutzgebiete", or any task involving distance / overlap analysis between a project area and protected areas (Naturschutz, Wasserschutz, Denkmalschutz). Typical for PV/Wind/Bauleitplanung Begründung.
license: MIT
---

## Schutzgebiete-Distanz- und Überlappungs-Analyse

Wiederkehrender Task: für ein Projekt die relevanten Schutzgebiete in der Umgebung identifizieren, **Distanzen und Überlappungen** ausgeben — als Tabelle für die Begründung und als Karten-Layer für den Lageplan.

### Required clarifications (in EINER Runde)

1. **Bundesland → Recipe-Auswahl**
   - Mecklenburg-Vorpommern → `mv_alkis` (Layer: `naturschutzrecht`, `schutzgebiet_natur`, `denkmalschutz`)
   - Schleswig-Holstein → `sh_lrp_karte1` (22 Schutzgebiet-Layer: Natura 2000, FFH, SPA, NSG, Biosphärenreservat, Biotopverbund, WSG-Zonen, Wiesenvogel, Seeadler, …) plus `sh_uwat` für WRRL-Layer
   - Andere Bundesländer → Recipe ggf. erst anlegen (analog zu `sh_lrp_karte1`)

2. **Welche Gebietstypen relevant?** (häufige Auswahl je nach Verfahren)
   - **PV-Privilegierungs-Begründung**: NSG, Natura 2000 (FFH+SPA), Biotopverbund, WSG, ggf. Denkmalschutz
   - **FFH-Vorprüfung**: nur Natura 2000 (FFH-Gebiete), Distanz < ~500 m relevant
   - **B-Plan-Vollverfahren**: alle Naturschutzkategorien + Wasserschutz + Denkmalschutz
   - Wind: zusätzlich Vogelschutz-spezifisch (Seeadler, Wiesenvogel, …)

3. **Output-Form**
   - Tabelle für Begründungstext: Gebietstyp | Name | Distanz (m) | Innerhalb Projekt? — als CSV/Markdown
   - Karten-Layer für Lageplan: Distanz-Pfeile (`distance_lines_to_nearest`-Template) für die nächstgelegenen Gebiete
   - Optional: Schnittpolygone bei direkter Überlappung

4. **Suchradius**: häufig 500 m–2 km Buffer um Projektfläche (klein für FFH-Vorprüfung, weit für Bauleitplanung)

### Implementation pattern (workflow.yaml)

```yaml
- name: Schutzgebiete aus LRP holen
  recipe: sh_lrp_karte1                    # bzw. mv_alkis je Bundesland
  layers:
    - ffh_gebiete
    - spa_gebiete
    - nsg_bestand
    - biotopverbund
    - wsg_aussengrenze
  input_boundary: Geodaten/Projektfläche.gpkg
  buffer_m: 2000                            # Suchradius
  crs: "EPSG:25832"
  output_dir: Geodaten/Schutzgebiete

- name: Distanzlinien zu Schutzgebieten
  template: distance_lines_to_nearest
  params:
    target: Geodaten/Projektfläche.gpkg
    references:
      - {file: Geodaten/Schutzgebiete/ffh_gebiete.gpkg, name_col: gebietsnam, type: "FFH-Gebiet"}
      - {file: Geodaten/Schutzgebiete/spa_gebiete.gpkg, name_col: gebietsnam, type: "SPA-Gebiet"}
      - {file: Geodaten/Schutzgebiete/nsg_bestand.gpkg, name_col: name, type: "NSG"}
      - {file: Geodaten/Schutzgebiete/biotopverbund.gpkg, name_col: name, type: "Biotopverbund"}
    crs: "EPSG:25832"
  output: Geodaten/schutzgebiete_distanzen.gpkg
```

Plus optional ein project-side Skript `scripts/schutzgebiete_report.py` für die Tabelle (Distanz, Überlappung, Flächenangaben).

### Distinguishing direct overlap vs. distance

`distance_lines_to_nearest` liefert Distanz = 0 wenn Projekt im Schutzgebiet liegt — das ist ein wichtiges Sondersignal für die Begründung. **Immer prüfen** ob Distanz=0 vorkommt → direkte Überlappung → andere Argumentationslinie nötig (Verträglichkeit / FFH-Verträglichkeitsprüfung erforderlich statt nur Vorprüfung).

### Common gebiet-typen attribute names

(je nach Recipe / WFS-Quelle unterschiedlich — vor Schreiben des `name_col` einmal `gpd.read_file(...)` und Spalten ansehen)
- SH LRP: `gebietsnam`, `name`, `kennung`
- MV ALKIS: `bezeichnung`, `name`
- Bei Unsicherheit: `find_column(gdf, candidates=["name", "bezeichnung", ...])` aus gis_utils

### Discovery via MCP

```
mcp__gis-utils__list_recipes(search="schutzgebiet")
mcp__gis-utils__list_recipes(search="natura")
mcp__gis-utils__list_recipes(search="wsg")
mcp__gis-utils__check_recipe_layers("sh_lrp_karte1")    # alle 22 Layer + Spec-Vergleich
```

### User-side verification

- In QGIS Schutzgebiete-Layer + Distanzlinien auf Projekt + DOP überlagern
- Plausibilität: Distanzen ≈ visuell ablesbar?
- Bei Distanz=0: prüfen ob Geometrie korrekt klassifiziert (manchmal kleine Überlappungen durch Daten-Generalisierung)

### Common mistakes (avoid)

- **Vergessen Distanz=0 als Sonderfall zu behandeln** — direkte Überlappung erfordert andere Argumentation
- **Falscher Suchradius** — 500m für FFH-Vorprüfung ist tight; 2km für Bauleitplanung üblicher
- **Bundesland-Verwechslung beim Recipe** — `sh_lrp_karte1` nur für SH; MV hat andere Endpunkte
- **Fehlender Name-Spalten-Mapping** — distance_lines_to_nearest ohne `name_col` produziert "Schutzgebiet #0", #1, ... statt Gebiets-Namen — unbrauchbar für Tabellen
