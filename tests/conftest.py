"""Suite-Herkunfts-Zusicherung (FB 3.143).

Ein editable Install pinnt einen ABSOLUTEN Pfad (siehe
`.venv/**/site-packages/__editable__.pbs_gis-*.pth`). Läuft die Suite aus
einer Kopie dieses Repos, löst `import pbs_gis` trotzdem auf das ORIGINAL
auf — ein in der Kopie injizierter Defekt liegt dann nie unter Prüfung,
während `inspect.getsource` brav die Datei der Kopie zeigt und den Biss
verschweigt (Vorfall FB 3.143, Beinahe-Fehlschluss beim FB-130-Gegenprobe-Bau
am 18.08.).

Diese Zusicherung beantwortet zu Sammelbeginn genau eine Frage: liegt das
importierte `pbs_gis` unter der Wurzel, aus der diese Suite läuft? Kein
Moduswechsel, keine Heuristik, keine Abschalt-Variable.
"""

from __future__ import annotations

from pathlib import Path

import pbs_gis

_SUITE_ROOT = Path(__file__).resolve().parent.parent
_IMPORTED_PACKAGE = Path(pbs_gis.__file__).resolve()

if not _IMPORTED_PACKAGE.is_relative_to(_SUITE_ROOT):
    raise RuntimeError(
        "Suite-Herkunfts-Zusicherung (FB 3.143) fehlgeschlagen: das "
        "importierte pbs_gis liegt nicht unter der Suite-Wurzel, aus der "
        "diese Suite laeuft. Die Suite prueft damit nicht den Code dieses "
        "Baums, sondern eine andernorts installierte Kopie.\n"
        f"  importiertes Paket: {_IMPORTED_PACKAGE}\n"
        f"  Suite-Wurzel:       {_SUITE_ROOT}"
    )
