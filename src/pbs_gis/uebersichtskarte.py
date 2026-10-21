"""Übersichtskarte + DXF des Geltungsbereichs für Leitungsanfragen.

Erzeugt in einem Lauf die Anlagen einer Direktanfrage an Leitungsträger:
Übersichtskarte A4 quer als PDF und PNG, Umringgeometrie als DXF und
GeoPackage, dazu ein LIESMICH mit dem sha256 der Quelle. Quelle, Ausgabe,
Stand, Beschriftung, Kartendienst, Maßstab und Auflösung kommen aus
Parametern; das Blatt ist A4 quer. Der Quellenvermerk stammt wörtlich aus
dem <Fees>-Element der WMS-Capabilities — er ist Lizenzbedingung
(CC BY 4.0), nicht Dekoration.

    python -m pbs_gis.uebersichtskarte --quelle <gpkg> --ausgabe <dir> \\
        --titel <text> --untertitel <text> [--stand JJJJ-MM-TT] ...
    python -m pbs_gis.uebersichtskarte --test
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

WMS = "https://sgx.geodatenzentrum.de/wms_basemapde"
LAYER = "de_basemapde_web_raster_farbe"
QUELLE = "© GeoBasis-DE / BKG (2026) CC BY 4.0"
EPSG = 25833
MASSSTAB = 5000
DPI = 200


def geometrie(quelle):
    gj = json.loads(subprocess.run(
        ["ogr2ogr", "-f", "GeoJSON", "/vsistdout/", str(quelle)],
        capture_output=True, text=True, check=True).stdout)
    g = gj["features"][0]["geometry"]
    return g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]


def flaeche(ring):
    a = sum(ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
            for i in range(len(ring) - 1))
    return abs(a) / 2


def font(size, bold=False):
    for p in ("/usr/share/fonts/TTF/DejaVuSans%s.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"):
        try:
            return ImageFont.truetype(p % ("-Bold" if bold else ""), size)
        except OSError:
            continue
    return ImageFont.load_default()


def wms_karte(wms, layer, epsg, bbox, groesse):
    """GetMap des Kartendiensts für `bbox` in `groesse` Pixeln (RGBA)."""
    r = requests.get(wms, params={
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap", "LAYERS": layer,
        "STYLES": "", "CRS": f"EPSG:{epsg}",
        "BBOX": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
        "WIDTH": groesse[0], "HEIGHT": groesse[1], "FORMAT": "image/png"}, timeout=90)
    ct = r.headers.get("Content-Type", "")
    if "image" not in ct:
        sys.exit(f"WMS lieferte {ct}: {r.text[:300]}")
    return Image.open(io.BytesIO(r.content)).convert("RGBA")


def erzeuge(quelle, ausgabe, titel, untertitel, *, stand, wms=WMS, layer=LAYER,
            quellenvermerk=QUELLE, epsg=EPSG, massstab=MASSSTAB, dpi=DPI,
            hole_karte=wms_karte):
    """Schreibt die fünf Anlagen nach `ausgabe` und gibt ihre Pfade zurück
    (PDF, PNG, DXF, GPKG, LIESMICH)."""
    src = Path(quelle)
    out = Path(ausgabe)
    mm = dpi / 25.4
    page_size = (int(297 * mm), int(210 * mm))         # A4 quer
    frame = (int(240 * mm), int(140 * mm))             # Kartenfeld
    frame_xy = (int(28.5 * mm), int(30 * mm))

    parts = geometrie(src)
    xs = [p[0] for poly in parts for p in poly[0]]
    ys = [p[1] for poly in parts for p in poly[0]]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2

    # Kartenfeld in Metern aus Maßstab und Blattgröße — nicht umgekehrt,
    # damit der Maßstabsbalken eine runde Zahl trägt.
    w_m = frame[0] / mm / 1000 * massstab
    h_m = frame[1] / mm / 1000 * massstab
    bbox = (cx - w_m / 2, cy - h_m / 2, cx + w_m / 2, cy + h_m / 2)
    if max(xs) - min(xs) > w_m or max(ys) - min(ys) > h_m:
        sys.exit(f"Geltungsbereich passt nicht in 1:{massstab} auf A4 quer")

    karte = hole_karte(wms, layer, epsg, bbox, frame)

    def to_px(x, y):
        return ((x - bbox[0]) / w_m * frame[0], (bbox[3] - y) / h_m * frame[1])

    overlay = Image.new("RGBA", frame, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for poly in parts:
        pts = [to_px(*p) for p in poly[0]]
        od.polygon(pts, fill=(220, 30, 30, 45))
        od.line(pts + [pts[0]], fill=(200, 0, 0, 255), width=6)
    karte = Image.alpha_composite(karte, overlay)

    page = Image.new("RGB", page_size, "white")
    page.paste(karte.convert("RGB"), frame_xy)
    d = ImageDraw.Draw(page)
    d.rectangle([frame_xy, (frame_xy[0] + frame[0], frame_xy[1] + frame[1])],
                outline=(60, 60, 60), width=3)

    f_t, f_s, f_k = font(int(4.6 * mm), True), font(int(3.1 * mm)), font(int(2.6 * mm))
    d.text((frame_xy[0], int(10 * mm)), titel, font=f_t, fill="black")
    d.text((frame_xy[0], int(17.5 * mm)), untertitel, font=f_s, fill=(50, 50, 50))
    d.text((frame_xy[0], int(22.5 * mm)),
           "Anfragegebiet für Leitungs-/Spartenauskunft — rot umrandet",
           font=f_s, fill=(150, 0, 0))

    # Maßstabsbalken: 200 m, in Blattmillimetern aus dem Maßstab gerechnet
    bal_m, y0 = 200, frame_xy[1] + frame[1] + int(6 * mm)
    bal_px = int(bal_m / massstab * 1000 * mm)
    x0 = frame_xy[0]
    for i in range(4):
        seg = bal_px // 4
        d.rectangle([x0 + i * seg, y0, x0 + (i + 1) * seg, y0 + int(1.6 * mm)],
                    fill="black" if i % 2 == 0 else "white", outline="black")
    d.text((x0, y0 + int(2.4 * mm)), "0", font=f_k, fill="black")
    d.text((x0 + bal_px - int(4 * mm), y0 + int(2.4 * mm)), f"{bal_m} m", font=f_k, fill="black")
    d.text((x0 + bal_px + int(8 * mm), y0), f"Maßstab 1:{massstab} (A4 quer)", font=f_k, fill="black")

    # Nordpfeil
    nx, ny = frame_xy[0] + frame[0] - int(10 * mm), frame_xy[1] + int(8 * mm)
    d.polygon([(nx, ny - int(6 * mm)), (nx - int(2.6 * mm), ny), (nx + int(2.6 * mm), ny)],
              fill="black")
    d.text((nx - int(1.6 * mm), ny + int(1 * mm)), "N", font=f_k, fill="black")

    ges = sum(flaeche(p[0]) for p in parts)
    zeilen = [
        f"Koordinatenreferenzsystem: ETRS89 / UTM Zone 33N (EPSG:{epsg})",
        f"Eckpunkte des umschließenden Rechtecks:  SW {min(xs):.0f} / {min(ys):.0f}"
        f"   ·   NO {max(xs):.0f} / {max(ys):.0f}",
        f"Ausdehnung {max(xs)-min(xs):.0f} x {max(ys)-min(ys):.0f} m   ·   "
        + "   ·   ".join(f"Teilfläche {i}: {flaeche(p[0]):,.0f} m²".replace(",", ".")
                         for i, p in enumerate(parts, 1))
        + f"   ·   gesamt {ges:,.0f} m² ({ges/10000:.2f} ha)".replace(",", "."),
        f"Kartengrundlage: basemap.de Web Raster ({layer}) — {quellenvermerk}",
        f"Erstellt {stand} · Planungsbüro G. Schulz · Geometriequelle: {src.name}",
    ]
    yy = y0 + int(7 * mm)
    for z in zeilen:
        d.text((frame_xy[0], yy), z, font=f_k, fill=(40, 40, 40))
        yy += int(3.6 * mm)

    out.mkdir(parents=True, exist_ok=True)
    pdf = out / f"{stand}_Geltungsbereich_Uebersichtskarte.pdf"
    png = out / f"{stand}_Geltungsbereich_Uebersichtskarte.png"
    page.save(pdf, "PDF", resolution=dpi)
    page.save(png)

    # Der DXF-Treiber kennt nur das Feld "Layer"; jeder andere Spaltenname
    # bricht die Übersetzung ab. Der SELECT setzt zugleich einen sprechenden
    # CAD-Layernamen, statt alles auf "0" abzulegen.
    dxf = out / f"{stand}_Geltungsbereich_EPSG25833.dxf"
    subprocess.run(["ogr2ogr", "-f", "DXF", str(dxf), str(src),
                    "-sql", "SELECT geom, 'GELTUNGSBEREICH' AS Layer FROM geltungsbereich"],
                   check=True)
    gpkg = out / f"{stand}_Geltungsbereich_EPSG25833.gpkg"
    subprocess.run(["ogr2ogr", "-f", "GPKG", str(gpkg), str(src)], check=True)

    src_sha = hashlib.sha256(src.read_bytes()).hexdigest()
    man = out / f"{stand}_Geltungsbereich_LIESMICH.md"
    man.write_text(
        f"# Geltungsbereich — Anlagen für Leitungsanfragen ({stand})\n\n"
        f"MASCHINE-Derivate, erzeugt aus dem Projektbestand. Nicht selbst Quelle.\n\n"
        f"- Quelle: `{quelle}`\n"
        f"- sha256 der Quelle: `{src_sha}`\n"
        f"- CRS: ETRS89 / UTM 33N (EPSG:{epsg})\n"
        f"- Erzeugt mit: `intern/abgeleitet/uebersichtskarte.py` (Kopie des Skripts)\n\n"
        "| Datei | Zweck |\n|---|---|\n"
        f"| `{pdf.name}` | Übersichtskarte A4 quer, M 1:{massstab} |\n"
        f"| `{png.name}` | dieselbe Karte als Bild |\n"
        f"| `{dxf.name}` | Umringgeometrie für CAD |\n"
        f"| `{gpkg.name}` | Umringgeometrie für GIS |\n\n"
        f"Kartengrundlage basemap.de Web Raster, Quellenvermerk (Lizenzbedingung "
        f"CC BY 4.0, wörtlich aus den WMS-Capabilities): {quellenvermerk}\n",
        encoding="utf-8")

    for p in (pdf, png, dxf, gpkg, man):
        print(f"{p.stat().st_size:>9,} B  {p.name}".replace(",", "."))
    return pdf, png, dxf, gpkg, man


def feature_count(pfad):
    """Summe der `Feature Count`-Zeilen aus `ogrinfo -so -al` über `pfad`."""
    info = subprocess.run(["ogrinfo", "-so", "-al", str(pfad)],
                          capture_output=True, text=True, check=True).stdout
    return sum(int(n) for n in re.findall(r"^Feature Count: (\d+)$", info, re.M))


def selbstprobe():
    """Lauf ohne Netz und ohne Projektdaten: synthetisches Quadrat als Quelle,
    leeres Raster statt WMS. PASS verlangt alle fünf Anlagen und einen DXF mit
    Geometrie — eine leere DXF entsteht lautlos, wenn der SELECT die
    Geometriespalte verliert."""
    import geopandas as gpd
    from shapely.geometry import Polygon

    def leeres_raster(wms, layer, epsg, bbox, groesse):
        return Image.new("RGBA", groesse, (255, 255, 255, 255))

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        quelle = tmp / "geltungsbereich.gpkg"
        x0, y0, s = 400000, 5900000, 300
        gpd.GeoDataFrame(
            {"geometry": [Polygon([(x0, y0), (x0 + s, y0), (x0 + s, y0 + s), (x0, y0 + s)])]},
            crs=f"EPSG:{EPSG}",
        ).to_file(quelle, layer="geltungsbereich", driver="GPKG")
        pfade = erzeuge(quelle, tmp / "abgeleitet", "Selbstprobe", "synthetisches Quadrat",
                        stand="2000-01-01", hole_karte=leeres_raster)
        fehlend = [p.name for p in pfade if not p.is_file()]
        n_dxf = feature_count(pfade[2])
    ok = not fehlend and n_dxf > 0
    print(f"{'PASS' if ok else 'FAIL'}: fehlende Anlagen {fehlend or 'keine'}, "
          f"DXF Feature Count {n_dxf}")
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python -m pbs_gis.uebersichtskarte",
        description="Übersichtskarte (PDF/PNG), DXF, GeoPackage und LIESMICH "
                    "eines Geltungsbereichs für Leitungsanfragen.")
    ap.add_argument("--test", action="store_true",
                    help="Selbstprobe ohne Netz und Projektdaten; Exit 0 = PASS")
    ap.add_argument("--quelle", help="GeoPackage des Geltungsbereichs (Pflicht)")
    ap.add_argument("--ausgabe", help="Zielordner, im Projekt intern/abgeleitet/ (Pflicht)")
    ap.add_argument("--titel", help="Kartentitel (Pflicht)")
    ap.add_argument("--untertitel", help="Untertitel (Pflicht)")
    ap.add_argument("--stand", default=datetime.date.today().isoformat(),
                    help="Datum für Dateinamen und Vermerke (Standard: heute)")
    ap.add_argument("--wms", default=WMS)
    ap.add_argument("--layer", default=LAYER)
    ap.add_argument("--quellenvermerk", default=QUELLE)
    ap.add_argument("--epsg", type=int, default=EPSG)
    ap.add_argument("--massstab", type=int, default=MASSSTAB)
    ap.add_argument("--dpi", type=int, default=DPI)
    args = ap.parse_args(argv)

    if args.test:
        return 0 if selbstprobe() else 1
    fehlend = [f"--{n}" for n in ("quelle", "ausgabe", "titel", "untertitel")
               if getattr(args, n) is None]
    if fehlend:
        ap.error(f"Pflicht-Argumente fehlen: {', '.join(fehlend)}")
    erzeuge(args.quelle, args.ausgabe, args.titel, args.untertitel, stand=args.stand,
            wms=args.wms, layer=args.layer, quellenvermerk=args.quellenvermerk,
            epsg=args.epsg, massstab=args.massstab, dpi=args.dpi)
    return 0


if __name__ == "__main__":
    sys.exit(main())
