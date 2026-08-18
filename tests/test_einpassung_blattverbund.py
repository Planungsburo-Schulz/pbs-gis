"""Mechanismen 2, 3 und 4 — Blattverbund.

Je Mechanismus ein Instrumenten-Paar: der Mechanismus GREIFT auf einer
Vorlage, deren Soll bekannt ist, und er greift NICHT, wo er nicht soll. Die
Pflicht-Details (Rahmenfilter, Stimmen-Konzentration, Verkettungs-Probe,
Rechteck-Gegenprobe) bekommen jeweils ein eigenes Paar — sie sind der Grund,
warum der Mechanismus funktioniert, und ein Test, der sie nicht prüft, geht
grün, wenn sie fehlen.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from pbs_gis.einpassung import (
    EinpassungError,
    SheetFrame,
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

_spec = importlib.util.spec_from_file_location(
    "synthplan", Path(__file__).parent / "fixtures" / "einpassung" / "synthplan.py")
synthplan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(synthplan)

DASH = synthplan.CADASTRE_DASH


def _world(n=60, span=90.0, seed=1):
    """Ein Weltpunktmuster, das die Blätter gemeinsam zeichnen.

    Eigener Seed je Aufruf: ein modulweiter Generator macht die Fixtures von
    der Test-REIHENFOLGE abhängig, und ein Test, dessen Eingabe sich je nach
    Nachbar ändert, misst nicht mehr, was er behauptet.
    """
    return np.random.default_rng(seed).uniform(5.0, 5.0 + span, size=(n, 2))


# --- Mechanismus 2: Blattpaar-Registrierung -------------------------------

def test_pair_offset_findet_den_versatz_bei_ueberlapp():
    W = _world()
    off = np.array([12.34, -56.78])
    va = W
    vb = W + off                                   # dieselbe Quellgeometrie
    po = pair_offset(va, vb)
    assert po.accepted
    assert np.allclose(po.offset, off, atol=0.05)


def test_pair_offset_verwirft_ein_paar_ohne_ueberlapp():
    """Negativprobe: OHNE gemeinsame Geometrie darf keine Kante entstehen.

    Stimmen bekommt jedes Punktpaar-Feld; konzentrierte Stimmen nur, wenn
    dieselbe Geometrie zweimal gezeichnet ist.
    """
    po = pair_offset(_world(seed=1), _world(seed=2) + np.array([300.0, 400.0]))
    assert not po.accepted
    assert po.concentration < 2.0


def test_rahmenpunkte_erzeugen_ohne_filter_eine_schein_kante():
    """Instrumenten-Paar zum Rahmenfilter — der teuerste Einzeldetail.

    Zwei Blätter OHNE gemeinsame Zeichnungsgeometrie, aber mit denselben am
    Kartenrahmen gekappten Enden: ungefiltert stimmen die für den Versatz
    (0, 0) und liefern eine konzentrierte Schein-Kante. Gefiltert bleibt
    nichts übrig, was stimmen könnte.
    """
    frame = SheetFrame(x=(2.0, 100.0), y=(2.0, 80.0), tol=0.35)
    # Unregelmäßig verteilt, wie gekappte Linienenden wirklich liegen: bei
    # GLEICHEN Abständen bilden die Differenzen ein Gitter, das die
    # Konzentrations-Probe von selbst besteht — dann fiele der Rahmenfilter
    # nicht auf, obwohl die Schein-Kante genauso falsch wäre.
    rand = np.column_stack([np.full(40, 2.0), np.random.default_rng(99).uniform(5.0, 75.0, 40)])
    ketten_a = [rand, _world(seed=3)]
    ketten_b = [rand, _world(seed=4) + np.array([250.0, 250.0])]

    ungefiltert = pair_offset(vertices(ketten_a), vertices(ketten_b))
    gefiltert = pair_offset(vertices(ketten_a, frame), vertices(ketten_b, frame))

    assert ungefiltert.accepted, "ohne Filter muss die Schein-Kante durchgehen"
    assert np.allclose(ungefiltert.offset, [0.0, 0.0], atol=0.05)
    assert not gefiltert.accepted, "mit Filter darf keine Kante entstehen"


def test_stimmen_konzentration_ist_die_probe_nicht_die_stimmenzahl():
    """Auch VIELE Stimmen zählen nicht, wenn die zweitbeste Zelle mithält."""
    W = _world()
    po = pair_offset(W, W + np.array([5.0, 5.0]), vote_concentration=1e9)
    assert po.votes >= 10
    assert not po.accepted


def test_sheet_mosaic_loest_die_blattursprünge():
    W = _world()
    lagen = {2: np.zeros(2), 3: np.array([40.0, -10.0]), 4: np.array([75.0, -25.0])}
    verts = {p: W[i * 5:] + lagen[p] for i, p in enumerate(sorted(lagen))}
    mos = sheet_mosaic(verts, reference=2)
    assert len(mos.edges) == 3
    for p in lagen:
        assert np.allclose(mos.origins[p], -lagen[p] + lagen[2], atol=0.1)
    assert mos.max_residual < 0.1


def test_sheet_mosaic_wirft_ohne_einzige_kante():
    verts = {2: _world(seed=5), 3: _world(seed=6) + 500.0, 4: _world(seed=7) + 900.0}
    with pytest.raises(EinpassungError, match="keine einzige Blattkante"):
        sheet_mosaic(verts)


def test_sheet_mosaic_wirft_bei_einem_blatt():
    with pytest.raises(EinpassungError, match="mindestens 2 Blätter"):
        sheet_mosaic({2: _world()})


# --- Mechanismus 3: Verkettung über das Übersichtsblatt --------------------

def _chaining_case(s=0.75):
    """Übersichtsblatt in pt, Detailblätter in Bodenmetern, Lagen bekannt."""
    W_pt = _world(n=70, span=520.0, seed=11)
    lagen = {2: np.array([-150.0, -430.0]), 3: np.array([-205.0, -365.0]),
             4: np.array([-240.0, -266.0])}
    sheets = {p: W_pt[i * 8:] * s + lagen[p] for i, p in enumerate(sorted(lagen))}
    return W_pt, sheets, lagen


def test_chain_over_overview_misst_massstab_und_lagen():
    W_pt, sheets, lagen = _chaining_case()
    ch = chain_over_overview(sheets, W_pt)
    # Die Hough-Zellweite (0,4 m) begrenzt die Massstabsschaerfe: ueber die
    # Blattdiagonale sind 0,002 m/pt weniger als eine Zelle.
    assert ch.m_per_pt == pytest.approx(0.75, abs=0.002)
    for p, L in lagen.items():
        assert np.allclose(ch.positions[p], L, atol=0.4)


def test_verkettungs_probe_bestaetigt_die_direkten_kanten():
    """Positivprobe der Pflicht-Probe: Verkettung ↔ direkte Kanten.

    Vorzeichen-scharf: eine Kante ist ``v_b - v_a``, also
    ``Lage_b - Lage_a``. Mit der umgekehrten Differenz meldet die Probe auf
    genau diesen gesunden Daten WIDERSPRUCH und wäre damit ein Alarm, der
    immer an ist.
    """
    W_pt, sheets, _lagen = _chaining_case()
    ch = chain_over_overview(sheets, W_pt)
    mos = sheet_mosaic(sheets, reference=2)
    checks = verify_chaining(ch.positions, mos.edges)
    assert checks, "es muss direkte Kanten zum Prüfen geben"
    assert all(c.ok for c in checks)
    assert max(c.deviation for c in checks) < 0.5


def test_verkettungs_probe_meldet_eine_verschobene_lage():
    """Negativprobe: eine um 3 m verschobene Blattlage muss auffallen."""
    W_pt, sheets, _lagen = _chaining_case()
    ch = chain_over_overview(sheets, W_pt)
    mos = sheet_mosaic(sheets, reference=2)
    kaputt = dict(ch.positions)
    kaputt[3] = kaputt[3] + np.array([3.0, 0.0])
    checks = verify_chaining(kaputt, mos.edges)
    betroffen = [c for c in checks if 3 in (c.a, c.b)]
    assert betroffen and not any(c.ok for c in betroffen)


def test_massstabs_gewicht_waehlt_das_probeblatt():
    """``scale_weights`` entscheidet das Probeblatt — nachweislich, nicht nur nominell."""
    W_pt, sheets, _ = _chaining_case()
    schwer = chain_over_overview(sheets, W_pt, scale_weights={2: 1.0, 3: 9.0, 4: 2.0})
    gesetzt = chain_over_overview(sheets, W_pt, scale_probe_sheet=4)
    assert schwer.scale_probe_sheet == 3
    assert gesetzt.scale_probe_sheet == 4


def test_chain_over_overview_wirft_ohne_uebersichtspunkte():
    _W, sheets, _ = _chaining_case()
    with pytest.raises(EinpassungError, match="keine Stützpunkte"):
        chain_over_overview(sheets, np.zeros((0, 2)))


# --- Mechanismus 4: Blattschnitt-Rechtecke --------------------------------

RECTS = {2: (100.0, 100.0, 300.0, 240.0),
         3: (300.0, 100.0, 500.0, 240.0),
         4: (100.0, 240.0, 300.0, 380.0)}


@pytest.fixture
def overview_pdf(tmp_path):
    """Übersichtsblatt: drei blaue Blattschnitt-Rechtecke mit Seitenlabel."""
    content = "".join(
        synthplan.rectangle(*r) + synthplan.text((r[0] + r[2]) / 2 - 10,
                                                 (r[1] + r[3]) / 2, f"{p}/9")
        for p, r in RECTS.items())
    content += synthplan.polyline([(120, 120), (480, 360)], dash=DASH)
    return synthplan.write_pdf(tmp_path / "uebersicht.pdf", [content])


def test_blattschnitt_findet_rechtecke_und_ordnet_labels_zu(overview_pdf):
    cut = sheet_cut_rectangles(overview_pdf, 1)
    assert sorted(cut.rects) == [2, 3, 4]
    for p, soll in RECTS.items():
        assert np.allclose(cut.rects[p], soll, atol=1.0)


def test_blattschnitt_wirft_ohne_rechtecke(tmp_path):
    pdf = synthplan.write_pdf(tmp_path / "ohne.pdf",
                              [synthplan.polyline([(100, 100), (500, 100)], dash=DASH)
                               + synthplan.text(200, 200, "2/9")])
    with pytest.raises(EinpassungError, match="kein Pfad mit Rahmenfarbe"):
        sheet_cut_rectangles(pdf, 1)


def test_blattschnitt_wirft_ohne_seitenlabel(tmp_path):
    pdf = synthplan.write_pdf(tmp_path / "ohne_label.pdf",
                              [synthplan.rectangle(*RECTS[2])])
    with pytest.raises(EinpassungError, match="kein Seitenlabel"):
        sheet_cut_rectangles(pdf, 1)


def test_rechteck_vorhersage_reproduziert_die_gemessenen_lagen(overview_pdf):
    """Positivprobe: die Rechtecke sagen die Lagen unabhängig vorher."""
    cut = sheet_cut_rectangles(overview_pdf, 1)
    s = 0.74
    gemessen = {p: np.array([-r[0] * s + 7.0, -r[1] * s - 3.0])
                for p, r in cut.rects.items()}
    pred, _bias = predict_positions(cut, s, gemessen)
    for p in gemessen:
        assert np.allclose(pred[p], gemessen[p], atol=0.05)


def test_konstante_ist_robust_gegen_ein_grob_falsches_blatt(overview_pdf):
    """Die Vorhersage existiert, um EIN falsches Blatt zu finden.

    Sie darf sich von genau diesem Blatt nicht in die Bezugsgröße ziehen
    lassen: mit dem Mittel als Konstante verschiebt ein 300-m-Ausreißer alle
    übrigen Vorhersagen um 100 m und die Gegenprobe ist wertlos.
    """
    cut = sheet_cut_rectangles(overview_pdf, 1)
    s = 0.74
    gemessen = {p: np.array([-r[0] * s + 7.0, -r[1] * s - 3.0])
                for p, r in cut.rects.items()}
    gemessen[4] = gemessen[4] + np.array([300.0, 0.0])
    pred, _bias = predict_positions(cut, s, gemessen)
    assert np.allclose(pred[2], gemessen[2], atol=0.05)
    assert np.allclose(pred[3], gemessen[3], atol=0.05)
    assert np.hypot(*(pred[4] - gemessen[4])) > 250.0


def test_pin_ersetzt_die_schwach_gestuetzte_falsche_lage():
    gemessen = {2: np.zeros(2), 3: np.array([10.0, 10.0]), 8: np.array([322.0, 0.0])}
    vorhersage = {2: np.zeros(2), 3: np.array([10.0, 10.0]), 8: np.zeros(2)}
    votes = {2: (140, 28), 3: (76, 17), 8: (3, 3)}
    gepinnt, log = pin_positions(gemessen, vorhersage, votes=votes)
    assert np.allclose(gepinnt[8], vorhersage[8])
    assert np.allclose(gepinnt[2], gemessen[2])
    assert [p.pinned for p in log if p.page == 8] == [True]


def test_pin_laesst_eine_gut_gestuetzte_widersprechende_messung_stehen():
    """Negativprobe: der Pin darf NICHT jede Abweichung überschreiben.

    Eine konzentriert gestützte Messung, die der Vorhersage widerspricht, ist
    ein Befund über die Vorhersage — nicht über die Messung.
    """
    gemessen = {2: np.zeros(2), 3: np.array([50.0, 0.0])}
    vorhersage = {2: np.zeros(2), 3: np.zeros(2)}
    votes = {2: (140, 28), 3: (120, 4)}
    gepinnt, log = pin_positions(gemessen, vorhersage, votes=votes)
    assert np.allclose(gepinnt[3], gemessen[3])
    eintrag = next(p for p in log if p.page == 3)
    assert not eintrag.pinned and "BEFUND" in eintrag.reason


def test_predict_positions_wirft_ohne_gemeinsames_blatt(overview_pdf):
    cut = sheet_cut_rectangles(overview_pdf, 1)
    with pytest.raises(EinpassungError, match="Konstante ist nicht schätzbar"):
        predict_positions(cut, 0.74, {99: np.zeros(2)})


# --- Kandidaten-Auswahl am Übersichtsblatt ---------------------------------

def test_overview_vertices_nimmt_kataster_und_gewaehlte_staerken(tmp_path):
    content = (synthplan.polyline([(100, 100), (300, 100)], width=1.73, dash=DASH)
               + synthplan.polyline([(100, 200), (300, 200)], width=1.15)
               + synthplan.polyline([(100, 300), (300, 300)], width=4.0)
               + synthplan.polyline([(100, 400), (300, 400)], width=1.15, rgb=(1, 0, 1)))
    pdf = synthplan.write_pdf(tmp_path / "ov.pdf", [content])
    eng = overview_vertices(pdf, 1)
    weit = overview_vertices(pdf, 1, solid_stroke_widths_pt=None)
    assert len(eng) == 4        # Kataster + die 1,15er Linie, je 2 Punkte
    assert len(weit) == 6       # zusätzlich die 4,0er Linie; magenta bleibt draußen


def test_overview_vertices_wirft_ohne_kandidaten(tmp_path):
    pdf = synthplan.write_pdf(tmp_path / "leer.pdf",
                              [synthplan.polyline([(100, 100), (300, 100)], width=9.0)])
    with pytest.raises(EinpassungError, match="keine Kandidatenpunkte"):
        overview_vertices(pdf, 1)


# --- Kurven-Pfade in den beiden Blattverbund-Konsumenten -------------------
# Beide lesen über read_svg_paths und filtern anders als sheet_vectors: hier
# behält `solid_stroke_widths_pt=None` ALLES, und die Rechteck-Sammlung bildet
# die Umhüllende, wo ein Bezier-Kontrollpunkt den Rahmen still aufbliese.
# Gesperrt wird darum dort, wo der Pfad beiträgt — nicht beim Lesen.

_KURVE = 'd="M 100 100 C 200 200 300 200 400 100"'
_GERADE = 'd="M 100 400 L 500 400 L 500 600"'
_RAHMENFARBE = 'stroke="rgb(0%, 0%, 100%)"'


def _svg_roh(tmp_path, koerper, name="mix.svg"):
    p = tmp_path / name
    p.write_text('<svg xmlns="http://www.w3.org/2000/svg"><g>'
                 + koerper + '</g></svg>')
    return p


def test_overview_vertices_wirft_wenn_alles_behalten_wird(tmp_path):
    """`solid_stroke_widths_pt=None` nimmt alle Stärken — auch die Kurve."""
    body = (f'<path {_GERADE} stroke="#000" stroke-width="1.73"/>'
            f'<path {_KURVE} stroke="#000"/>')
    with pytest.raises(EinpassungError, match="Kommando 'C'"):
        overview_vertices(_svg_roh(tmp_path, body), 1, solid_stroke_widths_pt=None)


def test_overview_vertices_schweigt_wenn_die_staerkenwahl_die_kurve_verwirft(tmp_path):
    body = (f'<path {_GERADE} stroke="#000" stroke-width="1.73"/>'
            f'<path {_KURVE} stroke="#000"/>')
    V = overview_vertices(_svg_roh(tmp_path, body), 1,
                          solid_stroke_widths_pt=(1.73,))
    assert len(V) == 3


def test_sheet_cut_rectangles_wirft_bei_kurve_in_rahmenfarbe(tmp_path):
    body = f'<path {_KURVE} {_RAHMENFARBE} stroke-width="0.5"/>'
    with pytest.raises(EinpassungError, match="Kommando 'C'"):
        sheet_cut_rectangles(_svg_roh(tmp_path, body), 1)


def test_sheet_cut_rectangles_schweigt_bei_kurve_anderer_farbe(tmp_path):
    """Die Kurve fällt am Farbfilter — der Lauf klagt über den FEHLENDEN
    Blattschnitt, nicht über das Kommando. Das ist die stille Richtung."""
    body = f'<path {_KURVE} stroke="#000" stroke-width="0.5"/>'
    with pytest.raises(EinpassungError, match="kein Pfad mit Rahmenfarbe"):
        sheet_cut_rectangles(_svg_roh(tmp_path, body), 1)
