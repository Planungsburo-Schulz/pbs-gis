"""layout_from_qpt — Generate a print layout from a QGIS .qpt template.

Loads a saved QGIS layout template, populates items by ID (text labels,
map item layers + extent), and exports to PDF/PNG/SVG/JPG.

The standard PBS pattern:

1. Build a layout once in QGIS (DIN A3 quer, schriftfeld, logo, legend,
   scale bar, north arrow) — manual cartographic work where visual
   judgement matters.
2. Assign meaningful **item IDs** in the Item Properties panel
   (e.g. ``main_map``, ``title``, ``subtitle``, ``legend``).
3. Save as ``.qpt`` template (Layout Manager → Save as Template).
4. From then on, fill the template per project via this workflow step
   — title text, project-specific extent, layer selection, output PDF.

**Requires** a running QGIS instance with the qgis-mcp plugin
(``[qgis]`` extra installed).  Without QGIS the template logs a
warning and the workflow continues with a graceful failure.

Example workflow.yaml::

    - name: Lageplan PDF erzeugen
      template: layout_from_qpt
      params:
        template: ~/dev/Gunther-Schulz/PBS-Templates/Lageplan-A3.qpt
        layout_name: "Wölzow Lageplan A3"
        items:
          title: "PV-Anlage Wölzow — Privilegierungsplan"
          subtitle: "B-Plan Nr. 25-01, Stand Mai 2026"
          source: "Quelle: ATKIS Basis-DLM, ALKIS, eigene Erhebung"
        map:
          id: main_map
          theme: "Wölzow_Übersicht"   # PBS convention: map follows this map theme
          layers: ["Modulflächen", "BAB-Pufferzonen", "Projektfläche"]
          extent_from_layer: "Projektfläche"
          buffer_m: 200
        format: pdf
        dpi: 300
      output: Output/Karten/Lageplan_A3.pdf
"""

from __future__ import annotations

from pathlib import Path

from gis_utils.templates import register


@register(
    "layout_from_qpt",
    description=(
        "Load a QGIS layout template (.qpt), populate items + map, "
        "export to PDF/PNG/SVG/JPG (requires running QGIS via qgis-mcp)"
    ),
    params=["template", "layout_name", "items", "map", "format", "dpi"],
)
def layout_from_qpt(
    params: dict, project_dir: Path, output_path: Path | None
) -> bool:
    """Render a print layout from a QGIS .qpt template."""
    from gis_utils import qgis_bridge

    if not qgis_bridge.is_available():
        print("  [ERROR] QGIS not running — cannot render layout from .qpt")
        return False

    template_raw = params["template"]
    qpt = Path(template_raw).expanduser()
    if not qpt.is_absolute():
        qpt = (project_dir / qpt).resolve()
    if not qpt.is_file():
        print(f"  [ERROR] Template file not found: {qpt}")
        return False

    layout_name = params.get("layout_name") or qpt.stem
    items = params.get("items") or {}
    map_cfg = params.get("map") or {}
    fmt = params.get("format", "pdf").lower()
    dpi = int(params.get("dpi", 300))

    if output_path is None:
        print("  [ERROR] output: must be set in workflow.yaml step")
        return False
    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    code = _build_pyqgis_code(
        qpt=str(qpt),
        layout_name=layout_name,
        items=items,
        map_cfg=map_cfg,
        out=str(out),
        fmt=fmt,
        dpi=dpi,
    )
    resp = qgis_bridge.execute(code)
    if resp is None:
        return False
    inner = resp.get("result") if isinstance(resp, dict) else None
    if isinstance(inner, dict):
        # Surface console output. Legacy qgis-mcp returned a ``result`` dict with
        # ok/msg; the current plugin returns {executed, stdout, stderr} and drops
        # the ``result`` variable — so file-existence is the success signal.
        for _k in ("stdout", "stderr"):
            _v = inner.get(_k)
            if _v and str(_v).strip():
                print("  " + str(_v).strip().replace("\n", "\n  "))
        if "ok" in inner:  # legacy protocol
            if inner.get("msg"):
                print(f"  {inner['msg']}")
            return bool(inner["ok"])
    return out.is_file()


def _build_pyqgis_code(
    *,
    qpt: str,
    layout_name: str,
    items: dict,
    map_cfg: dict,
    out: str,
    fmt: str,
    dpi: int,
) -> str:
    """Generate the PyQGIS code that loads, populates, and exports the layout."""
    import json

    return f"""
from qgis.core import (
    QgsPrintLayout, QgsReadWriteContext, QgsProject, QgsLayoutItemMap,
    QgsLayoutItemLabel, QgsRectangle, QgsLayoutExporter,
)
from qgis.PyQt.QtXml import QDomDocument

_qpt_path = {qpt!r}
_layout_name = {layout_name!r}
_items = {json.dumps(items)}
_map_cfg = {json.dumps(map_cfg)}
_out = {out!r}
_fmt = {fmt!r}
_dpi = {dpi}

_msg = []
_ok = False

try:
    # 1) Load + create layout
    with open(_qpt_path, "r", encoding="utf-8") as _f:
        _xml = _f.read()
    _doc = QDomDocument()
    _doc.setContent(_xml)
    _layout = QgsPrintLayout(QgsProject.instance())
    # PyQGIS returns (items, ok); only a hard failure raises below
    _loaded = _layout.loadFromTemplate(_doc, QgsReadWriteContext())
    _ok_load = _loaded[1] if isinstance(_loaded, tuple) else bool(_loaded)
    if not _ok_load:
        raise RuntimeError("loadFromTemplate failed")
    _layout.setName(_layout_name)

    # If a layout with this name already exists, remove it first (re-export friendly)
    _mgr = QgsProject.instance().layoutManager()
    _existing = _mgr.layoutByName(_layout_name)
    if _existing is not None:
        _mgr.removeLayout(_existing)
    _mgr.addLayout(_layout)

    # 2) Populate text labels by ID
    for _id, _text in _items.items():
        _it = _layout.itemById(_id)
        if isinstance(_it, QgsLayoutItemLabel):
            _it.setText(str(_text))
        elif _it is None:
            _msg.append(f"  [warn] label item id '{{_id}}' not found in template")
        else:
            _msg.append(f"  [warn] item '{{_id}}' is not a label")

    # 3) Populate map item if configured
    if _map_cfg:
        _map_id = _map_cfg.get("id") or "main_map"
        _map = _layout.itemById(_map_id)
        if not isinstance(_map, QgsLayoutItemMap):
            raise RuntimeError(f"Map item id '{{_map_id}}' not found / wrong type in template")

        # Theme-follow (PBS convention): drive map visibility from a named
        # map theme so canvas edits never change the exported layout.
        _theme = _map_cfg.get("theme")
        if _theme:
            _map.setFollowVisibilityPreset(True)
            _map.setFollowVisibilityPresetName(_theme)
        else:
            _msg.append("  [warn] map.theme not set — layout map follows explicit "
                        "layers / canvas, not a map theme (PBS convention)")

        # Layer selection (by name) — seeds the legend; theme drives visibility
        _layer_names = _map_cfg.get("layers") or []
        if _layer_names:
            _layers = []
            for _ln in _layer_names:
                _matches = QgsProject.instance().mapLayersByName(_ln)
                if _matches:
                    _layers.append(_matches[0])
                else:
                    _msg.append(f"  [warn] layer '{{_ln}}' not in QGIS project; skipped")
            if _layers:
                _map.setLayers(_layers)

        # Extent: explicit > extent_from_layer + buffer_m
        _extent = _map_cfg.get("extent")
        if _extent:
            _xmin, _ymin, _xmax, _ymax = _extent
            _map.setExtent(QgsRectangle(_xmin, _ymin, _xmax, _ymax))
        else:
            _ext_layer_name = _map_cfg.get("extent_from_layer")
            if _ext_layer_name:
                _matches = QgsProject.instance().mapLayersByName(_ext_layer_name)
                if _matches:
                    _ext = _matches[0].extent()
                    _buf = float(_map_cfg.get("buffer_m", 0))
                    if _buf:
                        _ext = QgsRectangle(
                            _ext.xMinimum() - _buf, _ext.yMinimum() - _buf,
                            _ext.xMaximum() + _buf, _ext.yMaximum() + _buf,
                        )
                    _map.setExtent(_ext)

    # 4) Export
    _exp = QgsLayoutExporter(_layout)
    if _fmt == "pdf":
        _settings = QgsLayoutExporter.PdfExportSettings()
        _settings.dpi = _dpi
        _r = _exp.exportToPdf(_out, _settings)
    elif _fmt == "svg":
        _settings = QgsLayoutExporter.SvgExportSettings()
        _settings.dpi = _dpi
        _r = _exp.exportToSvg(_out, _settings)
    else:
        # png / jpg → image export
        _settings = QgsLayoutExporter.ImageExportSettings()
        _settings.dpi = _dpi
        _r = _exp.exportToImage(_out, _settings)

    if _r != QgsLayoutExporter.Success:
        raise RuntimeError(f"Layout export returned status {{_r}}")

    _ok = True
    _msg.insert(0, f"  Layout exported: {{_out}}")

except Exception:
    import traceback as _tb
    _msg.append("  [ERROR] " + _tb.format_exc())

# print so the message survives plugins that only return stdout/stderr;
# keep ``result`` for the legacy qgis-mcp protocol
print("\\n".join(_msg))
result = {{"ok": _ok, "msg": "\\n".join(_msg)}}
"""
