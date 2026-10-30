"""Post-Process-Hook für die LUNG-MV-WFS (ng_schutzgebiete, wg_schutzgebiete).

Zwei Eigenheiten der Dienste, die hier ausgeglichen werden:

* Textattribute, die als UTF-8-Bytes ankommen, aber als Latin-1 gelesen
  wurden („OstrÃ¼gen" statt „Ostrügen"), werden zurückgewandelt — je Wert
  nur, wenn die Rückwandlung latin-1 -> utf-8 gelingt; korrekter Text und
  Nicht-Strings bleiben unverändert.
* Beide Dienste liefern in EPSG:5650 (ETRS89 / UTM zone 33N mit
  Zonen-Präfix im Rechtswert). Das Recipe liest deshalb in EPSG:5650;
  der Hook gibt das Ergebnis in EPSG:25833 zurück.
"""

import pandas as pd

TARGET_CRS = "EPSG:25833"


def _repair(value):
    if not isinstance(value, str):
        return value
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def post_process(gdf):
    geometry_column = gdf.geometry.name
    for column in gdf.columns:
        if column != geometry_column and pd.api.types.is_string_dtype(gdf[column]):
            gdf[column] = gdf[column].map(_repair)
    return gdf.to_crs(TARGET_CRS)
