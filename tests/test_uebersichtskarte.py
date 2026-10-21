"""Selbstprobe der Übersichtskarte (``pbs_gis.uebersichtskarte``): fünf Anlagen
aus einem synthetischen Geltungsbereich, der DXF trägt Geometrie."""

from pbs_gis.uebersichtskarte import selbstprobe


def test_selbstprobe_pass():
    assert selbstprobe() is True
