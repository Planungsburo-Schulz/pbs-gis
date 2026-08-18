"""Mechanismus 5 — robuster Gesamtfit, ICP/Helmert, Isolations-Ausweis.

Die Vorlage bildet den entscheidenden Zug des echten Falls nach: nur eine
TEILMENGE der Quellgeometrie hat ein Gegenstück in der Referenz, der Rest ist
Parallelversatz ohne Entsprechung. Genau an dieser Mehrheit scheitert eine
ungesättigte Zielfunktion — deshalb ist die Sättigung hier ein eigenes
Instrumenten-Paar und nicht nur ein Parameter mit Docstring.
"""

from __future__ import annotations

import numpy as np
import pytest

from pbs_gis.einpassung import (
    EinpassungError,
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
from pbs_gis.georef import SimilarityTransform

WAHRE_ROTATION = -1.5   # Grad; das Blatt ist nicht in Gitternord gezeichnet
WAHRE_VERSCHIEBUNG = np.array([40.0, 55.0])


def _rechteck(x0, y0, w, h):
    return np.array([[x0, y0], [x0 + w, y0], [x0 + w, y0 + h],
                     [x0, y0 + h], [x0, y0]], float)


def _polygon(cx, cy, r, n, rng):
    """Ein unregelmäßiger geschlossener Umring um (cx, cy)."""
    w = np.sort(rng.uniform(0, 2 * np.pi, n))
    rad = r * rng.uniform(0.6, 1.0, n)
    P = np.column_stack([cx + rad * np.cos(w), cy + rad * np.sin(w)])
    return np.vstack([P, P[:1]])


N_ANKER = 3       # Züge MIT Gegenstück in der Referenz
N_OHNE = 6        # Züge OHNE — die Mehrheit, wie im echten Fall


def _fall_geometrie():
    """Referenz und gezeichnete Signatur aus EINEM Zufallsstrom.

    Zwei Entartungen, beide gemessen und beide vermieden:

    * **Kein Raster.** Auf einem regelmäßigen Gitter bildet die Referenz sich
      unter Drehung annähernd auf sich selbst ab — der Fit findet dann eine
      Gitter-Symmetrie statt der Lage (gemessen: +1,5° statt −1,5°, 7 m
      daneben). Freie Lage mit Mindestabstand.
    * **Keine achsparallelen Rechtecke.** Richtungsvielfalt ist das, was im
      echten Fall die Form trägt, „inklusive aller Knicke".

    Die Ausdehnung überragt die Quelle deutlich, sonst gibt es keine
    Vergleichslagen für den Isolations-Ausweis.
    """
    rng = np.random.default_rng(4711)
    zentren: list[np.ndarray] = []
    while len(zentren) < 55:
        c = rng.uniform(30.0, 620.0, 2)
        if all(np.hypot(*(c - o)) > 55.0 for o in zentren):
            zentren.append(c)
    rings = [_polygon(c[0], c[1], rng.uniform(16.0, 26.0), int(rng.integers(5, 9)), rng)
             for c in zentren]

    # Der Plan deckt nur einen AUSSCHNITT der Referenz ab — wie im echten
    # Fall. Deckt die Quelle die Referenz fast aus, bleiben kaum
    # Vergleichslagen, und der Isolations-Ausweis misst dann den Rand des
    # Suchraums statt die Konkurrenz der Lagen (gemessen: Rauschen kam auf
    # Isolation 1,32 statt 1,16, also über die vorregistrierte Schwelle).
    im_ausschnitt = [i for i, c in enumerate(zentren)
                     if 150.0 < c[0] < 430.0 and 150.0 < c[1] < 430.0]
    idx = rng.permutation(im_ausschnitt)
    anker = [rings[i] for i in idx[:N_ANKER]]
    ohne = []
    for i in idx[N_ANKER:N_ANKER + N_OHNE]:
        # Je Zug ein EIGENER Parallelversatz (8-16 m), wie im echten Fall die
        # Straßenzüge eines älteren Katasterstands. Ein GEMEINSAMER Versatz
        # wäre eine zweite gültige Gesamtlage — dann prüfte der Test die
        # Mehrdeutigkeit der Vorlage statt die Sättigung.
        richtung = rng.uniform(0, 2 * np.pi)
        betrag = rng.uniform(8.0, 16.0)
        ohne.append(rings[i] + np.array([np.cos(richtung), np.sin(richtung)]) * betrag)
    return rings, anker, ohne


def _referenz():
    return _fall_geometrie()[0]


def _quelle():
    _rings, anker, ohne = _fall_geometrie()
    return anker, ohne


def _blattmeter():
    """Quelle in Blattkoordinaten: die Rück-Transformation der wahren Lage."""
    anker, ohne = _quelle()
    T = SimilarityTransform(scale=1.0, rotation_deg=WAHRE_ROTATION,
                            tx=float(WAHRE_VERSCHIEBUNG[0]),
                            ty=float(WAHRE_VERSCHIEBUNG[1]))
    inv = SimilarityTransform(scale=1.0, rotation_deg=-WAHRE_ROTATION, tx=0.0, ty=0.0)
    zurueck = []
    for ring in anker + ohne:
        Q = resample(ring, 0.75)
        Q = apply_points(inv, Q - WAHRE_VERSCHIEBUNG)
        zurueck.append(Q)
    return np.vstack(zurueck), T


@pytest.fixture(scope="module")
def fall():
    pts, T_wahr = _blattmeter()
    rings = _referenz()
    return pts, T_wahr, rings, chamfer_field(rings), sample_rings(rings, 0.25)


# --- Positivprobe: der Fit findet die Lage --------------------------------

def test_coarse_fit_findet_rotation_und_lage(fall):
    pts, T_wahr, _rings, field, _ref = fall
    co = coarse_fit(pts, field, rotations=np.arange(-3.0, 3.01, 0.25))
    assert co.transform.rotation_deg == pytest.approx(WAHRE_ROTATION, abs=0.3)
    versetzt = apply_points(co.transform, pts)
    soll = apply_points(T_wahr, pts)
    assert np.median(np.hypot(*(versetzt - soll).T)) < 2.0


def test_icp_verfeinert_bis_auf_dezimeter(fall):
    pts, T_wahr, _rings, field, ref = fall
    co = coarse_fit(pts, field)
    fine = icp_helmert(apply_points(co.transform, pts), ref)
    T = compose(fine, co.transform)
    assert T.rotation_deg == pytest.approx(WAHRE_ROTATION, abs=0.1)
    assert T.scale == pytest.approx(1.0, abs=0.01)
    assert T.median_residual < 0.5
    assert np.median(np.hypot(*(apply_points(T, pts) - apply_points(T_wahr, pts)).T)) < 0.5


def test_massstab_und_rotation_sind_messgroessen(fall):
    """Frei gelassen, nicht wegoptimiert: ein Maßstab ≈ 1 BESTÄTIGT 1:500."""
    pts, _T, _rings, field, ref = fall
    fine = icp_helmert(apply_points(coarse_fit(pts, field).transform, pts), ref)
    assert 0.98 < fine.scale < 1.02
    assert fine.n_inliers > 100


# --- Instrumenten-Paar zur Sättigung --------------------------------------

def _blatt(rings):
    """Umringe in Blattkoordinaten — die Rück-Transformation der wahren Lage."""
    inv = SimilarityTransform(scale=1.0, rotation_deg=-WAHRE_ROTATION, tx=0.0, ty=0.0)
    return np.vstack([apply_points(inv, resample(r, 0.75) - WAHRE_VERSCHIEBUNG)
                      for r in rings])


def test_saettigung_deckelt_den_beitrag_der_zuege_ohne_gegenstueck():
    """Die Spec-Aussage wörtlich: „Züge ohne Referenz-Gegenstück zahlen konstant."

    Konstant zahlen heißt: sie können das Optimum nicht mehr ziehen. Geprüft
    wird genau das — dieselben Anker, aber die Züge OHNE Gegenstück um
    (22, 17) m verrückt. Ändert sich die gefundene Lage kaum, deckelt die
    Sättigung; wandert sie mit, tut sie es nicht. Beide Arme unterscheiden
    sich NUR in ``cap``.

    Die frühere Fassung dieses Tests verglich die Fit-GENAUIGKEIT beider
    Arme. Sie hat über drei Vorlagen-Varianten nicht getrennt (mal gewann
    der eine Arm, mal der andere) — was sie maß, war die Zufallsgeometrie
    der Vorlage, nicht die Sättigung.
    """
    anker, ohne = _quelle()
    field = chamfer_field(_referenz())
    basis = _blatt(anker + ohne)
    verrueckt = _blatt(anker + [r + np.array([22.0, 17.0]) for r in ohne])

    def wanderung(cap):
        a = coarse_fit(basis, field, cap=cap).transform
        b = coarse_fit(verrueckt, field, cap=cap).transform
        return float(np.hypot(a.tx - b.tx, a.ty - b.ty))

    gesaettigt = wanderung(6.0)
    ungesaettigt = wanderung(1e6)
    assert gesaettigt < 5.0, "gesättigt darf die Lage nicht mitziehen"
    assert ungesaettigt > 4 * gesaettigt, (
        "ungesättigt MUSS mitziehen — sonst trennt der Test die beiden Arme nicht")


def test_saettigung_begrenzt_den_zielfunktionswert(fall):
    """Der Deckel ist wirksam, nicht nur deklariert: kein Wert liegt darüber."""
    pts, _T, _rings, field, _ref = fall
    gesaettigt = coarse_fit(pts, field, cap=3.0)
    assert all(wert <= 3.0 + 1e-6 for _rot, wert, _iso in gesaettigt.scan)
    assert max(wert for _r, wert, _i in coarse_fit(pts, field, cap=1e6).scan) > 3.0


# --- Isolation: Positiv- und Negativprobe ---------------------------------

def test_isolation_weist_ein_echtes_optimum_aus(fall):
    pts, _T, rings, field, ref = fall
    co = coarse_fit(pts, field)
    T = compose(icp_helmert(apply_points(co.transform, pts), ref), co.transform)
    iso = isolation(apply_points(T, pts), rings)
    assert iso.passes(1.25)
    assert iso.anchor_fraction > iso.rank_fraction


def test_isolation_faellt_bei_rauschen_durch(fall):
    """Negativprobe: wo kein Signal ist, darf keine Isolation ausgewiesen werden."""
    _pts, _T, rings, _field, _ref = fall
    rauschen = np.random.default_rng(7).uniform([150.0, 150.0], [430.0, 430.0], size=(1500, 2))
    iso = isolation(rauschen, rings)
    assert not iso.passes(1.25)


def test_isolation_stempelt_den_transform(fall):
    pts, _T, rings, field, ref = fall
    co = coarse_fit(pts, field)
    T = compose(icp_helmert(apply_points(co.transform, pts), ref), co.transform)
    iso = isolation(apply_points(T, pts), rings)
    gestempelt = iso.stamp(T)
    assert gestempelt.isolation == iso.isolation
    assert gestempelt.anchor_fraction == iso.anchor_fraction
    assert gestempelt.tx == T.tx and gestempelt.rotation_deg == T.rotation_deg


# --- fail-loud ------------------------------------------------------------

def test_coarse_fit_wirft_ohne_punkte(fall):
    _pts, _T, _rings, field, _ref = fall
    with pytest.raises(EinpassungError, match="ohne Punkte"):
        coarse_fit(np.zeros((0, 2)), field)


def test_coarse_fit_wirft_wenn_die_referenz_zu_klein_ist():
    klein = chamfer_field([_rechteck(0, 0, 5, 5)], res=1.0, pad=1.0)
    with pytest.raises(EinpassungError, match="größer als das Referenzfeld"):
        coarse_fit(_rechteck(0, 0, 300, 300), klein)


def test_icp_wirft_wenn_die_groblage_nicht_traegt(fall):
    _pts, _T, _rings, _field, ref = fall
    weit_weg = np.random.default_rng(3).uniform(9000.0, 9100.0, size=(200, 2))
    with pytest.raises(EinpassungError, match="Inlier"):
        icp_helmert(weit_weg, ref)


def test_isolation_wirft_wenn_der_rang_nicht_besetzt_ist(fall):
    pts, _T, rings, _field, _ref = fall
    with pytest.raises(EinpassungError, match="nicht besetzt"):
        isolation(apply_points(_blattmeter()[1], pts), rings, rank=10 ** 7)


def test_reference_rings_wirft_ohne_umring():
    with pytest.raises(EinpassungError, match="keinen auswertbaren Umring"):
        reference_rings([])


def test_nearest_distances_wirft_ohne_referenz():
    with pytest.raises(EinpassungError, match="Referenz-Punktwolke ist leer"):
        nearest_distances(np.zeros((3, 2)), np.zeros((0, 2)))


# --- Transform-Algebra ----------------------------------------------------

def test_compose_traegt_die_guete_des_aeusseren_transforms():
    innen = SimilarityTransform(scale=1.0, rotation_deg=2.0, tx=1.0, ty=2.0)
    aussen = SimilarityTransform(scale=1.0, rotation_deg=0.1, tx=0.0, ty=0.0,
                                 median_residual=0.24, n_inliers=1030)
    T = compose(aussen, innen)
    assert T.median_residual == 0.24 and T.n_inliers == 1030


def test_compose_ist_erst_innen_dann_aussen():
    innen = SimilarityTransform(scale=2.0, rotation_deg=30.0, tx=5.0, ty=-3.0)
    aussen = SimilarityTransform(scale=0.5, rotation_deg=-10.0, tx=1.0, ty=7.0)
    P = np.array([[0.0, 0.0], [10.0, 4.0], [-3.0, 8.0]])
    assert np.allclose(apply_points(compose(aussen, innen), P),
                       apply_points(aussen, apply_points(innen, P)))


def test_compose_mit_identitaet_aendert_nichts():
    T = SimilarityTransform(scale=1.3, rotation_deg=12.0, tx=4.0, ty=-9.0)
    ident = SimilarityTransform(scale=1.0, rotation_deg=0.0, tx=0.0, ty=0.0)
    P = np.array([[1.0, 2.0], [30.0, -4.0]])
    assert np.allclose(apply_points(compose(T, ident), P), apply_points(T, P))
    assert np.allclose(apply_points(compose(ident, T), P), apply_points(T, P))


def test_resample_haelt_den_schrittabstand():
    Q = resample(np.array([[0.0, 0.0], [10.0, 0.0]]), 0.5)
    assert np.allclose(np.diff(Q[:, 0]), 0.5)
