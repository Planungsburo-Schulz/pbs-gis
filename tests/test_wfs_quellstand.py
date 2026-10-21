"""``wfs.download`` hält den publizierten Stand der Quelle fest (FB 3.34b,
Schnitt 1, Producer-Seite): Beistell-Datei ``<datei>.quellstand.json`` neben
Cache- und Ausgabe-Datei, Register der zuletzt gesehenen Stände je Dienst.

Offline — GetFeature und HTTP sind Stubs. Die Stub-Antworten folgen den
Formen der Normen: WFS-2.0-Capabilities (``wfs:MetadataURL`` mit
``xlink:href`` je FeatureType, INSPIRE ``inspire_common:MetadataUrl`` auf
Dienst-Ebene) und ISO-19139-Metadatensatz (``gmd:dateStamp``,
``gmd:CI_Date``/``gmd:CI_DateTypeCode codeListValue``)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import geopandas as gpd
import pytest
import yaml
from shapely.geometry import Point

from pbs_gis import wfs
from pbs_gis.recipes import load_recipe

DIENST = "https://stub.invalid/wfs"
# Kennung je Dienst UND Layer: zwei Layer eines Dienstes tragen eigene Metadatensätze.
KENNUNG = DIENST + "#adv:AX_Flurstueck"
CSW_FLST = "https://csw.invalid/csw?REQUEST=GetRecordById&ID=flst"
CSW_GEB = "https://csw.invalid/csw?REQUEST=GetRecordById&ID=geb"
CSW_DIENST = "https://csw.invalid/csw?REQUEST=GetRecordById&ID=dienst"


def _caps(*, flst_url: str | None = CSW_FLST, dienst_url: str | None = None) -> bytes:
    flst_meta = f'<wfs:MetadataURL xlink:href="{flst_url.replace("&", "&amp;")}"/>' if flst_url else ""
    ext = ""
    if dienst_url:
        ext = (
            "<ows:OperationsMetadata><ows:ExtendedCapabilities>"
            "<inspire_dls:ExtendedCapabilities>"
            "<inspire_common:MetadataUrl>"
            f"<inspire_common:URL>{dienst_url.replace('&', '&amp;')}</inspire_common:URL>"
            "</inspire_common:MetadataUrl>"
            "</inspire_dls:ExtendedCapabilities>"
            "</ows:ExtendedCapabilities></ows:OperationsMetadata>"
        )
    return (
        '<wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs/2.0" '
        'xmlns:ows="http://www.opengis.net/ows/1.1" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        'xmlns:inspire_common="http://inspire.ec.europa.eu/schemas/common/1.0" '
        'xmlns:inspire_dls="http://inspire.ec.europa.eu/schemas/inspire_dls/1.0">'
        f"{ext}"
        "<wfs:FeatureTypeList>"
        "<wfs:FeatureType><wfs:Name>adv:AX_Gebaeude</wfs:Name>"
        f'<wfs:MetadataURL xlink:href="{CSW_GEB.replace("&", "&amp;")}"/></wfs:FeatureType>'
        f"<wfs:FeatureType><wfs:Name>adv:AX_Flurstueck</wfs:Name>{flst_meta}</wfs:FeatureType>"
        "</wfs:FeatureTypeList></wfs:WFS_Capabilities>"
    ).encode()


def _ci_date(datum: str, typ: str) -> str:
    tag = "gco:DateTime" if "T" in datum else "gco:Date"
    return (
        "<gmd:date><gmd:CI_Date>"
        f"<gmd:date><{tag}>{datum}</{tag}></gmd:date>"
        "<gmd:dateType><gmd:CI_DateTypeCode "
        'codeList="http://standards.iso.org/iso/19139/resources/gmxCodelists.xml#CI_DateTypeCode" '
        f'codeListValue="{typ}">{typ}</gmd:CI_DateTypeCode></gmd:dateType>'
        "</gmd:CI_Date></gmd:date>"
    )


def _csw(*, datestamp: str = "2026-07-09", citation: list[tuple[str, str]] = (),
         spezifikation: list[tuple[str, str]] = ()) -> bytes:
    cit = "".join(_ci_date(d, t) for d, t in citation)
    spec = "".join(_ci_date(d, t) for d, t in spezifikation)
    return (
        '<csw:GetRecordByIdResponse xmlns:csw="http://www.opengis.net/cat/csw/2.0.2">'
        '<gmd:MD_Metadata xmlns:gmd="http://www.isotc211.org/2005/gmd" '
        'xmlns:gco="http://www.isotc211.org/2005/gco">'
        f"<gmd:dateStamp><gco:Date>{datestamp}</gco:Date></gmd:dateStamp>"
        "<gmd:identificationInfo><gmd:MD_DataIdentification>"
        "<gmd:citation><gmd:CI_Citation>"
        "<gmd:title><gco:CharacterString>ALKIS</gco:CharacterString></gmd:title>"
        f"{cit}"
        "</gmd:CI_Citation></gmd:citation>"
        "</gmd:MD_DataIdentification></gmd:identificationInfo>"
        "<gmd:dataQualityInfo><gmd:DQ_DataQuality><gmd:report><gmd:DQ_DomainConsistency>"
        "<gmd:result><gmd:DQ_ConformanceResult><gmd:specification><gmd:CI_Citation>"
        f"{spec}"
        "</gmd:CI_Citation></gmd:specification></gmd:DQ_ConformanceResult></gmd:result>"
        "</gmd:DQ_DomainConsistency></gmd:report></gmd:DQ_DataQuality></gmd:dataQualityInfo>"
        "</gmd:MD_Metadata></csw:GetRecordByIdResponse>"
    ).encode()


class _Http:
    """HTTP-Stub: GetCapabilities am Dienst, Metadatensätze per exakter URL.
    Zählt jeden Aufruf; ein Wert vom Typ Exception wird geworfen."""

    def __init__(self, caps, records=None):
        self.caps = caps
        self.records = records or {}
        self.calls: list[str] = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        assert timeout == wfs.QUELLSTAND_TIMEOUT_S
        if "GetCapabilities" in url:
            antwort = self.caps
        else:
            antwort = self.records.get(url, OSError(f"404 {url}"))
        if isinstance(antwort, Exception):
            raise antwort
        return antwort


@pytest.fixture
def getfeature(monkeypatch):
    """GetFeature-Stub: ein Feature in EPSG:25833; lokale Dateien liest das
    echte read_file (Cache-Treffer)."""
    echt = gpd.read_file

    def stub(source, **kwargs):
        if not str(source).startswith(("WFS:", "https://stub.invalid")):
            return echt(source, **kwargs)
        return gpd.GeoDataFrame({"fid_x": [1], "geometry": [Point(400000, 6035000)]},
                                crs="EPSG:25833")

    monkeypatch.setattr(wfs.gpd, "read_file", stub)


def _http(monkeypatch, caps, records=None) -> _Http:
    h = _Http(caps, records)
    monkeypatch.setattr(wfs, "_http_get", h)
    return h


def _recipe(tmp_path, url=DIENST, layer="adv:AX_Flurstueck"):
    data = {"name": "stub_qs",
            "connection": {"type": "wfs", "url": url, "crs": "EPSG:25833", "layer": layer}}
    (tmp_path / "sources").mkdir(exist_ok=True)
    (tmp_path / "sources" / "stub_qs.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return load_recipe("stub_qs", project_dir=tmp_path)


def _download(tmp_path, *, no_cache=True, output="out.gpkg", **kw):
    recipe = kw.pop("recipe", None) or _recipe(tmp_path)
    out = tmp_path / output if output else None
    gdf = wfs.download(None, "", recipe=recipe, cache_dir=tmp_path / "cache",
                       no_cache=no_cache, output_path=out, **kw)
    return gdf, out


def _sidecar(p: Path) -> dict:
    return json.loads(p.with_name(p.name + ".quellstand.json").read_text(encoding="utf-8"))


def _cache_files(tmp_path) -> list[Path]:
    return sorted((tmp_path / "cache").glob("*.gpkg"))


def _register() -> Path:
    return Path(os.environ["XDG_STATE_HOME"]) / "pbs-gis" / "quellstand.json"


# --- Ermittlung des publizierten Stands -----------------------------------

def test_revision_datum_des_angefragten_layers(tmp_path, monkeypatch, getfeature):
    _http(monkeypatch, _caps(), {
        CSW_FLST: _csw(citation=[("2019-03-01", "creation"), ("2026-08-15", "revision")]),
        CSW_GEB: _csw(citation=[("2025-01-01", "revision")]),
    })
    _, out = _download(tmp_path)
    s = _sidecar(out)
    assert s["quell_stand"] == "2026-08-15"
    assert s["dienst"] == KENNUNG
    assert s["abgerufen"]  # ISO-Zeitpunkt
    (cache,) = _cache_files(tmp_path)
    assert _sidecar(cache) == s


def test_revision_als_datetime_wird_auf_datum_gekuerzt(tmp_path, monkeypatch, getfeature):
    _http(monkeypatch, _caps(), {CSW_FLST: _csw(citation=[("2026-08-15T10:30:00", "revision")])})
    _, out = _download(tmp_path)
    assert _sidecar(out)["quell_stand"] == "2026-08-15"


def test_ohne_revision_gilt_datestamp(tmp_path, monkeypatch, getfeature):
    _http(monkeypatch, _caps(), {
        CSW_FLST: _csw(datestamp="2026-07-09", citation=[("2019-03-01", "publication")]),
    })
    _, out = _download(tmp_path)
    assert _sidecar(out)["quell_stand"] == "2026-07-09"


def test_revision_ausserhalb_der_zitation_zaehlt_nicht(tmp_path, monkeypatch, getfeature):
    # Eine Spezifikations-Zitation (INSPIRE-Verordnung) mit revision-Datum ist
    # nicht der Stand der Daten.
    _http(monkeypatch, _caps(), {
        CSW_FLST: _csw(datestamp="2026-07-09", spezifikation=[("2030-12-31", "revision")]),
    })
    _, out = _download(tmp_path)
    assert _sidecar(out)["quell_stand"] == "2026-07-09"


def test_dienst_ebene_metadataurl_wenn_featuretype_keine_traegt(tmp_path, monkeypatch, getfeature):
    _http(monkeypatch, _caps(flst_url=None, dienst_url=CSW_DIENST), {
        CSW_DIENST: _csw(datestamp="2026-06-01"),
    })
    _, out = _download(tmp_path)
    assert _sidecar(out)["quell_stand"] == "2026-06-01"


def test_metadataurl_fehlt_ergibt_null(tmp_path, monkeypatch, getfeature):
    _http(monkeypatch, _caps(flst_url=None))
    _, out = _download(tmp_path)
    assert _sidecar(out)["quell_stand"] is None
    assert _sidecar(out)["dienst"] == KENNUNG


def test_netzfehler_ergibt_null_und_download_laeuft(tmp_path, monkeypatch, getfeature):
    _http(monkeypatch, OSError("Netz weg"))
    gdf, out = _download(tmp_path)
    assert len(gdf) == 1
    assert out.exists()
    assert _sidecar(out)["quell_stand"] is None


def test_unlesbarer_metadatensatz_ergibt_null(tmp_path, monkeypatch, getfeature):
    _http(monkeypatch, _caps(), {CSW_FLST: b"<html>Wartung</html"})
    _, out = _download(tmp_path)
    assert _sidecar(out)["quell_stand"] is None


def test_dienst_ohne_query_string(tmp_path, monkeypatch, getfeature):
    _http(monkeypatch, _caps(), {CSW_FLST: _csw()})
    recipe = _recipe(tmp_path, url=DIENST + "?map=alkis&x=1")
    _, out = _download(tmp_path, recipe=recipe)
    assert _sidecar(out)["dienst"] == KENNUNG


# --- Cache ----------------------------------------------------------------

def test_cache_treffer_kopiert_beistelldatei_ohne_abruf(tmp_path, monkeypatch, getfeature):
    _http(monkeypatch, _caps(), {CSW_FLST: _csw(citation=[("2026-08-15", "revision")])})
    _, out1 = _download(tmp_path, no_cache=False, output="a.gpkg")
    erst = _sidecar(out1)

    h2 = _http(monkeypatch, _caps(), {CSW_FLST: _csw(citation=[("2027-01-01", "revision")])})
    _, out2 = _download(tmp_path, no_cache=False, output="b.gpkg")
    assert h2.calls == []  # Cache-Treffer: kein Abruf, keine Ermittlung
    assert _sidecar(out2) == erst  # trägt den Stand des Abrufs, der den Cache erzeugte


def test_alter_cache_ohne_beistelldatei_gibt_null(tmp_path, monkeypatch, getfeature):
    _http(monkeypatch, _caps(), {CSW_FLST: _csw(citation=[("2026-08-15", "revision")])})
    _download(tmp_path, no_cache=False, output=None)
    (cache,) = _cache_files(tmp_path)
    cache.with_name(cache.name + ".quellstand.json").unlink()

    _, out = _download(tmp_path, no_cache=False, output="alt.gpkg")
    s = _sidecar(out)
    assert s["quell_stand"] is None
    assert s["dienst"] == KENNUNG


# --- Register ---------------------------------------------------------------

def test_register_wird_geschrieben_und_erhaelt_fremde_eintraege(tmp_path, monkeypatch, getfeature):
    reg = _register()
    reg.parent.mkdir(parents=True)
    reg.write_text(json.dumps({"schema_version": 1, "dienste": {
        "https://andere.invalid/wfs": {"quell_stand": "2025-01-01",
                                       "gesehen": "2025-01-02T00:00:00+00:00"}}}),
                   encoding="utf-8")
    _http(monkeypatch, _caps(), {CSW_FLST: _csw(citation=[("2026-08-15", "revision")])})
    _download(tmp_path)

    r = json.loads(reg.read_text(encoding="utf-8"))
    assert r["schema_version"] == 1
    assert r["dienste"][KENNUNG]["quell_stand"] == "2026-08-15"
    assert r["dienste"][KENNUNG]["gesehen"]
    assert r["dienste"]["https://andere.invalid/wfs"]["quell_stand"] == "2025-01-01"


def test_register_neu_angelegt(tmp_path, monkeypatch, getfeature):
    assert not _register().exists()
    _http(monkeypatch, _caps(), {CSW_FLST: _csw(citation=[("2026-08-15", "revision")])})
    _download(tmp_path)
    r = json.loads(_register().read_text(encoding="utf-8"))
    assert set(r["dienste"]) == {KENNUNG}


def test_null_ueberschreibt_keinen_eintrag(tmp_path, monkeypatch, getfeature):
    reg = _register()
    reg.parent.mkdir(parents=True)
    vorher = json.dumps({"schema_version": 1, "dienste": {
        KENNUNG: {"quell_stand": "2026-07-09", "gesehen": "2026-07-10T00:00:00+00:00"}}})
    reg.write_text(vorher, encoding="utf-8")
    _http(monkeypatch, OSError("Netz weg"))
    _download(tmp_path)
    assert reg.read_text(encoding="utf-8") == vorher


def test_null_legt_kein_register_an(tmp_path, monkeypatch, getfeature):
    _http(monkeypatch, _caps(flst_url=None))
    _download(tmp_path)
    assert not _register().exists()


@pytest.mark.parametrize("inhalt", [
    "{kein json",
    json.dumps({"dienste": {}}),  # schema_version fehlt
    json.dumps({"schema_version": 2, "dienste": {}}),  # unbekannte Version
    json.dumps({"schema_version": 1, "dienste": []}),
])
def test_kaputtes_register_bleibt_unberuehrt_und_meldet_laut(tmp_path, monkeypatch, getfeature,
                                                              capsys, inhalt):
    reg = _register()
    reg.parent.mkdir(parents=True)
    reg.write_text(inhalt, encoding="utf-8")
    _http(monkeypatch, _caps(), {CSW_FLST: _csw(citation=[("2026-08-15", "revision")])})
    gdf, out = _download(tmp_path)
    assert len(gdf) == 1 and out.exists()
    assert reg.read_text(encoding="utf-8") == inhalt
    zeilen = [z for z in capsys.readouterr().out.splitlines() if str(reg) in z]
    assert len(zeilen) == 1
    assert "Quell-Stand-Register" in zeilen[0]


def test_register_pfad_xdg_relativ_faellt_auf_home(monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", "relativ/state")
    assert wfs.register_pfad() == Path.home() / ".local" / "state" / "pbs-gis" / "quellstand.json"
    monkeypatch.setenv("XDG_STATE_HOME", "")
    assert wfs.register_pfad() == Path.home() / ".local" / "state" / "pbs-gis" / "quellstand.json"
    monkeypatch.setenv("XDG_STATE_HOME", "/abs/state")
    assert wfs.register_pfad() == Path("/abs/state/pbs-gis/quellstand.json")


# --- Abgleich gegen das maßgebliche pbs-projekt-Modell ----------------------

def _load_pbs_projekt_schema():
    """Import ``pbs_projekt.schema`` aus der Geschwister-Arbeitskopie (Muster
    ``tests/test_manifest.py``); skippt, wenn sie fehlt oder nicht importierbar
    ist."""
    src = os.environ.get("PBS_PROJEKT_SRC") or str(
        Path("~/dev/Planungsbüro-Schulz/pbs-projekt/src").expanduser()
    )
    if not Path(src).is_dir():
        pytest.skip(f"pbs-projekt src not found ({src}); Abgleich skipped")
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        from pbs_projekt import schema  # type: ignore
    except Exception as e:
        pytest.skip(f"pbs_projekt not importable ({e}); Abgleich skipped")
    return schema


def test_register_validiert_gegen_pbs_projekt_quellstandregister(tmp_path, monkeypatch, getfeature):
    schema = _load_pbs_projekt_schema()
    _http(monkeypatch, _caps(), {CSW_FLST: _csw(citation=[("2026-08-15", "revision")])})
    _download(tmp_path)
    data = json.loads(_register().read_text(encoding="utf-8"))
    reg = schema.QuellstandRegister.model_validate(data)
    assert reg.schema_version == wfs.QUELLSTAND_SCHEMA_VERSION == schema.QUELLSTAND_SCHEMA_VERSION
    assert str(reg.dienste[KENNUNG].quell_stand) == "2026-08-15"


def test_aelterer_stand_senkt_das_register_nicht(tmp_path, monkeypatch, getfeature):
    """Das Register hält je Kennung den NEUESTEN gesehenen Stand: ein Abruf, der
    ein älteres Datum liest, rückt nur `gesehen` vor."""
    reg = _register()
    reg.parent.mkdir(parents=True)
    reg.write_text(json.dumps({"schema_version": 1, "dienste": {
        KENNUNG: {"quell_stand": "2026-09-01", "gesehen": "2026-09-02T00:00:00+00:00"}}}),
                   encoding="utf-8")
    _http(monkeypatch, _caps(), {CSW_FLST: _csw(citation=[("2026-07-09", "revision")])})
    _download(tmp_path)
    r = json.loads(reg.read_text(encoding="utf-8"))
    assert r["dienste"][KENNUNG]["quell_stand"] == "2026-09-01"
    assert r["dienste"][KENNUNG]["gesehen"] != "2026-09-02T00:00:00+00:00"
