"""Suite-Herkunfts-Zusicherung (FB 3.143).

Ein editable Install pinnt einen ABSOLUTEN Pfad (siehe
`.venv/**/site-packages/__editable__.pbs_gis-*.pth`). Läuft die Suite aus
einer Kopie dieses Repos, löst `import pbs_gis` trotzdem auf das ORIGINAL
auf — ein in der Kopie injizierter Defekt liegt dann nie unter Prüfung,
während `inspect.getsource` brav die Datei der Kopie zeigt und den Biss
verschweigt. Genau dieser Aufbau kann den gesuchten Effekt nicht erzeugen und
liefert trotzdem dasselbe Bild wie eine echte Entwarnung.

Diese Zusicherung beantwortet zu Sammelbeginn genau eine Frage: liegt das
importierte `pbs_gis` unter der Wurzel, aus der diese Suite läuft? Kein
Moduswechsel, keine Heuristik, keine Abschalt-Variable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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


@pytest.fixture(autouse=True)
def _xdg_state_isoliert(tmp_path_factory: pytest.TempPathFactory,
                        monkeypatch: pytest.MonkeyPatch) -> None:
    """Jeder Test sieht ein eigenes XDG_STATE_HOME: `wfs.download` schreibt
    dort das Quell-Stand-Register (`wfs.register_pfad`), und ein Test darf nie
    den Werkzeug-Zustand der Maschine schreiben."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("xdg-state")))


@pytest.fixture(autouse=True)
def _kein_netz_fuer_quell_stand(monkeypatch: pytest.MonkeyPatch) -> None:
    """Die Quell-Stand-Ermittlung eines `wfs.download` geht nie ins Netz:
    ein Test, der sie braucht, setzt seinen eigenen Stub."""
    from pbs_gis import wfs

    def kein_netz(url, timeout):
        raise OSError(f"kein Netz im Test: {url}")

    monkeypatch.setattr(wfs, "_http_get", kein_netz)
