"""Unit tests for the CAD input reader (DXF native, DWG via ODA File Converter).

The DWG path is asserted through its *contract*, not through a real conversion:
without the ODA File Converter the reader must fail loudly and name the fix, and
it must never fall back to a partial document. See ``src/pbs_gis/dxf/read.py``.
"""

from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest

from pbs_gis.dxf.read import (
    CAD_SUFFIXES,
    CadReadError,
    dwg_to_dxf,
    is_dwg_supported,
    read_cad,
)


@pytest.fixture()
def sample_dxf(tmp_path: Path) -> Path:
    """A minimal real DXF with one known entity on one known layer."""
    doc = ezdxf.new("R2010")
    doc.layers.add("PROBE")
    doc.modelspace().add_line((0, 0), (10, 5), dxfattribs={"layer": "PROBE"})
    path = tmp_path / "sample.dxf"
    doc.saveas(path)
    return path


# --- DXF path ---------------------------------------------------------------

def test_read_cad_reads_dxf(sample_dxf: Path) -> None:
    doc = read_cad(sample_dxf)
    lines = list(doc.modelspace().query("LINE"))
    assert len(lines) == 1
    assert lines[0].dxf.layer == "PROBE"


def test_read_cad_accepts_str_path(sample_dxf: Path) -> None:
    assert read_cad(str(sample_dxf)).modelspace().query("LINE")


def test_read_cad_suffix_is_case_insensitive(sample_dxf: Path) -> None:
    upper = sample_dxf.with_name("SAMPLE.DXF")
    upper.write_bytes(sample_dxf.read_bytes())
    assert read_cad(upper).modelspace().query("LINE")


# --- loud failures ----------------------------------------------------------

def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(CadReadError, match="not found"):
        read_cad(tmp_path / "nope.dxf")


def test_unknown_suffix_raises_and_names_accepted_formats(tmp_path: Path) -> None:
    """Unknown format must brüll, never be guessed at."""
    stray = tmp_path / "plan.shp"
    stray.write_bytes(b"not cad")
    with pytest.raises(CadReadError, match="Unsupported CAD format"):
        read_cad(stray)


def test_corrupt_dxf_raises_cad_read_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.dxf"
    broken.write_text("this is not a DXF at all")
    with pytest.raises(CadReadError, match="Cannot read DXF"):
        read_cad(broken)


def test_suffix_set_is_closed() -> None:
    assert CAD_SUFFIXES == {".dxf", ".dwg"}


# --- DWG path ---------------------------------------------------------------

def test_is_dwg_supported_returns_bool() -> None:
    assert isinstance(is_dwg_supported(), bool)


@pytest.mark.skipif(is_dwg_supported(), reason="ODA File Converter is installed")
def test_dwg_without_oda_fails_loudly_with_install_hint(tmp_path: Path) -> None:
    """No converter must mean a loud error naming the fix — not a silent skip."""
    dwg = tmp_path / "vermesser.dwg"
    dwg.write_bytes(b"AC1032 dummy")
    with pytest.raises(CadReadError, match="oda-file-converter"):
        read_cad(dwg)


@pytest.mark.skipif(is_dwg_supported(), reason="ODA File Converter is installed")
def test_dwg_to_dxf_without_oda_fails_loudly(tmp_path: Path) -> None:
    dwg = tmp_path / "vermesser.dwg"
    dwg.write_bytes(b"AC1032 dummy")
    with pytest.raises(CadReadError, match="oda-file-converter"):
        dwg_to_dxf(dwg)


def test_dwg_to_dxf_rejects_non_dwg(sample_dxf: Path) -> None:
    with pytest.raises(CadReadError, match="Not a DWG file"):
        dwg_to_dxf(sample_dxf)


def test_dwg_to_dxf_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(CadReadError, match="not found"):
        dwg_to_dxf(tmp_path / "nope.dwg")


def test_dwg_to_dxf_refuses_to_clobber_existing_dxf(tmp_path: Path) -> None:
    """Existing output must not be overwritten unless explicitly allowed."""
    dwg = tmp_path / "plan.dwg"
    dwg.write_bytes(b"AC1032 dummy")
    existing = tmp_path / "plan.dxf"
    existing.write_text("keep me")
    with pytest.raises(CadReadError, match="already exists"):
        dwg_to_dxf(dwg)
    assert existing.read_text() == "keep me"
