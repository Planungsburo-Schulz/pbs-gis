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

import os
import shutil
from pathlib import Path

import ezdxf
from ezdxf.document import Drawing

# Input formats we accept. Anything else is a loud error, never a guess.
DXF_SUFFIXES = frozenset({".dxf"})
DWG_SUFFIXES = frozenset({".dwg"})
CAD_SUFFIXES = DXF_SUFFIXES | DWG_SUFFIXES

# The real converter executables, preferred over any PATH wrapper. Distro wrappers
# (Arch/AUR /usr/bin/oda-file-converter) forward their arguments as unquoted "$@",
# which word-splits every path containing a space — the converter then exits 0 and
# writes NOTHING. Our project paths are full of spaces ("Öffentlich Planungsbüro
# Schulz/…"), so the wrapper is a silent-failure trap; call the binary directly and
# supply its private library dir ourselves. Verified 2026-07-16 on CachyOS:
# wrapper + spaced path -> exit 0, no output; direct binary + same path -> converts.
ODA_DIRECT_CANDIDATES = (
    Path("/opt/oda-file-converter/oda-file-converter"),
    Path("/opt/ODAFileConverter/ODAFileConverter"),
)

# Fallback: resolve by name from PATH. Distributions disagree on the name, and
# ezdxf only ever probes for "ODAFileConverter", so a correctly installed
# converter otherwise reports "not installed".
ODA_EXEC_NAMES = (
    "ODAFileConverter",
    "oda-file-converter",
    "TeighaFileConverter",
)

_ODA_MISSING_HINT = (
    "DWG input requires the ODA File Converter, which is not installed.\n"
    "  Arch/CachyOS: yay -S oda-file-converter\n"
    "  otherwise:    https://www.opendesign.com/guestfiles/oda_file_converter\n"
    "Alternative without any install: open the file in CAD and save it as DXF "
    "next to the DWG (the pattern already used in project Zeichnung/ folders)."
)


class CadReadError(Exception):
    """Raised when a CAD input file cannot be read. Always names the cause."""


def find_oda_exec() -> Path | None:
    """Locate the ODA File Converter executable.

    Prefers the real binary in :data:`ODA_DIRECT_CANDIDATES` over any PATH wrapper
    (see the note there: wrappers silently drop spaced paths). Falls back to
    resolving :data:`ODA_EXEC_NAMES` from PATH.

    Returns the resolved path, or None if no converter is installed.
    """
    for candidate in ODA_DIRECT_CANDIDATES:
        if candidate.is_file():
            return candidate
    for name in ODA_EXEC_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def _ensure_odafc_configured() -> bool:
    """Point ezdxf's odafc addon at the installed converter and make it runnable.

    Sets ezdxf's ``odafc-addon.unix_exec_path`` and, when the converter ships its
    shared libraries in a private directory next to the binary, prepends that
    directory to ``LD_LIBRARY_PATH`` in this process's environment — the distro
    wrapper we bypass is what would normally do this, and ezdxf's subprocess
    inherits the environment.

    An explicitly configured ``unix_exec_path`` is respected and never clobbered.
    Returns True if a usable converter is configured.
    """
    from ezdxf.addons import odafc

    if odafc.is_installed():
        return True

    found = find_oda_exec()
    if found is None:
        return False

    # The binary needs its own libs on the loader path (normally the wrapper's job).
    lib_dir = found.parent
    if (lib_dir / "libTD_Root.so").exists() or list(lib_dir.glob("libTD_*.so")):
        current = os.environ.get("LD_LIBRARY_PATH", "")
        parts = [p for p in current.split(os.pathsep) if p]
        if str(lib_dir) not in parts:
            os.environ["LD_LIBRARY_PATH"] = os.pathsep.join([str(lib_dir), *parts])

    ezdxf.options.set("odafc-addon", "unix_exec_path", str(found))
    return bool(odafc.is_installed())


def is_dwg_supported() -> bool:
    """Report whether DWG input can be read (i.e. ODA File Converter present).

    Resolves the converter under its distribution-specific names and locations, so
    a correctly installed converter is reported as available even when ezdxf's own
    probe (which only knows the name ``ODAFileConverter``) misses it.

    Note: reading DWG runs the converter headlessly (ezdxf starts a dummy X display
    via Xvfb); no GUI window appears.
    """
    return _ensure_odafc_configured()


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
    if not _ensure_odafc_configured():
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

    if not _ensure_odafc_configured():
        raise CadReadError(f"Cannot read {path}. {_ODA_MISSING_HINT}")
    try:
        return odafc.readfile(str(path), audit=audit)
    except Exception as exc:  # noqa: BLE001 - re-raised with the path attached
        raise CadReadError(f"Cannot read DWG {path}: {exc}") from exc
