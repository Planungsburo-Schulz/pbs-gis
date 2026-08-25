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

    ACHTUNG, gemessen an einem realen DOP20: Otsu SETZT eine zweigipflige
    Verteilung VORAUS. Ein Sommerbild mit viel Halbschatten, vergrasten Rändern
    und trockenem Rasen hat einen einzigen breiten Gipfel; die Schwelle landet
    dann in einer Flanke und schneidet mitten durch die Vegetation — an der
    Messung: 0,0464 statt der visuell richtigen ~0,03, wodurch eine ganze
    trockene Rasenfläche als Belag zählte. :func:`bimodal` prüft die
    Voraussetzung, und wo sie fehlt, gehört die Schwelle am Bild kalibriert.

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


def bimodal(werte: np.ndarray, *, bins: int = 60, mindesttiefe: float = 0.25) -> bool:
    """Hat die Verteilung zwei Gipfel mit einem Tal dazwischen?

    Die Voraussetzung von Otsu, und in Luftbildern oft nicht erfüllt. Ohne Tal
    ist jede automatische Schwelle eine Setzung — dann muss sie am Bild
    kalibriert werden, statt als Messwert aufzutreten.

    Args:
        werte: Eindimensionale Stichprobe.
        bins: Auflösung des Histogramms.
        mindesttiefe: Wie tief das Tal zwischen den beiden höchsten Gipfeln
            liegen muss, als Anteil des kleineren Gipfels.

    Returns:
        True, wenn zwei Gipfel durch ein hinreichend tiefes Tal getrennt sind.
    """
    h, _ = np.histogram(werte[np.isfinite(werte)], bins=bins)
    if h.sum() == 0:
        return False
    gipfel = [i for i in range(1, len(h) - 1) if h[i] >= h[i - 1] and h[i] > h[i + 1]]
    if len(gipfel) < 2:
        return False
    beste = sorted(gipfel, key=lambda i: h[i], reverse=True)[:2]
    a, b = sorted(beste)
    tal = h[a : b + 1].min()
    kleiner = min(h[a], h[b])
    return kleiner > 0 and (kleiner - tal) / kleiner >= mindesttiefe


def talschwelle(
    werte: np.ndarray,
    gewichte: np.ndarray | None = None,
    *,
    bins: int = 40,
    glaettung: int = 3,
) -> float:
    """Schwelle im TAL zwischen den beiden grössten Gipfeln der Verteilung.

    Der Unterschied zu Otsu ist kein Detail, sondern der Grund für dieses Modul:
    Otsu maximiert die Varianz zwischen zwei Klassen und landet damit in der
    FLANKE eines breiten Gipfels, wenn die Verteilung schief ist — gemessen an
    einem realen DOP20 bei +0,048, während das Tal bei +0,024 lag. Die Folge war
    sichtbar: eine ganze trockene Rasenfläche zählte als Belag. Die Talsuche
    trifft die Stelle, an der das Bild selbst die beiden Populationen trennt.

    Args:
        werte: Stichprobe, etwa der mittlere Vegetationsindex je Bildsegment.
        gewichte: Gewicht je Wert, üblicherweise die Segmentfläche — ohne sie
            zählt ein 3-m²-Splitter so viel wie eine 60-m²-Wiese.
        bins: Auflösung des Histogramms.
        glaettung: Fensterbreite der gleitenden Mittelung; unterdrückt
            Scheingipfel aus dem Zählrauschen.

    Returns:
        Schwellwert in der Talsohle.

    Raises:
        ValueError: Die Verteilung hat keine zwei Gipfel — dann gibt es kein Tal,
            und eine Schwelle wäre eine Setzung, die als Messwert aufträte.
    """
    from scipy.ndimage import uniform_filter1d

    werte = np.asarray(werte, dtype=float)
    endlich = np.isfinite(werte)
    werte = werte[endlich]
    gew = np.asarray(gewichte, dtype=float)[endlich] if gewichte is not None else None

    h, kanten = np.histogram(werte, bins=bins, weights=gew)
    hg = uniform_filter1d(h.astype(float), max(glaettung, 1))
    gipfel = [i for i in range(1, len(hg) - 1) if hg[i] >= hg[i - 1] and hg[i] > hg[i + 1]]
    if len(gipfel) < 2:
        raise ValueError(
            "Verteilung hat keine zwei Gipfel — kein Tal, also keine Schwelle. "
            "Die Grenze gehört hier am Bild gesetzt, nicht gerechnet."
        )
    a, b = sorted(sorted(gipfel, key=lambda i: hg[i], reverse=True)[:2])
    tal = a + int(np.argmin(hg[a : b + 1]))
    return float((kanten[tal] + kanten[tal + 1]) / 2)


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


def bildsegmente(
    raster_path: str | Path,
    *,
    clip: gpd.GeoSeries | gpd.GeoDataFrame | None = None,
    segmentgroesse_m2: float = 12.0,
    kompaktheit: float = 8.0,
    arbeitsaufloesung_m: float = 0.25,
) -> gpd.GeoDataFrame:
    """Das Bild in Segmente zerlegen, deren Ränder echten Bildkanten folgen.

    Eine pixelweise Schwelle liefert ausgefranste Vielecke mit Treppenkanten und
    Tausenden Stützpunkten — im GIS unbrauchbar, weil jede Fläche aussieht wie
    ein Rasterartefakt und keine Kante dort liegt, wo sie im Gelände liegt.
    Superpixel legen die Grenzen stattdessen an die Farbkanten des Bildes: der
    Rasenrand wird eine Linie, nicht eine Pixeltreppe.

    Der zweite Gewinn ist die Schwelle: jedes Segment trägt seinen mittleren
    Vegetationsindex als Attribut. Die Grenze Grün/Belag lässt sich damit im GIS
    verschieben und am Luftbild prüfen, statt beim Rechnen festgelegt und später
    geglaubt zu werden.

    Args:
        raster_path: Georeferenziertes RGB-Bild.
        clip: Fläche, auf die zugeschnitten wird.
        segmentgroesse_m2: Angestrebte Grösse eines Segments.
        kompaktheit: Höher = rundere Segmente, niedriger = kantentreuer.
        arbeitsaufloesung_m: Auf diese Pixelgrösse wird vor der Segmentierung
            heruntergerechnet; feiner kostet Zeit und bringt nur Rauschen.

    Returns:
        GeoDataFrame mit ``exg`` (mittlerer Vegetationsindex), ``helligkeit``,
        ``flaeche_m2`` und den Segmentflächen.
    """
    import rasterio
    from rasterio.features import shapes
    from rasterio.mask import mask as rio_mask
    from shapely.geometry import shape
    from skimage.segmentation import slic

    with rasterio.open(raster_path) as src:
        crs = src.crs
        if clip is not None:
            geoms = list(clip.geometry) if hasattr(clip, "geometry") else list(clip)
            bild, transform = rio_mask(src, geoms, crop=True, filled=True, nodata=0)
        else:
            bild, transform = src.read(), src.transform

    if bild.shape[0] < 3:
        raise ValueError(f"RGB erwartet, Bild hat {bild.shape[0]} Band/Bänder")

    schritt = max(int(round(arbeitsaufloesung_m / abs(transform.a))), 1)
    rgb = np.transpose(bild[:3], (1, 2, 0))[::schritt, ::schritt]
    gueltig = rgb.sum(axis=2) > 0
    if not gueltig.any():
        raise ValueError("Zuschnitt enthält keine Bilddaten")

    transform2 = transform * rasterio.Affine.scale(schritt, schritt)
    pixelflaeche = abs(transform2.a * transform2.e)
    n_segmente = max(int(gueltig.sum() * pixelflaeche / segmentgroesse_m2), 1)

    seg = slic(rgb / 255.0, n_segments=n_segmente, compactness=kompaktheit,
               start_label=1, mask=gueltig, channel_axis=2)

    exg = excess_green(rgb.astype(np.float32))
    hell = rgb.astype(np.float32).mean(axis=2)

    ids = np.unique(seg[seg > 0])
    exg_je_id = {int(i): float(exg[seg == i].mean()) for i in ids}
    hell_je_id = {int(i): float(hell[seg == i].mean()) for i in ids}

    geoms, sid = [], []
    for geom, val in shapes(seg.astype(np.int32), mask=seg > 0, transform=transform2):
        geoms.append(shape(geom))
        sid.append(int(val))

    gdf = gpd.GeoDataFrame(
        {"segment": sid,
         "exg": [exg_je_id[i] for i in sid],
         "helligkeit": [hell_je_id[i] for i in sid]},
        geometry=geoms, crs=crs,
    )
    gdf = gdf.dissolve(by="segment", as_index=False, aggfunc="first")
    gdf["flaeche_m2"] = gdf.area
    return gdf[["segment", "exg", "helligkeit", "flaeche_m2", "geometry"]]


def vegetationssegmente(
    raster_path: str | Path,
    *,
    clip: gpd.GeoSeries | gpd.GeoDataFrame | None = None,
    schwelle: float | None = None,
    unsicherheitsband: float = 0.005,
    segmentgroesse_m2: float = 12.0,
    **segment_kwargs,
) -> tuple[gpd.GeoDataFrame, dict]:
    """Grünfläche gegen befestigt, als saubere Flächen mit ausgewiesener Grauzone.

    Der empfohlene Weg. Er verbindet die beiden Erkenntnisse aus der Arbeit an
    einem realen DOP20: Bildsegmente statt Pixel (sonst sind die Ränder
    Rastertreppen mit tausenden Stützpunkten, im GIS unbrauchbar), und die
    Talschwelle statt Otsu (das trifft bei schiefen Verteilungen die Flanke und
    zählt trockenen Rasen als Belag).

    Segmente dicht an der Schwelle werden nicht zugeordnet, sondern als
    ``unsicher`` ausgewiesen: dort entscheidet ein Blick aufs Bild, nicht die
    vierte Nachkommastelle eines Index.

    Args:
        raster_path: Georeferenziertes RGB-Bild.
        clip: Fläche, auf die zugeschnitten wird.
        schwelle: Vegetationsschwelle. ``None`` sucht das Tal im
            flächengewichteten Histogramm der Segmentwerte.
        unsicherheitsband: Halbe Breite des Bandes um die Schwelle, in dem
            Segmente als ``unsicher`` gelten. ``0`` schaltet es ab.
        segmentgroesse_m2: Angestrebte Segmentgrösse.
        **segment_kwargs: Weiter an :func:`bildsegmente`.

    Returns:
        ``(GeoDataFrame, info)`` — Spalten ``klasse`` (``"Grünfläche"`` /
        ``"befestigt"`` / ``"unsicher"``), ``exg``, ``flaeche_m2``; ``info`` mit
        ``schwelle``, ``schwelle_quelle`` und den Flächen je Klasse.
    """
    seg = bildsegmente(raster_path, clip=clip,
                       segmentgroesse_m2=segmentgroesse_m2, **segment_kwargs)

    quelle = "übergeben"
    if schwelle is None:
        schwelle = talschwelle(seg["exg"].to_numpy(), seg["flaeche_m2"].to_numpy())
        quelle = "Tal im flächengewichteten Histogramm"

    unten, oben = schwelle - unsicherheitsband, schwelle + unsicherheitsband
    seg["klasse"] = np.where(
        seg["exg"] > oben, "Grünfläche",
        np.where(seg["exg"] < unten, "befestigt", "unsicher"))

    je_klasse = seg.groupby("klasse")["flaeche_m2"].sum().to_dict()
    info = {
        "schwelle": float(schwelle),
        "schwelle_quelle": quelle,
        "segmente": int(len(seg)),
        **{f"flaeche_{k}_m2": float(v) for k, v in je_klasse.items()},
    }
    return seg[["klasse", "exg", "helligkeit", "flaeche_m2", "geometry"]], info
