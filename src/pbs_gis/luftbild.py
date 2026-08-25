"""
Vegetation aus einem RGB-Luftbild trennen — und die Grenzen dieser Trennung ausweisen.

Für eine Versiegelungsbilanz ist die Grenze zwischen Grünfläche und befestigter
Fläche die tragende. Sie ist im Echtfarben-DOP zuverlässig zu ziehen: Vegetation
reflektiert im Grünkanal deutlich stärker als jeder Belag, und der Excess-Green-
Index (2G − R − B) macht das ohne Infrarotkanal messbar.

Was NICHT geht, und warum dieses Modul es nicht anbietet: Asphalt von Pflaster
von wassergebundener Decke zu trennen. Gemessen an einem realen DOP20 landen alle
drei im selben Helligkeitscluster — ausgeblichener Asphalt ist heller als neues
Pflaster —, und die Fuge, an der man Pflaster erkennt, ist bei 20 cm Bodenauflösung
kleiner als ein Pixel. Eine Funktion dafür lieferte plausible und falsche Zahlen.

Der zweite Vorbehalt hat eine Lösung statt eines Verzichts: Was unter Baumkronen
liegt, ist im Sommerbild unsichtbar, und ein Vegetationsindex zählt es fälschlich
als Grün — ein überschirmter Weg wird zur Wiese. Wo die Kronen vermessen sind
(:func:`pbs_gis.dxf.block_circles`), lässt sich diese Zone benennen statt raten:
das Ergebnis trägt sie als eigene Klasse, nicht als stille Fehlzuordnung.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np

# Excess Green: 2G − R − B, auf die Helligkeit normiert. Ohne Normierung wandert
# der Wert mit der Belichtung, und eine Schwelle aus einem Bild passt im nächsten
# nicht mehr.
_EPS = 1.0


def excess_green(rgb: np.ndarray) -> np.ndarray:
    """Excess-Green-Index je Pixel, normiert auf die Summe der Kanäle.

    Args:
        rgb: Array (Höhe, Breite, 3), Werte 0–255.

    Returns:
        Array (Höhe, Breite) mit Werten etwa in [−1, 1]; Vegetation liegt hoch.
    """
    r, g, b = (rgb[..., i].astype(np.float32) for i in range(3))
    summe = np.clip(r + g + b, _EPS, None)
    return (2.0 * g - r - b) / summe


def vegetationsschwelle(exg: np.ndarray, gueltig: np.ndarray | None = None) -> float:
    """Schwelle zwischen Vegetation und Belag, aus dem Bild selbst bestimmt.

    Otsu über die tatsächliche Verteilung — eine feste Konstante trifft ein
    anderes Bildjahr, eine andere Jahreszeit oder einen anderen Dienst nicht.

    Args:
        exg: Excess-Green-Werte.
        gueltig: Maske der auswertbaren Pixel; ``None`` wertet alle aus.

    Returns:
        Schwellwert; darüber gilt als Vegetation.
    """
    from skimage.filters import threshold_otsu

    werte = exg[gueltig] if gueltig is not None else exg.ravel()
    werte = werte[np.isfinite(werte)]
    if werte.size == 0:
        raise ValueError("Keine auswertbaren Pixel für die Schwellenbestimmung")
    return float(threshold_otsu(werte))


def vegetationsflaechen(
    raster_path: str | Path,
    *,
    clip: gpd.GeoSeries | gpd.GeoDataFrame | None = None,
    schwelle: float | None = None,
    glaettung_m: float = 0.25,
    min_flaeche_m2: float = 2.0,
) -> tuple[gpd.GeoDataFrame, dict]:
    """Vegetations- und Belagsflächen aus einem RGB-Luftbild.

    Args:
        raster_path: Georeferenziertes RGB-Bild (GeoTIFF).
        clip: Fläche, auf die zugeschnitten wird (etwa der Geltungsbereich).
        schwelle: Excess-Green-Schwelle. ``None`` bestimmt sie per Otsu aus dem
            Bild und gibt sie zurück — reproduzierbar durch Übergeben des Werts.
        glaettung_m: Radius für Schliessen und Öffnen der Maske. Grosszügig
            gewählt kostet er echte Fläche: ein 0,5-m-Radius entfernte an einem
            realen DOP20 37 % der Vegetation, weil Bankette und Rasenstreifen
            schmaler sind als das Strukturelement. Der Verlust steht deshalb im
            Rückgabewert und ist zu prüfen, nicht zu glauben.
        min_flaeche_m2: Flächen darunter fallen weg.

    Returns:
        ``(GeoDataFrame, info)``. Der Rahmen trägt ``klasse`` (``"Vegetation"`` /
        ``"befestigt"``), ``flaeche_m2``, ``geometry``; ``info`` trägt
        ``schwelle``, die Vegetationsfläche vor und nach der Glättung sowie
        ``glaettungsverlust_pct``.
    """
    import rasterio
    from rasterio.features import shapes
    from rasterio.mask import mask as rio_mask
    from scipy import ndimage
    from shapely.geometry import shape

    with rasterio.open(raster_path) as src:
        crs = src.crs
        if clip is not None:
            geoms = list(clip.geometry) if hasattr(clip, "geometry") else list(clip)
            bild, transform = rio_mask(src, geoms, crop=True, filled=True, nodata=0)
        else:
            bild, transform = src.read(), src.transform
        pixelgroesse = abs(transform.a)

    if bild.shape[0] < 3:
        raise ValueError(f"RGB erwartet, Bild hat {bild.shape[0]} Band/Bänder")

    rgb = np.transpose(bild[:3], (1, 2, 0))
    gueltig = rgb.sum(axis=2) > 0
    exg = excess_green(rgb)

    if schwelle is None:
        schwelle = vegetationsschwelle(exg, gueltig)

    vegetation = (exg > schwelle) & gueltig
    pixelflaeche = pixelgroesse ** 2
    roh_m2 = float(vegetation.sum()) * pixelflaeche

    radius_px = max(int(round(glaettung_m / pixelgroesse)), 1)
    struktur = np.ones((radius_px, radius_px), bool)
    # Erst schliessen, dann öffnen: umgekehrt löscht das Öffnen schmale Streifen,
    # bevor das Schliessen sie zusammenhängend machen könnte.
    vegetation = ndimage.binary_closing(vegetation, struktur)
    vegetation = ndimage.binary_opening(vegetation, struktur)
    geglaettet_m2 = float(vegetation.sum()) * pixelflaeche

    klassen = np.where(gueltig, np.where(vegetation, 1, 2), 0).astype(np.int16)

    geoms, werte = [], []
    for geom, val in shapes(klassen, mask=klassen > 0, transform=transform):
        geoms.append(shape(geom))
        werte.append("Vegetation" if int(val) == 1 else "befestigt")

    gdf = gpd.GeoDataFrame({"klasse": werte}, geometry=geoms, crs=crs)
    gdf = gdf[gdf.area >= min_flaeche_m2].reset_index(drop=True)
    gdf["flaeche_m2"] = gdf.area

    vektor_m2 = float(gdf.loc[gdf["klasse"] == "Vegetation", "flaeche_m2"].sum())
    info = {
        "schwelle": float(schwelle),
        "vegetation_roh_m2": roh_m2,
        "vegetation_geglaettet_m2": geglaettet_m2,
        "vegetation_vektor_m2": vektor_m2,
        "glaettungsverlust_pct": (100.0 * (roh_m2 - vektor_m2) / roh_m2) if roh_m2 else 0.0,
    }
    return gdf[["klasse", "flaeche_m2", "geometry"]], info
