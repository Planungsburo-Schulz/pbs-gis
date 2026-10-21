"""Stand-Manifest emission for published workflow artefacts (Phase-4 P4-B.1).

A workflow step marked ``publiziert: true`` — or a template that publishes a
data product (e.g. ``publish_bilanz``) — writes a *Stand-Manifest* next to its
output: ``<artefakt>.manifest.yaml``.  The manifest records the production date,
a sha256 of the artefact, an echo of the generation parameters, the input
sources with their hashes, and the producing tool.

ABGLEICH (pattern SCHRITT_VOKABULAR): the authoritative schema for this file is
``pbs_projekt.schema.ManifestDatei`` (pbs-projekt, commit d0db256,
schema_version 1).  pbs_gis is the *producer*; pbs-projekt is the *consumer*
that pins ``stand``+``hash`` via a ``produziert`` edge and strictly re-reads this
manifest (never re-invokes the producer).  This module emits the format from its
own copy — there is NO runtime dependency on pbs-projekt.  On every field change
to ManifestDatei, this emitter must be pulled along; the Abgleich-Test
(``tests/test_manifest.py``) validates a real emission against the authoritative
pydantic model when pbs-projekt is reachable.

Field mapping (must stay in lockstep with ManifestDatei):
    schema_version : int   — MANIFEST_SCHEMA_VERSION (== 1)
    stand          : date  — production date (ISO, today)
    artefakt       : str   — artefact file name (echo)
    hash           : str   — sha256 hex of the artefact bytes
    parameter      : dict  — echo of the generation parameters
    quellen        : list  — [{pfad, hash[, quell_stand, dienst]}] input
                             provenance (sha256 each); quell_stand (date|null)
                             and dienst only where the source has a
                             ``<quelle>.quellstand.json`` (wfs.download)
    werkzeug       : str   — producer tool/version (diagnostic)
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import yaml

# Mirror of pbs_projekt.schema.MANIFEST_SCHEMA_VERSION (authority: commit
# d0db256).  A bump there is a breaking change here — see module docstring and
# the Abgleich-Test.
MANIFEST_SCHEMA_VERSION = 1

MANIFEST_SUFFIX = ".manifest.yaml"

# Beistell-Datei des Quell-Stands (geschrieben von wfs.download, FB 3.34b).
QUELLSTAND_SUFFIX = ".quellstand.json"


def sha256_file(path: str | Path) -> str:
    """Return the hex sha256 digest of a file's bytes (streamed)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def werkzeug_id(producer: str) -> str:
    """Build the ``werkzeug`` diagnostic string: ``pbs_gis/<version> (<producer>)``.

    *producer* names the emitting template/recipe/script/step.
    """
    try:
        from importlib.metadata import version

        v = version("pbs-gis")
    except Exception:
        v = "unknown"
    return f"pbs_gis/{v} ({producer})"


def _rel(path: Path, basis: Path | None) -> str:
    """Path string for the manifest — relative to *basis* when possible.

    Portable provenance: a manifest that travels with its project folder (HiDrive)
    should not carry absolute machine paths.  Falls back to the absolute string
    when *path* is not under *basis*.
    """
    if basis is not None:
        try:
            return str(path.relative_to(basis))
        except ValueError:
            pass
    return str(path)


def _quell_stand(quelle: Path) -> dict[str, str | None]:
    """``quell_stand`` und ``dienst`` aus der Beistell-Datei
    ``<quelle>.quellstand.json``, die ``wfs.download`` neben jede Download-Datei
    legt; ohne Beistell-Datei keines der beiden Felder.  Eine Beistell-Datei,
    die kein JSON-Objekt mit ``dienst`` ist, bricht: ein Manifest dokumentiert
    keine Herkunft, die es nicht lesen kann."""
    beistell = quelle.with_name(quelle.name + QUELLSTAND_SUFFIX)
    if not beistell.exists():
        return {}
    try:
        daten = json.loads(beistell.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"Quell-Stand-Beistell-Datei ist kein gültiges JSON: {beistell}") from exc
    dienst = daten.get("dienst") if isinstance(daten, dict) else None
    if not isinstance(dienst, str) or not dienst:
        raise ValueError(f"Quell-Stand-Beistell-Datei ohne 'dienst': {beistell}")
    return {"quell_stand": daten.get("quell_stand"), "dienst": dienst}


def write_manifest(
    artefakt: str | Path,
    *,
    parameter: dict[str, Any],
    quellen: list[str | Path],
    werkzeug: str,
    stand: date | None = None,
    basis: str | Path | None = None,
) -> Path:
    """Write ``<artefakt>.manifest.yaml`` next to *artefakt*.

    Args:
        artefakt: Path to the produced artefact (must exist — it is hashed).
        parameter: Echo of the generation parameters (diagnostic/provenance).
        quellen: Input source files; each is hashed for provenance.  A missing
            source is a hard error — a manifest documenting a source that wasn't
            there is a lie, not a partial result.
        werkzeug: Producer tool identifier/version string (see :func:`werkzeug_id`).
        stand: Production date; defaults to today.
        basis: If given, ``quellen`` paths are recorded relative to it (portable
            provenance); the artefact echo is always the bare file name.

    Returns:
        Path to the written manifest file.
    """
    artefakt = Path(artefakt)
    if not artefakt.exists():
        raise FileNotFoundError(f"Artefakt für Manifest fehlt: {artefakt}")

    basis_path = Path(basis) if basis is not None else None

    quellen_out: list[dict[str, str | None]] = []
    for q in quellen:
        q = Path(q)
        if not q.exists():
            raise FileNotFoundError(f"Quelle für Manifest fehlt: {q}")
        eintrag: dict[str, str | None] = {"pfad": _rel(q, basis_path), "hash": sha256_file(q)}
        eintrag.update(_quell_stand(q))
        quellen_out.append(eintrag)

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "stand": (stand or date.today()).isoformat(),
        "artefakt": artefakt.name,
        "hash": sha256_file(artefakt),
        "parameter": dict(parameter),
        "quellen": quellen_out,
        "werkzeug": werkzeug,
    }

    manifest_path = artefakt.with_name(artefakt.name + MANIFEST_SUFFIX)
    with open(manifest_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(manifest, f, allow_unicode=True, sort_keys=False)
    return manifest_path
