"""A WFS ExceptionReport must fail loudly, not degrade to "0 Features".

Some infrastructures answer an invalid GetFeature with an OGC
``ows:ExceptionReport`` XML body and HTTP 200.  Parsed as a FeatureCollection
this yields zero members — a silent empty classification.  ``_parse_wfs_response``
detects the error XML and raises instead.
"""

from __future__ import annotations

import pytest

from pbs_gis import atkis

_EXCEPTION_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/1.1" version="2.0.0">
  <ows:Exception exceptionCode="InvalidParameterValue" locator="typeNames">
    <ows:ExceptionText>Unknown type name 'adv:Foo'</ows:ExceptionText>
  </ows:Exception>
</ows:ExceptionReport>"""

_EMPTY_COLLECTION_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" '
    b'numberMatched="0" numberReturned="0"/>'
)


class _FakeResp:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


def test_parse_wfs_response_raises_on_exception_report():
    with pytest.raises(RuntimeError, match="Unknown type name"):
        atkis._parse_wfs_response(_EXCEPTION_XML)


def test_parse_wfs_response_passes_valid_collection():
    root = atkis._parse_wfs_response(_EMPTY_COLLECTION_XML)
    assert root.tag.endswith("FeatureCollection")


def test_download_raises_on_http200_exception_report(monkeypatch):
    monkeypatch.setattr(atkis.requests, "get", lambda *a, **k: _FakeResp(_EXCEPTION_XML))
    with pytest.raises(RuntimeError, match="WFS-Dienstfehler"):
        atkis._download_features_with_xlinks(
            "http://example.invalid/wfs", "adv:Foo", (0, 0, 1, 1), "EPSG:25833",
        )


def test_download_empty_collection_yields_zero_features(monkeypatch):
    monkeypatch.setattr(
        atkis.requests, "get", lambda *a, **k: _FakeResp(_EMPTY_COLLECTION_XML)
    )
    gdf, xlinks = atkis._download_features_with_xlinks(
        "http://example.invalid/wfs", "adv:AX_Strassenachse", (0, 0, 1, 1), "EPSG:25833",
    )
    assert len(gdf) == 0
    assert xlinks == {}
