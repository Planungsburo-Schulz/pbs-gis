"""Mechanismus 5 — robuster Gesamtfit, ICP/Helmert und der Isolations-Ausweis.

Erst hier trifft das starre Blattsystem (Mechanismen 2–4) auf die amtliche
Referenz. Vier Einsichten tragen den Mechanismus; jede war im Ursprungslauf
teuer:

* **Rotation nie bei 0 festhalten.** Leitungsträger-Altbestand ist häufig
  nicht in Gitternord des Ziel-CRS gezeichnet (am gemessenen Plan −2,035°
  gegen UTM33). Sieben Anläufe mit fester Rotation 0 sind daran gescheitert.
* **Zielfunktion sättigen.** Nur eine TEILMENGE der gezeichneten
  Katastersignatur entspricht dem heutigen Kataster. Ein ungesättigtes
  Abstandsmittel mittelt die Mehrheit ohne Gegenstück ein — jedes Optimum
  wird flach. Züge jenseits der Sättigung zahlen konstant und können das
  Optimum weder verschieben noch verwässern (:func:`coarse_fit`, ``cap``).
* **Isolation ausweisen, nicht nur den Median.** Ein flaches Optimum ist kein
  Ergebnis. :func:`isolation` misst, ob es anderswo eine vergleichbar gute
  Lage gibt — auf dem **Anker-Anteil** (Anteil der Samples nahe einer
  Referenzkante), nicht auf einem gesättigten Mittel, das die Frage nicht
  mehr trennt.
* **Freier Maßstab und freie Rotation im ICP sind MESSGRÖSSEN**, keine
  Freiheitsgrade, die man wegoptimiert: Maßstab ≈ 1 bestätigt den nominellen
  Kartenmaßstab, die Rotation entscheidet das Zeichen-Bezugssystem.

Ergebnis jeder Stufe ist ein :class:`pbs_gis.georef.SimilarityTransform` —
derselbe Transform-Typ wie in :mod:`pbs_gis.georef`, mit den
Einpassungs-Kennwerten (Median, Isolation, Anker-Anteil) in seinen Feldern.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from pbs_gis.einpassung.pdfvektor import EinpassungError
from pbs_gis.georef import SimilarityTransform

__all__ = [
    "ChamferField",
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


def resample(points, step: float) -> np.ndarray:
    """Einen Stützpunktzug in gleiche Bogenlängenschritte umtasten."""
    P = np.asarray(points, float).reshape(-1, 2)
    if len(P) < 2:
        return np.zeros((0, 2))
    d = np.hypot(*np.diff(P, axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(d)])
    if s[-1] < 1e-9:
        return np.zeros((0, 2))
    t = np.arange(0.0, s[-1], step)
    return np.column_stack([np.interp(t, s, P[:, 0]), np.interp(t, s, P[:, 1])])


def reference_rings(source) -> list[np.ndarray]:
    """Alle Umringe einer Referenz-Flächenmenge als Liste von ``(n, 2)``-Arrays.

    ``source`` ist ein GeoDataFrame, ein von geopandas lesbarer Pfad
    (GeoPackage, Shapefile, GeoJSON) oder bereits eine Liste von Arrays.

    Raises:
        EinpassungError: die Referenz enthält keinen Umring.
    """
    if isinstance(source, (list, tuple)) and (
            not len(source) or isinstance(source[0], np.ndarray)):
        rings = [np.asarray(r, float)[:, :2] for r in source]
    else:
        import geopandas as gpd
        gdf = source if hasattr(source, "geometry") else gpd.read_file(source)
        rings = []
        for geom in gdf.geometry:
            if geom is None or geom.is_empty:
                continue
            parts = getattr(geom, "geoms", [geom])
            for part in parts:
                ext = getattr(part, "exterior", None)
                if ext is None:
                    if hasattr(part, "coords"):
                        rings.append(np.asarray(part.coords, float)[:, :2])
                    continue
                rings.append(np.asarray(ext.coords, float)[:, :2])
                for ring in part.interiors:
                    rings.append(np.asarray(ring.coords, float)[:, :2])
    rings = [r for r in rings if len(r) >= 2]
    if not rings:
        raise EinpassungError("Referenz enthält keinen auswertbaren Umring.")
    return rings


def sample_rings(rings, step: float = 0.25) -> np.ndarray:
    """Referenz-Umringe zu einer Punktwolke in festem Abstand umtasten."""
    out = [resample(r, step) for r in rings]
    out = [q for q in out if len(q)]
    if not out:
        raise EinpassungError("Referenz-Umringe ergeben keine Punktwolke.")
    return np.vstack(out)


@dataclass
class ChamferField:
    """Abstandsfeld zur Referenz: je Zelle der Abstand zur nächsten Kante (m)."""

    dt: np.ndarray
    extent: tuple[float, float, float, float]
    res: float

    @property
    def shape(self) -> tuple[int, int]:
        return self.dt.shape


def _chamfer(mask: np.ndarray, a: float = 1.0, b: float = 1.41421356) -> np.ndarray:
    """Zwei-Pass-Chamfer über ein Bool-Gitter, vektorisiert je Zeile."""
    INF = 1e7
    d = np.where(mask, 0.0, INF).astype(np.float32)
    ny, nx = d.shape
    xs = (np.arange(nx) * a).astype(np.float32)

    def hsweep(row):
        f = np.minimum.accumulate(row - xs) + xs
        g = np.minimum.accumulate((row - xs[::-1])[::-1])[::-1] + xs[::-1]
        return np.minimum(f, g)

    for y in range(ny):
        row = d[y]
        if y > 0:
            prev = d[y - 1]
            row = np.minimum(row, prev + a)
            row[1:] = np.minimum(row[1:], prev[:-1] + b)
            row[:-1] = np.minimum(row[:-1], prev[1:] + b)
        d[y] = hsweep(row)
    for y in range(ny - 2, -1, -1):
        row = d[y]
        nxt = d[y + 1]
        row = np.minimum(row, nxt + a)
        row[1:] = np.minimum(row[1:], nxt[:-1] + b)
        row[:-1] = np.minimum(row[:-1], nxt[1:] + b)
        d[y] = hsweep(row)
    return d


def _rasterize(rings, res: float, pad: float, dtype=bool):
    allp = np.vstack([np.asarray(r, float)[:, :2] for r in rings])
    x0, y0 = np.floor(allp.min(0) - pad)
    x1, y1 = np.ceil(allp.max(0) + pad)
    nx, ny = int((x1 - x0) / res), int((y1 - y0) / res)
    g = np.zeros((ny, nx), dtype)
    for r in rings:
        Q = resample(r, res / 2)
        if not len(Q):
            continue
        ix = ((Q[:, 0] - x0) / res).astype(int)
        iy = ((y1 - Q[:, 1]) / res).astype(int)
        ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
        g[iy[ok], ix[ok]] = True if dtype is bool else 1.0
    return g, (x0, x1, y0, y1)


def chamfer_field(rings, *, res: float = 1.0, pad: float = 20.0) -> ChamferField:
    """Abstandsfeld der Referenzkanten, Zellweite ``res`` Meter."""
    mask, ext = _rasterize(reference_rings(rings), res, pad)
    return ChamferField(dt=_chamfer(mask) * res, extent=ext, res=res)


def _correlate(field_values: np.ndarray, points: np.ndarray, res: float):
    """Alle Translationen eines Punktmusters über ein Feld — via FFT.

    Returns:
        (Ergebnisgitter mit ``nan`` außerhalb, Punktmuster-Maße, Feld-Maße).
    """
    ny, nx = field_values.shape
    P = points - points.min(0)
    bw = int(np.ceil(P[:, 0].max() / res)) + 1
    bh = int(np.ceil(P[:, 1].max() / res)) + 1
    if bw >= nx or bh >= ny:
        raise EinpassungError(
            f"Punktmuster ({bw}×{bh} Zellen) ist größer als das Referenzfeld "
            f"({nx}×{ny}) — die Referenz deckt das Plangebiet nicht ab.")
    mask = np.zeros((bh, bw), np.float32)
    ix = (P[:, 0] / res).astype(int)
    iy = (bh - 1 - (P[:, 1] / res)).astype(int)
    np.add.at(mask, (iy, ix), 1.0)
    n = float(mask.sum())
    sy, sx = ny + bh, nx + bw
    S = np.fft.irfft2(np.fft.rfft2(field_values, s=(sy, sx))
                      * np.fft.rfft2(mask[::-1, ::-1], s=(sy, sx)), s=(sy, sx))
    out = np.full(S.shape, np.nan, np.float32)
    out[bh - 1:ny, bw - 1:nx] = S[bh - 1:ny, bw - 1:nx] / n
    return out, (bw, bh), n


def apply_points(T: SimilarityTransform, points) -> np.ndarray:
    """Eine :class:`SimilarityTransform` auf ein ``(n, 2)``-Punktarray anwenden."""
    a, b, d, e, xoff, yoff = T.matrix
    P = np.asarray(points, float).reshape(-1, 2)
    return np.column_stack([a * P[:, 0] + b * P[:, 1] + xoff,
                            d * P[:, 0] + e * P[:, 1] + yoff])


def compose(outer: SimilarityTransform, inner: SimilarityTransform) -> SimilarityTransform:
    """``outer ∘ inner`` — erst ``inner``, dann ``outer``, als EIN Transform.

    Die Kette Groblage → Feinausgleich ist damit ein einziges Objekt statt
    einer Reihenfolge, die der Aufrufer richtig erinnern muss.

    Die Güte-Felder (Residuen, Inlier, Isolation) kommen vom ÄUSSEREN
    Transform: in der vorgesehenen Verwendung hat ``outer`` seinen Fit auf
    den bereits mit ``inner`` transformierten Punkten gemessen, seine
    Residuen gehören also zu genau dem Ergebnis, das die Komposition
    liefert. Wer zwei unabhängig gemessene Transforms verkettet, misst die
    Güte danach neu.
    """
    return SimilarityTransform(
        scale=outer.scale * inner.scale,
        rotation_deg=outer.rotation_deg + inner.rotation_deg,
        tx=float(outer.scale * (np.cos(np.radians(outer.rotation_deg)) * inner.tx
                                - np.sin(np.radians(outer.rotation_deg)) * inner.ty)
                 + outer.tx),
        ty=float(outer.scale * (np.sin(np.radians(outer.rotation_deg)) * inner.tx
                                + np.cos(np.radians(outer.rotation_deg)) * inner.ty)
                 + outer.ty),
        rms=outer.rms, max_residual=outer.max_residual, n_inliers=outer.n_inliers,
        median_residual=outer.median_residual, isolation=outer.isolation,
        anchor_fraction=outer.anchor_fraction,
        source_crs=inner.source_crs, reference_crs=outer.reference_crs)


@dataclass
class CoarseFit:
    """Grobe Gesamtlage: bester Transform plus die ganze Rotations-Kurve."""

    transform: SimilarityTransform
    mean_distance: float
    scan: list[tuple[float, float, float]]


def coarse_fit(
    points,
    field: ChamferField,
    *,
    rotations=None,
    cap: float = 6.0,
    isolation_rank: int = 200,
) -> CoarseFit:
    """Robuster Gesamtfit über Rotation UND Translation gegen das Abstandsfeld.

    Args:
        points: Katastersamples des starren Blattsystems, in Blattmetern.
        field: Abstandsfeld der Referenz (:func:`chamfer_field`).
        rotations: zu prüfende Rotationen in Grad. Default −3…+3 in
            0,25°-Schritten — an der Telekom-Vorlage gemessen: dort liegt die
            Verdrehung gegen UTM33-Gitternord bei −2,035°, und Kandidaten wie
            Meridiankonvergenz (≈ −1,3°) oder GK-Differenz (≈ −2,4°) liegen
            in diesem Fenster. Rotation 0 zu erzwingen ist der teuerste
            Einzelfehler dieses Verfahrens.
        cap: Sättigung des Abstandsfelds in Metern; 6,0 an derselben Vorlage
            gemessen. Züge ohne Referenz-Gegenstück zahlen darüber konstant.
        isolation_rank: Rang, dessen Wert als Vergleich zum Besten dient
            (200 vorregistriert).

    Raises:
        EinpassungError: leere Punktmenge.
    """
    P = np.asarray(points, float).reshape(-1, 2)
    if len(P) == 0:
        raise EinpassungError("Gesamtfit ohne Punkte — die Blätter haben nichts "
                              "beigetragen (Strichmuster oder Rahmenfilter prüfen).")
    if rotations is None:
        rotations = np.arange(-3.0, 3.0 + 1e-9, 0.25)
    x0, _x1, _y0, y1 = field.extent
    dt_fit = np.minimum(field.dt, cap)
    ctr = P.mean(0)

    scan: list[tuple[float, float, float]] = []
    best = None
    for rot in rotations:
        th = np.radians(rot)
        R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
        rp = (P - ctr) @ R.T + ctr
        grid, (bw, bh), _n = _correlate(dt_fit, rp, field.res)
        idx = np.unravel_index(np.nanargmin(grid), grid.shape)
        value = float(grid[idx])
        flat = grid[np.isfinite(grid)].ravel()
        rank = float(np.partition(flat, isolation_rank)[isolation_rank])
        iso = rank / max(value, 1e-9)
        scan.append((float(rot), value, iso))
        if best is None or value < best[1]:
            ty, tx = idx[0] - bh + 1, idx[1] - bw + 1
            corner = (x0 + tx * field.res, y1 - ty * field.res - (bh - 1) * field.res)
            best = (float(rot), value, iso, corner, rp.min(0))

    rot, value, iso, corner, pmin = best
    th = np.radians(rot)
    R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    # Rotation um ctr, danach Translation so, dass die Musterecke auf `corner` fällt.
    off = np.asarray(corner) - pmin
    t = ctr - R @ ctr + off
    return CoarseFit(
        transform=SimilarityTransform(scale=1.0, rotation_deg=rot,
                                      tx=float(t[0]), ty=float(t[1]), isolation=iso),
        mean_distance=value, scan=scan)


def nearest_distances(points, reference, *, cell: float = 8.0, with_index: bool = False):
    """Abstand jedes Punkts zum nächsten Referenzpunkt, über einen Gitter-Hash."""
    pts = np.asarray(points, float).reshape(-1, 2)
    ref = np.asarray(reference, float).reshape(-1, 2)
    if len(ref) == 0:
        raise EinpassungError("Referenz-Punktwolke ist leer.")
    key = np.floor(ref / cell).astype(np.int64)
    grid: dict[tuple[int, int], list[int]] = {}
    for i, k in enumerate(map(tuple, key)):
        grid.setdefault(k, []).append(i)
    idx = np.full(len(pts), -1, np.int64)
    dist = np.full(len(pts), np.inf)
    pk = np.floor(pts / cell).astype(np.int64)
    for i, (p, k) in enumerate(zip(pts, map(tuple, pk))):
        cand: list[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                cand.extend(grid.get((k[0] + dx, k[1] + dy), ()))
        if not cand:
            continue
        C = ref[cand]
        d = np.hypot(C[:, 0] - p[0], C[:, 1] - p[1])
        j = int(np.argmin(d))
        idx[i] = cand[j]
        dist[i] = d[j]
    return (idx, dist) if with_index else dist


def _helmert(A: np.ndarray, B: np.ndarray):
    """Ähnlichkeitstransformation A -> B (Maßstab, Rotation, Translation)."""
    ca, cb = A.mean(0), B.mean(0)
    A0, B0 = A - ca, B - cb
    H = A0.T @ B0
    U, S, Vt = np.linalg.svd(H)
    D = np.diag([1.0, np.sign(np.linalg.det(Vt.T @ U.T))])
    R = Vt.T @ D @ U.T
    s = float((S * np.diag(D)).sum() / (A0 ** 2).sum())
    t = cb - s * (R @ ca)
    return s, R, t


def icp_helmert(
    points,
    reference_points,
    *,
    thresholds=(3.0, 2.0, 1.5, 1.0, 1.0, 0.8, 0.8),
    cell: float = 8.0,
) -> SimilarityTransform:
    """Feinausgleich der Groblage: ICP mit freiem Maßstab und freier Rotation.

    Args:
        points: bereits grob eingepasste Samples (Ergebnis von
            :func:`coarse_fit` auf die Blattmeter angewandt).
        reference_points: Referenz-Punktwolke (:func:`sample_rings`).
        thresholds: absteigende Inlier-Schwellen in Metern, je eine Iteration.
            Default an der Telekom-Vorlage gemessen.
        cell: Zellweite des Nachbarschafts-Hashs.

    Returns:
        Der ZUSÄTZLICHE Transform über die Groblage — Maßstab und Rotation
        darin sind Messgrößen (Maßstab ≈ 1 bestätigt den Kartenmaßstab,
        die Rotation ergänzt die Grobrotation zur Gesamtverdrehung).
        ``median_residual`` / ``n_inliers`` tragen den Endstand.

    Raises:
        EinpassungError: eine Iteration findet keine zwei Inlier — dann trägt
            die Groblage nicht, und ein „Feinausgleich" wäre Rauschen.
    """
    cur = np.asarray(points, float).reshape(-1, 2).copy()
    ref = np.asarray(reference_points, float).reshape(-1, 2)
    s_tot, R_tot, t_tot = 1.0, np.eye(2), np.zeros(2)
    for it, thr in enumerate(thresholds):
        idx, dist = nearest_distances(cur, ref, cell=cell, with_index=True)
        inl = (dist < thr) & (idx >= 0)
        if inl.sum() < 2:
            raise EinpassungError(
                f"ICP-Iteration {it} (Schwelle {thr} m): nur {int(inl.sum())} Inlier. "
                "Die Groblage trägt nicht — Rotationsfenster und Sättigung prüfen.")
        s, R, t = _helmert(cur[inl], ref[idx[inl]])
        cur = cur @ (s * R).T + t
        R_tot = R @ R_tot
        s_tot *= s
        t_tot = (s * R) @ t_tot + t
    dist = nearest_distances(cur, ref, cell=cell)
    finite = dist[np.isfinite(dist)]
    inl = finite < thresholds[-1]
    # `median_residual` ist der INLIER-Median, nicht der Median ueber alles:
    # wo nur eine Teilmenge der Quelle der Referenz entspricht (aelterer
    # Katasterstand, generalisierte Zeichnung), misst der Gesamtmedian die
    # Groesse der nicht-korrespondierenden Mehrheit und nicht die Guete der
    # Einpassung. `n_inliers` sagt, auf wie vielen Samples er ruht — beide
    # Zahlen gehoeren zusammen gelesen.
    return SimilarityTransform(
        scale=float(s_tot),
        rotation_deg=float(np.degrees(np.arctan2(R_tot[1, 0], R_tot[0, 0]))),
        tx=float(t_tot[0]), ty=float(t_tot[1]),
        rms=float(np.sqrt((finite[inl] ** 2).mean())) if inl.any() else float("nan"),
        max_residual=float(finite[inl].max()) if inl.any() else float("nan"),
        n_inliers=int(inl.sum()),
        median_residual=(float(np.median(finite[inl])) if inl.any() else float("nan")))


@dataclass
class Isolation:
    """Isolations-Ausweis der Endlage, auf dem Anker-Anteil gemessen."""

    isolation: float
    anchor_fraction: float
    rank_fraction: float
    rank: int
    n_samples: int
    offset_from_fit_m: float

    def passes(self, threshold: float = 1.25) -> bool:
        return self.isolation >= threshold

    def stamp(self, transform: SimilarityTransform) -> SimilarityTransform:
        """Den Ausweis an den ausgelieferten Transform heften.

        Ein Transform ohne seinen Isolations-Ausweis ist nicht beurteilbar:
        er trägt die Zahlen, an denen die vorregistrierte Bedingung hängt.
        """
        return replace(transform, isolation=self.isolation,
                       anchor_fraction=self.anchor_fraction)


def isolation(
    points,
    rings,
    *,
    res: float = 1.0,
    near_cells: int = 1,
    rank: int = 200,
    core_cells: int = 20,
    pad: float = 20.0,
) -> Isolation:
    """Gibt es anderswo eine vergleichbar gute Lage? — auf dem Anker-Anteil.

    Bei FESTER Endrotation wird für JEDE Translation der Anteil der Samples
    gezählt, die näher als ``near_cells`` Zellen an einer Referenzkante
    liegen. Verglichen wird der Bestwert mit dem ``rank``-besten Wert
    AUSSERHALB eines Kerns um das Optimum. Das ist dieselbe Frage wie die
    vorregistrierte Isolation, aber auf der Größe, die hier das Signal trägt:
    ein gesättigtes Abstandsmittel trennt die beiden Lagen nicht mehr, weil
    die Mehrheit der Züge ohnehin konstant zahlt.

    Args:
        points: Samples in Zielkoordinaten (Endlage angewandt).
        rings: Referenz-Umringe oder eine von :func:`reference_rings` lesbare
            Quelle.
        res: Zellweite in Metern (1,0 vorregistriert).
        near_cells: Dilatation des Referenzfelds; 1 Zelle ≙ ~1 m Nachbarschaft.
        rank: Vergleichsrang (200 vorregistriert).
        core_cells: Halbe Kantenlänge des ausgeblendeten Kerns um das Optimum;
            20 Zellen ≙ 20 m an der gemessenen Vorlage.
    """
    P = np.asarray(points, float).reshape(-1, 2)
    if len(P) == 0:
        raise EinpassungError("Isolation ohne Punkte — nichts zu messen.")
    grid_ref, ext = _rasterize(reference_rings(rings), res, pad, dtype=np.float32)
    near = grid_ref.copy()
    for _ in range(near_cells):
        d = near.copy()
        d[1:, :] = np.maximum(d[1:, :], near[:-1, :])
        d[:-1, :] = np.maximum(d[:-1, :], near[1:, :])
        e = d.copy()
        e[:, 1:] = np.maximum(e[:, 1:], d[:, :-1])
        e[:, :-1] = np.maximum(e[:, :-1], d[:, 1:])
        near = e

    x0, _x1, _y0, y1 = ext
    grid, (bw, bh), n = _correlate(near, P, res)
    idx = np.unravel_index(np.nanargmax(grid), grid.shape)
    best = float(grid[idx])
    vv = grid.copy()
    oy, ox = idx
    vv[max(0, oy - core_cells):oy + core_cells + 1,
       max(0, ox - core_cells):ox + core_cells + 1] = np.nan
    flat = vv[np.isfinite(vv)].ravel()
    if len(flat) <= rank:
        raise EinpassungError(
            f"nur {len(flat)} vergleichbare Lagen außerhalb des Kerns — Rang "
            f"{rank} nicht besetzt; die Referenz ist zu klein für diesen Ausweis.")
    kth = float(np.partition(flat, -rank)[-rank])
    ty, tx = idx[0] - bh + 1, idx[1] - bw + 1
    ex = x0 + tx * res
    ey = y1 - ty * res - (bh - 1) * res
    return Isolation(
        isolation=best / max(kth, 1e-9), anchor_fraction=best, rank_fraction=kth,
        rank=rank, n_samples=int(n),
        offset_from_fit_m=float(np.hypot(ex - P.min(0)[0], ey - P.min(0)[1])))
