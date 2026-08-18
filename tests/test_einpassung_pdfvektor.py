"""Mechanismus 1 — Vektorextraktion nach Strichmuster.

Instrumenten-Paar je Eigenschaft: eine Probe zeigt, dass der Mechanismus
GREIFT, eine zweite, dass er NICHT greift, wo er nicht soll. Ein Test, den
die richtige und die kaputte Fassung gleichermaßen bestehen, zählt nicht.

Die Vorlage ist synthetisch (``tests/fixtures/einpassung/synthplan.py``):
Strichmuster, Strichstärke, Farbe und Text sind gesetzt, das SOLL ist also
bekannt — und es wandert keine Zeile Kundendatenbestand ins Repo.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from pbs_gis.einpassung import (
    EinpassungError,
    SheetFrame,
    dominant_stroke_width,
    m_per_pt,
    read_svg_paths,
    sheet_frame,
    sheet_vectors,
)

_spec = importlib.util.spec_from_file_location(
    "synthplan", Path(__file__).parent / "fixtures" / "einpassung" / "synthplan.py")
synthplan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(synthplan)

DASH = synthplan.CADASTRE_DASH
OTHER_DASH = "3.0 3.0"
# pdftocairo skaliert die Seite beim SVG-Export um ~0,999622.
REL_TOL = 2e-3


def _plan(tmp_path, content, name="plan.pdf"):
    return synthplan.write_pdf(tmp_path / name, [content])


@pytest.fixture
def mixed_plan(tmp_path):
    """Vier Populationen: Kataster, Leitung, dünnes Beiwerk, magenta Beiwerk."""
    content = (
        synthplan.polyline([(100, 100), (300, 100), (300, 300)], dash=DASH)      # Kataster 400 pt
        + synthplan.polyline([(400, 100), (600, 100)])                            # Leitung  200 pt
        + synthplan.polyline([(700, 100), (1000, 100)], width=1.15)               # Beiwerk (Stärke)
        + synthplan.polyline([(100, 500), (900, 500)], rgb=(1, 0, 1))             # Beiwerk (Farbe)
    )
    return _plan(tmp_path, content)


# --- Positivprobe: das Muster trennt --------------------------------------

def test_dasharray_trennt_kataster_von_leitung(mixed_plan):
    sv = sheet_vectors(mixed_plan, 1)
    assert len(sv.kataster) == 1
    assert len(sv.leitung) == 1
    assert sv.kataster_length_m == pytest.approx(400 * m_per_pt(500), rel=REL_TOL)
    assert sv.leitung_length_m == pytest.approx(200 * m_per_pt(500), rel=REL_TOL)


def test_strichstaerke_addiert_beide_populationen(mixed_plan):
    """Die tragende Stärke fasst Kataster UND Leitung; das Muster trennt sie."""
    sv = sheet_vectors(mixed_plan, 1)
    total = sv.kataster_length_m + sv.leitung_length_m
    assert total == pytest.approx(600 * m_per_pt(500), rel=REL_TOL)


# --- Negativprobe: er greift NICHT, wo er nicht soll -----------------------

def test_anderes_dasharray_wandert_nicht_in_den_kataster(mixed_plan):
    """Mit einem anderen Muster ist der Strichpunktzug KEIN Katasterzug.

    Ohne diese Probe würde ein Mechanismus durchgehen, der jeden Zug als
    Kataster nimmt und dessen Positivprobe trotzdem grün ist.
    """
    sv = sheet_vectors(mixed_plan, 1, dasharray=OTHER_DASH)
    assert sv.kataster == []
    assert len(sv.leitung) == 2  # beide 1,73-pt-Züge sind jetzt „nicht Kataster"


def test_abweichende_strichstaerke_bleibt_draussen(mixed_plan):
    """Das dünne Beiwerk (1,15 pt) taucht in keiner der Populationen auf."""
    sv = sheet_vectors(mixed_plan, 1)
    laengen = [round(float(np.ptp(c[:, 0])), 1) for c in sv.kataster + sv.leitung]
    assert round(300 * m_per_pt(500), 1) not in laengen


def test_ausschlussmuster_haelt_beiwerk_draussen(mixed_plan):
    """Magenta zählt nicht — und OHNE Ausschluss zählt es, sonst prüft der Test nichts."""
    ohne = sheet_vectors(mixed_plan, 1, exclude_stroke=None)
    mit = sheet_vectors(mixed_plan, 1)
    assert len(ohne.leitung) == len(mit.leitung) + 1


# --- Die portable Regel: längste nicht-ausgeschlossene Gruppe --------------

def test_dominante_strichstaerke_ist_die_laengste_gruppe(tmp_path):
    content = (synthplan.polyline([(100, 100), (200, 100)], width=2.5)
               + synthplan.polyline([(100, 200), (900, 200)], width=0.9)
               + synthplan.polyline([(100, 300), (500, 300)], width=0.9))
    assert dominant_stroke_width(read_svg_paths(_plan(tmp_path, content), 1)) == 0.9


def test_dominante_strichstaerke_zaehlt_laenge_nicht_pfade(tmp_path):
    """LÄNGSTE Gruppe, nicht häufigste — die beiden gehen auseinander.

    Ein Plankopf besteht aus vielen kurzen Strichen, die tragende
    Katastersignatur aus wenigen langen Zügen. Wer Pfade zählt statt Länge zu
    summieren, wählt den Plankopf. Beide Regeln liefern auf einer Vorlage,
    wo die längste Gruppe auch die häufigste ist, dieselbe Antwort — deshalb
    trennt sie erst dieser Fall.
    """
    content = synthplan.polyline([(100, 100), (1100, 100)], width=2.5)
    for i in range(8):
        content += synthplan.polyline([(100, 200 + i * 20), (140, 200 + i * 20)],
                                      width=0.9)
    paths = read_svg_paths(_plan(tmp_path, content), 1)
    laengen = {}
    for p in paths:
        laengen[p.stroke_width_pt] = laengen.get(p.stroke_width_pt, 0.0) + p.length_pt
    assert laengen[2.5] > laengen[0.9], "Vorlage: 2,5 pt ist die längere Gruppe"
    assert sum(1 for p in paths if p.stroke_width_pt == 0.9) > \
        sum(1 for p in paths if p.stroke_width_pt == 2.5), "…und 0,9 pt die häufigere"
    assert dominant_stroke_width(paths) == 2.5


def test_dominante_strichstaerke_ignoriert_ausgeschlossene_gruppe(tmp_path):
    """Die LÄNGSTE Gruppe ist magenta — gewählt werden muss trotzdem die andere."""
    content = (synthplan.polyline([(100, 100), (1100, 100)], width=2.5, rgb=(1, 0, 1))
               + synthplan.polyline([(100, 200), (400, 200)], width=0.9))
    paths = read_svg_paths(_plan(tmp_path, content), 1)
    assert dominant_stroke_width(paths) == 0.9
    assert dominant_stroke_width(paths, exclude_stroke=None) == 2.5


def test_gemessene_staerke_ersetzt_den_default(tmp_path):
    """``stroke_width_pt=None`` misst am Blatt — der portable Weg."""
    content = (synthplan.polyline([(100, 100), (900, 100)], width=0.9, dash=DASH)
               + synthplan.polyline([(100, 200), (500, 200)], width=0.9))
    sv = sheet_vectors(_plan(tmp_path, content), 1, stroke_width_pt=None)
    assert sv.stroke_width_pt == 0.9
    assert len(sv.kataster) == 1 and len(sv.leitung) == 1


# --- Erfolgs-Kriterium 3: die gemessenen Werte sind Parameter --------------

def test_parameter_waehlen_nachweislich_andere_zuege(tmp_path):
    """Ein Aufruf mit abweichendem dasharray/abweichender Stärke wählt andere Züge.

    Der Plan trägt zwei Muster auf zwei Stärken. Jede der vier Kombinationen
    muss eine ANDERE Katasterlänge liefern — sonst sind die Werte de facto
    Konstanten, egal wie sie deklariert sind.
    """
    content = (
        synthplan.polyline([(100, 100), (200, 100)], width=1.73, dash=DASH)        # 100 pt
        + synthplan.polyline([(100, 200), (300, 200)], width=1.73, dash=OTHER_DASH)  # 200 pt
        + synthplan.polyline([(100, 300), (500, 300)], width=0.9, dash=DASH)         # 400 pt
        + synthplan.polyline([(100, 400), (900, 400)], width=0.9, dash=OTHER_DASH)   # 800 pt
    )
    pdf = _plan(tmp_path, content)
    k = m_per_pt(500)
    gemessen = {
        (DASH, 1.73): sheet_vectors(pdf, 1, dasharray=DASH, stroke_width_pt=1.73),
        (OTHER_DASH, 1.73): sheet_vectors(pdf, 1, dasharray=OTHER_DASH, stroke_width_pt=1.73),
        (DASH, 0.9): sheet_vectors(pdf, 1, dasharray=DASH, stroke_width_pt=0.9),
        (OTHER_DASH, 0.9): sheet_vectors(pdf, 1, dasharray=OTHER_DASH, stroke_width_pt=0.9),
    }
    laengen = {key: sv.kataster_length_m for key, sv in gemessen.items()}
    assert laengen[(DASH, 1.73)] == pytest.approx(100 * k, rel=REL_TOL)
    assert laengen[(OTHER_DASH, 1.73)] == pytest.approx(200 * k, rel=REL_TOL)
    assert laengen[(DASH, 0.9)] == pytest.approx(400 * k, rel=REL_TOL)
    assert laengen[(OTHER_DASH, 0.9)] == pytest.approx(800 * k, rel=REL_TOL)
    assert len(set(round(v, 3) for v in laengen.values())) == 4


# --- fail-loud: fehlende Eingabe wirft, statt still leer zurückzugeben -----

def test_seite_ohne_vektorpfade_wirft(tmp_path):
    with pytest.raises(EinpassungError, match="keine Vektorpfade"):
        read_svg_paths(_plan(tmp_path, ""), 1)


def test_text_wird_zu_glyphen_pfaden_ohne_strichstaerke(tmp_path):
    """``pdftocairo`` zeichnet Text als gefüllte Glyphen-Pfade.

    Die tragen keine ``stroke-width`` und dürfen deshalb in keiner
    Linienpopulation landen — sonst zieht jede Beschriftung Stützpunkte in
    die Abstimmung, die keiner Geometrie entsprechen.
    """
    pdf = _plan(tmp_path, synthplan.text(100, 100, "Blatt 3/9")
                + synthplan.polyline([(100, 300), (500, 300)], dash=DASH))
    paths = read_svg_paths(pdf, 1)
    assert any(p.stroke_width_pt is None for p in paths), "Glyphen-Pfade erwartet"
    sv = sheet_vectors(pdf, 1)
    assert len(sv.kataster) == 1 and sv.leitung == []


def test_strichstaerke_ohne_treffer_wirft(mixed_plan):
    with pytest.raises(EinpassungError, match="kein Pfad mit Strichstärke"):
        sheet_vectors(mixed_plan, 1, stroke_width_pt=9.99)


def test_dominante_staerke_ohne_kandidaten_wirft(tmp_path):
    content = synthplan.polyline([(100, 100), (900, 100)], rgb=(1, 0, 1))
    with pytest.raises(EinpassungError, match="keine Strichstärken-Gruppe"):
        dominant_stroke_width(read_svg_paths(_plan(tmp_path, content), 1))


# --- Der Rahmen als Instrument --------------------------------------------

def test_rahmen_maske_trifft_nur_die_rahmennahen_punkte():
    frame = sheet_frame()
    k = m_per_pt(500)
    innen = [[100.0, 100.0]]
    aussen = [[11.340222 * k, 100.0]]
    assert not frame.near(innen)[0]
    assert frame.near(aussen)[0]
    assert frame.inside(innen)[0]
    assert not frame.inside(aussen)[0]


def test_rahmen_toleranz_ist_parameter():
    eng = SheetFrame(x=(0.0, 100.0), y=(0.0, 100.0), tol=0.1)
    weit = SheetFrame(x=(0.0, 100.0), y=(0.0, 100.0), tol=5.0)
    punkt = [[2.0, 50.0]]
    assert not eng.near(punkt)[0]
    assert weit.near(punkt)[0]


# --- Zahlen im d-Attribut ---------------------------------------------------
# Der SVG-Weg ist laut read_svg_paths der Prüfstand-Eingang; hier trägt er die
# Zahlenformen, die ein Export einer fremden Vorlage liefern kann, ohne dass
# eine solche Vorlage im Repo liegen müsste.

def _svg(tmp_path, d, name="p.svg"):
    p = tmp_path / name
    p.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        f'<g><path d="{d}" stroke="#000"/></g></svg>')
    return p


def test_exponentialschreibweise_wird_als_zahl_gelesen(tmp_path):
    """Ein Muster aus Ziffern, Punkt und Minus zerschneidet 1e3 in 1 und 3."""
    pts = read_svg_paths(_svg(tmp_path, "M 1e3 2e3 L 1.5e3 2e3"),
                         page_height_pt=0.0)[0].points
    assert np.allclose(pts, [[1000.0, -2000.0], [1500.0, -2000.0]])


def test_vorzeichen_bleibt_am_wert(tmp_path):
    """Ohne Trennzeichen vor dem Minus: -10-20 sind zwei Werte, nicht einer."""
    pts = read_svg_paths(_svg(tmp_path, "M -10-20 L 30 40"),
                         page_height_pt=0.0)[0].points
    assert np.allclose(pts, [[-10.0, 20.0], [30.0, -40.0]])


def test_reiner_polygonzug_bleibt_unveraendert(tmp_path):
    """Neutralitätsprobe: der Normalfall liest sich wie zuvor."""
    pts = read_svg_paths(_svg(tmp_path, "M 0 0 L 10 0 L 10 10 Z"),
                         page_height_pt=0.0)[0].points
    assert np.allclose(pts, [[0.0, 0.0], [10.0, 0.0], [10.0, -10.0]])


# --- Kurven-Pfade: laut, wo sie beitragen; still, wo sie verworfen werden ---
# `pdftocairo` macht jede Beschriftung zu gefüllten Glyphen-Pfaden, und Glyphen
# sind Kurven. Eine Sperre beim LESEN träfe deshalb jede beschriftete Seite.
# Gesperrt wird darum ergebnis-seitig: ein markierter Pfad steuert nirgends
# Stützpunkte bei. Je Konsument ein Paar — eine Richtung allein zählt nicht.

_KURVE = 'd="M 100 100 C 200 200 300 200 400 100"'
_GERADE = 'd="M 100 400 L 500 400 L 500 600"'
_DASH_ATTR = f'stroke-dasharray="{DASH}"'


def _svg_roh(tmp_path, koerper, name="mix.svg"):
    p = tmp_path / name
    p.write_text('<svg xmlns="http://www.w3.org/2000/svg"><g>'
                 + koerper + '</g></svg>')
    return p


def test_kurve_wird_beim_lesen_nur_markiert(tmp_path):
    """Das Lesen selbst wirft nie — sonst stirbt jede beschriftete Seite."""
    paths = read_svg_paths(_svg_roh(tmp_path, f'<path {_KURVE} stroke="#000"/>'))
    assert paths[0].fremd_kommando == "C"


def test_polygonzug_bleibt_unmarkiert(tmp_path):
    paths = read_svg_paths(_svg_roh(tmp_path, f'<path {_GERADE} stroke="#000"/>'))
    assert paths[0].fremd_kommando is None


def test_sheet_vectors_wirft_wenn_die_kurve_die_filter_passiert(tmp_path):
    body = (f'<path {_GERADE} stroke="#000" stroke-width="1.73" {_DASH_ATTR}/>'
            f'<path {_KURVE} stroke="#000" stroke-width="1.73"/>')
    with pytest.raises(EinpassungError, match="Kommando 'C'"):
        sheet_vectors(_svg_roh(tmp_path, body), 1, stroke_width_pt=1.73)


def test_sheet_vectors_schweigt_wenn_die_kurve_ohnehin_rausfaellt(tmp_path):
    """Der Glyphen-Fall: keine Strichstärke, also kein Beitrag, also still."""
    body = (f'<path {_GERADE} stroke="#000" stroke-width="1.73" {_DASH_ATTR}/>'
            f'<path {_KURVE} stroke="#000"/>')
    sv = sheet_vectors(_svg_roh(tmp_path, body), 1, stroke_width_pt=1.73)
    assert len(sv.kataster) == 1 and sv.leitung == []
