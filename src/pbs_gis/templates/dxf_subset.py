"""dxf_subset — Write a DXF holding only the layers a project works with.

Reduces a planner's drawing (DXF or DWG) to the agreed working layers, keeping
geometry, coordinates and layer names as they are. Everything else stays behind.

Use when: The drawing that arrived carries hundreds of layers across xrefs and
the project touches a handful — for opening the working set in CAD, for handing
a reviewer exactly what a statement rests on, and as the input a workflow step
reads instead of the full bundle.

Example workflow.yaml::

    - name: Arbeits-DXF
      template: dxf_subset
      params:
        dxf: Grundlagen/Plan/X_Entwurf.dwg
        layers:
          - PL_LIN_Materialwechsel
      output: output/arbeitslayer.dxf
      run: always
"""

from __future__ import annotations

from pathlib import Path

from pbs_gis.templates import register


@register(
    "dxf_subset",
    description="Write a DXF holding only the named working layers of a source drawing",
    params=["dxf", "layers", "version"],
)
def dxf_subset_step(params: dict, project_dir: Path, output_path: Path) -> bool:
    """Write the working-layer subset of a CAD drawing.

    Params:
        dxf: Path to the source ``.dxf``/``.dwg`` (relative to project root).
        layers: List of layer names to carry over, spelled as CAD spells them
            (an xref layer keeps its ``<xref>|<layer>`` form).
        version (optional): DXF version to write.  Default ``"R2010"``.
    """
    from pbs_gis import dxf_subset

    src = project_dir / params["dxf"]
    layers = params["layers"]

    written = dxf_subset(
        src,
        output_path,
        layers,
        version=params.get("version", "R2010"),
        replace=True,
    )

    import ezdxf

    msp = ezdxf.readfile(written).modelspace()
    print(f"  {len(layers)} layer(s), {len(msp)} entities -> {written}")
    return True
