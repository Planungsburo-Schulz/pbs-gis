"""WFS download: fetch vector features directly from a WFS service.

Much simpler than WMS vectorization — no raster download, no color detection.
Use this when a WFS endpoint is available for the data source.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

import geopandas as gpd
from pyproj import CRS, Transformer

CACHE_DIR_NAME = "download_cache"

# --- Quell-Stand (FB 3.34b) --------------------------------------------------
# Neben jede Download-Datei legt download() die Beistell-Datei
# ``<datei>.quellstand.json`` = {quell_stand, dienst, abgerufen}: den Stand, den
# der Dienst für seine Daten publiziert (CSW-Metadatensatz hinter der
# MetadataURL der GetCapabilities). Nach einem echten Abruf mit lesbarem Stand
# wird er zusätzlich im Register der zuletzt gesehenen Stände je Dienst
# vermerkt. ABGLEICH: das Register-Format definiert
# ``pbs_projekt.schema.QuellstandRegister`` (pbs-projekt liest es im check
# §Quell-Stand); hier wird aus eigener Kopie geschrieben, ohne Laufzeit-
# Abhängigkeit — der Abgleich-Test (tests/test_wfs_quellstand.py) validiert
# eine echte Emission gegen das maßgebliche Modell.
QUELLSTAND_SUFFIX = ".quellstand.json"
QUELLSTAND_SCHEMA_VERSION = 1  # Spiegel von pbs_projekt.schema.QUELLSTAND_SCHEMA_VERSION
# Je HTTP-Anfrage der Stand-Ermittlung (GetCapabilities, Metadatensatz); der
# Download selbst wartet nie länger als zweimal so lange auf sie.
QUELLSTAND_TIMEOUT_S = 10
_REGISTER_RELPFAD = Path("pbs-gis") / "quellstand.json"


def _http_get(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def _local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _dienst(url: str) -> str:
    """Dienst-Kennung: die übergebene URL ohne Query-String, sonst wörtlich."""
    return url.split("?")[0]


def _href(elem: ET.Element) -> str | None:
    """URL eines MetadataURL-Elements: ``xlink:href`` (WFS 2.0), sonst ein
    ``URL``-Kind (INSPIRE ``inspire_common:MetadataUrl``), sonst der Text
    (WFS 1.1)."""
    for key, value in elem.attrib.items():
        if _local(key) == "href" and value.strip():
            return value.strip()
    for child in elem:
        if _local(child.tag) == "URL" and (child.text or "").strip():
            return child.text.strip()
    if (elem.text or "").strip():
        return elem.text.strip()
    return None


def _metadata_url(caps: bytes, layer: str) -> str | None:
    """MetadataURL des angefragten FeatureTypes, sonst die des Dienstes."""
    root = ET.fromstring(caps)
    want = layer.split(":")[-1]
    feature_type_urls: set[int] = set()
    for ft in root.iter():
        if _local(ft.tag) != "FeatureType":
            continue
        name = ""
        urls = []
        for child in ft:
            t = _local(child.tag)
            if t == "Name":
                name = (child.text or "").strip()
            elif t.lower() == "metadataurl":
                urls.append(child)
                feature_type_urls.add(id(child))
        if (name == layer or name.split(":")[-1] == want) and urls:
            for u in urls:
                href = _href(u)
                if href:
                    return href
    for elem in root.iter():
        if _local(elem.tag).lower() == "metadataurl" and id(elem) not in feature_type_urls:
            href = _href(elem)
            if href:
                return href
    return None


def _iso_datum(text: str | None) -> date | None:
    if not text or len(text.strip()) < 10:
        return None
    return date.fromisoformat(text.strip()[:10])


def _datum_aus_csw(record: bytes) -> date | None:
    """Stand aus einem ISO-19139-Metadatensatz: das jüngste ``CI_Date`` mit
    ``dateType = revision`` der Daten-Zitation
    (identificationInfo/*/citation/CI_Citation/date), sonst ``dateStamp``."""
    root = ET.fromstring(record)
    md = root if _local(root.tag) == "MD_Metadata" else next(
        (e for e in root.iter() if _local(e.tag) == "MD_Metadata"), None)
    if md is None:
        return None

    def kinder(elem, name):
        return [c for c in elem if _local(c.tag) == name]

    def datum_wert(elem):
        for c in elem:
            if _local(c.tag) in ("Date", "DateTime"):
                return _iso_datum(c.text)
        return None

    revisionen = []
    for ii in kinder(md, "identificationInfo"):
        for ident in ii:
            for cit in kinder(ident, "citation"):
                for ci in kinder(cit, "CI_Citation"):
                    for d in kinder(ci, "date"):
                        for cd in kinder(d, "CI_Date"):
                            typ = None
                            for dt in kinder(cd, "dateType"):
                                for code in dt:
                                    typ = code.get("codeListValue") or (code.text or "").strip()
                            if typ != "revision":
                                continue
                            for dv in kinder(cd, "date"):
                                wert = datum_wert(dv)
                                if wert is not None:
                                    revisionen.append(wert)
    if revisionen:
        return max(revisionen)
    for ds in kinder(md, "dateStamp"):
        return datum_wert(ds)
    return None


def _ermittle_quell_stand(dienst: str, layer: str, version: str) -> str | None:
    """Publizierter Stand (YYYY-MM-DD) der Daten hinter ``dienst``/``layer``:
    GetCapabilities → MetadataURL → CSW-Metadatensatz. Was auf diesem Weg nicht
    lesbar ist — kein Netz, keine MetadataURL, kein Datum, unparsbar — ist
    ``None`` („konnte nicht prüfen"); der Download scheitert nie daran."""
    try:
        caps_url = dienst + "?" + urllib.parse.urlencode(
            {"SERVICE": "WFS", "REQUEST": "GetCapabilities", "VERSION": version})
        meta_url = _metadata_url(_http_get(caps_url, QUELLSTAND_TIMEOUT_S), layer)
        if meta_url is None:
            return None
        stand = _datum_aus_csw(_http_get(meta_url, QUELLSTAND_TIMEOUT_S))
        return stand.isoformat() if stand is not None else None
    except Exception:
        return None


def register_pfad() -> Path:
    """``$XDG_STATE_HOME/pbs-gis/quellstand.json``; ist XDG_STATE_HOME leer,
    ungesetzt oder relativ (XDG-Spec: relative Werte sind ungültig):
    ``~/.local/state/pbs-gis/quellstand.json`` — dieselbe Regel wie
    ``pbs_projekt.quellstand.register_pfad`` (Leser)."""
    basis = os.environ.get("XDG_STATE_HOME")
    if basis and Path(basis).is_absolute():
        return Path(basis) / _REGISTER_RELPFAD
    return Path.home() / ".local" / "state" / _REGISTER_RELPFAD


def _register_eintragen(dienst: str, quell_stand: str) -> None:
    """Setzt ``dienste[dienst]`` im Register (atomar: temp + rename). Ein
    vorhandenes, nicht verwertbares Register bleibt unberührt und wird mit
    EINER lauten Zeile gemeldet."""
    pfad = register_pfad()
    if pfad.exists():
        try:
            register = json.loads(pfad.read_text(encoding="utf-8"))
            if not (isinstance(register, dict)
                    and register.get("schema_version") == QUELLSTAND_SCHEMA_VERSION
                    and isinstance(register.get("dienste"), dict)):
                raise ValueError("kein Register der schema_version "
                                 f"{QUELLSTAND_SCHEMA_VERSION}")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            print(f"[wfs] FEHLER Quell-Stand-Register {pfad} nicht verwertbar "
                  f"({type(exc).__name__}) — unverändert gelassen, Stand "
                  f"{quell_stand} für {dienst} NICHT vermerkt", flush=True)
            return
    else:
        register = {"schema_version": QUELLSTAND_SCHEMA_VERSION, "dienste": {}}
    register["dienste"][dienst] = {
        "quell_stand": quell_stand,
        "gesehen": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_name(pfad.name + ".tmp")
    tmp.write_text(json.dumps(register, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, pfad)


def _beistell_pfad(datei: Path) -> Path:
    return datei.with_name(datei.name + QUELLSTAND_SUFFIX)


def _schreibe_beistell(datei: Path, quell_stand: str | None, dienst: str,
                       abgerufen: str | None) -> None:
    _beistell_pfad(datei).write_text(json.dumps(
        {"quell_stand": quell_stand, "dienst": dienst, "abgerufen": abgerufen},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
        # KVP BBOX in x,y (easting,northing) order — per WFS 2.0 spec the axis
        # order follows the CRS definition, and EPSG's projected CRS (e.g.
        # 25832/25833) are defined E,N. Verified against LGLN deegree services.
        minx, miny, maxx, maxy = bbox
        params["BBOX"] = f"{minx},{miny},{maxx},{maxy},{crs}" if crs else f"{minx},{miny},{maxx},{maxy}"

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
    extent_crs: str | None = None,
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
        extent: (minx, miny, maxx, maxy) bounding box filter in extent_crs.
        extent_crs: CRS of ``extent``. Default: the recipe's ``ausgabe_crs``
            if set, else ``crs``. An extent in another CRS than ``crs`` is
            transformed into ``crs`` before any request is sent.
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
        from pbs_gis.recipes import Recipe as _RecipeCls, load_recipe, resolve_connection
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

    dienst = _dienst(url)

    # A bbox in another CRS than the request's is not an error at the service:
    # it matches nothing and answers zero features. So a hand-given extent is
    # transformed here, before any of the request paths below sends it.
    if extent is not None:
        if extent_crs is None:
            extent_crs = (_recipe.ausgabe_crs if _recipe else None) or crs
        if CRS.from_user_input(extent_crs) != CRS.from_user_input(crs):
            extent = tuple(Transformer.from_crs(extent_crs, crs, always_xy=True)
                           .transform_bounds(*extent))

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
        elif extent and _recipe and _recipe.connection.get("bbox_kvp"):
            # Plain KVP BBOX GetFeature — for services where OGR's generated
            # FES filter breaks (e.g. LGLN boris_wfs: geometry property lives
            # in the adv: namespace, OGR addresses it as boris:position and
            # the server silently matches nothing). Opt-in per recipe via
            # connection.bbox_kvp: true.
            kvp_url = _wfs_get_feature_url(
                url, layer,
                version=version,
                bbox=extent,
                crs=crs or "",
                max_features=max_features,
            )
            _want = (layer or "").split(":")[-1]
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="Field with same name")
                try:
                    gdf = gpd.read_file(kvp_url, layer=_want) if _want else gpd.read_file(kvp_url)
                except Exception:
                    gdf = gpd.read_file(kvp_url)
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

        abgerufen = datetime.now().astimezone().isoformat(timespec="seconds")
        quell_stand = _ermittle_quell_stand(dienst, layer, version)
        _schreibe_beistell(cache_file, quell_stand, dienst, abgerufen)
        print(f"[wfs] Quell-Stand {dienst}: {quell_stand or 'nicht lesbar'}", flush=True)
        if quell_stand is not None:
            _register_eintragen(dienst, quell_stand)

    # --- Recipe post-processing pipeline ---
    if _recipe is not None:
        from pbs_gis.recipes import (
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
        # Die Ausgabe trägt den Stand des Abrufs, der den Cache erzeugte; ein
        # Cache ohne Beistell-Datei kennt ihn nicht.
        if _beistell_pfad(cache_file).exists():
            shutil.copyfile(_beistell_pfad(cache_file), _beistell_pfad(output_path))
        else:
            _schreibe_beistell(output_path, None, dienst, None)
        if not _cache_hit:
            print(f"[wfs] Written: {output_path}", flush=True)

    return gdf
