"""Unit tests for the working-layer subset writer (``pbs_gis.dxf.subset``).

The property under test is *reduction without loss*: the layers asked for arrive
with all their geometry and coordinates, and nothing else comes along. Both halves
fail silently in CAD — a missing layer looks like an empty one, an extra layer
looks like the source — so each is asserted separately, including the absence
assertion that catches the subset degrading into a copy.
"""

from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest

from pbs_gis.dxf.read import CadReadError
from pbs_gis.dxf.subset import dxf_subset, dxf_subset_from_sources


@pytest.fixture()
def source_dxf(tmp_path: Path) -> Path:
    """A drawing with four layers: two wanted, one that must not travel, and one
    defined but empty.

    ``DEFINED_EMPTY`` holds no entity at all — the form an xref layer takes in a
    host drawing. Nothing imports it, so its definition is lost unless carried
    over deliberately.
    """
    doc = ezdxf.new("R2010")
    for name, color in [("WORK_LINES", 1), ("WORK_TEXT", 3), ("NOISE", 5), ("DEFINED_EMPTY", 6)]:
        doc.layers.add(name, color=color)

    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10)], dxfattribs={"layer": "WORK_LINES"})
    msp.add_lwpolyline([(20, 20), (30, 30)], dxfattribs={"layer": "WORK_LINES"})
    msp.add_text("Label", dxfattribs={"layer": "WORK_TEXT", "insert": (5, 5)})
    msp.add_circle((100, 100), radius=5, dxfattribs={"layer": "NOISE"})

    blk = doc.blocks.new(name="MARKER")
    blk.add_line((0, 0), (1, 1), dxfattribs={"layer": "WORK_LINES"})
    msp.add_blockref("MARKER", (50, 50), dxfattribs={"layer": "WORK_LINES"})

    path = tmp_path / "source.dxf"
    doc.saveas(path)
    return path


def test_subset_carries_the_requested_geometry(source_dxf: Path, tmp_path: Path) -> None:
    out = dxf_subset(source_dxf, tmp_path / "subset.dxf", ["WORK_LINES", "WORK_TEXT"])

    msp = ezdxf.readfile(out).modelspace()
    assert len(msp.query("LWPOLYLINE")) == 2
    assert len(msp.query("TEXT")) == 1
    assert len(msp.query("INSERT")) == 1


def test_subset_leaves_unrequested_layers_behind(source_dxf: Path, tmp_path: Path) -> None:
    """The absence half — a subset that quietly copied everything still passes
    every presence assertion above."""
    out = dxf_subset(source_dxf, tmp_path / "subset.dxf", ["WORK_LINES", "WORK_TEXT"])

    doc = ezdxf.readfile(out)
    assert not doc.modelspace().query("CIRCLE")
    assert "NOISE" not in doc.layers


def test_subset_keeps_coordinates_untouched(source_dxf: Path, tmp_path: Path) -> None:
    out = dxf_subset(source_dxf, tmp_path / "subset.dxf", ["WORK_LINES"])

    points = [
        tuple(round(c, 6) for c in p[:2])
        for pl in ezdxf.readfile(out).modelspace().query("LWPOLYLINE")
        for p in pl.get_points()
    ]
    assert (0.0, 0.0) in points
    assert (30.0, 30.0) in points


def test_subset_carries_a_requested_layer_that_holds_no_entity(
    source_dxf: Path, tmp_path: Path
) -> None:
    """An xref layer in a host drawing is a definition with nothing on it. It is
    still part of the working set, and a subset silently short of it sends the
    reader hunting for a layer that was never written."""
    out = dxf_subset(source_dxf, tmp_path / "subset.dxf", ["WORK_LINES", "DEFINED_EMPTY"])

    layers = ezdxf.readfile(out).layers
    assert "DEFINED_EMPTY" in layers
    assert layers.get("DEFINED_EMPTY").dxf.color == 6


def test_subset_rejects_a_layer_the_source_does_not_have(
    source_dxf: Path, tmp_path: Path
) -> None:
    with pytest.raises(CadReadError, match="WORK_LNIES"):
        dxf_subset(source_dxf, tmp_path / "subset.dxf", ["WORK_LNIES"])


def test_subset_rejects_an_empty_layer_list(source_dxf: Path, tmp_path: Path) -> None:
    with pytest.raises(CadReadError, match="No layers requested"):
        dxf_subset(source_dxf, tmp_path / "subset.dxf", [])


def test_subset_refuses_to_overwrite_without_replace(
    source_dxf: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "subset.dxf"
    dxf_subset(source_dxf, dest, ["WORK_LINES"])

    with pytest.raises(CadReadError, match="already exists"):
        dxf_subset(source_dxf, dest, ["WORK_LINES"])

    assert dxf_subset(source_dxf, dest, ["WORK_LINES"], replace=True) == dest


# --- several sources ---------------------------------------------------------

@pytest.fixture()
def second_dxf(tmp_path: Path) -> Path:
    """A second drawing, as an xref bundle splits a plan by trade."""
    doc = ezdxf.new("R2010")
    doc.layers.add("PG_BOUNDARY", color=2)
    doc.layers.add("WORK_LINES", color=1)  # name also used by source_dxf
    doc.modelspace().add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "PG_BOUNDARY"},
    )
    path = tmp_path / "second.dxf"
    doc.saveas(path)
    return path


def test_subset_gathers_layers_from_several_sources(
    source_dxf: Path, second_dxf: Path, tmp_path: Path
) -> None:
    out = dxf_subset_from_sources(
        [(source_dxf, ["WORK_LINES"]), (second_dxf, ["PG_BOUNDARY"])],
        tmp_path / "subset.dxf",
    )

    doc = ezdxf.readfile(out)
    layers = {e.dxf.layer for e in doc.modelspace()}
    assert layers == {"WORK_LINES", "PG_BOUNDARY"}
    assert len(doc.modelspace().query("LWPOLYLINE")) == 3  # 2 + the boundary


def test_subset_rejects_one_layer_name_claimed_by_two_sources(
    source_dxf: Path, second_dxf: Path, tmp_path: Path
) -> None:
    """Merged silently, the target layer holds entities from a file nobody
    expected, and the drawing looks correct."""
    with pytest.raises(CadReadError, match="two sources"):
        dxf_subset_from_sources(
            [(source_dxf, ["WORK_LINES"]), (second_dxf, ["WORK_LINES"])],
            tmp_path / "subset.dxf",
        )


def test_subset_rejects_a_source_without_layers(source_dxf: Path, tmp_path: Path) -> None:
    with pytest.raises(CadReadError, match="No layers requested"):
        dxf_subset_from_sources([(source_dxf, [])], tmp_path / "subset.dxf")
