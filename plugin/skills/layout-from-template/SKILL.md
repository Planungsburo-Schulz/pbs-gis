---
name: layout-from-template
description: This skill should be used when the user asks to "create a QGIS layout", "Layout aus Template", "Lageplan erstellen", "Maßnahmenplanung", "Übersichtsplan", "Bestandsplan", "Print Layout aus QPT", "fill PBS template", or any task that instantiates a `.qpt` template into a print layout for a PBS project (typically the central `Allgemein/PBS-Templates/layouts/*.qpt`). Walks through AOI/buffer/scale clarification, map theme setup, placeholder substitution, and the scale-bar / legend gotchas codified from past failures.
license: MIT
---

## Layout aus PBS-QPT-Template erzeugen

Instantiate a `.qpt` template (typically `Allgemein/PBS-Templates/layouts/uebersicht-A4-landscape.qpt`) into a project's print layout, with `{{...}}`-placeholder substitution, theme-driven map visibility, and proper scale-bar / legend wiring.

### Required clarifications (in EINER Runde)

Ask the user up front — never auto-decide these. They are project-specific judgement calls:

1. **AOI feature** — which layer's extent should the map be based on? List candidate vector layers (project AOIs typically named `Vorhabensfläche`, `Untersuchungsgebiet`, `Plangebiet`, `Baufeld`, `Modulflächen`). If multiple candidates: propose, let user pick. If the AOI consists of overlapping features that should be merged: confirm dissolve-and-overwrite first (see §"Dissolve before extent" below).

2. **Buffer** — how much pad around the AOI? Suggest a default proportional to AOI size (e.g. 10–20 % of the longest side, or a round number like 50 m / 100 m / 250 m). NEVER skip the buffer step; always ask.

3. **Scale** — pick a **standard cartographic scale** so the buffered AOI fits the map item. Offer a short list of standards: 1:200, 1:500, 1:1000, 1:2500, 1:5000, 1:10000, 1:25000. User picks. **Never compute a non-round scale from "extent + N % buffer" auto-fit** — odd scales (e.g. 1:682) break scale-bar geometry and look unprofessional.

4. **Theme name + layout name** — propose `<Project>_<Map-Type>` style; user can override. Theme and layout often share a name.

5. **Layers to include** — which project layers go into the map theme (visible)? Which stay hidden? The user decides; the skill enforces that the map item *follows the theme*, so canvas state can drift without affecting the layout.

6. **Auftraggeber-block content** — full address as the user wants it printed. Auftragnehmer-block is composed automatically from `Allgemein/PBS-Templates/auftragnehmer/<name>.yaml`.

### Implementation pattern

#### 1. Dissolve before extent (if needed)

If the AOI layer has multiple overlapping features: dissolve to a single non-overlapping (multi)polygon **before** taking its extent — overlapping polygons produce a bigger-than-real bounding box that throws off the scale calculation. Use `processing.run('native:dissolve', {'INPUT': src, 'FIELD': [], 'OUTPUT': tmp_shp})`, then move the temp `.shp/.shx/.dbf/.prj/.cpg` over the original (overwrite confirmed by the user).

#### 2. Map theme

Build the theme with the helper — idempotent, layers matched by name, every other layer hidden. Never hand-roll `createThemeFromCurrentState(root, None)`: a `None` model segfaults QGIS.

```python
from gis_utils import qgis_bridge
qgis_bridge.define_map_theme(theme_name, ["Vorhabensfläche", "Grundstück", "DOP20"])
```

#### 3. Placeholder substitution + load layout

```python
import xml.sax.saxutils as su
from qgis.core import QgsPrintLayout, QgsReadWriteContext
from qgis.PyQt.QtXml import QDomDocument

def attr_esc(s: str) -> str:
    return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
             .replace('"', '&quot;').replace('\n', '&#xa;'))

content = open(template_path, 'r', encoding='utf-8').read()
for k, v in substitutions.items():
    content = content.replace('{{' + k + '}}', attr_esc(v))

layout = QgsPrintLayout(p)
doc = QDomDocument()
doc.setContent(content, True)
layout.loadFromTemplate(doc, QgsReadWriteContext())
layout.setName(layout_name)
p.layoutManager().addLayout(layout)
```

Standard placeholders in the PBS template (see `auftragnehmer/<name>.yaml` for composition):

| Placeholder | Source |
|---|---|
| `{{title}}` | main h3 title (line 1) |
| `{{project_name}}` | h4 subtitle (line 2) |
| `{{auftragnehmer_block}}` | composed from `<name>.yaml` (`Auftragnehmer:\n` + name + firm_lines + address) |
| `{{bearbeiter_line}}` | `Bearbeiter: <Name>` |
| `{{bearbeitungsstand}}` | usually keep the QGIS expression `[% concat(day(now()),'.',month(now()),'.',year(now()))%]` |
| `{{auftraggeber_block}}` | user-supplied `Auftraggeber:` + address |

#### 4. Map item: scale, extent, theme-follow

```python
m = layout.itemById('main_map')
m.setFollowVisibilityPreset(True)
m.setFollowVisibilityPresetName(theme_name)

# Set scale FIRST, then extent (zoomToExtent overwrites scale)
m.setScale(chosen_scale)        # 1000 for 1:1000, 2500 for 1:2500, …

# Center extent on AOI at the chosen scale
aoi = aoi_layer.extent()
cx, cy = aoi.center().x(), aoi.center().y()
# map item size in mm → ground meters at chosen scale
w_m = m.sizeWithUnits().width()  / 1000.0 * chosen_scale
h_m = m.sizeWithUnits().height() / 1000.0 * chosen_scale
from qgis.core import QgsRectangle
m.setExtent(QgsRectangle(cx - w_m/2, cy - h_m/2, cx + w_m/2, cy + h_m/2))
```

#### 5. Scale bar — set EXPLICITLY (don't trust template defaults)

The template's serialized scale-bar XML doesn't carry a `method=` attribute, so `loadFromTemplate` picks an unstable default that mis-renders at non-standard scales. Always set explicitly:

```python
from qgis.core import Qgis, QgsUnitTypes, QgsScaleBarSettings

sb = layout.itemById('scalebar')
sb.setLinkedMap(m)
sb.setStyle('Single Box')
sb.setMethod(Qgis.ScaleCalculationMethod.HorizontalBottom)  # the critical one
sb.setUnits(QgsUnitTypes.DistanceMeters)
sb.setUnitLabel('m')
sb.setMapUnitsPerScaleBarUnit(1.0)
sb.setSegmentSizeMode(QgsScaleBarSettings.SegmentSizeFixed)
sb.setUnitsPerSegment(units_per_segment)   # 10 at 1:1000, 25 at 1:2500, …
sb.setNumberOfSegments(4)
sb.setNumberOfSegmentsLeft(0)
sb.update()
```

Pick `units_per_segment` from the chosen scale so the bar lands in the 50–80 mm range. Rule of thumb: at 1:N, 4 segments × U metres = `4·U·1000/N` mm. For 1:1000 → U≈10 m (40 mm bar) up to 20 m (80 mm bar). Adjust to taste.

#### 6. Legend — auto-update ON briefly, then OFF for curation

```python
lg = layout.itemById('legend')
lg.setLinkedMap(m)
lg.setLegendFilterByMapEnabled(True)

# Brief auto-update to seed legend tree from the map theme's visible layers
lg.setAutoUpdateModel(True)
lg.refresh()
lg.setAutoUpdateModel(False)   # turn OFF so the user can curate

# (User then drops the basemap entry, reorders, etc., manually in QGIS)
```

Auto-update OFF is mandatory after seeding — otherwise the user's manual edits (drop DOP, reorder, rename entries) get blown away on every refresh.

#### 7. Save + export

```python
p.write()    # save .qgz so layout persists
layout.renderContext().setDpi(150)   # default DPI for this layout

# Export to project-local Export/ directory (PDF + PNG)
import os
proj_dir = os.path.dirname(p.fileName())
export_dir = os.path.join(proj_dir, 'Export')
os.makedirs(export_dir, exist_ok=True)
base = os.path.join(export_dir, layout_name)

exporter = QgsLayoutExporter(layout)
pdf_settings = QgsLayoutExporter.PdfExportSettings()
pdf_settings.dpi = 150
exporter.exportToPdf(base + '.pdf', pdf_settings)

png_settings = QgsLayoutExporter.ImageExportSettings()
png_settings.dpi = 150
exporter.exportToImage(base + '.png', png_settings)
```

**Default DPI = 150.** Matches `printResolution="150"` in the canonical PBS template (`uebersicht-A4-landscape.qpt`). 150 dpi is sufficient for screen review, internal PDFs, and most Behörden-Einreichungen. Bump to 300 only if a printer specifically demands it.

**Default export path: `<project>/Export/<layout_name>.{pdf,png}`** — a sibling to `Geodaten/`, `Grundlagen/`, etc. Always show the user the rendered preview before declaring done.

### Hausstil-Kontrakt (apply on every map)

The `.qpt` pins the starting geometry and placeholders but not the visual conventions below. Apply them so a new map matches the existing corpus instead of drifting to generic cartographic defaults.

**Flächenrollen — fill vs. hatch vs. outline.** Decide by one test: *is the area's interior the message, or must the ground under it stay visible?* Apply the matching role QML from `Allgemein/PBS-Templates/styles/` (via `qgis_bridge.apply_qml(layer, qml)`), then recolour the symbol per layer:

| Role QML | Use when |
|---|---|
| `grenze_umriss.qml` | Geometry is a **boundary, distance or radius** — interior is not the message (Prüfradien, Teilflächen-Abgrenzung, Vorhabensgebiet, Distanzlinien). |
| `kategorie_flaeche.qml` | The area **is** a category, ground beneath irrelevant (Modulflächen, Ausgleichsfläche, Wege, Biotoptypen). |
| `wirkzone_schraffur.qml` | Area **designation/zone laid over meaningful ground** you must still see through (Vorhabensfläche über Gebäuden, Überschwenkbereich, Meide-/Kulissenzone über Acker). |

**Colour: one family per role, shade within it.** Pick the layer's colour from the house palette in `Allgemein/PBS-Templates/styles/` (`README.md` + `palette-reference.html`) — grün = ökologisch positiv, pink/magenta = Modul-/Vorhabensfläche, sand/braun = Wege, **rot only as line/outline, never a fill**. Sub-categories take different shades of the same family, not new hues. The muting comes from the ~43 % fill alpha over the aerial, not from a pale base colour.

**Template item width is the invariant; height and position flex.** Keep each template item's width (`title`, `legend`, `info_block`, `scalebar`) — the shared info-column width is what holds the layout aligned. Height and vertical position may change to fit content: the legend shrinks to its content, a `legend_note` block reflows directly under it, the title box grows downward for a longer title. A **changed width** is the drift signal, not a taller or shifted item. Exceptions exist (a deliberately wider block), but they are a conscious choice, never incidental.

**Title stays one conceptual line.** `{{title}}` (h3) carries the map's subject in one line; species names, method qualifiers and citations go to `{{project_name}}` (h4 subtitle). Do not let a long title force a taller title box.

**Area/distance labels: black, bold, white buffer** — never a coloured label. Set via layer labelling on the label field:
```python
from qgis.core import QgsTextFormat, QgsTextBufferSettings, QgsPalLayerSettings, QgsVectorLayerSimpleLabeling
from qgis.PyQt.QtGui import QFont, QColor
fmt = QgsTextFormat(); f = QFont(); f.setBold(True); fmt.setFont(f); fmt.setSize(9); fmt.setColor(QColor(0,0,0))
buf = QgsTextBufferSettings(); buf.setEnabled(True); buf.setSize(1.0); buf.setColor(QColor(255,255,255)); fmt.setBuffer(buf)
pal = QgsPalLayerSettings(); pal.fieldName = 'flaeche_label'; pal.setFormat(fmt)
layer.setLabeling(QgsVectorLayerSimpleLabeling(pal)); layer.setLabelsEnabled(True)
```

**Legend holds only symbol+label entries, optionally under sub-headings** (e.g. `Teilflächen nach LEP`, `Planung`). Explanatory prose (reference-line notes, methodology, distance rationale) never goes inside the legend — it goes in a **separate framed label styled like the legend**, placed directly below it (a `legend_note`). When the legend grows large, it may move to the **left** margin (mirrors the info column) — both placements exist in the corpus.

**Auftraggeber always carries the full postal address** (see clarification 6) — ask for it if the user gave only a name; never print a bare organisation name.

**Reference-check before declaring done.** Open one prior project's export from a sibling `Export/` (or `Ausgabe/`, `Output/Karten/`) directory and compare against it: title-box size, legend font/box, label colour+buffer, area-role choice, info-block address. State which reference you compared and each element's verdict. A map that was never diffed against the corpus has not met the house style.

### Per-project tuning the user does (NOT in skill scope)

These are judgement calls — propose if asked, but don't auto-apply:

- Symbol **colour** per layer, and fine-tuning within a chosen role (the fill/hatch/outline *role* itself is fixed by the Hausstil-Kontrakt above; only colour and exact hatch spacing/opacity are per-layer)
- Final legend curation (which entries to drop, reorder, rename)
- Final positioning of info_block / legend / scale bar after content is in
- Title-box background colour when map is full-bleed
- Scale-bar position (often top-right corner)
- Whether the map should be full-bleed or insetted

### Hand-off suggestions (PyQGIS hard, QGIS UI easy)

Some adjustments are noticeably faster in QGIS UI than via PyQGIS — when the work below is in front of you, **suggest** the hand-off rather than burning round-trips on it. The user decides; just flag it.

| Operation | PyQGIS friction | UI alternative (~30 s) |
|---|---|---|
| **Curating a layout legend** — drop entries, reorder, rename | `QgsLayoutItemLegend.setCustomLayerTree` doesn't exist in some QGIS versions; modifying `model().rootGroup()` and toggling `autoUpdateModel` interacts subtly with `legendFilterByMapEnabled` and frame sizing — easy to end up with only one entry rendering | Layout designer: open the legend item's properties → uncheck "Auto update" → drag entries / Remove unwanted ones |
| **Multi-symbol-layer fill rendering in the legend** (hatch + solid base together) | Legend swatch may render only the first sub-layer or skip entries entirely | Designer: legend properties → adjust patch size, or set per-entry custom symbol |
| **Item overlap fixes** (legend frame extending into info_block, info_block extending past page edge) | Iterative `attemptResize` / `attemptMove` cycles, `resizeToContents=True` interacts with z-order rendering | Designer: drag item handles, watch the canvas |
| **Symbol fine-tuning** (exact line caps, hatch angles, semi-transparent fill blending) | Property dicts are version-sensitive and hatch params are fiddly | Designer: Properties panel — sliders + previews |
| **Manual tracing / digitising** | Not a PyQGIS task at all | Edit mode → digitising tools |

Rule of thumb: if a PyQGIS attempt loops 2–3 times without converging on the desired render, **stop and suggest the user finish in QGIS**. Once they fix it, re-export the layout via `gis_utils.qgis_bridge.render_layout_template` or via a quick `exportToPdf` / `exportToImage` snippet.

This is a suggestion to the user, not a hard refusal: they may prefer to keep iterating in PyQGIS for reproducibility (e.g. CI). When they do, expand the diagnosis — print `lg.model().rootGroup().children()`, the renderer's `legendSymbolItems()`, the layout-item z-order — rather than guessing.

### Common pitfalls (lessons codified)

| Symptom | Cause | Fix |
|---|---|---|
| Scale bar shows only `0` label, segments invisible | `method` unset → defaults to a mode that breaks at non-round scales | `sb.setMethod(Qgis.ScaleCalculationMethod.HorizontalBottom)` explicitly |
| `unitsPerSegment` rounds to absurd value (`0.02 m`) in `SegmentSizeFitWidth` mode | Same root cause — wrong method makes the auto-sizing math diverge | Use `SegmentSizeFixed` with explicit `setUnitsPerSegment`, AND set `setMethod(...HorizontalBottom)` |
| Legend empty after `loadFromTemplate` | `autoUpdateModel=False` (template default) leaves the tree empty | Call `setAutoUpdateModel(True); refresh(); setAutoUpdateModel(False)` to seed once |
| Legend re-fills with all theme layers, undoing the user's curation | Left `autoUpdateModel=True` | Must be OFF after the seed step |
| `itemById('Map 1')` returns None | The genericized template uses standardized item IDs (`main_map`, `title`, `info_block`, `legend`, `scalebar`, `northarrow`, `auftragnehmer_logo`) — NOT the QGIS default `Map 1` | Use `main_map` (or list `[it.id() for it in layout.items()]` if uncertain) |
| Picture item shows broken-image marker | The template's `file=` attribute path no longer exists (e.g. after `PBS-Templates` was moved) | Update `auftragnehmer/<name>.yaml.logo_path`, the `LOGO_TARGET` constant in `scripts/genericize_qpt.py`, and any embedded path in the `.qpt` itself |
| Auftraggeber-block clipped at page edge | Info-block frame too short for new content | Resize info_block, but watch the page boundary; if room is tight, shorten content rather than overflow |
| Map renders canvas state, not the theme | `followVisibilityPreset` not set on the map item | `m.setFollowVisibilityPreset(True); m.setFollowVisibilityPresetName(theme_name)` |
| Scale bar's `linkedMap` resets after `loadFromTemplate` | UUID remap usually preserves the link via `templateUuid`, but always re-set defensively | `sb.setLinkedMap(m)` after layout load |
| Title still shows `{{title}}` after filling labels | The title is an HTML **multiframe** (`QgsLayoutItemHtml`), NOT a `QgsLayoutItemLabel` — a label-only fill loop skips it (HTML labels also render async/unreliably) | Also iterate `layout.multiFrames()`: `mf.setHtml(filled); mf.loadHtml()` (synchronous). `render_layout_template` ≥ 0.11 does this |
| Auto-fit-to-extent picks an odd scale (1:682, 1:1234, …) | Computing scale from "AOI extent ± buffer" produces non-round denominators | Pick a standard scale FIRST (1:500, 1:1000, 1:2500…), then center extent on AOI at that scale |

### Canonical paths

- Template: `/mnt/data2t/hidrive/Öffentlich Planungsbüro Schulz/Allgemein/PBS-Templates/layouts/uebersicht-A4-landscape.qpt`
- Auftragnehmer YAMLs: `/mnt/data2t/hidrive/Öffentlich Planungsbüro Schulz/Allgemein/PBS-Templates/auftragnehmer/<name>.yaml`
- Logo PNGs: `/mnt/data2t/hidrive/Öffentlich Planungsbüro Schulz/Allgemein/PBS-Templates/auftragnehmer/<name>-logo.png`

### Programmatic counterpart

For non-interactive / batch rendering of the same template (e.g. a `gis-workflow` step that auto-generates a Lageplan-PDF as part of a pipeline), use **`gis_utils.qgis_bridge.render_layout_template(...)`** instead of doing the steps above by hand. It loads the template, fills `{{...}}` placeholders from auftragnehmer/project YAMLs, resolves the logo path, and exports PDF/PNG against a running QGIS — useful when the cartographic decisions are already locked in. The interactive procedure in this skill is for the *first time* a project goes through layout setup; the library function is for repeats.

### Future map-type specialisations

This skill is the **foundation**. For recurring map types (PV-Lageplan, B-Plan-Übersicht, Privilegierungsplan, …) build per-type skills that delegate to this one for the layout mechanics but layer in:

- A canonical layer-set + theme name
- A canonical scale per AOI-size bracket
- Specific styling QMLs from `Allgemein/PBS-Templates/styles/`
- Title and Bearbeitungsstand conventions for that map type

Whether to coordinate via an orchestrator skill or via a `map_type:` discriminator in `workflow.yaml` is a decision to defer until 2–3 concrete map types exist.
