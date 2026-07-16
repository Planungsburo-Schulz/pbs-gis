"""
Read CAD input files (.dxf and .dwg) into ezdxf documents.

Single entry point for *foreign* CAD input — the files surveyors, architects and
utilities send us. DXF is read natively by ezdxf; DWG is converted on the fly via
the ODA File Converter (ezdxf's ``odafc`` addon).

DWG support requires the ODA File Converter, which is not a Python package and
must be installed separately (Arch/CachyOS: ``yay -S oda-file-converter``;
otherwise https://www.opendesign.com/guestfiles/oda_file_converter). Without it,
DWG input raises :class:`CadReadError` naming the fix — it never falls back
silently, and never returns a partial document.

libredwg (``dwg2dxf``) is deliberately *not* used as a fallback: its DXF output
was found to violate the group-code grammar (ezdxf rejects the MTEXT it emits),
so it would trade a loud error for silent data loss.
"""

from __future__ import annotations

from pathlib import Path

import ezdxf
from ezdxf.document import Drawing

# Input formats we accept. Anything else is a loud error, never a guess.
DXF_SUFFIXES = frozenset({".dxf"})
DWG_SUFFIXES = frozenset({".dwg"})
CAD_SUFFIXES = DXF_SUFFIXES | DWG_SUFFIXES

_ODA_MISSING_HINT = (
    "DWG input requires the ODA File Converter, which is not installed.\n"
    "  Arch/CachyOS: yay -S oda-file-converter\n"
    "  otherwise:    https://www.opendesign.com/guestfiles/oda_file_converter\n"
    "Alternative without any install: open the file in CAD and save it as DXF "
    "next to the DWG (the pattern already used in project Zeichnung/ folders)."
)


class CadReadError(Exception):
    """Raised when a CAD input file cannot be read. Always names the cause."""


def is_dwg_supported() -> bool:
    """Report whether DWG input can be read (i.e. ODA File Converter present)."""
    from ezdxf.addons import odafc

    return bool(odafc.is_installed())


def read_cad(path: str | Path, *, audit: bool = False) -> Drawing:
    """Read a DXF or DWG file into an ezdxf document.

    Args:
        path: Path to a ``.dxf`` or ``.dwg`` file.
        audit: Run ezdxf's audit/recover pass on the converted DWG. Ignored for DXF.

    Returns:
        The loaded ezdxf ``Drawing``.

    Raises:
        CadReadError: File missing, suffix not in :data:`CAD_SUFFIXES`, DWG given
            without the ODA File Converter installed, or the read/conversion failed.
    """
    p = Path(path)
    if not p.is_file():
        raise CadReadError(f"CAD file not found: {p}")

    suffix = p.suffix.lower()
    if suffix in DXF_SUFFIXES:
        try:
            return ezdxf.readfile(str(p))
        except Exception as exc:  # noqa: BLE001 - re-raised with the path attached
            raise CadReadError(f"Cannot read DXF {p}: {exc}") from exc
    if suffix in DWG_SUFFIXES:
        return _read_dwg(p, audit=audit)

    raise CadReadError(
        f"Unsupported CAD format {suffix!r} for {p} — "
        f"expected one of {sorted(CAD_SUFFIXES)}."
    )


def dwg_to_dxf(
    src: str | Path,
    dest: str | Path | None = None,
    *,
    version: str = "R2018",
    replace: bool = False,
) -> Path:
    """Convert a DWG to a DXF file on disk and return the DXF path.

    Use this to make a foreign DWG readable once and keep the DXF next to it,
    instead of re-converting on every run.

    Args:
        src: Path to the ``.dwg`` file.
        dest: Target ``.dxf`` path. Defaults to ``src`` with a ``.dxf`` suffix.
        version: DXF version to write.
        replace: Overwrite ``dest`` if it already exists.

    Returns:
        Path to the written DXF file.

    Raises:
        CadReadError: Source missing/not a DWG, ODA File Converter absent,
            destination exists while ``replace`` is False, or conversion failed.
    """
    from ezdxf.addons import odafc

    src_p = Path(src)
    if not src_p.is_file():
        raise CadReadError(f"DWG file not found: {src_p}")
    if src_p.suffix.lower() not in DWG_SUFFIXES:
        raise CadReadError(f"Not a DWG file: {src_p}")

    dest_p = Path(dest) if dest is not None else src_p.with_suffix(".dxf")
    if dest_p.exists() and not replace:
        raise CadReadError(
            f"Destination already exists: {dest_p} (pass replace=True to overwrite)"
        )
    if not odafc.is_installed():
        raise CadReadError(f"Cannot convert {src_p}. {_ODA_MISSING_HINT}")

    try:
        odafc.convert(src_p, dest_p, version=version, replace=replace)
    except Exception as exc:  # noqa: BLE001 - re-raised with the path attached
        raise CadReadError(f"DWG->DXF conversion failed for {src_p}: {exc}") from exc

    if not dest_p.is_file():
        raise CadReadError(
            f"DWG->DXF conversion reported success but produced no file: {dest_p}"
        )
    return dest_p


def _read_dwg(path: Path, *, audit: bool) -> Drawing:
    """Read a DWG via the ODA File Converter. Loud if the converter is absent."""
    from ezdxf.addons import odafc

    if not odafc.is_installed():
        raise CadReadError(f"Cannot read {path}. {_ODA_MISSING_HINT}")
    try:
        return odafc.readfile(str(path), audit=audit)
    except Exception as exc:  # noqa: BLE001 - re-raised with the path attached
        raise CadReadError(f"Cannot read DWG {path}: {exc}") from exc
