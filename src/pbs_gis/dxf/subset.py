"""
Write a reduced copy of a foreign CAD drawing holding only the layers we work with.

A planner's drawing arrives as a bundle of cross-referenced files carrying hundreds
of layers, of which a project uses a handful. Opening the working layers in CAD then
means hunting them inside the full drawing, and every reviewer repeats that hunt.
The subset written here is the drawing reduced to the agreed working set: same
geometry, same coordinates, same layer names — nothing else.

The source may be DXF or DWG (``read_cad`` converts DWG on the fly), so the subset
can be taken directly from what the planner sent.
"""

from __future__ import annotations

from pathlib import Path

from ezdxf.document import Drawing

from pbs_gis.dxf.document import new_dxf_document
from pbs_gis.dxf.read import CadReadError, read_cad


def dxf_subset(
    src: str | Path | Drawing,
    dest: str | Path,
    layers: list[str],
    *,
    version: str = "R2010",
    replace: bool = False,
) -> Path:
    """Write a DXF holding only *layers* of *src*, and return the written path.

    Layer names are matched exactly as CAD spells them, including the
    ``<xref>|<layer>`` form an external reference produces. A name absent from
    the source raises rather than writing a drawing that is silently short of a
    layer: an empty layer and a misspelt one look identical in CAD.

    Args:
        src: Source ``.dxf``/``.dwg`` path, or an already-read ezdxf document.
        dest: Target ``.dxf`` path.
        layers: Layer names to carry over. Must be non-empty.
        version: DXF version to write.
        replace: Overwrite *dest* if it already exists.

    Returns:
        Path to the written DXF file.

    Raises:
        CadReadError: No layers requested, a requested layer is absent from the
            source, or *dest* exists while ``replace`` is False.
    """
    from ezdxf.addons.importer import Importer

    if not layers:
        raise CadReadError("dxf_subset needs at least one layer name")

    dest_p = Path(dest)
    if dest_p.exists() and not replace:
        raise CadReadError(
            f"Destination already exists: {dest_p} (pass replace=True to overwrite)"
        )

    source = src if isinstance(src, Drawing) else read_cad(src)

    wanted = list(dict.fromkeys(layers))
    defined = {layer.dxf.name for layer in source.layers}
    missing = [name for name in wanted if name not in defined]
    if missing:
        raise CadReadError(
            "Layer(s) not in source drawing: "
            + ", ".join(repr(m) for m in missing)
            + f" — source defines {len(defined)} layers"
        )

    wanted_set = set(wanted)
    entities = [e for e in source.modelspace() if e.dxf.layer in wanted_set]

    target = new_dxf_document(version)
    importer = Importer(source, target)
    importer.import_entities(entities)
    importer.finalize()

    # A requested layer holding no model-space entity of its own — its geometry
    # sits inside blocks, or the planner left it empty — is imported by nothing,
    # so it would be absent from the subset's layer list. Carry those definitions
    # over by hand, with the colour and linetype the source gives them.
    for name in wanted:
        if name in target.layers:
            continue
        src_layer = source.layers.get(name)
        linetype = src_layer.dxf.get("linetype", "CONTINUOUS")
        if linetype not in target.linetypes:
            # Referencing a linetype the target does not define writes a drawing
            # CAD refuses to open; the layer is worth more than its dash pattern.
            linetype = "CONTINUOUS"
        target.layers.add(
            name,
            color=src_layer.dxf.get("color", 7),
            linetype=linetype,
        )

    dest_p.parent.mkdir(parents=True, exist_ok=True)
    target.saveas(dest_p)
    return dest_p
