"""Georeference an un-projected / locally-shifted vector dataset by matching it
to a reference dataset of the *same real-world objects*.

Typical case: a DXF/CAD Lageplan carries embedded ALKIS geometry (parcels,
building footprints) but in a local CAD coordinate system (no CRS, or a constant
origin shift, sometimes a plot rotation). The same parcels/buildings are
available officially (e.g. ``th_alkis``/``mv_alkis_vereinf`` recipe). Matching the
two by centroid + area yields the exact similarity transform (scale, rotation,
translation) that lifts the plan into the real CRS — usually to sub-decimetre.

Robust pipeline: build candidate correspondences by area similarity ->
RANSAC over correspondence pairs (similarity from 2 points) -> Umeyama
least-squares refine over inliers. Dependency-light (numpy + shapely only).

Example
-------
>>> from gis_utils import extract_dxf_layers, wfs, georef
>>> dxf = extract_dxf_layers("plan.dxf", "EPSG:25832")
>>> ref_fl = wfs.download(None, "flurstuecke", recipe="th_alkis", extent=bbox, crs="EPSG:25832")
>>> ref_gb = wfs.download(None, "gebaeude",    recipe="th_alkis", extent=bbox, crs="EPSG:25832")
>>> T = georef.register_features(
...     source=[dxf["K-Flurstueck"]["Polygon"], dxf["K-GebaeudeBauwerk"]["Polygon"]],
...     reference=[ref_fl, ref_gb], snap_translation=True)
>>> T.scale, round(T.rotation_deg, 4), T.rms
(1.0, 0.0, 0.0001)
>>> placed = T.apply(dxf["0-Zaun"]["LineString"])   # -> GeoDataFrame in reference CRS
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from itertools import combinations

import numpy as np

try:
    import geopandas as gpd
    from shapely.affinity import affine_transform
except Exception:  # pragma: no cover - geopandas always present in gis env
    gpd = None


@dataclass
class SimilarityTransform:
    """2D similarity (Helmert 4-parameter) transform ``p' = s·R(θ)·p + t``.

    Maps *source* coordinates onto *reference* coordinates.
    """
    scale: float
    rotation_deg: float
    tx: float
    ty: float
    rms: float = 0.0
    max_residual: float = 0.0
    n_inliers: int = 0
    source_crs: str | None = None
    reference_crs: str | None = None

    @property
    def matrix(self) -> list[float]:
        """``[a, b, d, e, xoff, yoff]`` for :func:`shapely.affinity.affine_transform`."""
        th = math.radians(self.rotation_deg)
        c, s = math.cos(th), math.sin(th)
        sc = self.scale
        return [sc * c, -sc * s, sc * s, sc * c, self.tx, self.ty]

    def apply(self, obj):
        """Apply to a shapely geometry, a GeoDataFrame, or a list of geometries.

        For a GeoDataFrame the result is returned in ``reference_crs`` (if known).
        """
        m = self.matrix
        if gpd is not None and isinstance(obj, gpd.GeoDataFrame):
            out = obj.copy()
            out["geometry"] = out.geometry.apply(lambda g: affine_transform(g, m))
            if self.reference_crs:
                out = out.set_crs(self.reference_crs, allow_override=True)
            return out
        if isinstance(obj, (list, tuple)):
            return [affine_transform(g, m) for g in obj]
        return affine_transform(obj, m)

    def is_pure_translation(self, *, scale_tol: float = 1e-4, rot_tol_deg: float = 0.02) -> bool:
        return abs(self.scale - 1.0) < scale_tol and abs(self.rotation_deg) < rot_tol_deg

    def as_dict(self) -> dict:
        return asdict(self)


def _xa(gdf_or_list, area_min):
    """Return (centroids Nx2, areas N, type-id N) for a GeoDataFrame or list of them."""
    if gpd is not None and isinstance(gdf_or_list, gpd.GeoDataFrame):
        seq = [gdf_or_list]
    else:
        seq = list(gdf_or_list)
    cs, ar, ty = [], [], []
    for k, gdf in enumerate(seq):
        g = gdf[gdf.geometry.area > area_min]
        for geom in g.geometry:
            c = geom.centroid
            cs.append((c.x, c.y)); ar.append(geom.area); ty.append(k)
    return np.asarray(cs, float), np.asarray(ar, float), np.asarray(ty, int)


def _umeyama(P, Q):
    """Least-squares similarity (scale, R, t) mapping P -> Q (Umeyama 1991)."""
    muP, muQ = P.mean(0), Q.mean(0)
    P0, Q0 = P - muP, Q - muQ
    H = (P0.T @ Q0) / len(P)
    U, S, Vt = np.linalg.svd(H)
    D = np.diag([1.0, np.sign(np.linalg.det(Vt.T @ U.T))])
    R = Vt.T @ D @ U.T
    s = float(np.trace(np.diag(S) @ D) / ((P0 ** 2).sum() / len(P)))
    t = muQ - s * R @ muP
    return s, R, t


def register_features(
    source,
    reference,
    *,
    area_rel: float = 0.01,
    min_pair_dist: float = 40.0,
    tol: float = 2.0,
    max_iter: int = 30000,
    refine_tols=(2.0, 0.8, 0.4, 0.2),
    scale_bounds=(0.5, 2.0),
    snap_translation: bool = False,
    source_crs: str | None = None,
    reference_crs: str | None = None,
) -> SimilarityTransform:
    """Register *source* features onto *reference* features (same real objects).

    Both arguments are a GeoDataFrame, or a list of GeoDataFrames where
    ``source[i]`` and ``reference[i]`` are the same feature type (e.g. parcels,
    buildings) — cross-type matches are excluded. Matching uses centroid + area.

    Args:
        area_rel: max relative area difference to treat two features as candidates.
        min_pair_dist: min distance (m) between the two seed points of a RANSAC sample.
        tol: inlier distance (m) after applying a candidate transform.
        max_iter: cap on RANSAC samples.
        refine_tols: shrinking inlier tolerances for iterative Umeyama refinement.
        scale_bounds: reject seed transforms whose scale is outside this range.
        snap_translation: if the fit is scale≈1 & rotation≈0, force an exact pure
            translation (recommended when source is a known shifted copy of the
            reference, e.g. CAD reduced coordinates) — removes ~ppm fit noise.

    Returns:
        SimilarityTransform mapping source -> reference (with rms / max_residual).

    Raises:
        ValueError if no consistent transform is found.
    """
    A, Aa, At = _xa(source, 0.0 if isinstance(area_rel, str) else 1e-9)
    B, Ba, Bt = _xa(reference, 1e-9)
    if reference_crs is None and gpd is not None:
        seq = reference if isinstance(reference, (list, tuple)) else [reference]
        try:
            reference_crs = str(seq[0].crs) if seq[0].crs else None
        except Exception:
            reference_crs = None
    if len(A) < 2 or len(B) < 2:
        raise ValueError("need >=2 features in both source and reference")

    # candidate correspondences by same-type + area similarity
    cand = []  # (i, j)
    for i in range(len(A)):
        rel = np.abs(Ba - Aa[i]) / Aa[i]
        for j in np.where((rel < area_rel) & (Bt == At[i]))[0]:
            cand.append((i, j))
    if len(cand) < 2:
        raise ValueError("no area-matched candidate correspondences; loosen area_rel")
    cand = np.array(cand)
    ci, cj = cand[:, 0], cand[:, 1]
    Ac, Bc = A[ci], B[cj]  # candidate source/ref points

    best = None  # (n_inliers, s, theta, a0, b0)
    seeds = list(combinations(range(len(cand)), 2))
    if len(seeds) > max_iter:
        step = len(seeds) / max_iter
        seeds = [seeds[int(k * step)] for k in range(max_iter)]
    for u, v in seeds:
        if ci[u] == ci[v]:
            continue
        da = Ac[v] - Ac[u]; nda = math.hypot(*da)
        if nda < min_pair_dist:
            continue
        db = Bc[v] - Bc[u]; ndb = math.hypot(*db)
        s = ndb / nda
        if not (scale_bounds[0] < s < scale_bounds[1]):
            continue
        th = math.atan2(db[1], db[0]) - math.atan2(da[1], da[0])
        c, sn = math.cos(th), math.sin(th)
        R = np.array([[c, -sn], [sn, c]])
        TA = (s * (Ac - Ac[u]) @ R.T) + Bc[u]
        d = np.hypot(*(TA - Bc).T)
        inl = int((d < tol).sum())
        if best is None or inl > best[0]:
            best = (inl, s, th, Ac[u].copy(), Bc[u].copy())
    if best is None:
        raise ValueError("RANSAC found no consistent similarity transform")

    _, s, th, a0, b0 = best
    c, sn = math.cos(th), math.sin(th)
    R = np.array([[c, -sn], [sn, c]]); t = b0 - s * R @ a0
    for rt in refine_tols:
        TA = (s * (Ac @ R.T)) + t
        m = np.hypot(*(TA - Bc).T) < rt
        if m.sum() < 2:
            break
        s, R, t = _umeyama(Ac[m], Bc[m])
    TA = (s * (Ac @ R.T)) + t
    res = np.hypot(*(TA - Bc).T); m = res < refine_tols[-1]
    rms = float(np.sqrt((res[m] ** 2).mean())) if m.any() else float("nan")
    rot = math.degrees(math.atan2(R[1, 0], R[0, 0]))

    T = SimilarityTransform(scale=float(s), rotation_deg=float(rot), tx=float(t[0]), ty=float(t[1]),
                            rms=rms, max_residual=float(res[m].max()) if m.any() else float("nan"),
                            n_inliers=int(m.sum()), source_crs=source_crs, reference_crs=reference_crs)
    if snap_translation and T.is_pure_translation():
        off = (Bc[m] - Ac[m]).mean(0)
        res2 = np.hypot(*((Ac[m] + off) - Bc[m]).T)
        T = SimilarityTransform(scale=1.0, rotation_deg=0.0, tx=float(off[0]), ty=float(off[1]),
                                rms=float(np.sqrt((res2 ** 2).mean())), max_residual=float(res2.max()),
                                n_inliers=int(m.sum()), source_crs=source_crs, reference_crs=reference_crs)
    return T
