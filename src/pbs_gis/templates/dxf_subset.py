"""dxf_subset — Write a DXF holding only the layers a project works with.

Reduces a planner's drawing (DXF or DWG) to the agreed working layers, keeping
geometry, coordinates and layer names as they are. Everything else stays behind.

Use when: The drawing that arrived carries hundreds of layers across xrefs and
the project touches a handful — for opening the working set in CAD, for handing
a reviewer exactly what a statement rests on, and as the input a workflow step
reads instead of the full bundle.

Example workflow.yaml, one source::

    - name: Arbeits-DXF
      template: dxf_subset
      params:
        dxf: Grundlagen/Plan/X_Entwurf.dwg
        layers:
          - PL_LIN_Materialwechsel
      output: output/arbeitslayer.dxf
      run: always

An xref bundle splits a plan by trade, so the working layers usually come from
several files — gather them into one drawing with ``sources:``::

    - name: Arbeits-DXF
      template: dxf_subset
      params:
        sources:
          - dxf: Grundlagen/Plan/Planung/X_Entwurf_PG.dxf
            layers: [PG_Planungsgrenze]
          - dxf: Grundlagen/Plan/Planung/X_Entwurf_PL.dxf
            layers: [PL_LIN_Materialwechsel]
      output: output/arbeitslayer.dxf
      run: always
"""

from __future__ import annotations

from pathlib import Path

from pbs_gis.templates import register


@register(
    "dxf_subset",
    description="Write a DXF holding only the named working layers of one or more source drawings",
    params=["dxf", "layers", "sources", "version"],
)
def dxf_subset_step(params: dict, project_dir: Path, output_path: Path) -> bool:
    """Write the working-layer subset of one or more CAD drawings.

    Params:
        dxf: Path to the source ``.dxf``/``.dwg`` (relative to project root).
            Use with ``layers``; mutually exclusive with ``sources``.
        layers: List of layer names to carry over, spelled as CAD spells them
            (an xref layer keeps its ``<xref>|<layer>`` form).
        sources: List of ``{dxf:, layers:}`` mappings, for a working set spread
            across the files of an xref bundle.
        version (optional): DXF version to write.  Default ``"R2010"``.
    """
    from pbs_gis import dxf_subset_from_sources
    from pbs_gis.dxf.read import CadReadError

    if ("sources" in params) == ("dxf" in params):
        raise CadReadError(
            "dxf_subset takes either 'dxf' + 'layers' or 'sources', not both and not neither"
        )

    if "sources" in params:
        entries = params["sources"]
    else:
        entries = [{"dxf": params["dxf"], "layers": params["layers"]}]

    sources = [(project_dir / e["dxf"], e["layers"]) for e in entries]
    layers = [name for _, names in sources for name in names]

    written = dxf_subset_from_sources(
        sources,
        output_path,
        version=params.get("version", "R2010"),
        replace=True,
    )

    import ezdxf

    msp = ezdxf.readfile(written).modelspace()
    print(f"  {len(layers)} layer(s), {len(msp)} entities -> {written}")
    return True
