"""ATKIS Basis-DLM helpers — classification-aware retrieval.

ATKIS's polygon layers (`AX_Strassenverkehr`, `AX_Bahnverkehr`) carry no
classification attributes themselves.  Classification (e.g. widmung=BAB,
bezeichnung="A24") lives on a separate entity, accessible via xlinks:

::

    AX_Strassenverkehr  ──── spatial proximity ────  AX_Strassenachse (line)
                                                              │
                                                              ▼ istTeilVon
                                                       AX_Strasse (widmung,
                                                                   bezeichnung)

For Bahn the cascade is shorter — classification (`bahnkategorie`,
`bezeichnung`) is directly on `AX_Bahnstrecke`, no parent entity needed.

This module resolves the cascade so callers can fetch only target polygons
matching a classification filter.
"""

from __future__ import annotations

import hashlib
import json
import warnings
from pathlib import Path
from xml.etree import ElementTree as ET

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.ops import unary_union

NS = {
    "adv": "http://www.adv-online.de/namespaces/adv/gid/7.1",
    "gml": "http://www.opengis.net/gml/3.2",
    "wfs": "http://www.opengis.net/wfs/2.0",
    "xlink": "http://www.w3.org/1999/xlink",
}

# Prefer same cache directory as wfs.py
CACHE_DIR_NAME = "download_cache"

REQUEST_TIMEOUT = 180
"""Timeout for WFS HTTP requests in seconds."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_classified_guide(
    wfs_url: str,
    *,
    guide_layer: str,
    extent: tuple[float, float, float, float],
    crs: str,
    classification: dict[str, str],
    classifier_layer: str | None = None,
    classifier_link_attr: str | None = None,
    cache_dir: Path | str | None = None,
    no_cache: bool = False,
    output_path: Path | str | None = None,
) -> gpd.GeoDataFrame:
    """Fetch a guide layer (line/axis) and filter to classification.

    This is the first half of :func:`fetch_classified_features` exposed as
    a standalone function — for use cases where the **guide line itself is
    the desired output** (e.g. road centerline / Strassenachse for buffer
    analyses where the polygon geometry is unreliable or unavailable).

    Args:
        guide_layer: WFS layer of lines (e.g. ``"adv:AX_Strassenachse"``,
            ``"adv:AX_Bahnstrecke"``).
        classification: ``{attribute: substring}`` to filter by, e.g.
            ``{"widmung": "1301", "bezeichnung": "A24"}``.
        classifier_layer: When classification attributes live on a parent
            entity (e.g. ``"adv:AX_Strasse"``).  Omit when on the guide
            itself (e.g. AX_Bahnstrecke).
        classifier_link_attr: Name of xlink attribute on ``guide_layer``
            pointing to the parent (e.g. ``"istTeilVon"``).  Required if
            ``classifier_layer`` is set.
        Other args: see :func:`fetch_classified_features`.

    Returns:
        GeoDataFrame with the filtered guide features and their attributes.
    """
    _cache_dir = Path(cache_dir) if cache_dir else Path.cwd() / CACHE_DIR_NAME
    cache_file = _guide_cache_path(
        _cache_dir, guide_layer, classifier_layer, classifier_link_attr,
        classification, extent, crs,
    )
    if not no_cache and cache_file.exists():
        gdf = gpd.read_file(cache_file)
        if output_path:
            _write(gdf, output_path)
        return gdf

    print(f"[atkis] Downloading guide {guide_layer}...", flush=True)
    guide_gdf, guide_xlinks = _download_features_with_xlinks(
        wfs_url, guide_layer, extent, crs, link_attr=classifier_link_attr,
    )
    print(f"[atkis]   {len(guide_gdf)} features in extent", flush=True)
    if guide_gdf.empty:
        return _empty_gdf(crs, output_path)

    if classifier_layer:
        if not classifier_link_attr:
            raise ValueError(
                "classifier_layer was given but classifier_link_attr is missing"
            )
        urns = sorted({u for u in guide_xlinks.values() if u})
        print(
            f"[atkis] Resolving {len(urns)} → {classifier_layer} for classification",
            flush=True,
        )
        classifier_attrs = _fetch_features_by_ids(wfs_url, classifier_layer, urns)
        matching_urns = {
            urn for urn, attrs in classifier_attrs.items()
            if _attrs_match(attrs, classification)
        }
        print(
            f"[atkis]   {len(matching_urns)} {classifier_layer} match {classification}",
            flush=True,
        )
        guide_filtered = guide_gdf[
            guide_gdf["gml_id"].map(
                lambda gid: guide_xlinks.get(gid, "") in matching_urns
            )
        ].copy()
    else:
        guide_filtered = _attribute_filter_gdf(guide_gdf, classification)

    if guide_filtered.empty:
        print(f"[atkis] No {guide_layer} match {classification}", flush=True)
        return _empty_gdf(crs, output_path)

    print(
        f"[atkis]   {len(guide_filtered)}/{len(guide_gdf)} guide lines retained",
        flush=True,
    )

    # Annotate with classification for traceability
    for k, v in classification.items():
        guide_filtered[k] = v

    # Cache + optional output
    _cache_dir.mkdir(parents=True, exist_ok=True)
    guide_filtered.to_file(cache_file, driver="GPKG")
    if output_path:
        _write(guide_filtered, output_path)

    return guide_filtered.reset_index(drop=True)


def fetch_classified_features(
    wfs_url: str,
    *,
    target_layer: str,
    guide_layer: str,
    extent: tuple[float, float, float, float],
    crs: str,
    classification: dict[str, str],
    classifier_layer: str | None = None,
    classifier_link_attr: str | None = None,
    spatial_buffer_m: float = 30.0,
    cache_dir: Path | str | None = None,
    no_cache: bool = False,
    output_path: Path | str | None = None,
) -> gpd.GeoDataFrame:
    """Fetch ATKIS target polygons filtered via a classified guide cascade.

    Three steps:

      1. Download ``guide_layer`` (a line layer with classification info or
         a link to it) within the bbox.
      2. Either filter the guide directly by ``classification`` (when
         classifier attributes are on the guide itself, e.g. AX_Bahnstrecke),
         OR resolve ``classifier_link_attr`` xlinks to ``classifier_layer``
         and filter the parent (e.g. AX_Strasse widmung/bezeichnung
         referenced via AX_Strassenachse.istTeilVon).
      3. Spatially intersect ``target_layer`` (polygon) features with the
         (buffered) filtered guide lines — return matching polygons.

    Args:
        target_layer: WFS layer of polygons we want
            (e.g. ``"adv:AX_Strassenverkehr"`` or ``"adv:AX_Bahnverkehr"``).
        guide_layer: see :func:`fetch_classified_guide`.
        spatial_buffer_m: Buffer applied to filtered guide lines before
            spatial selection of target polygons (compensates for
            non-overlapping line/polygon geometry).
        Other args: see :func:`fetch_classified_guide`.

    Returns:
        GeoDataFrame of target polygons matching the classification.
    """
    _cache_dir = Path(cache_dir) if cache_dir else Path.cwd() / CACHE_DIR_NAME
    cache_file = _cache_path(
        _cache_dir, target_layer, guide_layer, classifier_layer,
        classifier_link_attr, classification, extent, crs, spatial_buffer_m,
    )
    if not no_cache and cache_file.exists():
        gdf = gpd.read_file(cache_file)
        if output_path:
            _write(gdf, output_path)
        return gdf

    # 1+2) Download + filter guide via shared helper
    guide_filtered = fetch_classified_guide(
        wfs_url,
        guide_layer=guide_layer,
        extent=extent,
        crs=crs,
        classification=classification,
        classifier_layer=classifier_layer,
        classifier_link_attr=classifier_link_attr,
        cache_dir=cache_dir,
        no_cache=no_cache,
    )
    if guide_filtered.empty:
        return _empty_gdf(crs, output_path)

    # 3) Spatial filter on target layer
    print(f"[atkis] Downloading target {target_layer}...", flush=True)
    target_gdf = _wfs_download_geopandas(wfs_url, target_layer, extent, crs)
    print(f"[atkis]   {len(target_gdf)} features in extent", flush=True)
    if target_gdf.empty:
        return _empty_gdf(crs, output_path)

    corridor = unary_union(guide_filtered.geometry.buffer(spatial_buffer_m))
    mask = target_gdf.geometry.intersects(corridor)
    result = target_gdf[mask].copy().reset_index(drop=True)

    print(
        f"[atkis]   {len(result)}/{len(target_gdf)} target polygons match "
        f"(spatial buffer {spatial_buffer_m} m)",
        flush=True,
    )

    for k, v in classification.items():
        result[k] = v

    if not result.empty:
        _cache_dir.mkdir(parents=True, exist_ok=True)
        result.to_file(cache_file, driver="GPKG")
    if output_path:
        _write(result, output_path)

    return result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _empty_gdf(crs: str, output_path) -> gpd.GeoDataFrame:
    gdf = gpd.GeoDataFrame(geometry=[], crs=crs)
    if output_path:
        _write(gdf, output_path)
    return gdf


def _write(gdf: gpd.GeoDataFrame, output_path: Path | str) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ext = output_path.suffix.lower()
    if ext == ".shp":
        driver = "ESRI Shapefile"
    elif ext in (".gpkg", ".geopackage"):
        driver = "GPKG"
    elif ext == ".geojson":
        driver = "GeoJSON"
    else:
        driver = "GPKG"
    gdf.to_file(output_path, driver=driver)


def _cache_path(
    cache_dir: Path, target_layer, guide_layer, classifier_layer,
    classifier_link_attr, classification, extent, crs, spatial_buffer_m,
) -> Path:
    parts = [
        target_layer, guide_layer, classifier_layer or "",
        classifier_link_attr or "",
        json.dumps(classification, sort_keys=True),
        str(extent), crs, str(spatial_buffer_m),
    ]
    key = hashlib.md5("||".join(parts).encode()).hexdigest()[:12]
    safe = target_layer.replace(":", "_").replace("/", "_")
    return cache_dir / f"atkis_{safe}_{key}.gpkg"


def _guide_cache_path(
    cache_dir: Path, guide_layer, classifier_layer,
    classifier_link_attr, classification, extent, crs,
) -> Path:
    parts = [
        guide_layer, classifier_layer or "", classifier_link_attr or "",
        json.dumps(classification, sort_keys=True),
        str(extent), crs,
    ]
    key = hashlib.md5("||".join(parts).encode()).hexdigest()[:12]
    safe = guide_layer.replace(":", "_").replace("/", "_")
    return cache_dir / f"atkis_guide_{safe}_{key}.gpkg"


def _parse_wfs_response(content: bytes) -> ET.Element:
    """Parse a WFS XML payload, raising on an OGC ExceptionReport.

    A WFS may answer with an error XML body even after HTTP 200 (or an error
    body that slipped past ``raise_for_status``).  Read as GetFeature it would
    yield zero members → a silent "0 Features im Extent" and an empty
    classification flowing on as a result.  Detect the ExceptionReport
    namespace-tolerantly (root tag ends on ``ExceptionReport``; pattern from
    ``wms.py`` parse_feature_info_gml) and raise ``RuntimeError`` with the
    service message instead.
    """
    root = ET.fromstring(content)
    tag_root = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    if tag_root.endswith("ExceptionReport") or tag_root.endswith("ServiceExceptionReport"):
        msg = ""
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag.endswith("ExceptionText") or tag.endswith("ServiceException"):
                if elem.text and elem.text.strip():
                    msg = elem.text.strip()
                    break
        if not msg:
            # Fall back to the exceptionCode attribute of the first Exception
            for elem in root.iter():
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if tag.endswith("Exception"):
                    msg = elem.get("exceptionCode", "") or elem.get("code", "")
                    if msg:
                        break
        msg = msg[:300] if msg else "WFS returned an OGC ExceptionReport (no message)"
        raise RuntimeError(f"WFS-Dienstfehler statt Features: {msg}")
    return root


def _wfs_download_geopandas(
    wfs_url: str, layer: str, extent, crs: str
) -> gpd.GeoDataFrame:
    """Use GeoPandas/OGR for a vanilla WFS download (no xlink extraction)."""
    wfs_uri = f"WFS:{wfs_url}"
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Field with same name")
        gdf = gpd.read_file(wfs_uri, layer=layer, bbox=extent)
    if gdf.crs is None:
        gdf = gdf.set_crs(crs)
    elif str(gdf.crs) != crs:
        gdf = gdf.to_crs(crs)
    return gdf


def _download_features_with_xlinks(
    wfs_url: str,
    layer: str,
    extent: tuple[float, float, float, float],
    crs: str,
    *,
    link_attr: str | None = None,
) -> tuple[gpd.GeoDataFrame, dict[str, str]]:
    """One-pass XML download → (gdf with geometry+attrs, {gml_id: xlink_urn}).

    Parses GML directly because GeoPandas/OGR drops xlink relations.
    Supports LineString / Curve+LineStringSegment / MultiCurve geometries
    (the common types in ATKIS line layers).
    """
    bbox_str = f"{extent[0]},{extent[1]},{extent[2]},{extent[3]},{crs}"
    r = requests.get(wfs_url, params={
        "service": "WFS", "request": "GetFeature", "version": "2.0.0",
        "typeNames": layer, "srsName": crs, "bbox": bbox_str,
        "count": "10000",
    }, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()

    root = _parse_wfs_response(r.content)
    rows = []
    xlinks: dict[str, str] = {}
    for member in root.iter(f"{{{NS['wfs']}}}member"):
        if len(member) == 0:
            continue
        f = member[0]
        gml_id = f.get(f"{{{NS['gml']}}}id", "")
        # Geometry
        pos_el = f.find("adv:position", NS)
        geom = _parse_gml_geometry(pos_el) if pos_el is not None else None
        row = {"gml_id": gml_id, "geometry": geom}
        # Scalar attributes (skip metadata + relation tags)
        for child in f:
            tag = child.tag.split("}")[-1]
            if tag in (
                "lebenszeitintervall", "modellart", "position",
                "identifier", "anlass", "fachdatenobjekt",
            ):
                continue
            # xlink-only elements have no text — handled separately below
            if child.text and child.text.strip():
                row[tag] = child.text.strip()
        rows.append(row)
        if link_attr:
            link_el = f.find(f"adv:{link_attr}", NS)
            if link_el is not None:
                href = link_el.get(f"{{{NS['xlink']}}}href", "")
                xlinks[gml_id] = href.replace("urn:adv:oid:", "")

    if not rows:
        return gpd.GeoDataFrame(columns=["gml_id", "geometry"], crs=crs), xlinks

    gdf = gpd.GeoDataFrame(rows, crs=crs)
    return gdf, xlinks


def _fetch_features_by_ids(
    wfs_url: str, layer: str, urns: list[str], batch_size: int = 80,
) -> dict[str, dict[str, str]]:
    """Fetch features by gml:id (URN). Returns ``{urn: {attr: text}}``.

    Issues batched WFS 2.0 ``RESOURCEID`` KVP queries — efficient for hundreds
    of URNs.  Geometry is ignored; only scalar text attributes are returned.

    Uses the KVP parameter instead of an XML ResourceId filter: some
    infrastructures (e.g. LGLN Niedersachsen behind the niedersachsen.de WAF)
    reject XML in query parameters outright.
    """
    if not urns:
        return {}
    out: dict[str, dict[str, str]] = {}
    for i in range(0, len(urns), batch_size):
        batch = urns[i:i + batch_size]
        r = requests.get(wfs_url, params={
            "service": "WFS", "request": "GetFeature", "version": "2.0.0",
            "typeNames": layer, "resourceID": ",".join(batch),
        }, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        root = _parse_wfs_response(r.content)
        for member in root.iter(f"{{{NS['wfs']}}}member"):
            if len(member) == 0:
                continue
            f = member[0]
            gml_id = f.get(f"{{{NS['gml']}}}id", "")
            attrs: dict[str, str] = {}
            for child in f:
                tag = child.tag.split("}")[-1]
                if tag in (
                    "lebenszeitintervall", "modellart", "position",
                    "identifier", "anlass", "fachdatenobjekt",
                ):
                    continue
                if child.text and child.text.strip():
                    attrs[tag] = child.text.strip()
            out[gml_id] = attrs
    return out


def _attrs_match(attrs: dict[str, str], conditions: dict[str, str]) -> bool:
    """All conditions match (case-insensitive substring)."""
    for k, v in conditions.items():
        actual = attrs.get(k, "")
        if str(v).lower() not in str(actual).lower():
            return False
    return True


def _attribute_filter_gdf(
    gdf: gpd.GeoDataFrame, conditions: dict[str, str]
) -> gpd.GeoDataFrame:
    """Filter rows where all conditions match (case-insensitive substring)."""
    mask = None
    for k, v in conditions.items():
        if k not in gdf.columns:
            print(f"[atkis] Warning: column '{k}' missing in guide layer", flush=True)
            continue
        col_match = (
            gdf[k].fillna("").astype(str).str.lower()
                .str.contains(str(v).lower(), regex=False)
        )
        mask = col_match if mask is None else (mask & col_match)
    return gdf[mask].copy() if mask is not None else gdf.copy()


# ---------------------------------------------------------------------------
# GML parsing — minimal, sufficient for ATKIS line/polygon geometries
# ---------------------------------------------------------------------------


def _parse_gml_geometry(el: ET.Element):
    """Return a Shapely geometry from the children of an ATKIS <position> element.

    Handles LineString, Curve (segments → LineStringSegment), MultiCurve,
    Polygon, MultiSurface.  Returns None on unsupported types.
    """
    if el is None:
        return None
    # Iterate children — first geometry-bearing element wins
    for child in el:
        tag = child.tag.split("}")[-1]
        if tag == "LineString":
            return _line_from_poslist(child)
        if tag == "Curve":
            return _curve_to_line(child)
        if tag == "MultiCurve":
            lines = []
            for cm in child.iter():
                t = cm.tag.split("}")[-1]
                if t == "LineString":
                    g = _line_from_poslist(cm)
                    if g:
                        lines.append(g)
                elif t == "Curve":
                    g = _curve_to_line(cm)
                    if g:
                        lines.append(g)
            return MultiLineString(lines) if len(lines) > 1 else (
                lines[0] if lines else None
            )
        if tag == "Polygon":
            return _polygon_from_gml(child)
        if tag == "MultiSurface":
            polys = []
            for pm in child.iter():
                if pm.tag.split("}")[-1] == "Polygon":
                    p = _polygon_from_gml(pm)
                    if p:
                        polys.append(p)
            return MultiPolygon(polys) if len(polys) > 1 else (
                polys[0] if polys else None
            )
    return None


def _line_from_poslist(el: ET.Element) -> LineString | None:
    """Build LineString from the first <gml:posList> beneath this element."""
    for d in el.iter():
        if d.tag.split("}")[-1] == "posList":
            coords = _parse_pos_list(d.text)
            if len(coords) >= 2:
                return LineString(coords)
    return None


def _curve_to_line(el: ET.Element) -> LineString | None:
    """Concatenate all LineStringSegment posLists into one LineString."""
    coords: list[tuple[float, float]] = []
    for seg in el.iter():
        if seg.tag.split("}")[-1] == "LineStringSegment":
            for d in seg.iter():
                if d.tag.split("}")[-1] == "posList":
                    seg_coords = _parse_pos_list(d.text)
                    if not coords:
                        coords.extend(seg_coords)
                    else:
                        # Avoid duplicating the joining vertex
                        coords.extend(seg_coords[1:] if seg_coords else [])
    return LineString(coords) if len(coords) >= 2 else None


def _polygon_from_gml(el: ET.Element) -> Polygon | None:
    """Build Polygon from <gml:exterior> + optional <gml:interior> rings."""
    exterior = None
    interiors: list[list[tuple[float, float]]] = []
    for child in el.iter():
        tag = child.tag.split("}")[-1]
        if tag in ("exterior", "interior"):
            ring_coords = None
            for d in child.iter():
                if d.tag.split("}")[-1] == "posList":
                    ring_coords = _parse_pos_list(d.text)
                    break
            if ring_coords:
                if tag == "exterior":
                    exterior = ring_coords
                else:
                    interiors.append(ring_coords)
    if exterior and len(exterior) >= 4:
        return Polygon(exterior, interiors)
    return None


def _parse_pos_list(text: str | None) -> list[tuple[float, float]]:
    """Parse a GML posList: 'x1 y1 x2 y2 ...' (2D)."""
    if not text:
        return []
    nums = text.split()
    coords: list[tuple[float, float]] = []
    for i in range(0, len(nums) - 1, 2):
        try:
            coords.append((float(nums[i]), float(nums[i + 1])))
        except ValueError:
            continue
    return coords


__all__ = ["fetch_classified_features", "fetch_classified_guide"]
