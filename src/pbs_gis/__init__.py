"""GIS utilities: DXF tools, geometry operations, reporting, OSM, WMS vectorization.

Importing the package loads nothing; an attribute access imports the one
submodule that defines it. A missing optional dependency (ezdxf for the
DXF/CAD family) therefore only fails the callers that use it.
"""

import importlib

# public name -> defining submodule
_EXPORTS = {
    "markdown_table": "pbs_gis.md_table",
    "shapefile_to_dxf": "pbs_gis.dxf.convert",
    "new_dxf_document": "pbs_gis.dxf.document",
    "ensure_layer": "pbs_gis.dxf.document",
    "block_circles": "pbs_gis.dxf.blockflaechen",
    "extract_hatch_areas": "pbs_gis.dxf.hatch",
    "vegetationsflaechen": "pbs_gis.luftbild",
    "dxf_subset": "pbs_gis.dxf.subset",
    "dxf_subset_from_sources": "pbs_gis.dxf.subset",
    "extract_3dsolids": "pbs_gis.dxf.extract",
    "extract_dxf_circles": "pbs_gis.dxf.extract",
    "extract_dxf_layers": "pbs_gis.dxf.extract",
    "interpolate_bulge_arc": "pbs_gis.dxf.extract",
    "lwpolyline_to_coords": "pbs_gis.dxf.extract",
    "save_layers_as_shapefiles": "pbs_gis.dxf.extract",
    "solid3d_to_circle": "pbs_gis.dxf.extract",
    "_solid3d_center_2d": "pbs_gis.dxf.extract",
    "_solid3d_to_2d_polygon": "pbs_gis.dxf.extract",
    "_solid3d_to_world_points": "pbs_gis.dxf.extract",
    "OD_EXTENSION_DICT_KEY": "pbs_gis.dxf.map_od",
    "attach_od_to_entity": "pbs_gis.dxf.map_od",
    "encode_od_1004": "pbs_gis.dxf.map_od",
    "get_table_handle_by_name": "pbs_gis.dxf.map_od",
    "LayerSpec": "pbs_gis.cad",
    "Style": "pbs_gis.cad",
    "export_layers": "pbs_gis.cad",
    "load_styles": "pbs_gis.cad",
    "smooth_polygons": "pbs_gis.geometry",
    "smooth_partition": "pbs_gis.geometry",
    "partition_along": "pbs_gis.geometry",
    "absorb_small": "pbs_gis.geometry",
    "buffer_ring_zones": "pbs_gis.geometry",
    "classify_direction": "pbs_gis.geometry",
    "distance_to_nearest": "pbs_gis.geometry",
    "extend_line": "pbs_gis.geometry",
    "filter_lines_by_direction": "pbs_gis.geometry",
    "find_column": "pbs_gis.geometry",
    "lines_to_polygon": "pbs_gis.geometry",
    "load_and_union": "pbs_gis.geometry",
    "make_valid_gdf": "pbs_gis.geometry",
    "morphological_filter": "pbs_gis.geometry",
    "points_with_buffers": "pbs_gis.geometry",
    "repair_geometry": "pbs_gis.geometry",
    "remove_inner_rings": "pbs_gis.geometry",
    "snap_endpoints": "pbs_gis.geometry",
    "strip_utm_zone_prefix": "pbs_gis.geometry",
    "close_gaps": "pbs_gis.geometry",
    "close_slits": "pbs_gis.geometry",
    "transfer_spikes": "pbs_gis.geometry",
    "weld_fragments": "pbs_gis.geometry",
    "remove_spikes": "pbs_gis.geometry",
    "remove_spikes_geom": "pbs_gis.geometry",
    "subtract_geometries": "pbs_gis.geometry",
    "subtract_smaller_overlaps": "pbs_gis.geometry",
    "to_single_part": "pbs_gis.geometry",
    "area_by_category": "pbs_gis.reporting",
    "area_report": "pbs_gis.reporting",
    "conflict_matrix": "pbs_gis.reporting",
    "intersection_areas": "pbs_gis.reporting",
    "find_flurstuecke": "pbs_gis.alkis",
    "register_features": "pbs_gis.georef",
    "SimilarityTransform": "pbs_gis.georef",
    "EinpassungError": "pbs_gis.einpassung",
    "apply_points": "pbs_gis.einpassung",
    "chain_over_overview": "pbs_gis.einpassung",
    "chamfer_field": "pbs_gis.einpassung",
    "coarse_fit": "pbs_gis.einpassung",
    "compose": "pbs_gis.einpassung",
    "icp_helmert": "pbs_gis.einpassung",
    "isolation": "pbs_gis.einpassung",
    "overview_vertices": "pbs_gis.einpassung",
    "reference_rings": "pbs_gis.einpassung",
    "resample": "pbs_gis.einpassung",
    "sample_rings": "pbs_gis.einpassung",
    "sheet_mosaic": "pbs_gis.einpassung",
    "sheet_vectors": "pbs_gis.einpassung",
    "vertices": "pbs_gis.einpassung",
    "fetch_classified_features": "pbs_gis.atkis",
    "fetch_classified_guide": "pbs_gis.atkis",
    "catalog": "pbs_gis.catalog",
    "quick_map": "pbs_gis.maps",
    "Recipe": "pbs_gis.recipes",
    "apply_attribute_mappings": "pbs_gis.recipes",
    "check_recipe_layers": "pbs_gis.recipes",
    "list_recipes": "pbs_gis.recipes",
    "load_recipe": "pbs_gis.recipes",
    "run_multi_layer_recipe": "pbs_gis.recipes",
    "run_recipe": "pbs_gis.recipes",
}


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        try:
            return importlib.import_module(f"{__name__}.{name}")
        except ModuleNotFoundError as exc:
            if exc.name != f"{__name__}.{name}":
                raise
            raise AttributeError(
                f"module {__name__!r} has no attribute {name!r}"
            ) from None
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))


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
    "extract_hatch_areas",
    "block_circles",
    "vegetationsflaechen",
    "dxf_subset_from_sources",
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
    "to_single_part",
    "close_gaps",
    "close_slits",
    "transfer_spikes",
    "weld_fragments",
    "remove_spikes",
    "remove_spikes_geom",
    "extend_line",
    "snap_endpoints",
    "lines_to_polygon",
    "load_and_union",
    "find_column",
    "morphological_filter",
    "smooth_polygons",
    "smooth_partition",
    "partition_along",
    "absorb_small",
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
