"""DXF utilities: CAD input reading, document creation, geometry extraction,
conversion, and Map Object Data."""

from pbs_gis.dxf.convert import shapefile_to_dxf
from pbs_gis.dxf.document import new_dxf_document, ensure_layer
from pbs_gis.dxf.extract import (
    extract_dxf_circles,
    extract_dxf_layers,
    interpolate_bulge_arc,
    lwpolyline_to_coords,
    save_layers_as_shapefiles,
)
from pbs_gis.dxf.map_od import (
    OD_EXTENSION_DICT_KEY,
    attach_od_to_entity,
    encode_od_1004,
    get_table_handle_by_name,
)
from pbs_gis.dxf.read import (
    CAD_SUFFIXES,
    CadReadError,
    dwg_to_dxf,
    is_dwg_supported,
    read_cad,
)

__all__ = [
    "shapefile_to_dxf",
    "new_dxf_document",
    "ensure_layer",
    "extract_dxf_layers",
    "extract_dxf_circles",
    "interpolate_bulge_arc",
    "lwpolyline_to_coords",
    "save_layers_as_shapefiles",
    "read_cad",
    "dwg_to_dxf",
    "is_dwg_supported",
    "CadReadError",
    "CAD_SUFFIXES",
    "OD_EXTENSION_DICT_KEY",
    "attach_od_to_entity",
    "encode_od_1004",
    "get_table_handle_by_name",
]
