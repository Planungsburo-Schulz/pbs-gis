"""
Find commercial web-map tiles used where an official source exists.

Office rule: a map uses the official survey data of its state — DOP, ALKIS, ATKIS
— whenever such a source exists. Google, Bing and Esri tiles are a convenience
during exploration and a licence problem the moment a map leaves the office;
they are also not survey-accurate, so an area measured against them is measured
against a base nobody can cite.

The failure is quiet: a commercial layer added once as a quick backdrop stays in
the project file, gets carried into every later map, and nothing asks about it —
which is exactly what happened in project 26-06, where a Google layer sat in the
QGIS project from an earlier session and travelled unremarked through months of
work. Hence a check that reads the artefacts rather than a habit of remembering.

What counts as a hit is the tile HOST, not the layer's name: a layer called
"Luftbild" pointing at Google is the case worth catching.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

# Hosts that serve commercial tiles. Matched against the layer's data source
# string, so a renamed layer is still caught.
COMMERCIAL_HOSTS: dict[str, str] = {
    "google.com": "Google",
    "googleapis.com": "Google",
    "ggpht.com": "Google",
    "virtualearth.net": "Bing",
    "bing.com": "Bing",
    "arcgisonline.com": "Esri",
    "mapbox.com": "Mapbox",
    "here.com": "HERE",
}

# Marker a project may carry to declare a deliberate, reasoned exception. Placed
# in workflow.yaml as ``basemap_exception: "<reason>"``; an empty reason does not
# count — the point is the reason, not the key.
EXCEPTION_KEY = "basemap_exception"

_QGIS_SUFFIXES = frozenset({".qgs", ".qgz"})


@dataclass(frozen=True)
class BasemapHit:
    """One commercial tile source found in a project artefact."""

    file: Path
    provider: str
    layer_name: str
    source: str

    def __str__(self) -> str:
        return f"{self.file.name}: {self.provider} — Layer {self.layer_name!r}"


def _qgis_project_xml(path: Path) -> str:
    """Return the project XML, unzipping a .qgz container if needed."""
    if path.suffix.lower() == ".qgz":
        with zipfile.ZipFile(path) as zf:
            name = next((n for n in zf.namelist() if n.endswith(".qgs")), None)
            if name is None:
                return ""
            return zf.read(name).decode("utf-8", errors="replace")
    return path.read_text(encoding="utf-8", errors="replace")


def scan_text(text: str, source_file: Path) -> list[BasemapHit]:
    """Find commercial tile sources in one QGIS project's XML."""
    hits: list[BasemapHit] = []
    # <maplayer> ... <layername>X</layername> ... <datasource>Y</datasource>
    for block in re.findall(r"<maplayer\b.*?</maplayer>", text, re.DOTALL):
        ds = re.search(r"<datasource>(.*?)</datasource>", block, re.DOTALL)
        if ds is None:
            continue
        source = ds.group(1)
        provider = next(
            (label for host, label in COMMERCIAL_HOSTS.items() if host in source), None
        )
        if provider is None:
            continue
        name = re.search(r"<layername>(.*?)</layername>", block, re.DOTALL)
        hits.append(
            BasemapHit(
                file=source_file,
                provider=provider,
                layer_name=name.group(1) if name else "?",
                source=source[:200],
            )
        )
    return hits


def find_commercial_basemaps(project_dir: str | Path) -> list[BasemapHit]:
    """Scan a project's QGIS files for commercial tile sources.

    Args:
        project_dir: Project root. Every ``.qgs``/``.qgz`` below it is read.

    Returns:
        One hit per commercial layer found, in file order.
    """
    root = Path(project_dir)
    hits: list[BasemapHit] = []
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in _QGIS_SUFFIXES or not path.is_file():
            continue
        try:
            hits.extend(scan_text(_qgis_project_xml(path), path))
        except (OSError, zipfile.BadZipFile):
            continue
    return hits


def declared_exception(project_dir: str | Path) -> str | None:
    """Return the project's declared basemap exception, if it states a reason."""
    wf = Path(project_dir) / "workflow.yaml"
    if not wf.is_file():
        return None
    import yaml

    try:
        data = yaml.safe_load(wf.read_text()) or {}
    except yaml.YAMLError:
        return None
    reason = (data.get("project") or {}).get(EXCEPTION_KEY) or data.get(EXCEPTION_KEY)
    reason = (reason or "").strip() if isinstance(reason, str) else ""
    return reason or None


def official_aerial_recipes() -> list[tuple[str, str]]:
    """Official aerial-imagery recipes that could replace a commercial basemap.

    Returns:
        ``(name, description)`` pairs, sorted by name.
    """
    from pbs_gis.recipes import list_recipes

    out = []
    for recipe in list_recipes():
        tags = {t.lower() for t in getattr(recipe, "tags", [])}
        if {"dop", "luftbild", "orthophoto", "aerial"} & tags:
            out.append((recipe.name, (recipe.description or "").split("—")[0].strip()))
    return sorted(out)
