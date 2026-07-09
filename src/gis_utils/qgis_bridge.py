"""Optional bridge to a running QGIS instance via the qgis-mcp plugin.

This module is **optional** — gis_utils stays headless-capable.  If the
optional ``[qgis]`` extra is not installed (or the ``qgis-mcp`` Python
package is otherwise unavailable), every public function in this module
becomes a silent no-op.  This lets workflow runners and templates
**opt in** to live-QGIS interaction without breaking on machines / CI
runs without QGIS.

Activation requires three things:

1. ``pip install gis-utils[qgis]`` (or ``pip install qgis-mcp`` directly).
2. The matching QGIS plugin (``QGIS MCP`` by N. Karasiak) installed and
   enabled in QGIS, with its socket server started
   (Plugins → QGIS MCP → Start Server, port 9876 by default).
3. A QGIS process actually running with that plugin active.

If any of those is missing, :func:`is_available` returns ``False`` and
all other helpers print a single warning and return without action.

Typical use cases
-----------------

- **Auto-reload after workflow step**: ``reload_paths(['Shape/Layer.shp'])``
  refreshes any layer in the open project whose data source matches.
- **Open generated layer**: ``add_layer('Shape/Result.gpkg')`` adds it to
  the current project (idempotent — won't add twice).
- **Custom code**: ``execute('iface.mapCanvas().refresh()')`` for ad-hoc
  PyQGIS calls.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Iterable

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 9876
PROBE_TIMEOUT_S = 0.3


def _import_client():
    """Lazy import of qgis_mcp.client.  Returns class or None."""
    try:
        from qgis_mcp.client import QgisMCPClient  # type: ignore[import-not-found]
        return QgisMCPClient
    except ImportError:
        return None


def is_available(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> bool:
    """Cheap reachability probe — does the QGIS plugin's socket server respond?

    Performs a quick TCP connect with a short timeout.  Does NOT load the
    qgis-mcp Python package.  Returns False if either the package or the
    plugin server is missing — both situations make all bridge calls
    silently no-op.
    """
    if _import_client() is None:
        return False
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT_S):
            return True
    except (OSError, socket.timeout):
        return False


def _connect(host: str, port: int):
    """Open a short-lived client connection.  Returns None if unavailable."""
    cls = _import_client()
    if cls is None:
        return None
    try:
        client = cls(host=host, port=port)
        client.connect()
        return client
    except Exception as exc:
        print(f"[qgis_bridge] Cannot connect to QGIS at {host}:{port}: {exc}")
        return None


def reload_paths(
    paths: Iterable[str | Path],
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> int:
    """Reload all layers in the open QGIS project whose source matches one
    of *paths*.

    Source-path matching uses ``layer.source().startswith(absolute_path)``
    so a single GeoPackage file matches all its layers, and shapefiles
    match by their absolute path prefix.

    Args:
        paths: Iterable of file paths (relative or absolute).  Relative
            paths are resolved against the current working directory.

    Returns:
        Number of layers reloaded.  ``0`` if QGIS is not reachable or
        no layers matched.
    """
    abs_paths = [str(Path(p).resolve()) for p in paths]
    if not abs_paths:
        return 0

    client = _connect(host, port)
    if client is None:
        return 0

    code = (
        "from qgis.core import QgsProject\n"
        f"_paths = {abs_paths!r}\n"
        "_n = 0\n"
        "for _l in QgsProject.instance().mapLayers().values():\n"
        "    _src = _l.source()\n"
        "    if any(_src.startswith(_p) for _p in _paths):\n"
        "        _l.dataProvider().reloadData()\n"
        "        _l.triggerRepaint()\n"
        "        _n += 1\n"
        "iface.mapCanvas().refresh() if _n else None\n"
        "result = _n\n"
    )
    try:
        resp = client.execute_code(code)
        # client returns dict {'status': 'success', 'result': {...}}
        n = 0
        if isinstance(resp, dict):
            inner = resp.get("result")
            if isinstance(inner, dict):
                n = int(inner.get("result", 0) or 0)
        if n:
            print(f"[qgis_bridge] Reloaded {n} layer(s) in QGIS")
        return n
    except Exception as exc:
        print(f"[qgis_bridge] reload_paths failed: {exc}")
        return 0
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


_RASTER_EXTENSIONS = {".tif", ".tiff", ".jp2", ".png", ".jpg", ".jpeg",
                      ".gif", ".bmp", ".vrt", ".asc", ".img", ".dem"}


def _is_raster_path(path: Path) -> bool:
    """Heuristic — is this a raster file based on extension?"""
    return path.suffix.lower() in _RASTER_EXTENSIONS


def add_layer(
    path: str | Path,
    *,
    name: str | None = None,
    provider: str = "ogr",
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> bool:
    """Add a vector layer to the open QGIS project (idempotent).

    If a layer with the same source path already exists it is reloaded
    instead of being added a second time.  Returns ``True`` on success
    (layer added or reloaded), ``False`` if QGIS not reachable.
    """
    p = Path(path).resolve()
    client = _connect(host, port)
    if client is None:
        return False
    try:
        # Check if already in project — reload instead of double-adding.
        existing = client.execute_code(
            f"from qgis.core import QgsProject\n"
            f"result = any(l.source().startswith({str(p)!r}) "
            f"for l in QgsProject.instance().mapLayers().values())\n"
        )
        already = False
        if isinstance(existing, dict):
            inner = existing.get("result")
            if isinstance(inner, dict):
                already = bool(inner.get("result"))
        if already:
            return reload_paths([p], host=host, port=port) > 0
        client.add_vector_layer(str(p), name=name, provider=provider)
        return True
    except Exception as exc:
        print(f"[qgis_bridge] add_layer failed for {p}: {exc}")
        return False
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


def add_raster(
    path: str | Path,
    *,
    name: str | None = None,
    provider: str = "gdal",
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> bool:
    """Add a raster layer to the open QGIS project (idempotent).

    If a layer with the same source path already exists it is reloaded
    instead of being added a second time.  Returns ``True`` on success.
    """
    p = Path(path).resolve()
    client = _connect(host, port)
    if client is None:
        return False
    try:
        existing = client.execute_code(
            f"from qgis.core import QgsProject\n"
            f"result = any(l.source().startswith({str(p)!r}) "
            f"for l in QgsProject.instance().mapLayers().values())\n"
        )
        already = False
        if isinstance(existing, dict):
            inner = existing.get("result")
            if isinstance(inner, dict):
                already = bool(inner.get("result"))
        if already:
            return reload_paths([p], host=host, port=port) > 0
        client.add_raster_layer(str(p), name=name, provider=provider)
        return True
    except Exception as exc:
        print(f"[qgis_bridge] add_raster failed for {p}: {exc}")
        return False
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


def add_wms_layer(
    wms_url: str,
    layer: str,
    *,
    style: str | None = None,
    crs: str | None = None,
    name: str | None = None,
    image_format: str = "image/png",
    apply_scale_visibility: bool = True,
    min_scale_denominator: float | None = None,
    max_scale_denominator: float | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> bool:
    """Add a WMS raster layer to the open QGIS project, respecting endpoint metadata.

    Fetches GetCapabilities to fill in defaults that the user did not pass:

    * ``style`` defaults to the layer's first own style (falls back to first
      inherited style); some servers reject empty styles or inherited-only
      ones, so this is more reliable than ``styles=`` in the URI.
    * ``crs`` defaults to the layer's first advertised CRS.
    * Scale-based visibility is configured from the layer's
      ``MinScaleDenominator`` / ``MaxScaleDenominator``. Caller can override
      either via keyword. Pass ``apply_scale_visibility=False`` to skip
      entirely.

    Note WMS↔QGIS scale naming inversion: the WMS Min/MaxScaleDenominator
    fields map to QGIS ``setMaximumScale`` / ``setMinimumScale`` respectively.

    Idempotent — re-adding the same ``(wms_url, layer, style)`` triple is a
    no-op (returns True). Identity tracked via a custom layer property
    ``gis_utils/wms_id``.

    The layer is inserted at the *bottom* of the layer tree — WMS layers are
    basemaps (TK, DOP, …) and always sit under the data layers.

    Returns ``True`` if the layer was added (or already present), ``False``
    if QGIS is not reachable or the layer could not be constructed.
    """
    from gis_utils.wms import get_wms_layer_metadata  # avoid import at module load

    meta = get_wms_layer_metadata(wms_url, layer)
    if meta is None:
        # fall back to caller-provided values; if essentials are missing, fail
        if style is None or crs is None:
            print(
                f"[qgis_bridge] add_wms_layer: cannot fetch GetCapabilities "
                f"for {wms_url} and no style/crs supplied"
            )
            return False
    else:
        if style is None:
            style = meta.default_style or ""
        if crs is None:
            crs = meta.available_crs[0] if meta.available_crs else "EPSG:4326"
        if min_scale_denominator is None:
            min_scale_denominator = meta.min_scale_denominator
        if max_scale_denominator is None:
            max_scale_denominator = meta.max_scale_denominator

    wms_id = f"{wms_url}::{layer}::{style or ''}"
    display_name = name or (meta.title if meta and meta.title else layer)
    uri = (
        f"crs={crs}&dpiMode=7&format={image_format}"
        f"&layers={layer}&styles={style or ''}&url={wms_url}"
    )

    # Convert WMS denominators to QGIS scale-API values (note inversion).
    qgis_max_scale = min_scale_denominator if (min_scale_denominator and apply_scale_visibility) else 0.0
    qgis_min_scale = max_scale_denominator if (max_scale_denominator and apply_scale_visibility) else 0.0
    enable_scale = apply_scale_visibility and (qgis_min_scale or qgis_max_scale)

    client = _connect(host, port)
    if client is None:
        return False
    code = (
        "from qgis.core import QgsProject, QgsRasterLayer\n"
        f"_wms_id = {wms_id!r}\n"
        f"_uri = {uri!r}\n"
        f"_name = {display_name!r}\n"
        f"_enable_scale = {bool(enable_scale)!r}\n"
        f"_min_scale = {float(qgis_min_scale)!r}\n"
        f"_max_scale = {float(qgis_max_scale)!r}\n"
        "_existing = None\n"
        "for _l in QgsProject.instance().mapLayers().values():\n"
        "    if _l.customProperty('gis_utils/wms_id') == _wms_id:\n"
        "        _existing = _l\n"
        "        break\n"
        "if _existing is not None:\n"
        "    result = {'added': False, 'already_present': True, 'id': _existing.id()}\n"
        "else:\n"
        "    _layer = QgsRasterLayer(_uri, _name, 'wms')\n"
        "    if not _layer.isValid():\n"
        "        result = {'added': False, 'error': _layer.error().summary() or 'invalid layer'}\n"
        "    else:\n"
        "        _layer.setCustomProperty('gis_utils/wms_id', _wms_id)\n"
        "        if _enable_scale:\n"
        "            _layer.setScaleBasedVisibility(True)\n"
        "            _layer.setMinimumScale(_min_scale)\n"
        "            _layer.setMaximumScale(_max_scale)\n"
        "        _proj = QgsProject.instance()\n"
        "        _proj.addMapLayer(_layer, False)\n"
        "        _proj.layerTreeRoot().addLayer(_layer)  # basemaps go to the bottom\n"
        "        result = {'added': True, 'id': _layer.id(), "
        "                  'min_scale': _min_scale, 'max_scale': _max_scale}\n"
    )
    try:
        resp = client.execute_code(code)
        inner = {}
        if isinstance(resp, dict):
            r = resp.get("result")
            if isinstance(r, dict):
                inner = r.get("result") if isinstance(r.get("result"), dict) else r
        if inner.get("error"):
            print(f"[qgis_bridge] add_wms_layer: {inner['error']}")
            return False
        return True
    except Exception as exc:
        print(f"[qgis_bridge] add_wms_layer failed: {exc}")
        return False
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


def open_path(
    path: str | Path,
    *,
    name: str | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> bool:
    """Auto-detect raster vs vector and add the appropriate layer type.

    Detection by file extension; vector is the default fallback so that
    GeoPackage / Shapefile / GeoJSON / DXF / etc. are correctly added.
    """
    p = Path(path)
    if _is_raster_path(p):
        return add_raster(p, name=name, host=host, port=port)
    return add_layer(p, name=name, host=host, port=port)


def apply_qml(
    layer_path: str | Path,
    qml_path: str | Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> int:
    """Apply a QML style to layer(s) in the open QGIS project whose source
    matches ``layer_path``.

    Returns number of layers styled.  ``0`` if QGIS not reachable, no
    matching layer in project, or QML file missing.  No-op if file
    doesn't exist (logged warning, not error).
    """
    lp = Path(layer_path).resolve()
    qp = Path(qml_path).resolve()
    if not qp.is_file():
        print(f"[qgis_bridge] QML not found: {qp}")
        return 0
    client = _connect(host, port)
    if client is None:
        return 0
    code = (
        "from qgis.core import QgsProject\n"
        f"_lp = {str(lp)!r}\n"
        f"_qml = {str(qp)!r}\n"
        "_n = 0\n"
        "for _l in QgsProject.instance().mapLayers().values():\n"
        "    if _l.source().startswith(_lp):\n"
        "        _ok, _msg = _l.loadNamedStyle(_qml)\n"
        "        if _ok:\n"
        "            _l.triggerRepaint()\n"
        "            _n += 1\n"
        "iface.mapCanvas().refresh() if _n else None\n"
        "result = _n\n"
    )
    try:
        resp = client.execute_code(code)
        n = 0
        if isinstance(resp, dict):
            inner = resp.get("result")
            if isinstance(inner, dict):
                n = int(inner.get("result", 0) or 0)
        if n:
            print(f"[qgis_bridge] Applied QML {qp.name} to {n} layer(s)")
        return n
    except Exception as exc:
        print(f"[qgis_bridge] apply_qml failed: {exc}")
        return 0
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


def _compose_layout_values(
    auftragnehmer_yaml: str | Path | None,
    project_yaml: str | Path | None,
    freeze_bearbeitungsstand: bool,
) -> tuple[dict[str, str], str | None]:
    """Build placeholder-values dict + logo path from PBS-Templates YAMLs.

    Schema (both YAMLs optional — missing fields become empty strings):

    auftragnehmer.yaml:
        name, firm_lines (list), address (list), logo_path

    project.yaml:
        title, project_name, body_text, bearbeiter,
        auftraggeber: {name, address (list)} OR plain string,
        bearbeitungsstand (optional literal — overrides expression / freeze flag)

    Bearbeitungsstand resolution order:
        1. project_yaml.bearbeitungsstand (literal string) if set
        2. today's date as literal if ``freeze_bearbeitungsstand=True``
        3. live QGIS now() expression (default)
    """
    import yaml

    a: dict = {}
    p: dict = {}
    if auftragnehmer_yaml and Path(auftragnehmer_yaml).is_file():
        a = yaml.safe_load(Path(auftragnehmer_yaml).read_text(encoding="utf-8")) or {}
    if project_yaml and Path(project_yaml).is_file():
        p = yaml.safe_load(Path(project_yaml).read_text(encoding="utf-8")) or {}

    # auftragnehmer_block
    auftragnehmer_lines: list[str] = []
    if a:
        auftragnehmer_lines.append("Auftragnehmer:")
        if a.get("name"):
            auftragnehmer_lines.append(str(a["name"]))
        auftragnehmer_lines.extend(str(x) for x in a.get("firm_lines", []) if x)
        auftragnehmer_lines.extend(str(x) for x in a.get("address", []) if x)

    # auftraggeber_block — accept structured dict or pre-formatted string
    auftraggeber_lines: list[str] = []
    ag = p.get("auftraggeber")
    if isinstance(ag, dict):
        auftraggeber_lines.append("Auftraggeber:")
        if ag.get("name"):
            auftraggeber_lines.append(str(ag["name"]))
        auftraggeber_lines.extend(str(x) for x in ag.get("address", []) if x)
    elif isinstance(ag, str) and ag.strip():
        auftraggeber_lines = ag.splitlines()

    # bearbeitungsstand resolution
    if "bearbeitungsstand" in p:
        bearb = str(p["bearbeitungsstand"])
    elif freeze_bearbeitungsstand:
        from datetime import date
        d = date.today()
        bearb = f"{d.day}.{d.month}.{d.year}"
    else:
        bearb = "[% concat(day(now()),'.', month(now()),'.', year( now()  ))%]"

    bearbeiter = str(p.get("bearbeiter", "")).strip()
    bearbeiter_line = f"Bearbeiter: {bearbeiter}" if bearbeiter else ""

    values: dict[str, str] = {
        "title": str(p.get("title", "")),
        "project_name": str(p.get("project_name", "")),
        "body_text": str(p.get("body_text", "")),
        "auftragnehmer_block": "\n".join(auftragnehmer_lines),
        "bearbeiter_line": bearbeiter_line,
        "bearbeitungsstand": bearb,
        "auftraggeber_block": "\n".join(auftraggeber_lines),
    }
    logo_path = a.get("logo_path") if a else None
    return values, logo_path


def define_map_theme(
    name: str,
    visible_layers: Iterable[str],
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> bool:
    """Create (or update) a QGIS **map theme** from a set of visible layers.

    A map theme is the PBS single-source-of-truth for "what a given map
    shows": which layers are visible and with which style. Print layouts
    then *follow* the theme (see ``map_theme=`` on
    :func:`render_layout_template` / the ``layout_from_qpt`` template's
    ``map.theme``), so editing the live canvas never silently changes an
    exported layout.

    Layers are matched by **name first, then id**; every project layer not
    listed is hidden in the theme. Idempotent — re-running updates the
    theme in place.

    Args:
        name: Map theme name (convention: ``<Project>_<Map-Type>`` or simply
            the layout name).
        visible_layers: Layer names (or ids) that should be visible in the
            theme. Everything else in the project is hidden.

    Returns ``True`` on success, ``False`` if QGIS is unreachable.

    Note: this builds a valid ``QgsLayerTreeModel`` before calling
    ``createThemeFromCurrentState`` — passing ``None`` there crashes the
    whole QGIS process, so never hand-roll that call without the model.
    """
    visible = list(visible_layers)
    code = (
        "from qgis.core import (QgsProject, QgsMapThemeCollection,\n"
        "    QgsLayerTreeModel)\n"
        f"_name = {name!r}\n"
        f"_visible = set({visible!r})\n"
        "_proj = QgsProject.instance()\n"
        "_root = _proj.layerTreeRoot()\n"
        "for _lyr in _proj.mapLayers().values():\n"
        "    _node = _root.findLayer(_lyr.id())\n"
        "    if _node is None:\n"
        "        continue\n"
        "    _node.setItemVisibilityChecked(_lyr.name() in _visible or _lyr.id() in _visible)\n"
        "_model = QgsLayerTreeModel(_root)\n"
        "_record = QgsMapThemeCollection.createThemeFromCurrentState(_root, _model)\n"
        "_coll = _proj.mapThemeCollection()\n"
        "if _coll.hasMapTheme(_name):\n"
        "    _coll.update(_name, _record)\n"
        "else:\n"
        "    _coll.insert(_name, _record)\n"
        "result = {'ok': True, 'theme': _name, 'themes': _coll.mapThemes()}\n"
    )
    client = _connect(host, port)
    if client is None:
        return False
    try:
        client.execute_code(code)
        print(f"[qgis_bridge] map theme {name!r} ← {len(visible)} visible layer(s)")
        return True
    except Exception as exc:
        print(f"[qgis_bridge] define_map_theme failed: {exc}")
        return False
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


def render_layout_template(
    template_path: str | Path,
    *,
    auftragnehmer_yaml: str | Path | None = None,
    project_yaml: str | Path | None = None,
    output_pdf: str | Path | None = None,
    output_png: str | Path | None = None,
    layout_name: str | None = None,
    map_theme: str | None = None,
    freeze_bearbeitungsstand: bool = False,
    extent_from_canvas: bool = True,
    dpi: int = 300,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> bool:
    """Load a .qpt template into the running QGIS, fill placeholders, export.

    Targets PBS-style layout templates (see Gunther-Schulz/PBS-Templates).
    Fills label items by Item ID using ``{{placeholder}}`` substitution.
    Expected placeholder set::

        {{title}}, {{project_name}}, {{body_text}},
        {{auftragnehmer_block}}, {{bearbeiter_line}},
        {{bearbeitungsstand}}, {{auftraggeber_block}}

    Resolves the ``auftragnehmer_logo`` picture item's path from the
    auftragnehmer YAML's ``logo_path`` field. Optionally sets the
    ``main_map`` item's extent to the live canvas extent (default).

    Args:
        template_path: Path to .qpt file.
        auftragnehmer_yaml: Optional path to a contractor identity YAML.
        project_yaml: Optional path to a per-project YAML.
        output_pdf / output_png: Optional export targets. If both None, the
            layout is added to the project but not exported.
        layout_name: Layout name in the QGIS layout manager. Defaults to
            the template's embedded name. Replaces a same-named layout if
            it already exists in the project.
        map_theme: Name of a QGIS map theme the ``main_map`` item should
            **follow** (``setFollowVisibilityPreset``). This is the PBS
            convention: a layout's map visibility is driven by a named map
            theme, NOT by the live canvas — so canvas edits never silently
            change an exported layout. Define the theme first (see
            :func:`define_map_theme`). When omitted, the map follows the
            current canvas state (legacy behaviour) and an advisory is
            printed.
        freeze_bearbeitungsstand: If True, replaces the ``{{bearbeitungsstand}}``
            placeholder with today's literal date instead of the live
            QGIS now() expression. Useful for archival exports.
        extent_from_canvas: If True (default), the main_map item's extent
            is reset to the current canvas — useful when reusing a template
            that was authored against a different project's coordinates.
        dpi: Export DPI.

    Returns ``True`` on success, ``False`` if QGIS unreachable or the
    template fails to load.
    """
    tpath = Path(template_path).resolve()
    if not tpath.is_file():
        print(f"[qgis_bridge] template not found: {tpath}")
        return False

    if not map_theme:
        print("[qgis_bridge] render_layout_template: no map_theme set — the layout "
              "map will follow the LIVE CANVAS state. PBS convention is to drive "
              "layout maps from a named map theme (define_map_theme(...) + "
              "map_theme=...) so canvas edits never change an exported layout.")

    values, logo_path = _compose_layout_values(
        auftragnehmer_yaml, project_yaml, freeze_bearbeitungsstand,
    )

    out_pdf = str(Path(output_pdf).resolve()) if output_pdf else None
    out_png = str(Path(output_png).resolve()) if output_png else None
    logo = logo_path or ""

    code = (
        "from qgis.core import (QgsProject, QgsPrintLayout, QgsReadWriteContext,\n"
        "    QgsLayoutItemLabel, QgsLayoutItemPicture, QgsLayoutItemMap,\n"
        "    QgsLayoutItemHtml, QgsLayoutItemLegend, QgsLayoutItemScaleBar,\n"
        "    QgsLayoutExporter)\n"
        "from qgis.PyQt.QtXml import QDomDocument\n"
        "from qgis.utils import iface\n"
        f"_template_path = {str(tpath)!r}\n"
        f"_layout_name = {layout_name!r}\n"
        f"_values = {values!r}\n"
        f"_logo_path = {logo!r}\n"
        f"_map_theme = {(map_theme or '')!r}\n"
        f"_extent_from_canvas = {bool(extent_from_canvas)!r}\n"
        f"_out_pdf = {out_pdf!r}\n"
        f"_out_png = {out_png!r}\n"
        f"_dpi = {int(dpi)!r}\n"
        "_proj = QgsProject.instance()\n"
        "_mgr = _proj.layoutManager()\n"
        "_layout = QgsPrintLayout(_proj)\n"
        "_doc = QDomDocument()\n"
        "with open(_template_path, 'r', encoding='utf-8') as _f:\n"
        "    _doc.setContent(_f.read())\n"
        "_ctx = QgsReadWriteContext()\n"
        "_loaded = _layout.loadFromTemplate(_doc, _ctx)\n"
        "if isinstance(_loaded, tuple):\n"
        "    _ok = bool(_loaded[1]) if len(_loaded) > 1 else True\n"
        "else:\n"
        "    _ok = bool(_loaded)\n"
        "if not _ok:\n"
        "    result = {'ok': False, 'error': 'loadFromTemplate failed'}\n"
        "else:\n"
        "    if _layout_name:\n"
        "        _layout.setName(_layout_name)\n"
        "    _name = _layout.name()\n"
        "    for _existing in list(_mgr.layouts()):\n"
        "        if _existing is not _layout and _existing.name() == _name:\n"
        "            _mgr.removeLayout(_existing)\n"
        "    for _it in _layout.items():\n"
        "        if isinstance(_it, QgsLayoutItemLabel):\n"
        "            _t = _it.text()\n"
        "            for _k, _v in _values.items():\n"
        "                _t = _t.replace('{{' + _k + '}}', _v)\n"
        "            _it.setText(_t)\n"
        "    for _mf in _layout.multiFrames():\n"          # title is often an HTML multiframe
        "        if isinstance(_mf, QgsLayoutItemHtml):\n"
        "            _h = _mf.html()\n"
        "            for _k, _v in _values.items():\n"
        "                _h = _h.replace('{{' + _k + '}}', _v)\n"
        "            _mf.setHtml(_h); _mf.loadHtml()\n"
        "    _logo_item = _layout.itemById('auftragnehmer_logo')\n"
        "    if isinstance(_logo_item, QgsLayoutItemPicture) and _logo_path:\n"
        "        _logo_item.setPicturePath(_logo_path)\n"
        "    _map = _layout.itemById('main_map')\n"
        "    if isinstance(_map, QgsLayoutItemMap):\n"
        "        if _map_theme:\n"
        "            _map.setFollowVisibilityPreset(True)\n"
        "            _map.setFollowVisibilityPresetName(_map_theme)\n"
        "        else:\n"
        "            _map.setFollowVisibilityPreset(False)\n"
        "            _map.setKeepLayerSet(False)\n"
        "        if _extent_from_canvas:\n"
        "            _map.zoomToExtent(iface.mapCanvas().extent())\n"
        "        _lg = _layout.itemById('legend')\n"          # re-seed legend from the map theme
        "        if isinstance(_lg, QgsLayoutItemLegend):\n"
        "            _lg.setLinkedMap(_map); _lg.setLegendFilterByMapEnabled(True)\n"
        "            _lg.setAutoUpdateModel(True); _lg.refresh(); _lg.setAutoUpdateModel(False)\n"
        "        _sb = _layout.itemById('scalebar')\n"        # re-fit scale bar to the real extent
        "        if isinstance(_sb, QgsLayoutItemScaleBar):\n"
        "            _sb.setLinkedMap(_map); _sb.applyDefaultSize()\n"
        "    _mgr.addLayout(_layout)\n"
        "    _exported = []\n"
        "    if _out_pdf:\n"
        "        _exp = QgsLayoutExporter(_layout)\n"
        "        _s = QgsLayoutExporter.PdfExportSettings()\n"
        "        _s.dpi = _dpi\n"
        "        _exp.exportToPdf(_out_pdf, _s)\n"
        "        _exported.append(_out_pdf)\n"
        "    if _out_png:\n"
        "        _exp = QgsLayoutExporter(_layout)\n"
        "        _s = QgsLayoutExporter.ImageExportSettings()\n"
        "        _s.dpi = _dpi\n"
        "        _exp.exportToImage(_out_png, _s)\n"
        "        _exported.append(_out_png)\n"
        "    result = {'ok': True, 'name': _name, 'exported': _exported}\n"
    )

    client = _connect(host, port)
    if client is None:
        return False
    try:
        resp = client.execute_code(code)
        inner: dict = {}
        if isinstance(resp, dict):
            r = resp.get("result")
            if isinstance(r, dict):
                inner = r.get("result") if isinstance(r.get("result"), dict) else r
        if not inner.get("ok"):
            print(f"[qgis_bridge] render_layout_template: {inner.get('error', 'unknown error')}")
            return False
        if inner.get("exported"):
            print(f"[qgis_bridge] rendered {inner.get('name')!r} → {inner['exported']}")
        return True
    except Exception as exc:
        print(f"[qgis_bridge] render_layout_template failed: {exc}")
        return False
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


def take_canvas_screenshot(
    out_path: str | Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> bool:
    """Save the current QGIS map canvas to a PNG file.

    Returns True on success, False if QGIS unreachable.  Uses PyQGIS
    canvas grab — fast, no re-render, captures whatever the user is
    looking at right now.
    """
    out = Path(out_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    client = _connect(host, port)
    if client is None:
        return False
    code = (
        f"_out = {str(out)!r}\n"
        "_canvas = iface.mapCanvas()\n"
        "_pixmap = _canvas.grab()\n"
        "_pixmap.save(_out)\n"
        "result = _out\n"
    )
    try:
        client.execute_code(code)
        return out.is_file()
    except Exception as exc:
        print(f"[qgis_bridge] take_canvas_screenshot failed: {exc}")
        return False
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


def execute(
    code: str,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
):
    """Execute arbitrary PyQGIS code in the running QGIS instance.

    Returns the value of the ``result`` variable set inside *code*, or
    ``None`` if QGIS is not reachable.
    """
    client = _connect(host, port)
    if client is None:
        return None
    try:
        return client.execute_code(code)
    except Exception as exc:
        print(f"[qgis_bridge] execute failed: {exc}")
        return None
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def auto_reload_enabled() -> bool:
    """Whether the workflow runner should auto-reload after each step.

    Controlled by environment variable ``GIS_WORKFLOW_QGIS_RELOAD``.
    """
    return _env_truthy("GIS_WORKFLOW_QGIS_RELOAD")


def auto_open_enabled() -> bool:
    """Whether the workflow runner should auto-open new outputs in QGIS.

    Controlled by environment variable ``GIS_WORKFLOW_QGIS_OPEN``.
    Independent of auto_reload — open=add new, reload=refresh existing.
    Both can be active at once (recommended for live development).
    """
    return _env_truthy("GIS_WORKFLOW_QGIS_OPEN")


def screenshots_dir() -> Path | None:
    """Directory for the workflow runner's auto-screenshot audit trail.

    Set via env var ``GIS_WORKFLOW_QGIS_SCREENSHOTS`` to a directory path.
    Returns Path or None when disabled / not set.
    """
    val = os.environ.get("GIS_WORKFLOW_QGIS_SCREENSHOTS", "").strip()
    return Path(val) if val else None
