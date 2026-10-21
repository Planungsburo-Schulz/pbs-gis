"""Paket-Import ist lazy: `import pbs_gis` lädt keine Untermodule, und ein
fehlendes ezdxf trifft nur die CAD-Aufrufer (QGIS' gebündeltes Python hat
kein ezdxf, braucht aber `pbs_gis.qgis_bridge`)."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

import pbs_gis

_BLOCK_EZDXF = textwrap.dedent(
    """
    import sys
    sys.modules["ezdxf"] = None  # ImportError bei jedem `import ezdxf`
    """
)


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", _BLOCK_EZDXF + textwrap.dedent(code)],
        capture_output=True,
        text=True,
    )


def test_package_and_bridge_import_without_ezdxf():
    r = _run("import pbs_gis\nimport pbs_gis.qgis_bridge\nprint('ok')")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ok"


def test_cad_attribute_without_ezdxf_raises_importerror_naming_ezdxf():
    r = _run(
        """
        import pbs_gis
        try:
            pbs_gis.shapefile_to_dxf
        except ImportError as e:
            print("ImportError:", e)
        else:
            print("NO-ERROR")
        """
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("ImportError:"), r.stdout
    assert "ezdxf" in r.stdout


def test_package_import_loads_no_submodule():
    r = _run(
        """
        import sys
        import pbs_gis
        print(sorted(m for m in sys.modules if m.startswith("pbs_gis.")))
        """
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[]"


@pytest.mark.parametrize("name", list(pbs_gis.__all__))
def test_every_public_name_resolves(name):
    assert getattr(pbs_gis, name) is not None


def test_dir_lists_public_names():
    assert set(pbs_gis.__all__) <= set(dir(pbs_gis))


def test_unknown_attribute_raises_attributeerror():
    with pytest.raises(AttributeError):
        pbs_gis.does_not_exist
