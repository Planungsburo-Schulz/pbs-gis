"""mcp/server.py findet pbs_gis über das eigene src/ des Repos, wenn der
Interpreter kein Install trägt, und meldet sonst eine benannte Diagnose
statt des nackten ModuleNotFoundError."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SERVER = _REPO / "mcp" / "server.py"

# Ein FastMCP-Ersatz hält den Test unabhängig davon, ob fastmcp im
# Test-Interpreter installiert ist.
_FAKE_FASTMCP = textwrap.dedent(
    """
    import sys, types
    fm = types.ModuleType("fastmcp")
    class FastMCP:
        def __init__(self, *a, **k): pass
        def tool(self):
            return lambda fn: fn
        def run(self): pass
    fm.FastMCP = FastMCP
    sys.modules["fastmcp"] = fm
    """
)


def _run(server: Path, hide_pbs_gis_install: bool, call: str) -> subprocess.CompletedProcess:
    code = _FAKE_FASTMCP + textwrap.dedent(
        f"""
        import importlib.util, json, sys
        from pathlib import Path
        # Simuliert einen Interpreter ohne pbs_gis-Install: weder PYTHONPATH
        # noch ein editable-Install liefern das Paket. Entfernt wird jeder
        # Eintrag, aus dem pbs_gis importierbar ist, nicht nur das src/ dieses
        # Checkouts: die geteilte venv zeigt per .pth auf das src/ eines
        # anderen Checkouts (Main bzw. Worktree).
        sys.path[:] = [
            p for p in sys.path
            if Path(p).resolve() != Path({str(_REPO / "src")!r})
            and not (Path(p) / "pbs_gis").is_dir()
        ]
        sys.meta_path[:] = [f for f in sys.meta_path if "editable" not in type(f).__module__.lower() and "editable" not in type(f).__name__.lower()]
        assert importlib.util.find_spec("pbs_gis") is None, "pbs_gis noch auffindbar"
        spec = importlib.util.spec_from_file_location("server_under_test", {str(server)!r})
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        print({call})
        """
    )
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd="/")


def test_server_finds_pbs_gis_via_repo_src():
    r = _run(_SERVER, True, "[r['name'] for r in json.loads(mod.list_recipes('mv_alkis'))]")
    assert r.returncode == 0, r.stderr
    assert "'mv_alkis'" in r.stdout, r.stdout


def test_unreachable_src_gives_named_diagnosis(tmp_path):
    # Kopie ohne src/ daneben: __file__ -> tmp_path, dort gibt es kein ../src
    copy_dir = tmp_path / "mcp"
    copy_dir.mkdir()
    copy = copy_dir / "server.py"
    copy.write_text(_SERVER.read_text(encoding="utf-8"), encoding="utf-8")
    r = _run(copy, True, "mod.list_recipes()")
    assert r.returncode == 0, r.stderr
    out = r.stdout.strip()
    assert "ModuleNotFoundError" in out  # der Originalfehler wird mitgeführt
    assert sys.executable in out
    assert str(tmp_path / "src") in out
    assert out.startswith("{") and json.loads(out)["error"]
