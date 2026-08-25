"""GIS utilities: DXF tools, geometry operations, reporting, OSM, WMS vectorization."""

from pbs_gis.md_table import markdown_table
from pbs_gis.dxf.convert import shapefile_to_dxf
from pbs_gis.dxf.document import new_dxf_document, ensure_layer
from pbs_gis.dxf.subset import dxf_subset
from pbs_gis.dxf.extract import (
    extract_3dsolids,
    extract_dxf_circles,
    extract_dxf_layers,
    interpolate_bulge_arc,
    lwpolyline_to_coords,
    save_layers_as_shapefiles,
    solid3d_to_circle,
    _solid3d_center_2d,
    _solid3d_to_2d_polygon,
    _solid3d_to_world_points,
)
from pbs_gis.dxf.map_od import (
    OD_EXTENSION_DICT_KEY,
    attach_od_to_entity,
    encode_od_1004,
    get_table_handle_by_name,
)
from pbs_gis.cad import (
    LayerSpec,
    Style,
    export_layers,
    load_styles,
)
from pbs_gis.geometry import (
    buffer_ring_zones,
    classify_direction,
    distance_to_nearest,
    extend_line,
    filter_lines_by_direction,
    find_column,
    lines_to_polygon,
    load_and_union,
    make_valid_gdf,
    morphological_filter,
    points_with_buffers,
    repair_geometry,
    remove_inner_rings,
    snap_endpoints,
    strip_utm_zone_prefix,
    subtract_geometries,
    subtract_smaller_overlaps,
)
from pbs_gis.reporting import (
    area_by_category,
    area_report,
    conflict_matrix,
    intersection_areas,
)
from pbs_gis.alkis import find_flurstuecke
from pbs_gis.georef import register_features, SimilarityTransform
from pbs_gis.einpassung import (
    EinpassungError,
    apply_points,
    chain_over_overview,
    chamfer_field,
    coarse_fit,
    compose,
    icp_helmert,
    isolation,
    overview_vertices,
    reference_rings,
    resample,
    sample_rings,
    sheet_mosaic,
    sheet_vectors,
    vertices,
)
from pbs_gis.atkis import fetch_classified_features, fetch_classified_guide
from pbs_gis.catalog import catalog
from pbs_gis.maps import quick_map
from pbs_gis.recipes import (
    Recipe,
    apply_attribute_mappings,
    check_recipe_layers,
    list_recipes,
    load_recipe,
    run_multi_layer_recipe,
    run_recipe,
)

__all__ = [
    # Markdown tables
    "markdown_table",
    # DXF document creation
    "new_dxf_document",
    "ensure_layer",
    # DXF extraction
    "extract_dxf_layers",
    "extract_dxf_circles",
    "interpolate_bulge_arc",
    "lwpolyline_to_coords",
    "save_layers_as_shapefiles",
    "extract_3dsolids",
    "solid3d_to_circle",
    # DXF conversion
    "shapefile_to_dxf",
    "dxf_subset",
    # DXF Map Object Data
    "OD_EXTENSION_DICT_KEY",
    "attach_od_to_entity",
    "encode_od_1004",
    "get_table_handle_by_name",
    # CAD emitter (styled GPKG → DXF, one-way)
    "export_layers",
    "LayerSpec",
    "Style",
    "load_styles",
    # Geometry utilities
    "remove_inner_rings",
    "make_valid_gdf",
    "subtract_geometries",
    "subtract_smaller_overlaps",
    "extend_line",
    "snap_endpoints",
    "lines_to_polygon",
    "load_and_union",
    "find_column",
    "morphological_filter",
    "repair_geometry",
    "strip_utm_zone_prefix",
    "distance_to_nearest",
    "classify_direction",
    "filter_lines_by_direction",
    "points_with_buffers",
    "buffer_ring_zones",
    # Reporting
    "area_report",
    "area_by_category",
    "conflict_matrix",
    "intersection_areas",
    # Recipes
    "Recipe",
    "load_recipe",
    "list_recipes",
    "run_recipe",
    "run_multi_layer_recipe",
    "check_recipe_layers",
    "apply_attribute_mappings",
    # ALKIS convenience
    "find_flurstuecke",
    # Georeferencing (feature-match similarity transform)
    "register_features",
    "SimilarityTransform",
    # Lageplan-Einpassung (mehrblättriger Plan ohne Koordinaten -> CRS)
    "EinpassungError",
    "sheet_vectors",
    "vertices",
    "sheet_mosaic",
    "overview_vertices",
    "chain_over_overview",
    "reference_rings",
    "sample_rings",
    "chamfer_field",
    "coarse_fit",
    "icp_helmert",
    "compose",
    "isolation",
    "apply_points",
    "resample",
    # ATKIS classification cascade
    "fetch_classified_features",
    "fetch_classified_guide",
    # Discovery
    "catalog",
    # Map rendering
    "quick_map",
]
