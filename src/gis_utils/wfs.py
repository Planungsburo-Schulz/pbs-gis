"""WFS download: fetch vector features directly from a WFS service.

Much simpler than WMS vectorization — no raster download, no color detection.
Use this when a WFS endpoint is available for the data source.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import geopandas as gpd

CACHE_DIR_NAME = "download_cache"


def _build_ogc_filter(conditions: dict[str, str]) -> str:
    """Build an OGC Filter XML string from key-value equality conditions.

    Args:
        conditions: Dict of {field_name: value} for equality matching.
                   Values starting with ~ are treated as LIKE patterns.

    Returns:
        OGC Filter XML string.
    """
    if not conditions:
        return ""

    parts = []
    for field, value in conditions.items():
        value_str = str(value)
        if value_str.startswith("~"):
            parts.append(
                f"<ogc:PropertyIsLike wildCard='%' singleChar='_' escapeChar='\\'>"
                f"<ogc:PropertyName>{field}</ogc:PropertyName>"
                f"<ogc:Literal>{value_str[1:]}</ogc:Literal>"
                f"</ogc:PropertyIsLike>"
            )
        else:
            parts.append(
                f"<ogc:PropertyIsEqualTo>"
                f"<ogc:PropertyName>{field}</ogc:PropertyName>"
                f"<ogc:Literal>{value_str}</ogc:Literal>"
                f"</ogc:PropertyIsEqualTo>"
            )

    if len(parts) == 1:
        body = parts[0]
    else:
        body = "<ogc:And>" + "".join(parts) + "</ogc:And>"

    return (
        '<ogc:Filter xmlns:ogc="http://www.opengis.net/ogc">'
        + body
        + "</ogc:Filter>"
    )


def _wfs_get_feature_url(
    url: str,
    layer: str,
    *,
    version: str = "2.0.0",
    ogc_filter: str = "",
    bbox: tuple | None = None,
    crs: str = "",
    max_features: int | None = None,
) -> str:
    """Build a WFS GetFeature URL with OGC filter parameters."""
    params = {
        "SERVICE": "WFS",
        "REQUEST": "GetFeature",
        "VERSION": version,
        "TYPENAMES" if version >= "2.0.0" else "TYPENAME": layer,
        "OUTPUTFORMAT": "application/gml+xml; version=3.2",
    }
    if crs:
        params["SRSNAME"] = crs
    if max_features:
        key = "COUNT" if version >= "2.0.0" else "MAXFEATURES"
        params[key] = str(max_features)
    if ogc_filter:
        params["FILTER"] = ogc_filter
    elif bbox:
        minx, miny, maxx, maxy = bbox
        params["BBOX"] = f"{miny},{minx},{maxy},{maxx},{crs}" if crs else f"{miny},{minx},{maxy},{maxx}"

    base = url.split("?")[0]
    return f"{base}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"


def _wfs_stored_query_url(
    url: str,
    stored_query_id: str,
    params: dict[str, str],
    *,
    version: str = "2.0.0",
) -> str:
    """Build a WFS GetFeature URL using a stored query."""
    base_params = {
        "SERVICE": "WFS",
        "REQUEST": "GetFeature",
        "VERSION": version,
        "STOREDQUERY_ID": stored_query_id,
    }
    base_params.update(params)
    base = url.split("?")[0]
    return f"{base}?{urllib.parse.urlencode(base_params, quote_via=urllib.parse.quote)}"


def _cache_key(layer: str, extent: tuple | None, crs: str, filter_hash: str = "") -> str:
    """Build a deterministic cache filename from request parameters."""
    parts = [layer, crs, filter_hash]
    if extent:
        parts.extend(f"{v:.0f}" for v in extent)
    raw = "_".join(parts)
    short_hash = hashlib.md5(raw.encode()).hexdigest()[:8]
    safe_layer = layer.replace("/", "_").replace("\\", "_").replace(":", "_")
    if extent:
        minx, miny, maxx, maxy = extent
        return f"{safe_layer}_{minx:.0f}_{miny:.0f}_{maxx:.0f}_{maxy:.0f}_{short_hash}.gpkg"
    return f"{safe_layer}_{short_hash}.gpkg"


def download(
    url: str,
    layer: str,
    *,
    extent: tuple[float, float, float, float] | None = None,
    input_boundary: Path | str | None = None,
    output_path: Path | str | None = None,
    crs: str | None = None,
    version: str = "2.0.0",
    max_features: int | None = None,
    cache_dir: Path | str | None = None,
    no_cache: bool = False,
    recipe: "str | Recipe | None" = None,
    recipe_dir: Path | str | None = None,
    filter: dict[str, str] | None = None,
    stored_query: str | None = None,
    stored_query_params: dict[str, str] | None = None,
) -> gpd.GeoDataFrame:
    """Download vector features from a WFS service.

    Args:
        url: WFS service URL.
        layer: Feature type name (e.g. 'adv:AX_Gebaeude').
        extent: (minx, miny, maxx, maxy) bounding box filter in crs.
        input_boundary: Shapefile/GeoPackage to derive extent from.
        output_path: Output file path (.gpkg or .shp). If None, no file written.
        crs: CRS for the request and output.
        version: WFS version (default '2.0.0').
        max_features: Limit number of features returned.
        cache_dir: Directory for cached downloads. Default: download_cache/ in cwd.
        no_cache: If True, skip cache and always download fresh.
        recipe: Recipe name or Recipe object for attribute mappings and post-processing.
        recipe_dir: Project directory for recipe search.
        filter: Dict of {field_name: value} for attribute filtering.
                Keys are resolved through recipe query_fields if available.
                Used as client-side filter after download.
        stored_query: WFS stored query ID for server-side filtering.
        stored_query_params: Parameters for the stored query.

    Returns:
        GeoDataFrame with downloaded features.
    """
    # --- Recipe resolution ---
    _recipe = None
    if recipe is not None:
        from gis_utils.recipes import Recipe as _RecipeCls, load_recipe, resolve_connection
        if isinstance(recipe, str):
            _recipe = load_recipe(recipe, project_dir=Path(recipe_dir) if recipe_dir else None)
        else:
            _recipe = recipe
        _conn = resolve_connection(_recipe)
        url = url or _conn.get("wfs_url") or _conn.get("wms_url", "")
        layer = layer or _conn.get("layer", "")
        crs = crs or _conn.get("crs")

    if not crs:
        raise ValueError("crs is required (e.g. 'EPSG:25833'). No silent defaults — wrong CRS causes silent data corruption.")

    # Resolve extent from input_boundary
    if extent is None and input_boundary is not None:
        boundary_gdf = gpd.read_file(input_boundary)
        boundary_gdf = boundary_gdf.to_crs(crs)
        extent = tuple(boundary_gdf.total_bounds)

    # Check if recipe connection specifies a bbox_stored_query
    # (some WFS services require stored queries instead of standard bbox)
    if extent and not stored_query and _recipe:
        bbox_sq = _recipe.connection.get("bbox_stored_query")
        if bbox_sq:
            stored_query = bbox_sq["id"]
            # Some servers (e.g. Thüringen adv_alkis_v2_wfs) prepend the
            # "urn:ogc:def:crs:EPSG::" prefix themselves and expect the bare
            # EPSG code; others want the full "EPSG:25833". Controlled per recipe
            # via bbox_stored_query.crs_param: asis (default) | epsg_code | urn.
            _crs_fmt = bbox_sq.get("crs_param", "asis")
            _code = crs.split(":")[-1]
            _crs_val = {"epsg_code": _code,
                        "urn": f"urn:ogc:def:crs:EPSG::{_code}"}.get(_crs_fmt, crs)
            stored_query_params = {
                "x1": str(extent[0]),
                "y1": str(extent[1]),
                "x2": str(extent[2]),
                "y2": str(extent[3]),
                "CRS": _crs_val,
            }
            extent = None  # handled by stored query now

    # --- Resolve attribute filter for client-side post-filtering ---
    _client_filter = {}
    if filter:
        query_fields = {}
        if _recipe and _recipe.detection.get("query_fields"):
            query_fields = _recipe.detection["query_fields"]
        for key, value in filter.items():
            wfs_field = query_fields.get(key, key)
            _client_filter[wfs_field] = value

    # Build filter hash for cache key
    hash_parts = []
    if _recipe and _recipe.exclude_tags:
        hash_parts.append(str(sorted(_recipe.exclude_tags.items())))
    if stored_query:
        hash_parts.append(stored_query)
    if stored_query_params:
        hash_parts.append(str(sorted(stored_query_params.items())))
    if _client_filter:
        hash_parts.append(str(sorted(_client_filter.items())))
    filter_hash = hashlib.md5("".join(hash_parts).encode()).hexdigest()[:6] if hash_parts else ""

    # --- Cache check ---
    _cache_dir = Path(cache_dir) if cache_dir else Path.cwd() / CACHE_DIR_NAME
    cache_file = _cache_dir / _cache_key(layer, extent, crs, filter_hash)
    _cache_hit = False

    if not no_cache and cache_file.exists():
        gdf = gpd.read_file(cache_file)
        _cache_hit = True
    else:
        print(f"[wfs] Downloading {layer}...", flush=True)
        if stored_query:
            print(f"[wfs] Stored query: {stored_query}", flush=True)
            if stored_query_params:
                print(f"[wfs] Params: {stored_query_params}", flush=True)
        if _client_filter:
            print(f"[wfs] Client filter: {_client_filter}", flush=True)
        if extent:
            print(f"[wfs] Extent ({crs}): {extent[0]:.0f},{extent[1]:.0f} — {extent[2]:.0f},{extent[3]:.0f}", flush=True)

        import warnings

        if stored_query:
            # Use WFS stored query — server-side filtering
            sq_url = _wfs_stored_query_url(
                url, stored_query,
                stored_query_params or {},
                version=version,
            )
            # A bbox stored query (e.g. adv 'ave-by-bbox') returns *all* feature
            # types in one GML; select the requested sub-layer by its local name
            # (namespace-stripped wfs_layer), falling back to the first layer.
            _want = (layer or "").split(":")[-1]
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="Field with same name")
                try:
                    gdf = gpd.read_file(sq_url, layer=_want) if _want else gpd.read_file(sq_url)
                except Exception:
                    gdf = gpd.read_file(sq_url)
        else:
            # Use geopandas OGR WFS driver
            wfs_uri = f"WFS:{url}"
            read_kwargs = {"layer": layer}
            if extent:
                read_kwargs["bbox"] = extent
            if max_features:
                read_kwargs["max_features"] = max_features

            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="Field with same name")
                gdf = gpd.read_file(wfs_uri, **read_kwargs)

        if gdf.crs is None:
            gdf = gdf.set_crs(crs)
        else:
            gdf = gdf.to_crs(crs)

        print(f"[wfs] Downloaded {len(gdf)} features", flush=True)

        # Apply client-side attribute filter
        if _client_filter and len(gdf) > 0:
            mask = None
            for col, value in _client_filter.items():
                if col not in gdf.columns:
                    continue
                col_match = gdf[col].astype(str).str.strip() == str(value)
                mask = col_match if mask is None else (mask & col_match)
            if mask is not None:
                n_before = len(gdf)
                gdf = gdf[mask].copy()
                if len(gdf) < n_before:
                    print(f"[wfs] Filtered {n_before} → {len(gdf)} features", flush=True)

        # Apply exclude_tags filter before caching
        if _recipe and _recipe.exclude_tags and len(gdf) > 0:
            drop_mask = None
            for col, pattern in _recipe.exclude_tags.items():
                if col not in gdf.columns:
                    continue
                col_match = gdf[col].fillna("").astype(str).str.match(pattern)
                drop_mask = col_match if drop_mask is None else (drop_mask | col_match)
            if drop_mask is not None:
                n_before = len(gdf)
                gdf = gdf[~drop_mask].copy()
                n_dropped = n_before - len(gdf)
                if n_dropped > 0:
                    print(f"[wfs] Excluded {n_dropped} features by exclude_tags filter", flush=True)

        _cache_dir.mkdir(parents=True, exist_ok=True)
        gdf.to_file(cache_file, driver="GPKG")

    # --- Recipe post-processing pipeline ---
    if _recipe is not None:
        from gis_utils.recipes import (
            apply_attribute_mappings,
            apply_column_mapping,
            apply_post_processing,
            load_and_run_hook,
        )
        _proj_dir = Path(recipe_dir) if recipe_dir else None

        if _recipe.attribute_mappings:
            apply_attribute_mappings(gdf, _recipe.attribute_mappings)
        if _recipe.post_processing:
            gdf = apply_post_processing(gdf, _recipe.post_processing)
        if _recipe.hooks:
            gdf = load_and_run_hook(_recipe.hooks, "post_process", gdf, _proj_dir)
        if _recipe.column_mapping:
            is_shp = output_path is not None and str(output_path).lower().endswith(".shp")
            gdf = apply_column_mapping(gdf, _recipe.column_mapping, is_shapefile=is_shp)

    # --- Write output ---
    if output_path is not None:
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
        if not _cache_hit:
            print(f"[wfs] Writing output ({driver})...", flush=True)
        gdf.to_file(output_path, driver=driver)
        if not _cache_hit:
            print(f"[wfs] Written: {output_path}", flush=True)

    return gdf
