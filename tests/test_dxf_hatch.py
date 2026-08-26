"""Unit tests for the hatch-area reader (``pbs_gis.dxf.hatch``).

The defect this reader exists to avoid is silent: reading only a hatch's first
boundary path returns a plausible polygon for every hatch and a wrong area for
each one with a hole. The hole case is therefore asserted on the number, not on
the mere presence of a geometry.
"""

from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest

from pbs_gis.dxf.hatch import extract_hatch_areas
from pbs_gis.dxf.read import CadReadError

CRS = "EPSG:25833"
SHIFT = 33_000_000


def _add_hatch(msp, layer: str, outer: list[tuple[float, float]],
               hole: list[tuple[float, float]] | None = None) -> None:
    h = msp.add_hatch(dxfattribs={"layer": layer})
    h.paths.add_polyline_path(outer, is_closed=True)
    if hole:
        h.paths.add_polyline_path(hole, is_closed=True)


@pytest.fixture()
def surfaces_dxf(tmp_path: Path) -> Path:
    """Three material layers; one hatch has a hole cut out of it."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()

    # 20 x 10 = 200 m², with a 4 x 5 = 20 m² hole -> 180 m²
    _add_hatch(msp, "SRF_ASPHALT",
               [(0, 0), (20, 0), (20, 10), (0, 10)],
               [(2, 2), (6, 2), (6, 7), (2, 7)])
    # 10 x 10 = 100 m²
    _add_hatch(msp, "SRF_GRUEN", [(30, 0), (40, 0), (40, 10), (30, 10)])
    # two separate hatches on one layer: 25 + 25 = 50 m²
    _add_hatch(msp, "SRF_PFLASTER", [(0, 20), (5, 20), (5, 25), (0, 25)])
    _add_hatch(msp, "SRF_PFLASTER", [(10, 20), (15, 20), (15, 25), (10, 25)])

    msp.add_lwpolyline([(0, 0), (1, 1)], dxfattribs={"layer": "SRF_ASPHALT"})

    path = tmp_path / "surfaces.dxf"
    doc.saveas(path)
    return path


def test_reads_one_row_per_hatch(surfaces_dxf: Path) -> None:
    gdf = extract_hatch_areas(surfaces_dxf, crs=CRS)

    assert len(gdf) == 4
    assert set(gdf["layer"]) == {"SRF_ASPHALT", "SRF_GRUEN", "SRF_PFLASTER"}
    assert gdf.crs == CRS


def test_subtracts_holes(surfaces_dxf: Path) -> None:
    """Reading only the first boundary path yields 200 here — plausible, wrong,
    and invisible without this number."""
    gdf = extract_hatch_areas(surfaces_dxf, crs=CRS)

    asphalt = gdf.loc[gdf["layer"] == "SRF_ASPHALT", "area_m2"].sum()
    assert asphalt == pytest.approx(180.0, abs=0.01)


def test_dissolve_sums_a_layer(surfaces_dxf: Path) -> None:
    gdf = extract_hatch_areas(surfaces_dxf, crs=CRS, dissolve=True)

    assert len(gdf) == 3
    pflaster = gdf.loc[gdf["layer"] == "SRF_PFLASTER", "area_m2"].iloc[0]
    assert pflaster == pytest.approx(50.0, abs=0.01)


def test_layer_filter_keeps_only_what_was_asked(surfaces_dxf: Path) -> None:
    gdf = extract_hatch_areas(surfaces_dxf, crs=CRS, layers=["SRF_GRUEN"])

    assert set(gdf["layer"]) == {"SRF_GRUEN"}
    assert gdf["area_m2"].sum() == pytest.approx(100.0, abs=0.01)


def test_layer_without_hatch_raises(surfaces_dxf: Path) -> None:
    """An empty result and a misspelt layer name are indistinguishable."""
    with pytest.raises(CadReadError, match="SRF_GREUN"):
        extract_hatch_areas(surfaces_dxf, crs=CRS, layers=["SRF_GREUN"])


def test_ignores_non_hatch_entities(surfaces_dxf: Path) -> None:
    gdf = extract_hatch_areas(surfaces_dxf, crs=CRS, layers=["SRF_ASPHALT"])

    assert len(gdf) == 1


def test_strip_zone_shifts_x(tmp_path: Path) -> None:
    doc = ezdxf.new("R2010")
    _add_hatch(doc.modelspace(), "SRF_X",
               [(SHIFT + 100, 200), (SHIFT + 110, 200),
                (SHIFT + 110, 210), (SHIFT + 100, 210)])
    path = tmp_path / "zoned.dxf"
    doc.saveas(path)

    gdf = extract_hatch_areas(path, crs=CRS, strip_zone=True)

    minx, _, maxx, _ = gdf.total_bounds
    assert minx == pytest.approx(100.0, abs=0.01)
    assert maxx == pytest.approx(110.0, abs=0.01)
    assert gdf["area_m2"].sum() == pytest.approx(100.0, abs=0.01)


def test_strip_zone_rejects_a_crs_without_known_prefix(tmp_path: Path) -> None:
    doc = ezdxf.new("R2010")
    _add_hatch(doc.modelspace(), "SRF_X", [(0, 0), (1, 0), (1, 1), (0, 1)])
    path = tmp_path / "plain.dxf"
    doc.saveas(path)

    with pytest.raises(CadReadError, match="no zone prefix known"):
        extract_hatch_areas(path, crs="EPSG:4326", strip_zone=True)


def _degenerate_dxf(tmp_path: Path, n_paths: int) -> Path:
    """One sound hatch plus one whose boundary is *n_paths* separate loops.

    The shape a broken drawing delivers: a hatch created over a whole plan
    instead of over one area, so its boundary is thousands of unrelated loops.
    Reading it is not merely slow — the union over those loops does not return
    in any usable time, and a caller cannot tell that from a large drawing.
    """
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    _add_hatch(msp, "SRF_GUT", [(0, 0), (10, 0), (10, 10), (0, 10)])

    h = msp.add_hatch(dxfattribs={"layer": "SRF_KAPUTT"})
    for i in range(n_paths):
        x, y = (i % 200) * 3.0, (i // 200) * 3.0
        h.paths.add_polyline_path(
            [(x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1)], is_closed=True
        )
    path = tmp_path / f"degenerate_{n_paths}.dxf"
    doc.saveas(path)
    return path


def test_degenerate_hatch_is_reported_not_read(tmp_path: Path) -> None:
    path = _degenerate_dxf(tmp_path, 1500)

    with pytest.raises(CadReadError, match="SRF_KAPUTT"):
        extract_hatch_areas(path, crs=CRS)


def test_degenerate_hatch_can_be_skipped_keeping_the_rest(tmp_path: Path) -> None:
    path = _degenerate_dxf(tmp_path, 1500)

    with pytest.warns(UserWarning, match="SRF_KAPUTT"):
        gdf = extract_hatch_areas(path, crs=CRS, on_degenerate="skip")

    assert list(gdf["layer"]) == ["SRF_GUT"]
    assert gdf["area_m2"].sum() == pytest.approx(100.0, abs=0.01)


def test_a_hatch_below_the_limit_is_still_read(tmp_path: Path) -> None:
    """The guard must not fire on a hatch that is merely detailed.

    Without this case the limit could sit anywhere — a guard that also rejects
    sound input trains the habit of switching it off.
    """
    path = _degenerate_dxf(tmp_path, 40)

    gdf = extract_hatch_areas(path, crs=CRS)

    assert set(gdf["layer"]) == {"SRF_GUT", "SRF_KAPUTT"}
