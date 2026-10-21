"""LUNG-MV-Recipes: Mojibake-Reparatur, Ziel-CRS, Recipe-Struktur (offline)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point

from pbs_gis.recipes import load_and_run_hook, load_recipe

HOOK = "lung_demojibake.py"
LUNG_RECIPES = ("mv_lung_schutzgebiete", "mv_lung_wasserschutz")


def _fixture_gdf() -> gpd.GeoDataFrame:
    # EPSG:5650-Rechtswert mit Zonen-Präfix 33 (Insel Rügen)
    return gpd.GeoDataFrame(
        {
            "NAME": ["OstrÃ¼gen", "Ostrügen", "Granitz", None],
            "WERT": [1, 2, 3, 4],
            "ZUSATZ": ["GroÃ\x9fer Wotig", "Großer Wotig", "ASCII", "Ã"],
        },
        geometry=[Point(33400000, 6030000)] * 4,
        crs="EPSG:5650",
    )


def test_lung_hook_repairs_mojibake_names():
    out = load_and_run_hook(HOOK, "post_process", _fixture_gdf())
    assert list(out["NAME"][:3]) == ["Ostrügen", "Ostrügen", "Granitz"]
    assert out["ZUSATZ"][0] == "Großer Wotig"


def test_lung_hook_leaves_correct_text_and_non_strings_untouched():
    out = load_and_run_hook(HOOK, "post_process", _fixture_gdf())
    assert out["NAME"][1] == "Ostrügen"
    assert out["ZUSATZ"][1] == "Großer Wotig"
    assert out["ZUSATZ"][2] == "ASCII"
    assert out["NAME"].isna().iloc[3]
    # ein Einzelzeichen ohne gültige UTF-8-Folge bleibt, wie es ist
    assert out["ZUSATZ"][3] == "Ã"
    assert list(out["WERT"]) == [1, 2, 3, 4]


def test_lung_hook_outputs_etrs89_utm33():
    out = load_and_run_hook(HOOK, "post_process", _fixture_gdf())
    assert out.crs.to_epsg() == 25833
    # Rechtswert verliert das Zonen-Präfix: 33 400 000 -> ~400 000
    assert 300_000 < out.geometry.x.iloc[0] < 500_000


@pytest.mark.parametrize("name", LUNG_RECIPES)
def test_lung_recipe_declares_5650_connection_and_hook(name):
    recipe = load_recipe(name)
    assert recipe.connection["type"] == "wfs"
    assert recipe.connection["crs"] == "EPSG:5650"
    assert recipe.hooks == HOOK
    assert recipe.is_multi_layer


def test_lung_schutzgebiete_layer_set():
    recipe = load_recipe("mv_lung_schutzgebiete")
    assert sorted(recipe.layer_aliases()) == sorted([
        "nsg", "lsg", "ffh", "spa", "nationalpark", "naturpark",
        "biosphaerenreservat", "geschuetzter_landschaftsbestandteil",
        "naturdenkmal_flaeche", "naturdenkmal_punkt",
    ])


def test_lung_wasserschutz_layer_set():
    recipe = load_recipe("mv_lung_wasserschutz")
    assert sorted(recipe.layer_aliases()) == ["ksg", "uesg", "wsg"]


@pytest.mark.parametrize("name", LUNG_RECIPES)
def test_lung_layer_recipes_resolve_hook_per_layer(name):
    recipe = load_recipe(name)
    for alias in recipe.layer_aliases():
        assert recipe.get_layer_recipe(alias).hooks == HOOK

