---
name: lageplan-einpassung
description: This skill should be used when the user asks to "Lageplan ohne Koordinaten einpassen", "Telekom-Lageplan georeferenzieren", "Leitungsträger-Plan auf ALKIS", "Planauskunft kam als PDF statt Geodaten", "mehrblättrigen Lageplan georeferenzieren", "Trasse aus PDF-Lageplan in EPSG", or any task turning a koordinatenlosen Leitungsträger-/Vermesser-Lageplan into Geometrie in einem realen CRS — Blattverbund, robuster Fit gegen ALKIS, Isolations-Ausweis.
license: MIT
---

## Lageplan ohne Koordinaten einpassen

Der RL-Normalfall: Die Planauskunft beim Leitungsträger kommt als **PDF-Lageplan**, nicht als Geodaten. Die Blätter tragen keinen Koordinatenwert — aber eine gezeichnete **Katastersignatur**, und die zeigt dieselben Flurstücksgrenzen, die amtlich als ALKIS vorliegen. Fällt die gezeichnete Signatur unter EINER starren Ähnlichkeitstransformation auf das heutige Kataster, ist die Kartengrundlage lagetreu und die Leitung mit ihr eingepasst.

Werkzeug: `pbs_gis.einpassung` (Mechanismen 1–5), Referenz über `pbs_gis.wfs` (Mechanismus 6).

### VOR dem ersten bewertenden Lauf festschreiben

1. **Schwellen ins Ergebnisdokument schreiben.** Serien-Standard: **Median-Restabstand ≤ 1,5 m UND Isolation ≥ 1,25**. Unverändert übernehmen oder besser begründet ersetzen — nie nachträglich. Eine Schwelle, die nach dem Lauf entsteht, misst den Lauf nicht.
2. **Die drei zulässigen Ausgänge benennen.** Alle drei liefern ein Ergebnis:
   - **eingepasst** — Transformationsparameter je Blatt + die Probe, die sie trägt;
   - **Gegenbeweis zum tragenden Schluss** — die bisherige Annahme ist widerlegt, mit der Zahl;
   - **bestätigt gescheitert MIT Mechanismus** — nicht „hat nicht geklappt", sondern woran es liegt.
3. **Je Mechanismus ein Instrumenten-Paar planen:** eine Probe, die zeigt, dass der Mechanismus greift, und eine, die zeigt, dass er NICHT greift, wo er nicht soll. Ein Instrument, das beide Fälle besteht, hat nichts gemessen.

### Reihenfolge — das IST der Mechanismus

**Struktur → Blattverbund → robuster Fit → ICP → Isolation.** Diese fünf Schritte liefert `pbs_gis.einpassung`; sein Ergebnis ist der ausgewiesene Transform, nicht die geschriebene Datei. **Das Herausschreiben der eingepassten Geometrie ist Handarbeit des Bearbeiters** — `apply_points` auf Leitung und Signatur, Schreib-Weg nach dem Beleg (RUNBOOK RL-E). Der Blattverbund macht aus neun schwachen Einzeloptima ein starres System. Ein Einzelblatt direkt gegen ALKIS zu fitten hat im Ursprungsfall siebenmal versagt.

```python
from pbs_gis.einpassung import (
    sheet_vectors, vertices, sheet_mosaic, overview_vertices,
    chain_over_overview, verify_chaining, sheet_cut_rectangles,
    predict_positions, pin_positions, reference_rings, sample_rings,
    chamfer_field, coarse_fit, icp_helmert, compose, isolation, apply_points,
)

# 1 Struktur — das Muster trennt Kataster von Leitung
sheets = {p: sheet_vectors(PDF, p) for p in range(2, 10)}
verts  = {p: vertices(sv.kataster + sv.leitung, sv.frame) for p, sv in sheets.items()}

# 2 Blattpaare mit direktem Überlapp
mos = sheet_mosaic(verts, reference=2)

# 3 Verkettung über das Übersichtsblatt + PFLICHT-PROBE
ch = chain_over_overview(
    verts, overview_vertices(PDF, 1),
    scale_weights={p: sv.kataster_length_m for p, sv in sheets.items()})
for c in verify_chaining(ch.positions, mos.edges):
    assert c.ok, f"Verkettung reproduziert Kante {c.a}->{c.b} nicht: {c.deviation:.2f} m"

# 4 Blattschnitt-Rechtecke als UNABHÄNGIGE Gegenprobe
cut = sheet_cut_rectangles(PDF, 1)
pred, bias = predict_positions(cut, ch.m_per_pt, ch.positions)
lagen, pins = pin_positions(ch.positions, pred, votes=ch.votes)

# 5 robuster Fit + ICP + Isolation
rings  = reference_rings(alkis_gdf)
coarse = coarse_fit(netz, chamfer_field(rings))
fine   = icp_helmert(apply_points(coarse.transform, netz), sample_rings(rings, 0.25))
T      = compose(fine, coarse.transform)
iso    = isolation(apply_points(T, netz), rings)
T      = iso.stamp(T)          # der Transform trägt seinen Ausweis
```

### Referenz beschaffen (Mechanismus 6)

```python
from pbs_gis import wfs
from pbs_gis.recipes import load_recipe

layer = load_recipe("mv_alkis").get_layer_recipe("flurstuecke")   # -> adv:AX_Flurstueck
alkis = wfs.download(None, "", recipe=layer, crs="EPSG:25833", extent=bbox)
```

- Den Alias **über `get_layer_recipe`** auflösen. Als Stellungs-Argument (`wfs.download(None, "flurstuecke", recipe="mv_alkis", …)`) bleibt er unaufgelöst — der Dienst kennt `flurstuecke` nicht, und die Antwort ist ein `DataLayerError`.
- **BBOX in x,y-Achsordnung.** Mit y,x liefert der Dienst kommentarlos **0 Features** — gegen eine Positivprobe festgestellt, nicht vermutet. `wfs.download` sendet spec-konform; wer selbst eine URL baut, prüft es.
- Eine **zweite, unabhängige** Referenz (Projekt-Shapefile *und* Landes-WFS) trennt „Zug liegt falsch" von „Flurstück fehlt im Auszug".

### Die vier teuren Erfahrungssätze

- **Rotation nie bei 0 festhalten.** Leitungsträger-Altbestand ist häufig nicht in Gitternord des Ziel-CRS gezeichnet (am gemessenen Plan −2,035° gegen UTM33; Kandidaten Meridiankonvergenz ≈ −1,3°, GK-Differenz ≈ −2,4°). Der Nordpfeil ist kein Beleg. Über eine Blattdiagonale verschmiert eine ignorierte Verdrehung bis ±4 m, über das Übersichtsblatt bis ±12 m.
- **Nur eine TEILMENGE der gezeichneten Signatur entspricht dem heutigen ALKIS** — im Ursprungsfall etwa ein Viertel; der Rest sind Parallelversätze von 3–18 m zu heutigen Straßengrenzen. Zielfunktion **sättigen** (`cap`), Urteil auf der Stützung dort, wo die Leitung liegt. Der Gesamtmedian über alle 63 Züge war 4,55 m und war bewusst NICHT das Urteilsmaß.
- **Das Übersichtsblatt mit Blattschnitt ist der stärkste Hebel** — es koppelt die Blätter zu einem starren System und sagt jede Blattlage unabhängig vorher. Es rettet Blätter mit zu wenig Zeichnungsinhalt: eines lag mit 3 Schein-Stimmen 322 m falsch.
- **Isolation ausweisen, nicht nur den Median.** Ein flaches Optimum ist kein Ergebnis. Vor dem Blattverbund lagen alle globalen Optima bei Isolation 1,02–1,10 — unabhängig vom gewählten Maß.

### Portabilität: gemessen vs. übertragbar

Strichmuster (`5.76 2.88 1.15 2.88`) und Strichstärke (1,73 pt) sind an EINER Vorlage gemessen (Telekom A637417, PTI 23). Sie stehen als Schlüsselwort-Defaults, nie als Konstanten. Übertragbar ist die **Regel**:

- Das **Muster** trennt Kataster von Leitung; die **Stärke allein** addiert beides.
- Die tragende Stärke ist die **längste nicht-ausgeschlossene Gruppe** — an einem neuen Plan mit `sheet_vectors(..., stroke_width_pt=None)` messen, nicht übernehmen.
- Am neuen Plan zuerst `read_svg_paths` + `dominant_stroke_width` laufen lassen und die Klassen ansehen, bevor eine Zahl gesetzt wird.

### Pflicht-Proben (jede hat schon einmal etwas gefangen)

| Probe | Wogegen | Schwelle |
|---|---|---|
| Rahmen-Schnittpunkte ausschließen | Schein-Stimmen: gekappte Enden liegen auf jedem Blatt gleich | — |
| Stimmen-Konzentration | Blattpaar ohne Überlapp | beste ≥ 2× zweitbeste |
| Verkettung ↔ direkte Kanten | unglaubwürdige Verkettung | ≤ 0,5 m |
| Blattschnitt-Rechteck ↔ Hough-Lage | inhaltsarmes Blatt | ≤ 0,5 m |
| Isolation | flaches Optimum | ≥ 1,25 |
| Median-Restabstand der Anker-Züge | Fehleinpassung | ≤ 1,5 m |

Eine gut gestützte Messung, die der Rechteck-Vorhersage widerspricht, ist ein **Befund**, kein Pin-Fall — `pin_positions` markiert ihn als solchen und überschreibt ihn nicht.

### Nach der Einpassung ist die GEOMETRIEFRAGE zu, nicht die Verfahrensfrage

- **Sichtprüfung vor jeder Buchung:** eingepasste Trasse + Katastersignatur über ALKIS in QGIS legen. Eine sub-dezimetrige Restklaffung auf einer fehlplatzierten Referenz ist trotzdem falsch.
- **Lageklasse je Blatt mitführen** (`anker` = eigene Züge sitzen auf ALKIS · `kette` = Lage nur über Blattschnitt/Verkettung belegt) und als Attribut exportieren. Eine Kettenlage ist eine Schätzung und wird als solche gekennzeichnet.
- **Offen bleibt** die Aufnahme-/Zeichengenauigkeit des Trägerbestands selbst und dessen Unverbindlichkeitsvorbehalt — aus dem Material nicht messbar. Vor einer Festsetzung nach § 9 Abs. 1 Nr. 21 BauGB ist das eine Träger-/Betreiberfrage (Zusicherung, Geodaten oder Einmessung), keine Geometriefrage mehr — als Pflicht-Rückfrage behandeln.

### Häufige Fehler

- **Rotation 0 annehmen**, weil ein Nordpfeil auf dem Blatt ist — der teuerste Einzelfehler dieses Verfahrens.
- **Einzelblatt gegen ALKIS fitten**, statt zuerst den Blattverbund zu bauen: einem Einzelblatt fehlt die Forminformation, das Optimum wird flach.
- **Gesamtmedian als Urteilsmaß** nehmen, wo nur eine Teilmenge korrespondiert.
- **Schwelle nach dem Lauf setzen** oder eine verfehlte Schwelle anpassen — der verfehlte Wert IST der Befund.
- **Rahmenpunkte in die Abstimmung lassen** — liefert eine konzentriert aussehende Kante beim Versatz (0, 0).
- **Überlappbereiche doppelt exportieren** — Nachbarblätter zeichnen die Leitung im Überlapp beide. Die Zusammenführung ist ein bewusster Schritt (Anker-Blätter bevorzugt); die je-Blatt-Rohzüge bleiben der Beleg.
