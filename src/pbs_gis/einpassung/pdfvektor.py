"""Mechanismus 1 — Vektoren eines PDF-Lageplans nach Strichmuster trennen.

Ein Leitungsträger-Lageplan ohne Koordinaten trägt zwei Linienpopulationen
übereinander: die gezeichnete **Katastersignatur** (Strichpunkt) und die
**Leitung** selbst. Beide haben dieselbe Strichstärke — erst das
``stroke-dasharray`` trennt sie. Das ist die übertragbare Regel:

* **Strichstärke allein addiert beides** (Kataster + Leitung) und trennt sie
  vom Beiwerk (Rahmen, Beschriftung, Maßlinien).
* **Das Muster trennt** Kataster (gestrichelt) von Leitung (durchgezogen).
* Die tragende Stärke ist die **längste nicht-ausgeschlossene Gruppe** — so
  wird sie an einem neuen Plan gefunden statt geraten
  (:func:`dominant_stroke_width`).

Die Zahlenwerte (dasharray ``5.76 2.88 1.15 2.88``, Stärke 1,73 pt) sind an
EINER Vorlage gemessen (Telekom A637417, PTI 23) und stehen deshalb als
Default eines Schlüsselworts, nie als Konstante.

Die Vektorebene kommt über ``pdftocairo -svg``: das erhält Strichmuster,
Stärke und Farbe als SVG-Attribute, und die Transform-Kette wird beim Laufen
durch den Baum akkumuliert.

Example
-------
>>> from pbs_gis.einpassung import sheet_vectors
>>> sv = sheet_vectors("lageplan.pdf", 2)          # Blatt 2, Maßstab 1:500
>>> len(sv.kataster), len(sv.leitung)              # doctest: +SKIP
(9, 4)
>>> sv.kataster[0][:1]                             # Bodenmeter, y nach oben
array([[...]])
"""
from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

__all__ = [
    "DEFAULT_DASHARRAY",
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
]

#: Strichmuster der Katastersignatur, gemessen an Telekom-Vorlage A637417.
DEFAULT_DASHARRAY = "5.76 2.88 1.15 2.88"
#: Strichstärke der tragenden Linienpopulation (pt), gemessen an derselben Vorlage.
DEFAULT_STROKE_WIDTH_PT = 1.73
#: A3 quer in pt — das Blattformat der gemessenen Vorlage.
DEFAULT_PAGE_SIZE_PT = (1190.55, 841.89)
#: Abstand der Kartenrahmenlinien von der Blattkante (pt), gemessene Vorlage.
DEFAULT_FRAME_INSET_PT = 11.340222

_NUM = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")
#: SVG-Pfadkommandos. ``e``/``E`` steht bewusst NICHT darin — es ist der
#: Exponent einer Zahl, kein Kommando.
_CMD = re.compile(r"[MmZzLlHhVvCcSsQqTtAa]")
#: Kommandos, bei denen ALLE Zahlenpaare Stützpunkte des Zuges sind.
_CMD_STUETZPUNKTE = frozenset("MLZmlz")


class EinpassungError(ValueError):
    """Eine Einpassungs-Stufe konnte ihre Eingabe nicht verarbeiten.

    Jede Stufe wirft laut, statt ein leeres oder Default-Ergebnis
    zurückzugeben: ein stilles Leerergebnis wandert durch die ganze Kette
    und wird erst am Endprodukt als Unsinn sichtbar, wo es niemand mehr der
    Stufe zuordnet.
    """


def fail_bei_fremdkommando(path: "SvgPath", page: int, index: int) -> None:
    """Wirft, wenn ein markierter Pfad Stützpunkte beitragen würde.

    Aufzurufen an der Stelle, an der ein Pfad die Filter des Konsumenten
    PASSIERT hat und in die Auswertung eingeht — nicht beim Lesen. Ein
    Kurven-Pfad, den die Filter ohnehin verwerfen, trägt zu keinem Ergebnis
    bei und bleibt darum still; das ist der Normalfall der Glyphen-Pfade,
    zu denen ``pdftocairo`` jede Beschriftung macht.

    Geht ein markierter Pfad dagegen ein, wandert Nicht-Geometrie als
    Geometrie mit — eine plausibel aussehende falsche Linie, also genau das
    stille Default-Ergebnis, gegen das der Grundsatz von
    :class:`EinpassungError` geschrieben ist.

    Args:
        path: der Pfad, der gerade beitragen würde.
        page: Seite, aus der er gelesen wurde.
        index: seine Position in der von :func:`read_svg_paths` gelieferten
            Liste (nicht die Pfad-Nummer im SVG — die Liste ist um Pfade
            unter ``min_points`` gekürzt).
    """
    if path.fremd_kommando is None:
        return
    raise EinpassungError(
        f"Seite {page}, Pfad {index} der gelesenen Pfade trägt das Kommando "
        f"'{path.fremd_kommando}' und würde Stützpunkte beitragen — diese "
        f"Stufe liest nur Polygonzüge (M/L/Z). Ein Plan MIT Kurven braucht "
        f"einen echten Pfad-Parser und einen Goldstandard, an dem er sich "
        f"messen lässt.")


def m_per_pt(scale: float = 500.0) -> float:
    """Bodenmeter je Papier-Punkt bei Kartenmaßstab 1:``scale``."""
    return 25.4 / 72 / 1000 * scale


def chain_length(chain) -> float:
    """Gestreckte Länge eines Stützpunktzugs (gleiche Einheit wie die Punkte)."""
    P = np.asarray(chain, float)
    if len(P) < 2:
        return 0.0
    return float(np.hypot(*np.diff(P, axis=0).T).sum())


@dataclass(frozen=True)
class SvgPath:
    """Ein ``<path>`` aus dem SVG des Blatts, Transform-Kette bereits angewandt.

    ``points`` liegt in Seiten-pt mit y nach OBEN (die SVG-Konvention y nach
    unten ist bereits gespiegelt), damit alles Weitere in Kartenlogik denkt.
    """

    points: np.ndarray
    stroke_width_pt: float | None
    stroke: str
    dasharray: str
    #: Erster Kommandobuchstabe außerhalb ``M/L/Z`` im ``d``-Attribut, sonst
    #: ``None``. Bei einem markierten Pfad sind ``points`` NICHT die
    #: Stützpunkte des gezeichneten Zuges: bei C/Q/S/T stehen
    #: Bezier-Kontrollpunkte darin, bei A die Bogenparameter, bei H/V
    #: verschiebt der Einzelwert die Paarung. Wer die Punkte auswertet, ruft
    #: davor :func:`fail_bei_fremdkommando` — die Marke allein schützt nicht.
    fremd_kommando: str | None = None

    @property
    def length_pt(self) -> float:
        return chain_length(self.points)


@dataclass(frozen=True)
class SheetFrame:
    """Der Kartenrahmen eines Blatts, in Bodenmetern (y nach oben).

    Der Rahmen ist keine Zierde, sondern ein Instrument: an ihm gekappte
    Linienenden liegen auf JEDEM Blatt an derselben Stelle und stimmen
    deshalb bei der Blattpaar-Registrierung für Schein-Versätze
    (:func:`pbs_gis.einpassung.blattverbund.vertices`).
    """

    x: tuple[float, float]
    y: tuple[float, float]
    tol: float = 0.35

    def near(self, pts) -> np.ndarray:
        """Bool-Maske: Punkt liegt innerhalb ``tol`` an einer Rahmenlinie."""
        V = np.asarray(pts, float).reshape(-1, 2)
        near = np.zeros(len(V), bool)
        for fx in self.x:
            near |= np.abs(V[:, 0] - fx) < self.tol
        for fy in self.y:
            near |= np.abs(V[:, 1] - fy) < self.tol
        return near

    def inside(self, pts) -> np.ndarray:
        """Bool-Maske: Punkt liegt echt innerhalb des Rahmens (``tol`` Abstand)."""
        V = np.asarray(pts, float).reshape(-1, 2)
        return ((V[:, 0] > self.x[0] + self.tol) & (V[:, 0] < self.x[1] - self.tol)
                & (V[:, 1] > self.y[0] + self.tol) & (V[:, 1] < self.y[1] - self.tol))


def sheet_frame(
    *,
    page_size_pt: tuple[float, float] = DEFAULT_PAGE_SIZE_PT,
    inset_pt: float = DEFAULT_FRAME_INSET_PT,
    scale: float = 500.0,
    tol: float = 0.35,
) -> SheetFrame:
    """Rahmen aus Blattformat und Rahmen-Einzug, in Bodenmetern.

    Args:
        page_size_pt: Blattformat (Breite, Höhe) in pt; Default A3 quer.
        inset_pt: Abstand der Rahmenlinie von der Blattkante; 11,340222 pt an
            der Telekom-Vorlage gemessen.
        scale: Kartenmaßstabsnenner (500 = 1:500).
        tol: Fangbreite in Metern, mit der ein Punkt als „am Rahmen" gilt.
    """
    w, h = page_size_pt
    k = m_per_pt(scale)
    return SheetFrame(x=(inset_pt * k, (w - inset_pt) * k),
                      y=(inset_pt * k, (h - inset_pt) * k), tol=tol)


@dataclass
class SheetVectors:
    """Die getrennten Linienpopulationen EINES Blatts, in Bodenmetern.

    ``kataster`` und ``leitung`` sind Listen von ``(n, 2)``-Arrays; der
    Blattursprung ist die linke untere Blattecke, y zeigt nach oben.
    """

    page: int
    kataster: list[np.ndarray]
    leitung: list[np.ndarray]
    stroke_width_pt: float
    dasharray: str
    frame: SheetFrame
    scale: float = 500.0

    @property
    def kataster_length_m(self) -> float:
        return sum(chain_length(c) for c in self.kataster)

    @property
    def leitung_length_m(self) -> float:
        return sum(chain_length(c) for c in self.leitung)


def _matmul(outer, inner):
    a, b, c, d, e, f = outer
    A, B, C, D, E, F = inner
    return (a * A + c * B, b * A + d * B,
            a * C + c * D, b * C + d * D,
            a * E + c * F + e, b * E + d * F + f)


def _parse_dash(value) -> tuple[float, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(float(v) for v in _NUM.findall(value))
    return tuple(float(v) for v in value)


def _dash_equal(a: tuple[float, ...], b: tuple[float, ...], tol: float = 1e-6) -> bool:
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


def read_svg_paths(
    source,
    page: int = 1,
    *,
    page_height_pt: float = DEFAULT_PAGE_SIZE_PT[1],
    min_points: int = 2,
) -> list[SvgPath]:
    """Alle Pfade einer PDF-Seite (oder einer SVG-Datei) als :class:`SvgPath`.

    Die Punkte kommen in Seiten-pt mit y nach oben zurück, Transform-Kette
    angewandt. ``source`` ist ein PDF (dann wird ``page`` per ``pdftocairo
    -svg`` konvertiert) oder direkt eine ``.svg``-Datei — der SVG-Weg ist der
    Prüfstand-Eingang, mit dem sich die Klassifikation ohne PDF testen lässt.

    Args:
        source: Pfad auf ein PDF oder ein SVG.
        page: 1-basierte Seitenzahl (nur für PDF-Eingang).
        page_height_pt: Blatthöhe für die y-Spiegelung.
        min_points: Pfade mit weniger Stützpunkten werden verworfen.

    Raises:
        EinpassungError: die Quelle liefert überhaupt keinen Pfad.
    """
    src = Path(source)
    tmp = None
    if src.suffix.lower() == ".svg":
        svg = src
    else:
        import tempfile
        tmp = tempfile.TemporaryDirectory(prefix="pbs_einpassung_")
        svg = Path(tmp.name) / f"p{page}.svg"
        subprocess.run(["pdftocairo", "-svg", "-f", str(page), "-l", str(page),
                        str(src), str(svg)], check=True, capture_output=True)
    try:
        root = ET.parse(svg).getroot()
    finally:
        if tmp is not None:
            tmp.cleanup()

    out: list[SvgPath] = []

    def walk(el, tf):
        t = el.get("transform")
        if t:
            m = [float(v) for v in _NUM.findall(t)]
            if len(m) == 6:
                tf = _matmul(tf, tuple(m))
        if el.tag.split("}")[-1] == "path":
            a, b, c, d, e, f = tf
            # Zahlen kommen über _NUM: ein Muster aus Ziffern, Punkt und
            # Minus liest 1e3 als 1 und schneidet ein vorangestelltes
            # Vorzeichen vom Wert ab — beides ergibt eine plausibel
            # aussehende falsche Linie statt eines Fehlers.
            geom = el.get("d") or ""
            werte = _NUM.findall(geom)
            pts = [(a * float(x) + c * float(y) + e, b * float(x) + d * float(y) + f)
                   for x, y in zip(werte[0::2], werte[1::2])]
            if len(pts) >= min_points:
                sw = el.get("stroke-width")
                out.append(SvgPath(
                    points=np.asarray([(x, page_height_pt - y) for x, y in pts], float),
                    stroke_width_pt=float(sw) if sw is not None else None,
                    stroke=el.get("stroke") or "",
                    dasharray=el.get("stroke-dasharray") or "",
                    fremd_kommando=next(
                        (k for k in _CMD.findall(geom)
                         if k not in _CMD_STUETZPUNKTE), None)))
        for ch in el:
            walk(ch, tf)

    walk(root, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))
    if not out:
        raise EinpassungError(
            f"{src.name}: keine Vektorpfade auf Seite {page} — ist die Seite ein "
            f"Rasterbild (Scan)? Dann trägt sie keine Strichmuster-Information.")
    return out


def dominant_stroke_width(
    paths,
    *,
    exclude_stroke: str | None = "100%",
    round_to: int = 3,
) -> float:
    """Die tragende Strichstärke: **längste nicht-ausgeschlossene Gruppe**.

    Das ist der portable Teil der Regel — an einem neuen Plan wird die
    Strichstärke so GEMESSEN statt aus dieser Vorlage übernommen. Gruppiert
    wird nach Strichstärke, gewertet nach Gesamtlänge.

    Args:
        paths: Pfade aus :func:`read_svg_paths`.
        exclude_stroke: Teilstring im ``stroke``-Attribut, der eine Gruppe
            ausschließt. Default an der Telekom-Vorlage gemessen: dort trägt
            das Beiwerk ``rgb(100%, 0%, 100%)`` (magenta); dasselbe Muster
            schließt zugleich reines Blau (``rgb(0%, 0%, 100%)`` — die
            Blattschnitt-Rechtecke des Übersichtsblatts) aus. Beides ist am
            Ursprungsplan gewollt. ``None`` schließt nichts aus.
        round_to: Nachkommastellen, mit denen Strichstärken zu einer Gruppe
            zusammenfallen.

    Raises:
        EinpassungError: nach dem Ausschluss bleibt keine Gruppe übrig.
    """
    groups: dict[float, float] = {}
    for p in paths:
        if p.stroke_width_pt is None:
            continue
        if exclude_stroke is not None and exclude_stroke in p.stroke:
            continue
        key = round(p.stroke_width_pt, round_to)
        groups[key] = groups.get(key, 0.0) + p.length_pt
    if not groups:
        raise EinpassungError(
            "keine Strichstärken-Gruppe übrig — alle Pfade tragen das "
            f"Ausschluss-Muster {exclude_stroke!r} oder keine Strichstärke.")
    return max(groups, key=groups.__getitem__)


def sheet_vectors(
    source,
    page: int = 1,
    *,
    dasharray=DEFAULT_DASHARRAY,
    stroke_width_pt: float | None = DEFAULT_STROKE_WIDTH_PT,
    stroke_width_tol: float = 0.005,
    exclude_stroke: str | None = "100%",
    scale: float = 500.0,
    page_size_pt: tuple[float, float] = DEFAULT_PAGE_SIZE_PT,
    frame_inset_pt: float = DEFAULT_FRAME_INSET_PT,
    frame_tol_m: float = 0.35,
) -> SheetVectors:
    """Katastersignatur und Leitung EINES Blatts trennen, in Bodenmetern.

    Args:
        source: PDF- (oder SVG-) Pfad.
        page: 1-basierte Seitenzahl.
        dasharray: Strichmuster der Katastersignatur — String wie im SVG oder
            Zahlenfolge. Default ``"5.76 2.88 1.15 2.88"``, gemessen an der
            Telekom-Vorlage A637417 (PTI 23).
        stroke_width_pt: tragende Strichstärke; ``None`` misst sie am Blatt
            selbst (:func:`dominant_stroke_width`) — der portable Weg.
            Default 1,73 pt, an derselben Vorlage gemessen.
        stroke_width_tol: Toleranz des Strichstärken-Vergleichs in pt.
        exclude_stroke: siehe :func:`dominant_stroke_width`.
        scale: Kartenmaßstabsnenner; 500 = 1:500.
        page_size_pt: Blattformat in pt (Default A3 quer).
        frame_inset_pt: Abstand der Kartenrahmenlinie von der Blattkante.
        frame_tol_m: Fangbreite des Rahmens in Metern.

    Raises:
        EinpassungError: das Blatt trägt keinen Pfad der tragenden
            Strichstärke — dann ist entweder die Stärke falsch geraten oder
            das Blatt trägt die Population nicht.
    """
    paths = read_svg_paths(source, page, page_height_pt=page_size_pt[1])
    if stroke_width_pt is None:
        stroke_width_pt = dominant_stroke_width(paths, exclude_stroke=exclude_stroke)

    want = _parse_dash(dasharray)
    k = m_per_pt(scale)
    kataster: list[np.ndarray] = []
    leitung: list[np.ndarray] = []
    for i, p in enumerate(paths):
        if p.stroke_width_pt is None:
            continue
        if abs(p.stroke_width_pt - stroke_width_pt) > stroke_width_tol:
            continue
        if exclude_stroke is not None and exclude_stroke in p.stroke:
            continue
        fail_bei_fremdkommando(p, page, i)
        pts = p.points * k
        (kataster if _dash_equal(_parse_dash(p.dasharray), want) else leitung).append(pts)

    if not kataster and not leitung:
        raise EinpassungError(
            f"Seite {page}: kein Pfad mit Strichstärke {stroke_width_pt} pt "
            f"(±{stroke_width_tol}) außerhalb des Ausschlusses {exclude_stroke!r}. "
            "Strichstärke am Blatt messen (stroke_width_pt=None) statt raten.")

    return SheetVectors(
        page=page, kataster=kataster, leitung=leitung,
        stroke_width_pt=float(stroke_width_pt),
        dasharray=dasharray if isinstance(dasharray, str) else " ".join(map(str, want)),
        frame=sheet_frame(page_size_pt=page_size_pt, inset_pt=frame_inset_pt,
                          scale=scale, tol=frame_tol_m),
        scale=scale)
