"""Synthetische Lageplan-Fixtures für die Einpassungs-Tests.

Baut ein minimales, unkomprimiertes PDF aus bekannten Teilen — Strichmuster,
Strichstärke, Farbe und Text sind gesetzt, nicht geerbt. Damit wird die
Vektorextraktion an einer Vorlage geprüft, deren SOLL bekannt ist, ohne eine
Zeile Kundendatenbestand ins Repo zu holen.

``pdftocairo`` skaliert die Seite beim SVG-Export um ~0,999622; die Tests
rechnen deshalb mit relativer Toleranz, nicht auf die Nachkommastelle.
"""
from __future__ import annotations

A3_LANDSCAPE = (1190.55, 841.89)
CADASTRE_DASH = "5.76 2.88 1.15 2.88"


def polyline(points, *, width=1.73, dash=None, rgb=(0, 0, 0)) -> str:
    """Content-Stream-Fragment für einen Linienzug in Seiten-pt (y nach oben)."""
    d = f"[{dash}] 0 d" if dash else "[] 0 d"
    body = " ".join(
        f"{x:.4f} {y:.4f} " + ("m" if i == 0 else "l")
        for i, (x, y) in enumerate(points))
    return f"{width} w\n{d}\n{rgb[0]} {rgb[1]} {rgb[2]} RG\n{body} S\n"


def rectangle(x0, y0, x1, y1, *, width=0.5, rgb=(0, 0, 1)) -> str:
    """Geschlossenes Rechteck — die Blattschnitt-Signatur des Übersichtsblatts."""
    return polyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)],
                    width=width, rgb=rgb)


def text(x, y, s, *, size=12) -> str:
    """Text-Fragment; wird von ``pdftotext -bbox`` als Wort gefunden."""
    return f"BT /F1 {size} Tf {x:.2f} {y:.2f} Td ({s}) Tj ET\n"


def write_pdf(path, pages, page_size=A3_LANDSCAPE) -> str:
    """Ein PDF mit je einem Content-Stream pro Seite schreiben.

    Args:
        path: Zieldatei.
        pages: Liste von Content-Stream-Strings (einer je Seite).
        page_size: (Breite, Höhe) in pt.
    """
    w, h = page_size
    n = len(pages)
    font = "<< /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >>"
    objs: list[bytes] = []
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    for i, content in enumerate(pages):
        objs.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w} {h}] "
                     f"/Contents {4 + 2 * i} 0 R /Resources {font} >>").encode())
        cs = content.encode("latin-1")
        objs.append(b"<< /Length " + str(len(cs)).encode() + b" >>\nstream\n"
                    + cs + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    with open(path, "wb") as fh:
        fh.write(bytes(out))
    return str(path)
