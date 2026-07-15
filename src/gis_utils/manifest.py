"""Stand-Manifest emission for published workflow artefacts (Phase-4 P4-B.1).

A workflow step marked ``publiziert: true`` — or a template that publishes a
data product (e.g. ``publish_bilanz``) — writes a *Stand-Manifest* next to its
output: ``<artefakt>.manifest.yaml``.  The manifest records the production date,
a sha256 of the artefact, an echo of the generation parameters, the input
sources with their hashes, and the producing tool.

ABGLEICH (pattern SCHRITT_VOKABULAR): the authoritative schema for this file is
``pbs_projekt.schema.ManifestDatei`` (pbs-projekt, commit d0db256,
schema_version 1).  gis_utils is the *producer*; pbs-projekt is the *consumer*
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
    quellen        : list  — [{pfad, hash}] input provenance (sha256 each)
    werkzeug       : str   — producer tool/version (diagnostic)
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Any

import yaml

# Mirror of pbs_projekt.schema.MANIFEST_SCHEMA_VERSION (authority: commit
# d0db256).  A bump there is a breaking change here — see module docstring and
# the Abgleich-Test.
MANIFEST_SCHEMA_VERSION = 1

MANIFEST_SUFFIX = ".manifest.yaml"


def sha256_file(path: str | Path) -> str:
    """Return the hex sha256 digest of a file's bytes (streamed)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def werkzeug_id(producer: str) -> str:
    """Build the ``werkzeug`` diagnostic string: ``gis_utils/<version> (<producer>)``.

    *producer* names the emitting template/recipe/script/step.
    """
    try:
        from importlib.metadata import version

        v = version("gis-utils")
    except Exception:
        v = "unknown"
    return f"gis_utils/{v} ({producer})"


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

    quellen_out: list[dict[str, str]] = []
    for q in quellen:
        q = Path(q)
        if not q.exists():
            raise FileNotFoundError(f"Quelle für Manifest fehlt: {q}")
        quellen_out.append({"pfad": _rel(q, basis_path), "hash": sha256_file(q)})

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
