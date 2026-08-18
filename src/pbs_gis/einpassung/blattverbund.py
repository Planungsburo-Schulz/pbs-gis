"""Mechanismen 2, 3 und 4 — neun lose Blätter zu EINEM starren System.

Diese drei Mechanismen haben ein Subjekt: die Blätter eines mehrblättrigen
Lageplans in einen gemeinsamen Rahmen bringen, BEVOR irgendetwas gegen eine
amtliche Referenz gefittet wird. Neun schwache Einzeloptima sind kein
Ergebnis; ein starres System aus neun Blättern ist eins.

* **Mechanismus 2 — Blattpaar-Registrierung** (:func:`pair_offset`,
  :func:`sheet_mosaic`): Nachbarblätter zeichnen im Überlapp DIESELBE
  Quellgeometrie. Eine Hough-Abstimmung über alle Vertex-Differenzen findet
  den Relativversatz zentimetergenau. Zwei Pflicht-Details, beide teuer
  gelernt: Rahmen-Schnittpunkte VOR der Abstimmung ausschließen (sie liegen
  auf jedem Blatt gleich und stimmen für Schein-Versätze), und eine Kante
  zählt nur bei **Stimmen-Konzentration** — beste Zelle ≥ 2× zweitbeste.
* **Mechanismus 3 — Verkettung über das Übersichtsblatt**
  (:func:`chain_over_overview`): Blätter ohne direkten Überlapp hängen über
  das Übersichtsblatt zusammen, dessen Maßstab dabei mitgemessen wird.
  Pflicht-Probe: die Verkettung muss die vorhandenen direkten Kanten
  reproduzieren (:func:`verify_chaining`, ≤ 0,5 m).
* **Mechanismus 4 — Blattschnitt-Rechtecke** (:func:`sheet_cut_rectangles`,
  :func:`predict_positions`, :func:`pin_positions`): das Übersichtsblatt
  zeichnet je Blatt ein Rechteck mit Seitenlabel. Das sagt jede Blattlage
  UNABHÄNGIG von der Hough-Abstimmung vorher — die Gegenprobe, die ein Blatt
  mit zu wenig Zeichnungsinhalt rettet (im Ursprungslauf lag ein Blatt mit
  3 Schein-Stimmen 322 m falsch).

Alle gemessenen Zahlen stehen als Schlüsselwort-Defaults, nie als Konstante;
jede sagt im Docstring, woran sie gemessen wurde.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pbs_gis.einpassung.pdfvektor import (
    DEFAULT_DASHARRAY,
    DEFAULT_FRAME_INSET_PT,
    DEFAULT_PAGE_SIZE_PT,
    EinpassungError,
    SheetFrame,
    m_per_pt,
    read_svg_paths,
)
from pbs_gis.einpassung.pdfvektor import _dash_equal, _parse_dash

__all__ = [
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
    "vertices",
]


def vertices(chains, frame: SheetFrame | None = None) -> np.ndarray:
    """Alle Stützpunkte der Züge, OHNE die am Kartenrahmen gekappten.

    Der Ausschluss ist kein Feinschliff: ein am Rahmen gekapptes Linienende
    liegt auf jedem Blatt auf demselben Rahmen. Zwei Blätter teilen dadurch
    Punktpaare, die nichts über ihre Relativlage sagen, aber alle für
    denselben Versatz stimmen — eine Schein-Kante, die keine Probe mehr
    einfängt, weil sie konzentriert aussieht.
    """
    chains = [np.asarray(c, float) for c in chains if len(np.asarray(c)) > 0]
    if not chains:
        return np.zeros((0, 2))
    V = np.vstack(chains)
    if frame is None:
        return V
    return V[~frame.near(V)]


def _hough(D: np.ndarray, bin_w: float) -> tuple[np.ndarray, int, int]:
    """Beste Zelle einer 2D-Differenzwolke -> (Versatz, Stimmen, zweitbeste)."""
    keys = np.round(D / bin_w).astype(np.int64)
    kk = keys[:, 0] * 10_000_019 + keys[:, 1]
    _uniq, inv, cnt = np.unique(kk, return_inverse=True, return_counts=True)
    order = np.argsort(-cnt)
    best = order[0]
    off = D[inv == best].mean(0)
    fine = D[np.all(np.abs(D - off) < bin_w, axis=1)]
    if len(fine):
        off = fine.mean(0)
    second = int(cnt[order[1]]) if len(order) > 1 else 0
    return off, int(max(cnt[best], len(fine))), second


@dataclass(frozen=True)
class PairOffset:
    """Relativversatz zweier Blätter samt der Probe, die ihn trägt."""

    offset: np.ndarray | None
    votes: int
    second: int
    accepted: bool

    @property
    def concentration(self) -> float:
        """Stimmen der besten Zelle je Stimme der zweitbesten."""
        return self.votes / max(self.second, 1)


def pair_offset(
    va,
    vb,
    *,
    bin_m: float = 0.10,
    min_votes: int = 10,
    vote_concentration: float = 2.0,
) -> PairOffset:
    """Hough-Abstimmung über alle Vertex-Differenzen ``vb - va``.

    Args:
        va, vb: rahmenbereinigte Stützpunkte zweier Blätter (:func:`vertices`),
            in Bodenmetern.
        bin_m: Zellweite der Abstimmung; 0,10 m an der Telekom-Vorlage
            gemessen (dort ist der Versatz auf ~1 cm bestimmt).
        min_votes: Mindeststimmen, ab denen ein Paar überhaupt zählt; 10 an
            derselben Vorlage gemessen.
        vote_concentration: die beste Zelle muss mindestens dieses Vielfache
            der zweitbesten tragen. Der Wert 2,0 ist die eigentliche Probe:
            ein Paar OHNE Überlapp bekommt Stimmen, aber keine konzentrierten.

    Returns:
        PairOffset mit ``accepted=False``, wenn eine der Proben reißt.
    """
    va = np.asarray(va, float).reshape(-1, 2)
    vb = np.asarray(vb, float).reshape(-1, 2)
    if len(va) == 0 or len(vb) == 0:
        return PairOffset(None, 0, 0, False)
    D = (vb[None, :, :] - va[:, None, :]).reshape(-1, 2)
    off, votes, second = _hough(D, bin_m)
    ok = votes >= min_votes and votes >= vote_concentration * max(second, 1)
    return PairOffset(off, votes, second, bool(ok))


@dataclass(frozen=True)
class MosaicEdge:
    a: int
    b: int
    offset: np.ndarray
    votes: int
    second: int


@dataclass
class Mosaic:
    """Blattursprünge relativ zu einem Referenzblatt, plus die Kanten dahinter."""

    origins: dict[int, np.ndarray]
    edges: list[MosaicEdge]
    reference: int
    residuals: dict[tuple[int, int], float] = field(default_factory=dict)

    @property
    def max_residual(self) -> float:
        return max(self.residuals.values()) if self.residuals else 0.0


def sheet_mosaic(
    sheet_vertices: dict[int, np.ndarray],
    *,
    reference: int | None = None,
    bin_m: float = 0.10,
    min_votes: int = 10,
    vote_concentration: float = 2.0,
    anchor_weight: float = 100.0,
) -> Mosaic:
    """Alle Blattpaare registrieren und die Ursprünge im Ausgleich lösen.

    Aus den akzeptierten Kanten wird ein gewichtetes Least-Squares-System über
    die Blattursprünge gelöst (Gewicht ``sqrt(Stimmen)``), verankert am
    Referenzblatt. Vorzeichen: ``offset = v_b - v_a`` bedeutet
    ``Ursprung_a - Ursprung_b`` — derselbe Weltpunkt erfüllt
    ``U_a + v_a = U_b + v_b``.

    Args:
        sheet_vertices: Blattnummer -> rahmenbereinigte Stützpunkte.
        reference: Blatt, dessen Ursprung auf (0, 0) gesetzt wird; Default das
            kleinstnummerierte.
        anchor_weight: Gewicht der Verankerungsgleichung.

    Raises:
        EinpassungError: kein einziges Blattpaar überlappt — dann gibt es
            keinen Blattverbund, und ein Ergebnis wäre erfunden.
    """
    pages = sorted(sheet_vertices)
    if len(pages) < 2:
        raise EinpassungError(
            f"Blattverbund braucht mindestens 2 Blätter, bekam {len(pages)}.")
    ref = pages[0] if reference is None else reference
    if ref not in sheet_vertices:
        raise EinpassungError(f"Referenzblatt {ref} ist nicht in der Eingabe.")

    edges: list[MosaicEdge] = []
    for i, a in enumerate(pages):
        for b in pages[i + 1:]:
            po = pair_offset(sheet_vertices[a], sheet_vertices[b], bin_m=bin_m,
                             min_votes=min_votes, vote_concentration=vote_concentration)
            if po.accepted:
                edges.append(MosaicEdge(a, b, po.offset, po.votes, po.second))
    if not edges:
        raise EinpassungError(
            "keine einzige Blattkante hat die Stimmen-Konzentration bestanden — "
            "die Blätter überlappen nicht (oder die Vektorextraktion hat die "
            "falsche Linienpopulation geliefert). Es gibt keinen Blattverbund.")

    idx = {p: k for k, p in enumerate(pages)}
    A, rhs, w = [], [], []
    for e in edges:
        row = np.zeros(len(pages))
        row[idx[e.b]] = 1.0
        row[idx[e.a]] = -1.0
        A.append(row)
        rhs.append(e.offset)
        w.append(e.votes ** 0.5)
    A.append(np.eye(len(pages))[idx[ref]])
    rhs.append(np.zeros(2))
    w.append(anchor_weight)
    Aw = np.asarray(A) * np.asarray(w)[:, None]
    Rw = np.asarray(rhs) * np.asarray(w)[:, None]
    sol, *_ = np.linalg.lstsq(Aw, Rw, rcond=None)
    origins = {p: -sol[idx[p]] for p in pages}

    residuals = {(e.a, e.b): float(np.hypot(*(origins[e.a] - origins[e.b] - e.offset)))
                 for e in edges}
    return Mosaic(origins=origins, edges=edges, reference=ref, residuals=residuals)


def overview_vertices(
    source,
    page: int = 1,
    *,
    dasharray=DEFAULT_DASHARRAY,
    solid_stroke_widths_pt: tuple[float, ...] | None = (1.15, 1.73, 0.576),
    exclude_stroke: str | None = "100%",
    stroke_width_tol: float = 0.005,
    page_size_pt: tuple[float, float] = DEFAULT_PAGE_SIZE_PT,
) -> np.ndarray:
    """Kandidaten-Stützpunkte des Übersichtsblatts, in **pt**.

    Das Übersichtsblatt wird als Ganzes gegen die Detailblätter abgestimmt, es
    braucht also möglichst viel gezeichnete Substanz — aber kein Beiwerk, das
    auf den Detailblättern gar nicht vorkommt (Plankopf, Legende, Rahmen).

    Args:
        dasharray: Strichmuster der Katastersignatur (siehe
            :func:`pbs_gis.einpassung.sheet_vectors`).
        solid_stroke_widths_pt: Strichstärken der durchgezogenen Linien, die
            als Substanz zählen. Default an der Telekom-Vorlage gemessen;
            ``None`` nimmt alle nicht ausgeschlossenen Stärken.
        exclude_stroke: Teilstring im ``stroke``-Attribut, der ausschließt
            (Default siehe :func:`pbs_gis.einpassung.dominant_stroke_width`);
            greift NUR auf durchgezogene Linien — die Katastersignatur zählt
            unabhängig von der Farbe.

    Raises:
        EinpassungError: das Blatt liefert keinen Kandidatenpunkt.
    """
    want = _parse_dash(dasharray)
    paths = read_svg_paths(source, page, page_height_pt=page_size_pt[1])
    keep = []
    for p in paths:
        if _dash_equal(_parse_dash(p.dasharray), want):
            keep.append(p.points)
            continue
        if exclude_stroke is not None and exclude_stroke in p.stroke:
            continue
        if solid_stroke_widths_pt is not None:
            if p.stroke_width_pt is None or not any(
                    abs(p.stroke_width_pt - w) <= stroke_width_tol
                    for w in solid_stroke_widths_pt):
                continue
        keep.append(p.points)
    if not keep:
        raise EinpassungError(
            f"Übersichtsblatt (Seite {page}) liefert keine Kandidatenpunkte — "
            "Strichstärken-Auswahl prüfen (solid_stroke_widths_pt=None nimmt alle).")
    return np.vstack(keep)


@dataclass
class Chaining:
    """Ergebnis von Mechanismus 3: Übersichtsmaßstab + Blattlagen im Übersichtsrahmen."""

    m_per_pt: float
    positions: dict[int, np.ndarray]
    votes: dict[int, tuple[int, int]]
    scale_probe_sheet: int
    scale_scan: list[tuple[float, int, int]] = field(default_factory=list)
    refine_scan: list[tuple[float, int, int]] = field(default_factory=list)


def chain_over_overview(
    sheet_vertices: dict[int, np.ndarray],
    overview_vertices_pt,
    *,
    scale_range: tuple[float, float] = (0.690, 0.780),
    scale_step: float = 0.001,
    refine_span: float = 0.002,
    refine_step: float = 0.0005,
    bin_m: float = 0.4,
    refine_bin_m: float = 0.25,
    scale_weights: dict[int, float] | None = None,
    scale_probe_sheet: int | None = None,
) -> Chaining:
    """Alle Blätter gegen das Übersichtsblatt registrieren, Maßstab mitmessen.

    Das Übersichtsblatt zeichnet dieselbe Quellgeometrie wie die
    Detailblätter, nur kleiner. Der Maßstab ist zunächst unbekannt: gesucht
    wird der Wert, der die Hough-Stimmen BÜNDELT — die Stimmenzahl selbst ist
    also die Maßstabsmessung. Danach liefert dieselbe Abstimmung je Blatt den
    Versatz im Übersichtsrahmen, auch für Blätter ohne direkten Überlapp.

    Args:
        sheet_vertices: Blattnummer -> rahmenbereinigte Stützpunkte in
            Bodenmetern (Detailblatt-Maßstab).
        overview_vertices_pt: Stützpunkte des Übersichtsblatts in **pt** —
            sein Maßstab ist ja das Gesuchte.
        scale_range, scale_step: Suchbereich für Bodenmeter je pt. Default
            0,690–0,780 in 0,001-Schritten, an der Telekom-Vorlage gemessen
            (dort liegt der Übersichtsmaßstab bei ~0,742 m/pt ≈ 1:2100).
        refine_span, refine_step: Feinabtastung um den Grobbesten. Sie ist
            DIAGNOSE, nicht Ergebnis: zurückgegeben wird der Grobbeste — so
            hat der Ursprungslauf gerechnet, und die Feinstufe zeigt nur, wie
            flach oder scharf das Stimmenmaximum ist.
        bin_m, refine_bin_m: Zellweiten der Abstimmung in Metern.
        scale_weights: Gewicht je Blatt für die Wahl des Maßstabs-Probeblatts,
            üblich die gezeichnete Katasterlänge
            (:attr:`SheetVectors.kataster_length_m`). Ein Maßstab wird über
            LÄNGEN gemessen: das Blatt mit der meisten gezeichneten Substanz
            bestimmt ihn am schärfsten, während eine hohe Stützpunktzahl auf
            wenig Länge nichts über den Maßstab sagt. Ohne Angabe entscheidet
            die Stützpunktzahl — das ist der schwächere Ersatz.
        scale_probe_sheet: Probeblatt ausdrücklich setzen und ``scale_weights``
            übergehen.

    Raises:
        EinpassungError: kein Blatt oder kein Übersichtspunkt.

    Note:
        Der Übersichtsmaßstab ist die am schwächsten bestimmte Größe der
        Kette. An der gemessenen Vorlage kamen die acht Blätter einzeln auf
        0,737–0,742 m/pt; die Spanne verschiebt die spätere Endrotation um
        ~0,3° und den Endmaßstab um ~0,2 %. ``scale_scan`` und
        ``refine_scan`` gehören deshalb in den Ergebnisbericht, nicht nur der
        Bestwert.
    """
    ov = np.asarray(overview_vertices_pt, float).reshape(-1, 2)
    if len(ov) == 0:
        raise EinpassungError("Übersichtsblatt liefert keine Stützpunkte.")
    usable = {p: np.asarray(v, float).reshape(-1, 2)
              for p, v in sheet_vertices.items() if len(np.asarray(v).reshape(-1, 2))}
    if not usable:
        raise EinpassungError("kein Blatt mit Stützpunkten — nichts zu verketten.")
    if scale_probe_sheet is not None:
        probe = scale_probe_sheet
    elif scale_weights:
        probe = max((p for p in usable if p in scale_weights),
                    key=lambda p: scale_weights[p], default=None)
    else:
        probe = max(usable, key=lambda p: len(usable[p]))
    if probe not in usable:
        raise EinpassungError(f"Maßstabs-Probeblatt {probe} hat keine Stützpunkte.")

    def tally(s: float, bw: float) -> tuple[int, int]:
        D = (usable[probe][None, :, :] - (ov * s)[:, None, :]).reshape(-1, 2)
        _off, v, sec = _hough(D, bw)
        return v, sec

    scan: list[tuple[float, int, int]] = []
    best = None
    for s in np.arange(scale_range[0], scale_range[1] + 1e-9, scale_step):
        votes, second = tally(float(s), bin_m)
        scan.append((float(s), votes, second))
        if best is None or votes > best[1]:
            best = (float(s), votes, second)
    s_best = best[0]

    refine: list[tuple[float, int, int]] = []
    for sf in np.arange(s_best - refine_span, s_best + refine_span + 1e-9, refine_step):
        votes, second = tally(float(sf), refine_bin_m)
        refine.append((float(sf), votes, second))

    w1 = ov * s_best
    positions: dict[int, np.ndarray] = {}
    votes_by_sheet: dict[int, tuple[int, int]] = {}
    for p, V in usable.items():
        D = (V[None, :, :] - w1[:, None, :]).reshape(-1, 2)
        off, votes, second = _hough(D, bin_m)
        positions[p] = off
        votes_by_sheet[p] = (votes, second)
    return Chaining(m_per_pt=s_best, positions=positions, votes=votes_by_sheet,
                    scale_probe_sheet=probe, scale_scan=scan, refine_scan=refine)


@dataclass(frozen=True)
class ChainCheck:
    a: int
    b: int
    direct: np.ndarray
    chained: np.ndarray
    deviation: float
    ok: bool


def verify_chaining(positions: dict[int, np.ndarray], edges, *, tol_m: float = 0.5) -> list[ChainCheck]:
    """Pflicht-Probe: reproduziert die Verkettung die direkten Kanten?

    Die direkten Blattpaar-Kanten aus Mechanismus 2 sind hoch bestimmt
    (Zentimeter). Wenn die über das Übersichtsblatt gewonnene Verkettung sie
    nicht reproduziert, ist sie unglaubwürdig — unabhängig davon, wie gut das
    spätere ALKIS-Optimum aussieht.

    Args:
        positions: Blattlagen aus :func:`chain_over_overview`.
        edges: die akzeptierten Kanten aus :func:`sheet_mosaic`.
        tol_m: zulässige Abweichung; 0,5 m an der Telekom-Vorlage
            vorregistriert (erreicht wurden 0,19/0,25 m).
    """
    out = []
    for e in edges:
        if e.a not in positions or e.b not in positions:
            continue
        # Vorzeichen: eine Kante ist v_b - v_a fuer denselben Weltpunkt, und
        # v_p - Lage_p ist die gemeinsame Rahmenkoordinate; daraus folgt
        # v_b - v_a = Lage_b - Lage_a. Die umgekehrte Differenz laesst die
        # Probe auf jedem gesunden Datensatz feuern — sie ist dann kein
        # Instrument mehr, sondern ein Alarm, der immer an ist.
        chained = positions[e.b] - positions[e.a]
        dev = float(np.hypot(*(chained - np.asarray(e.offset, float))))
        out.append(ChainCheck(e.a, e.b, np.asarray(e.offset, float), chained, dev,
                              dev <= tol_m))
    return out


@dataclass
class SheetCut:
    """Blattschnitt des Übersichtsblatts: je Blatt ein Rechteck in Übersichts-pt."""

    rects: dict[int, tuple[float, float, float, float]]
    n_rects: int
    labels: dict[int, tuple[float, float]]


def sheet_cut_rectangles(
    source,
    page: int = 1,
    *,
    rect_stroke: str = "0%, 0%, 100%",
    label_pattern: str = r"(\d+)/(\d+)",
    page_size_pt: tuple[float, float] = DEFAULT_PAGE_SIZE_PT,
    min_points: int = 4,
) -> SheetCut:
    """Blattschnitt-Rechtecke und ihre Seitenlabel vom Übersichtsblatt lesen.

    Die Rechtecke sind das stärkste unabhängige Instrument des Verfahrens:
    sie sagen jede Blattlage vorher, OHNE die Hough-Abstimmung zu benutzen.

    Args:
        source: PDF (oder SVG) des Plans.
        page: Seite des Übersichtsblatts.
        rect_stroke: Farb-Teilstring der Rechtecke; an der Telekom-Vorlage
            sind sie blau (``rgb(0%, 0%, 100%)``).
        label_pattern: Regex, dem ein Seitenlabel als Ganzes entsprechen muss;
            Gruppe 1 ist die Blattnummer. Default ``(\\d+)/(\\d+)`` trifft
            „3/9" — die Beschriftung der gemessenen Vorlage.
        min_points: Stützpunkte, ab denen ein Pfad als Rechteck gilt.

    Raises:
        EinpassungError: kein Rechteck oder kein Label gefunden — dann trägt
            das Blatt keinen auswertbaren Blattschnitt, und die Gegenprobe
            fehlt, statt still zu bestehen.
    """
    src = Path(source)
    paths = read_svg_paths(src, page, page_height_pt=page_size_pt[1],
                           min_points=min_points)
    rects_all = []
    for p in paths:
        if rect_stroke not in p.stroke:
            continue
        X, Y = p.points[:, 0], p.points[:, 1]
        rects_all.append((float(X.min()), float(Y.min()), float(X.max()), float(Y.max())))
    if not rects_all:
        raise EinpassungError(
            f"Seite {page}: kein Pfad mit Rahmenfarbe {rect_stroke!r} — kein "
            "Blattschnitt, also keine unabhängige Gegenprobe.")

    if src.suffix.lower() == ".svg":
        raise EinpassungError(
            "Seitenlabel brauchen die Textebene des PDF (pdftotext -bbox); "
            "ein SVG trägt sie nicht auswertbar.")
    xml = subprocess.run(["pdftotext", "-f", str(page), "-l", str(page), "-bbox",
                          str(src), "-"], capture_output=True, text=True).stdout
    words = re.findall(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)"'
                       r' yMax="([\d.]+)">([^<]+)</word>', xml)
    labels: dict[int, tuple[float, float]] = {}
    pat = re.compile(label_pattern)
    for x0, y0, x1, y1, txt in words:
        m = pat.fullmatch(txt.strip())
        if m:
            labels[int(m.group(1))] = ((float(x0) + float(x1)) / 2,
                                       page_size_pt[1] - (float(y0) + float(y1)) / 2)
    if not labels:
        raise EinpassungError(
            f"Seite {page}: kein Seitenlabel nach Muster {label_pattern!r} — die "
            "Rechtecke sind ohne Zuordnung wertlos.")

    rects: dict[int, tuple[float, float, float, float]] = {}
    for p, (cx, cy) in labels.items():
        inside = [r for r in rects_all if r[0] <= cx <= r[2] and r[1] <= cy <= r[3]]
        if inside:
            rects[p] = min(inside, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
    if not rects:
        raise EinpassungError(
            "kein Seitenlabel liegt in einem Rechteck — Label und Blattschnitt "
            "gehören nicht zusammen.")
    return SheetCut(rects=rects, n_rects=len(rects_all), labels=labels)


def predict_positions(
    cut: SheetCut,
    m_per_pt_overview: float,
    measured: dict[int, np.ndarray],
    *,
    frame_inset_pt: float = DEFAULT_FRAME_INSET_PT,
    scale: float = 500.0,
) -> tuple[dict[int, np.ndarray], np.ndarray]:
    """Blattlagen aus den Blattschnitt-Rechtecken vorhersagen.

    Ein Rechteck gibt die Blattecke im Übersichtsrahmen; zusammen mit dem
    Rahmen-Einzug des Detailblatts folgt die Blattlage bis auf EINE für alle
    Blätter gemeinsame Konstante, die aus den gemessenen Lagen geschätzt wird.

    Die Konstante ist der **Median** über alle gemessenen Blätter, nicht das
    Mittel: die Vorhersage existiert, um ein grob falsch gemessenes Blatt zu
    finden — ein Mittel würde genau dieses Blatt in die Bezugsgröße
    hineinziehen, gegen die es geprüft wird.

    Returns:
        (Vorhersage je Blatt, geschätzte Konstante).
    """
    k = m_per_pt(scale)
    raw = {p: np.array([frame_inset_pt * k - r[0] * m_per_pt_overview,
                        frame_inset_pt * k - r[1] * m_per_pt_overview])
           for p, r in cut.rects.items()}
    common = [p for p in raw if p in measured]
    if not common:
        raise EinpassungError(
            "kein Blatt hat zugleich eine Rechteck-Vorhersage und eine gemessene "
            "Lage — die Konstante ist nicht schätzbar.")
    bias = np.median(np.asarray([np.asarray(measured[p], float) - raw[p]
                                 for p in common]), axis=0)
    return {p: v + bias for p, v in raw.items()}, bias


@dataclass(frozen=True)
class Pin:
    page: int
    deviation: float
    votes: int
    second: int
    pinned: bool
    reason: str


def pin_positions(
    measured: dict[int, np.ndarray],
    predicted: dict[int, np.ndarray],
    *,
    votes: dict[int, tuple[int, int]] | None = None,
    tol_m: float = 0.5,
    min_votes: int = 10,
    vote_concentration: float = 2.0,
) -> tuple[dict[int, np.ndarray], list[Pin]]:
    """Inhaltsarme Blätter auf ihre Rechteck-Lage pinnen — und das ausweisen.

    Ein Blatt mit wenig Zeichnungsinhalt kann eine konzentriert AUSSEHENDE,
    aber falsche Hough-Lage haben (im Ursprungslauf: 3 Schein-Stimmen,
    322 m daneben). Weicht die gemessene Lage weiter als ``tol_m`` von der
    unabhängigen Rechteck-Vorhersage ab, gilt die Vorhersage — aber nur, wenn
    die Messung auch schwach gestützt ist; eine gut gestützte Messung, die der
    Vorhersage widerspricht, ist ein BEFUND, kein Pin-Fall.

    Args:
        tol_m: zulässige Abweichung Messung ↔ Vorhersage; 0,5 m, an der
            Telekom-Vorlage belegt (7 von 8 Blättern lagen bei 0,03–0,17 m).
        min_votes, vote_concentration: dieselbe Stützungs-Probe wie in
            :func:`pair_offset`.

    Returns:
        (Lagen nach dem Pinnen, Protokoll je Blatt).
    """
    out = dict(measured)
    log: list[Pin] = []
    for p, pred in predicted.items():
        v, s = (votes or {}).get(p, (0, 0))
        if p not in measured:
            out[p] = pred
            log.append(Pin(p, float("nan"), v, s, True, "keine Messung"))
            continue
        dev = float(np.hypot(*(np.asarray(measured[p], float) - pred)))
        weak = v < min_votes or v < vote_concentration * max(s, 1)
        if dev <= tol_m:
            log.append(Pin(p, dev, v, s, False, "Messung bestätigt"))
        elif weak:
            out[p] = pred
            log.append(Pin(p, dev, v, s, True, "Messung schwach gestützt"))
        else:
            log.append(Pin(p, dev, v, s, False,
                           "BEFUND: gut gestützte Messung widerspricht der Vorhersage"))
    return out, log
