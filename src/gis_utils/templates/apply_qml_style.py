"""apply_qml_style — Apply a saved QML style to a layer in the open QGIS project.

Standalone template for applying carefully-curated QGIS styles
(``.qml``) to layers programmatically.  Common pattern: keep a central
``styles/`` directory with QMLs maintained for your bureau (e.g.
``bab_pufferzonen.qml``, ``schutzgebiete_ffh.qml``, ``modulflaechen.qml``)
and apply them per-project via this template.

The template is **opt-in to a live QGIS session** — if QGIS is not
running (or the ``[qgis]`` extra is not installed), the template is a
silent no-op and the workflow proceeds normally.

The runner already auto-applies *sibling* ``.qml`` files (e.g.
``Geodaten/foo.gpkg`` + ``Geodaten/foo.qml``) when ``qgis_open`` /
``GIS_WORKFLOW_QGIS_OPEN`` is active.  Use this template explicitly
when:

* The QML lives **elsewhere** (central styles directory, not next to
  the layer file).
* You want to apply a specific style as its own workflow step (for
  documentation / reproducibility).

Example workflow.yaml::

    - name: Pufferzonen stylen
      template: apply_qml_style
      params:
        layer: Geodaten/bab_pufferzonen.gpkg
        qml: ~/dev/Gunther-Schulz/PBS-Templates/styles/bab_pufferzonen.qml
"""

from __future__ import annotations

from pathlib import Path

from gis_utils.templates import register


@register(
    "apply_qml_style",
    description=(
        "Apply a saved QML style to a layer in the running QGIS project "
        "(opt-in via QGIS bridge; silent no-op without QGIS)"
    ),
    params=["layer", "qml"],
)
def apply_qml_style(
    params: dict, project_dir: Path, output_path: Path | None
) -> bool:
    """Apply a QML style to the matching layer in the open QGIS project.

    Params:
        layer: Path to the layer file (relative or absolute).  Source-path
            matching is by ``startswith`` so GeoPackages with multiple
            inner layers all get the same style applied.
        qml: Path to the QML file (relative or absolute).  If it doesn't
            exist, the template logs a warning and returns True (so the
            workflow continues); think "graceful skip" — the QML doesn't
            block the pipeline.

    Returns True on success or graceful skip; False only on outright error.
    """
    from gis_utils import qgis_bridge

    layer_path = (project_dir / params["layer"]).resolve()
    qml_path_raw = params["qml"]
    qml_path = Path(qml_path_raw).expanduser()
    if not qml_path.is_absolute():
        qml_path = (project_dir / qml_path).resolve()

    if not qml_path.is_file():
        print(f"  [skip] QML not found: {qml_path}")
        return True  # graceful skip — workflow continues

    if not qgis_bridge.is_available():
        print("  [skip] QGIS not running; style will be applied next time")
        return True

    n = qgis_bridge.apply_qml(layer_path, qml_path)
    if n == 0:
        print(f"  [info] No layer in QGIS project matches {layer_path.name}")
        # Not a failure — layer might just not be loaded yet.
    return True
