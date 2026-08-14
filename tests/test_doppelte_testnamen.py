"""Konsument des büroweiten Duplikat-Wächters.

Ein Wächter ohne Aufrufer ist Prosa. Seine Heimat ist
`pbs-wissen/scripts/doppelte_testnamen.py` (büroweit eine Heimat, M2); sein
garantierter Auslöser ist dieser Test: er läuft bei jedem Suite-Lauf mit, also
auch bei jeder Dispatcher-Verifikation und in jedem Gate, das die Suite fährt.
Aufgelöst wird über den Sibling-Pfad — dasselbe Muster, mit dem die
commit-msg-Hooks `korpus_index.py` und `modell_trailer_warnung.sh` ziehen.
Fehlt der Nachbar (Frischklon ohne pbs-wissen), überspringt sich dieser Test
mit dem erwarteten Pfad im Grund: kein Befund, sondern keine Aussage.

Warum der Wächter existiert: Python behält die LETZTE Definition eines Namens.
Zwei `def test_*` gleichen Namens in einer Datei bedeuten, dass pytest nur die
zweite sammelt — die erste läuft nie, wird nirgends gemeldet, und die Suite
zählt unverändert grün. Am 14.08. war es real so, und ein Mutations-Beweis
zielte dabei unbemerkt auf die tote Fassung.
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(subprocess.run(
    ["git", "rev-parse", "--show-toplevel"],
    check=True, capture_output=True, text=True,
    cwd=Path(__file__).resolve().parent).stdout.strip())
HEIMAT = (REPO / ".." / "pbs-wissen" / "scripts").resolve()
WAECHTER = HEIMAT / "doppelte_testnamen.py"
TESTS = Path(__file__).resolve().parent

if not WAECHTER.is_file():
    pytest.skip(
        f"Duplikat-Wächter nicht auffindbar — erwartet unter {WAECHTER}. "
        "Frischklon ohne Nachbar-Repo pbs-wissen: keine Aussage, kein Befund.",
        allow_module_level=True)

sys.path.insert(0, str(HEIMAT))

from doppelte_testnamen import doppelte_namen, pruefe  # noqa: E402


def test_eigene_testsuite_hat_keine_doppelten_namen(capsys):
    """Der Bestand selbst — der Fall, für den der Wächter gebaut ist."""
    assert pruefe(TESTS) == 0
    assert "OK" in capsys.readouterr().out


def test_waechter_faengt_den_realen_vorfall(tmp_path):
    """Rot-Beweis am nachgestellten Vorfall vom 14.08.: derselbe Testname
    zweimal in einer Datei. Ohne diesen Arm wäre das Grün oben nicht von dem
    eines Wächters zu unterscheiden, der nie etwas findet."""
    datei = tmp_path / "test_beispiel.py"
    datei.write_text(
        "def test_verschwundenes(tmp_path):\n    pass\n\n\n"
        "def test_verschwundenes(tmp_path):\n    pass\n", encoding="utf-8")

    assert doppelte_namen(datei.read_text(encoding="utf-8")) == [
        ("test_verschwundenes", [1, 5])]
    assert pruefe(tmp_path) == 2


def test_gleicher_methodenname_in_zwei_klassen_ist_kein_befund(tmp_path):
    """Die benannte Nicht-Fehlfeuer-Klasse: `TestA.test_x` und `TestB.test_x`
    kollidieren nicht — ein Wächter, der darauf feuerte, würde abgeschaltet."""
    datei = tmp_path / "test_klassen.py"
    datei.write_text(
        "class TestA:\n    def test_x(self):\n        pass\n\n"
        "class TestB:\n    def test_x(self):\n        pass\n", encoding="utf-8")

    assert pruefe(tmp_path) == 0
