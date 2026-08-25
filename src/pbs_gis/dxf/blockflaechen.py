"""
Symbolblöcke einer Vermessung als Flächen lesen — Baumkronen, Schirme, Radien.

Ein Vermesser trägt eine Baumkrone nicht als Polygon ein, sondern als Symbolblock,
dessen SKALIERUNG den gemessenen Radius trägt. Für eine Bilanz ist das eine
Flächenangabe: sie sagt, welcher Teil des Gebiets im Luftbild überschirmt und
damit nicht beurteilbar ist — die Information, die dem Bild fehlt und die die
Aufnahme hat.

Der Radius steht nicht im Block, sondern im Verhältnis von Blockdefinition und
Einfügeskalierung. Deshalb wird die Definition ausgemessen statt ein Einheitskreis
angenommen: ein Block, dessen Grundfigur Radius 1 hat, ergibt bei Skalierung 8 acht
Meter — einer mit Radius 0,5 vier. Die Verwechslung halbiert oder verdoppelt jede
Fläche, ohne dass etwas auffällt.
"""

from __future__ import annotations

import math
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point

from pbs_gis.dxf.read import CadReadError, read_cad


def block_basisradius(doc, block_name: str) -> float:
    """Radius der Blockdefinition, gemessen an ihrer Geometrie.

    Returns:
        Grösster Abstand einer Stützstelle vom Blockursprung.

    Raises:
        CadReadError: Block fehlt oder trägt keine ausmessbare Geometrie.
    """
    from ezdxf import path as ezpath

    if block_name not in doc.blocks:
        raise CadReadError(
            f"Block {block_name!r} nicht in der Zeichnung "
            f"(vorhanden: {len(doc.blocks)} Blöcke)"
        )

    rmax = 0.0
    for e in doc.blocks.get(block_name):
        try:
            p = ezpath.make_path(e)
        except Exception:
            continue
        for v in p.flattening(0.05):
            rmax = max(rmax, math.hypot(v.x, v.y))

    if rmax <= 0:
        raise CadReadError(
            f"Block {block_name!r} hat keine ausmessbare Geometrie — "
            "Radius nicht bestimmbar"
        )
    return rmax


def block_circles(
    dxf_path: str | Path,
    block_name: str,
    *,
    crs: str,
    strip_zone_shift: float = 0.0,
    min_radius_m: float = 0.0,
) -> gpd.GeoDataFrame:
    """Blockeinfügungen als Kreisflächen, Radius aus Definition × Skalierung.

    Args:
        dxf_path: Quellzeichnung (``.dxf``/``.dwg``).
        block_name: Name des Symbolblocks, etwa ``"BAUMKR"``.
        crs: CRS der Zeichnungskoordinaten.
        strip_zone_shift: Betrag, der von X abgezogen wird (UTM-Zonenpräfix).
        min_radius_m: Einfügungen mit kleinerem Radius fallen weg.

    Returns:
        GeoDataFrame mit ``radius_m``, ``flaeche_m2`` und Kreisflächen.

    Raises:
        CadReadError: Der Block fehlt oder hat keine Einfügung.
    """
    doc = read_cad(dxf_path)
    basis = block_basisradius(doc, block_name)

    radien, geoms = [], []
    for e in doc.modelspace():
        if e.dxftype() != "INSERT" or e.dxf.name != block_name:
            continue
        skala = abs(float(e.dxf.xscale or 1.0))
        radius = basis * skala
        if radius < min_radius_m:
            continue
        mitte = Point(e.dxf.insert.x - strip_zone_shift, e.dxf.insert.y)
        radien.append(radius)
        geoms.append(mitte.buffer(radius))

    if not geoms:
        raise CadReadError(
            f"Keine Einfügung des Blocks {block_name!r} gefunden — "
            "eine leere Rückgabe wäre von einem Tippfehler nicht zu unterscheiden"
        )

    gdf = gpd.GeoDataFrame({"radius_m": radien}, geometry=geoms, crs=crs)
    gdf["flaeche_m2"] = gdf.area
    return gdf[["radius_m", "flaeche_m2", "geometry"]]
