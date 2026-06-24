---
name: dxf-lageplan-extraction
description: This skill should be used when the user asks to "DXF einlesen", "Lageplan extrahieren", "AutoCAD-Plan in Shape", "Layer aus DXF holen", "DXF nach GeoPackage", "shapefile aus DXF", "DXF konvertieren", "extract_dxf_layers", "DXF-Lageplan vom Architekten/Vermesser einlesen", or any task involving reading a DXF/CAD file and converting selected layers to GeoPackage/Shapefile. Typical first step in PV-Lageplan / B-Plan / Bestandsanalyse projects.
license: MIT
---

## DXF-Lageplan zu GeoPackage-Extraktion

Erster Schritt fast jedes PBS-Projekts: AutoCAD-Lageplan vom Vermesser/Architekt liegt als `.dxf` vor und einzelne Layer (Baufeld, Modulflächen, Wege, Zaun, Vegetation, Gewässer, …) sollen als georeferenzierte Vektor-Shapes weiterverarbeitet werden.

### Required clarifications (in EINER Runde)

1. **DXF-Layer auflisten lassen** und Mapping zu Output-Files klären:
   ```python
   import ezdxf
   doc = ezdxf.readfile("Grundlagen/...dxf")
   layers = sorted(set(e.dxf.layer for e in doc.modelspace()))
   ```
   Dann mit User: welche Layer brauchen wir, wie sollen die Output-Shapes heißen?

2. **CRS** des DXF-Plans
   - Vermesser-Pläne: meist EPSG:25832 (UTM Zone 32, west-DE) oder EPSG:25833 (UTM Zone 33, ost-DE)
   - Bei DDR-Bauten oft Gauss-Krüger 4 oder 5 — heute eher selten
   - Falls UTM-Zonen-Präfix in den Koordinaten (z.B. 33266881 statt 266881): `strip_utm_zone_prefix()` aus gis_utils oder Template-Parameter `strip_zone: true`

3. **Koordinaten-Offset** falls DXF nicht exakt georeferenziert
   - Häufig kleine Offsets (z.B. -0.12 m / +0.12 m) zwischen DXF und tatsächlichem Lagepunkt — vom Vermesser dokumentiert oder per DOP-Vergleich ermitteln
   - Wölzow-Beispiel hat `X_OFFSET = -0.12, Y_OFFSET = 0.12`

4. **Geometrie-Typ je Layer** entscheiden
   - **Polygon** (häufigster Fall): Baufeld-Umgrenzung, Modulflächen, Wege-Flächen, Zaun-als-Polygon
   - **Linie** (wenn DXF als Polyline statt geschlossen): Zaunlinie, Achslinie — oft mit `dxf_lines_to_polygon`-Template zu Polygon konvertieren (Extend + Polygonize)
   - **Punkt** (selten): Kreisbohrungen, Bäume

5. **Block-INSERTs** behandeln
   - Wenn der DXF Symbol-Blocks enthält (z.B. Bäume als Block-Insert mit innerer Geometrie): `extract_dxf_layers(..., process_blocks=True)` (Default)
   - Wenn Blocks NICHT rekursiv extrahiert werden sollen: `process_blocks=False`

### Implementation pattern (drei Wege je nach Komplexität)

**Weg 1 — Template (einfacher Fall, alle Layer)**:
```yaml
- name: DXF-Layer extrahieren
  template: dxf_extract
  params:
    dxf: Grundlagen/lageplan.dxf
    layers: ["Baufeld Umgrenzung", "Modulflächen", "Wege"]
    crs: "EPSG:25833"
    strip_zone: false
  output_dir: Geodaten/dxf_extract
```

**Weg 2 — Template `dxf_lines_to_polygon` (wenn Layer als Polylinien vorliegen die zusammengefügt werden müssen)**:
```yaml
- name: Baufeld-Polygon aus Polylinien
  template: dxf_lines_to_polygon
  params:
    dxf: Grundlagen/lageplan.dxf
    layer: "Baufeld Umgrenzung"
    crs: "EPSG:25833"
    extend: 5.0           # erweitere Linien um max 5m für Lückenschluss
    snap_tolerance: 0.5   # snap Endpunkte mit Abstand <0.5m
  output: Geodaten/Baufeld.gpkg
```

**Weg 3 — Project-Skript** (wenn projektspezifische Logik dazukommt: Offset, Differenz-Berechnungen, Ableitungen wie Wege = Baufeld − Modulflächen):
```python
# scripts/create_shapes.py
from gis_utils import extract_dxf_layers, make_valid_gdf, subtract_geometries
from shapely.affinity import translate

layers = extract_dxf_layers(DXF_PATH, CRS, layers=["Baufeld", "Module"])
# ... apply offset, derive Wege, save shapes
```

(Wölzow's `scripts/create_shapes.py` ist ein gutes Reference-Pattern für Weg 3.)

### DXF in lokalem CAD-System georeferenzieren

Über kleine Offsets hinaus: manche Vermesser-/Architekten-DXF liegen in einem **lokalen oder ursprungs-reduzierten CAD-System**, in **keinem** realen CRS (Indizien: Block-Name wie `_Kataster-nicht-georef`; kein Kandidaten-EPSG bringt die Geometrie auf den realen Standort).

1. **Lokal bestätigen**: DXF-Extent durch die plausiblen CRS transformieren — landet keiner am Standort, ist es lokal (kein bloßer Offset).
2. **Über das eingebettete Kataster einpassen**: solche DXF tragen meist ALKIS-Layer (Flurstücke, Gebäude). Mit `gis_utils.register_features()` an das **amtliche ALKIS** des Standorts matchen (Ähnlichkeitstransformation; meist reine **Translation** — Maßstab 1, Rotation 0). Offset speichern, alle Planlayer damit transformieren.
3. **Über physische Plausibilität auf dem DOP prüfen, nicht nur über Restklaffung** — ein sub-mm-Fit auf eine *fehlplatzierte* Referenz ist trotzdem falsch. Sanity-Check: Liegen Bestandsobjekte richtig? Ist etwas physisch unmöglich (z.B. Baumkrone auf dem Parkplatz)? Eine widersprechende Operator-Beobachtung als Prüf-Anlass nehmen, nicht verwerfen.

### Saubere Footprints extrahieren

- Gefüllte Flächen kommen als **HATCH** durch — diese als Footprint nehmen; offene Linienzüge (Geräte-/Container-Umrisse) mit `polygonize` schließen.
- **Auf eine Standort-Bbox clippen** — Legenden-/Detail-/Plankopf-Geometrie liegt auf denselben Layern weit weg vom Standort.
- Mikro-Slivers (< ~0,25 m²) aus Boolean-Ergebnissen filtern (numerische Artefakte); echte kleine Überlagerungen behalten und benennen.

### 3D-Solids (ACIS) — kantig ja, gekrümmt nein

DXF-`3DSOLID`s sind ACIS-Bodies. `extract_3dsolids()` projiziert die Eckpunkte → korrekte Grundfläche nur bei **kantigen** Solids (Quader: Gebäude-Sohle, Wände, Boden/Decke). **Gekrümmte** Solids (Tank/Zylinder, Kegel — oft als `spline-surface` codiert) haben kaum echte Vertices → die Projektion liefert nichts oder Müll. ezdxf bringt keinen ACIS-Kernel (kann gekrümmte Flächen nicht tesselieren), und `acis.load` / `mesh.from_body` scheitern bei manchen SAB-Encodings ganz; ein brauchbarer Open-Source-ACIS-Kernel für Python existiert nicht (Stand 2026).
- Gekrümmtes Objekt gebraucht? → aus der **Maß-Beschriftung rekonstruieren** (`solid3d_to_circle(entity, diameter)` für Tanks/Zylinder) ODER den Solid im CAD zu 2D auflösen (FLATSHOT / Explode → Arcs/Polylinien) und 2D extrahieren. Footprint NIE aus der Vertex-Projektion eines gekrümmten Solids ziehen.

### Verifikation (immer durchziehen)

Nach Extraktion:
- **`verification_dxf`-Template** für visual QA: schreibt original-DXF-Linien + abgeleitetes Polygon zurück in eine DXF-Datei → in AutoCAD/QGIS überlagern, prüfen ob Vermessungsgenauigkeit erhalten
- **Areas plausibel?** — kurzes `area_report` über die extrahierten Polygone, mit User abgleichen ob die Größenordnung passt (Vermesser hat oft die Flächen schon gemeldet)
- **DOP-Überlagerung** in QGIS — Geometrie auf realem Luftbild kontrollieren

### Discovery via MCP

```
mcp__gis-utils__list_templates                  # zeigt dxf_extract, dxf_lines_to_polygon, verification_dxf
mcp__gis-utils__get_function_help("extract_dxf_layers")   # alle Optionen
mcp__gis-utils__get_function_help("shapefile_to_dxf")     # für Round-Trip
```

### Common mistakes (avoid)

- **CRS raten statt fragen** — DXF haben kein eingebettetes CRS; falscher CRS = stille Geometrie-Verschiebung. **Immer beim Vermesser oder im Plan-Stempel nachsehen.**
- **`make_valid_gdf` vergessen** — DXF-Polygone haben oft Self-Intersections / Mikro-Spikes; ohne `make_valid_gdf()` failen spätere shapely-Operationen
- **Layer-Namen falsch geschrieben** — Sonderzeichen (ä/ö/ü), Leerzeichen, Umlaute — kopieren statt abtippen
- **Block-INSERTs übersehen** — wenn Modulflächen als Block-Insert pro Modul mit inner Geometry sind, ohne `process_blocks=True` kommen 0 Polygone raus
- **Strip-Zone falsch** — wenn X-Werte als 33266881 (Zone 33 + Easting) ankommen, ohne `strip_zone=true` ist alles 33 Mio Meter zu weit
