"""Export — die eingepasste Geometrie je Klasse als GeoPackage-Layer.

Ergebnis der Einpassung ist EIN Transform für den ganzen Blattverbund und
je Blatt seine Lage im gemeinsamen Rahmen. Ein Zug ``P`` in Blattmetern von
Blatt ``p`` landet damit bei ``T(P − Lage_p)`` im Ziel-CRS — dieselbe Kette
für Leitung und Katastersignatur, sonst stimmt die Leitung nicht mehr zu der
Signatur, an der sie eingepasst wurde.

Je Zug werden geschrieben: ``blatt``, ``lageklasse`` (``anker`` = eigene
Züge sitzen auf der Referenz · ``kette`` = Lage nur über den Blattverbund
belegt, eine Schätzung) und ``laenge_m`` (Länge im Ziel-CRS). Die je-Blatt-
Züge bleiben je Blatt: Überlappbereiche benachbarter Blätter stehen doppelt
darin, und die Zusammenführung ist ein eigener, bewusster Schritt.

Example
-------
>>> from pbs_gis.einpassung import export_gpkg
>>> export_gpkg("einpassung_25833.gpkg",                         # doctest: +SKIP
...             layers={"leitung": {p: sv.leitung for p, sv in sheets.items()},
...                     "katastersignatur": {p: sv.kataster
...                                          for p, sv in sheets.items()}},
...             transform=T, positions=lagen, lageklasse=klasse,
...             crs="EPSG:25833")
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np

from pbs_gis.einpassung.fit import apply_points
from pbs_gis.einpassung.pdfvektor import EinpassungError, chain_length
from pbs_gis.georef import SimilarityTransform

__all__ = ["LAGEKLASSEN", "export_gpkg"]

#: Zulässige Lageklassen je Blatt (Skill ``lageplan-einpassung``).
LAGEKLASSEN = ("anker", "kette")


def export_gpkg(
    path,
    *,
    layers: Mapping[str, Mapping[int, Sequence[np.ndarray]]],
    transform: SimilarityTransform,
    positions: Mapping[int, np.ndarray],
    lageklasse: Mapping[int, str],
    crs: str,
):
    """Die Züge je Klasse eingepasst in EIN GeoPackage schreiben, ein Layer je Klasse.

    Args:
        path: Ziel-GeoPackage. Es darf noch nicht existieren — ein Layer, der
            von einem früheren Lauf übrig bliebe, sähe aus wie ein Ergebnis
            dieses Laufs.
        layers: Layername -> Blatt -> Züge in Blattmetern (``(n, 2)``-Arrays,
            y nach oben, Ursprung linke untere Blattecke), z. B.
            ``SheetVectors.classes[...]`` je Blatt.
        transform: der Endtransform des Verbunds (Blattrahmen -> Ziel-CRS).
        positions: Blatt -> Lage im gemeinsamen Rahmen; der Zug geht als
            ``P − Lage_p`` in den Transform.
        lageklasse: Blatt -> ``"anker"`` oder ``"kette"``.
        crs: Ziel-CRS, z. B. ``"EPSG:25833"`` — ausdrücklich, kein Default.

    Returns:
        Layername -> geschriebener GeoDataFrame.

    Raises:
        EinpassungError: die Datei existiert; ein Blatt mit Zügen hat keine
            Lage oder keine (oder eine unbekannte) Lageklasse; ein Zug hat
            weniger als zwei Stützpunkte; ein Layer bekäme keinen einzigen
            Zug — ein leerer Layer sähe aus wie eine Fehlanzeige der Sparte.
    """
    import geopandas as gpd
    from shapely.geometry import LineString

    path = Path(path)
    if path.exists():
        raise EinpassungError(
            f"{path} existiert — export_gpkg schreibt nur in eine neue Datei; "
            "die alte bewusst entfernen oder einen neuen Namen wählen.")

    rows_by_layer: dict[str, list[dict]] = {}
    for name, per_sheet in layers.items():
        rows = []
        for p, chains in sorted(per_sheet.items()):
            if not len(chains):
                continue
            if p not in positions:
                raise EinpassungError(
                    f"Layer {name!r}: Blatt {p} trägt Züge, aber keine Lage in "
                    "positions — ohne Lage hat der Zug keinen Ort.")
            if p not in lageklasse:
                raise EinpassungError(
                    f"Layer {name!r}: Blatt {p} hat keine Lageklasse — "
                    f"zulässig: {LAGEKLASSEN}.")
            if lageklasse[p] not in LAGEKLASSEN:
                raise EinpassungError(
                    f"Blatt {p}: Lageklasse {lageklasse[p]!r} unbekannt — "
                    f"zulässig: {LAGEKLASSEN}.")
            lage = np.asarray(positions[p], float).reshape(2)
            for i, P in enumerate(chains):
                P = np.asarray(P, float).reshape(-1, 2)
                if len(P) < 2:
                    raise EinpassungError(
                        f"Layer {name!r}, Blatt {p}, Zug {i}: {len(P)} Stützpunkt(e) "
                        "— eine Linie braucht mindestens zwei.")
                W = apply_points(transform, P - lage)
                rows.append(dict(blatt=int(p), lageklasse=lageklasse[p],
                                 laenge_m=chain_length(W), geometry=LineString(W)))
        if not rows:
            raise EinpassungError(
                f"Layer {name!r} bekäme keinen einzigen Zug — eine Klasse ohne "
                "Leitung auf allen Blättern ist ein Befund, kein leerer Layer.")
        rows_by_layer[name] = rows

    out = {}
    for name, rows in rows_by_layer.items():
        gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)
        gdf.to_file(path, layer=name, driver="GPKG")
        out[name] = gdf
    return out
