"""Unit tests for the strict CAD style schema and colour resolution.

Pure unit tests: schema validation, closed-field enforcement, and ACI colour
resolution against the packaged table. No ezdxf, no I/O beyond tmp_path.
See ``src/pbs_gis/cad/styles.py`` and ``colors.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pbs_gis.cad.colors import ColorError, normalize_transparency, resolve_color
from pbs_gis.cad.styles import (
    SCHEMA_VERSION,
    HatchStyle,
    StyleError,
    load_styles,
    parse_styles,
)

FIXTURE = Path(__file__).parent / "fixtures" / "cad_styles_georgendorf.yaml"


# --- colours ---------------------------------------------------------------

def test_resolve_named_color():
    # The packaged table names "red" twice (aci 1 and aci 10); last-wins,
    # matching the reference ProjectLoader dict-comprehension — so red → 10.
    # This is what the real Georgendorf DXF carries on the Geltungsbereich layer.
    assert resolve_color("red") == 10
    assert resolve_color("white") == 7


def test_resolve_int_color_passthrough():
    assert resolve_color(140) == 140


def test_resolve_none_is_default():
    assert resolve_color(None) == 7


def test_resolve_rgb_string_and_sequence():
    assert resolve_color("248,215,49") == (248, 215, 49)
    assert resolve_color([1, 2, 3]) == (1, 2, 3)


def test_unknown_color_name_raises():
    with pytest.raises(ColorError):
        resolve_color("not-a-color")


def test_bad_rgb_string_raises():
    with pytest.raises(ColorError):
        resolve_color("1,2")


def test_extended_color_name_resolves():
    # Extended palette from the salvaged aci_colors.yaml table.
    assert isinstance(resolve_color("vermilion-light"), int)


def test_normalize_transparency_clamps():
    assert normalize_transparency(0.6) == 0.6
    assert normalize_transparency(2.0) == 1.0
    assert normalize_transparency(-1) == 0.0
    assert normalize_transparency("bad") is None
    assert normalize_transparency(None) is None


# --- schema ----------------------------------------------------------------

def test_load_fixture_styles():
    styles = load_styles(FIXTURE)
    assert set(styles) == {"geltungsbereich", "baufeld", "baugrenze"}
    gb = styles["geltungsbereich"]
    assert gb.layer.color == "red"
    assert gb.layer.lineweight == 50
    assert gb.layer.plot is False
    assert gb.entity.linetype_generation is True
    baufeld = styles["baufeld"]
    assert isinstance(baufeld.hatch, HatchStyle)
    assert baufeld.hatch.transparency == 0.6
    bg = styles["baugrenze"]
    assert bg.layer.linetype == "ACAD_ISO11W100"
    assert bg.entity.close is True


def test_missing_schema_version_rejected():
    with pytest.raises(StyleError):
        parse_styles({"styles": {"x": {}}})


def test_wrong_schema_version_rejected():
    with pytest.raises(StyleError):
        parse_styles({"schema_version": 99, "styles": {}})


def test_unknown_layer_field_rejected():
    data = {"schema_version": SCHEMA_VERSION,
            "styles": {"x": {"layer": {"colour": "red"}}}}  # typo: colour
    with pytest.raises(StyleError) as exc:
        parse_styles(data)
    assert "colour" in str(exc.value)


def test_unknown_top_style_block_rejected():
    data = {"schema_version": SCHEMA_VERSION,
            "styles": {"x": {"hats": {}}}}  # unknown block
    with pytest.raises(StyleError):
        parse_styles(data)


def test_unknown_attachment_rejected():
    data = {"schema_version": SCHEMA_VERSION,
            "styles": {"x": {"text": {"attachment": "SIDEWAYS"}}}}
    with pytest.raises(StyleError):
        parse_styles(data)


def test_missing_styles_block_rejected():
    with pytest.raises(StyleError):
        parse_styles({"schema_version": SCHEMA_VERSION})


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_styles(tmp_path / "nope.yaml")
