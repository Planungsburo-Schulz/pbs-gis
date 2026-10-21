"""mv_alkis bietet keine Schutzgebiets-Layer ohne Geometrie bzw. ohne Treffer an."""

from pbs_gis.recipes import load_recipe


def test_mv_alkis_offers_no_dead_schutzgebiet_layers():
    aliases = load_recipe("mv_alkis").layer_aliases()
    assert "naturschutzrecht" not in aliases
    assert "schutzgebiet_natur" not in aliases
    assert "denkmalschutz" in aliases
