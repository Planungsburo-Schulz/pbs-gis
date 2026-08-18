"""Einen mehrblättrigen Lageplan OHNE Koordinaten in ein CRS einpassen.

Typischer Fall: eine Planauskunft beim Leitungsträger (Telekom, Netzbetreiber,
Wasserverband) beantwortet die Anfrage mit PDF-**Lageplänen** statt Geodaten.
Die Blätter tragen keinen einzigen Koordinatenwert, dafür eine gezeichnete
Katastersignatur — und die ist der Hebel: sie zeigt dieselben
Flurstücksgrenzen, die amtlich als ALKIS vorliegen. Fällt die gezeichnete
Signatur unter EINER starren Ähnlichkeitstransformation auf das heutige
Kataster, ist die Kartengrundlage lagetreu und die Leitung mit ihr eingepasst.

Pipeline (die Reihenfolge ist der Mechanismus, nicht Geschmack)
---------------------------------------------------------------
1. **Struktur** — :func:`sheet_vectors` trennt Katastersignatur von Leitung
   über das Strichmuster (Mechanismus 1).
2. **Blattverbund** — :func:`sheet_mosaic` registriert überlappende
   Blattpaare, :func:`chain_over_overview` verkettet den Rest über das
   Übersichtsblatt, :func:`sheet_cut_rectangles` + :func:`pin_positions`
   prüfen die Verkettung unabhängig (Mechanismen 2–4). Erst hier entsteht aus
   neun schwachen Einzeloptima EIN starres System.
3. **Robuster Fit** — :func:`coarse_fit` sucht Rotation UND Translation gegen
   ein gesättigtes Abstandsfeld (Mechanismus 5, Grobstufe).
4. **ICP** — :func:`icp_helmert` gleicht fein aus; Maßstab und Rotation sind
   dabei MESSGRÖSSEN.
5. **Isolation** — :func:`isolation` weist aus, ob das Optimum überhaupt eins
   ist. Ein Median ohne Isolation ist kein Ergebnis.
6. **Export** — der komponierte :class:`~pbs_gis.georef.SimilarityTransform`
   auf Leitung und Signatur angewandt (:func:`apply_points`), je Blatt mit
   seiner Lageklasse (eigener Anker vs. nur verkettet).

Referenz (Mechanismus 6) kommt aus der bestehenden WFS-Schicht — kein eigener
Weg in diesem Paket. Der Layer-Alias eines MEHRLAYER-Rezepts wird über
``get_layer_recipe`` aufgelöst; als Stellungs-Argument an ``download``
gereicht bleibt er unaufgelöst und der Dienst kennt ihn nicht::

    from pbs_gis import wfs
    from pbs_gis.recipes import load_recipe

    layer = load_recipe("mv_alkis").get_layer_recipe("flurstuecke")
    ref = wfs.download(None, "", recipe=layer, crs="EPSG:25833",
                       extent=(minx, miny, maxx, maxy))

``mv_alkis:flurstuecke`` löst auf ``adv:AX_Flurstueck`` auf. Die teuer
gelernte Kante dabei: die BBOX muss in **x,y-Achsordnung** gehen — mit y,x
liefert der Dienst kommentarlos 0 Features. ``wfs.download`` sendet sie
spec-konform.

Disziplin (der Skill ``lageplan-einpassung`` erzwingt sie)
-----------------------------------------------------------
* Schwellen VOR dem ersten bewertenden Lauf festschreiben (an der gemessenen
  Vorlage: Median ≤ 1,5 m UND Isolation ≥ 1,25) — nie nachträglich ersetzen.
* Je Mechanismus ein Instrumenten-Paar: eine Positiv- und eine Negativprobe.
* Drei zulässige Ausgänge, alle liefern: eingepasst · Gegenbeweis zum
  tragenden Schluss · bestätigt gescheitert MIT Mechanismus.

Alle an EINER Vorlage gemessenen Werte (Strichmuster, Strichstärke, Schwellen,
Stimmen-Konzentration) sind Schlüsselwort-Defaults, nie Konstanten — jeder mit
einem Docstring-Satz, woran er gemessen wurde.

Example
-------
>>> from pbs_gis.einpassung import (sheet_vectors, vertices, sheet_mosaic,
...                                 overview_vertices, chain_over_overview,
...                                 chamfer_field, coarse_fit, apply_points,
...                                 icp_helmert, compose, isolation,
...                                 reference_rings, sample_rings, resample)
>>> sheets = {p: sheet_vectors("plan.pdf", p) for p in range(2, 10)}   # doctest: +SKIP
>>> verts = {p: vertices(sv.kataster + sv.leitung, sv.frame)           # doctest: +SKIP
...          for p, sv in sheets.items()}
>>> mos = sheet_mosaic(verts)                                          # doctest: +SKIP
>>> ch = chain_over_overview(verts, overview_vertices("plan.pdf", 1))  # doctest: +SKIP
>>> rings = reference_rings(ref)                                       # doctest: +SKIP
>>> coarse = coarse_fit(net_points, chamfer_field(rings))              # doctest: +SKIP
>>> fine = icp_helmert(apply_points(coarse.transform, net_points),     # doctest: +SKIP
...                    sample_rings(rings))
>>> T = compose(fine, coarse.transform)                                # doctest: +SKIP
>>> iso = isolation(apply_points(T, net_points), rings)                # doctest: +SKIP
>>> T = iso.stamp(T)                                                   # doctest: +SKIP
"""

from pbs_gis.einpassung.blattverbund import (
    ChainCheck,
    Chaining,
    Mosaic,
    MosaicEdge,
    PairOffset,
    Pin,
    SheetCut,
    chain_over_overview,
    overview_vertices,
    pair_offset,
    pin_positions,
    predict_positions,
    sheet_cut_rectangles,
    sheet_mosaic,
    verify_chaining,
    vertices,
)
from pbs_gis.einpassung.fit import (
    ChamferField,
    CoarseFit,
    Isolation,
    apply_points,
    chamfer_field,
    coarse_fit,
    compose,
    icp_helmert,
    isolation,
    nearest_distances,
    reference_rings,
    resample,
    sample_rings,
)
from pbs_gis.einpassung.pdfvektor import (
    DEFAULT_DASHARRAY,
    DEFAULT_FRAME_INSET_PT,
    DEFAULT_PAGE_SIZE_PT,
    DEFAULT_STROKE_WIDTH_PT,
    EinpassungError,
    SheetFrame,
    SheetVectors,
    SvgPath,
    chain_length,
    dominant_stroke_width,
    fail_bei_fremdkommando,
    m_per_pt,
    read_svg_paths,
    sheet_frame,
    sheet_vectors,
)

__all__ = [
    # Mechanismus 1 — Struktur
    "DEFAULT_DASHARRAY",
    "DEFAULT_FRAME_INSET_PT",
    "DEFAULT_PAGE_SIZE_PT",
    "DEFAULT_STROKE_WIDTH_PT",
    "EinpassungError",
    "SheetFrame",
    "SheetVectors",
    "SvgPath",
    "chain_length",
    "dominant_stroke_width",
    "fail_bei_fremdkommando",
    "m_per_pt",
    "read_svg_paths",
    "sheet_frame",
    "sheet_vectors",
    # Mechanismen 2-4 — Blattverbund
    "ChainCheck",
    "Chaining",
    "Mosaic",
    "MosaicEdge",
    "PairOffset",
    "Pin",
    "SheetCut",
    "chain_over_overview",
    "overview_vertices",
    "pair_offset",
    "pin_positions",
    "predict_positions",
    "sheet_cut_rectangles",
    "sheet_mosaic",
    "verify_chaining",
    "vertices",
    # Mechanismus 5 — Fit, ICP, Isolation
    "ChamferField",
    "CoarseFit",
    "Isolation",
    "apply_points",
    "chamfer_field",
    "coarse_fit",
    "compose",
    "icp_helmert",
    "isolation",
    "nearest_distances",
    "reference_rings",
    "resample",
    "sample_rings",
]
