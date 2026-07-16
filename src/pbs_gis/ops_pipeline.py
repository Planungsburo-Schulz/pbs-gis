"""Declarative geometry-op pipelines for the ``ops:`` workflow step type.

An ``ops`` step runs a chain of ``gdf -> gdf`` operations (from
:mod:`pbs_gis.operations` and the :mod:`pbs_gis.geometry` cleaning family) in
order: read one input layer, apply each op, write one output. The chain is
declared inline in ``workflow.yaml``::

    - name: Baufeld säubern
      ops:
        - {op: clean_line, min_segment_length: 0.5}
        - {makro: saeuberung_standard}
      input: Geodaten/baufeld_roh.gpkg
      output: Geodaten/baufeld.gpkg

Each op-definition is a one-key-plus-params mapping ``{op: <name>, **params}``;
``params`` are passed as keyword arguments to the registered function. A
``{makro: <name>}`` element expands inline to the makro's op list.

**Makros** are named, reusable op lists. One library makro
(``saeuberung_standard``) ships built-in; a project may add its own under a
top-level ``makros:`` key in ``workflow.yaml`` (same op-list form). Rules:

* an unknown op name is a hard error (no silent skip);
* an unknown makro name is a hard error;
* a makro body may not reference another makro (no recursion) — a hard error;
* a project makro may not reuse a built-in makro's name (collision) — a hard
  error, never a silent override.

Only single-input ``gdf -> gdf`` ops are registered. Two-input operations
(``dissolve_by_majority_intersection``, ``filter_by_intersection``,
``subtract_geometries``) cannot run in a single-layer chain and are
deliberately absent — referencing them raises the unknown-op error.
"""

from __future__ import annotations

from typing import Any, Callable

import geopandas as gpd

from pbs_gis import geometry, operations


class OpsPipelineError(ValueError):
    """Raised for an invalid ops/makro definition (unknown name, recursion, …)."""


# Registered single-input gdf->gdf operations. ``repair`` is an alias for the
# gdf-level make_valid, so the cleaning chain reads naturally.
_OPS: dict[str, Callable[..., gpd.GeoDataFrame]] = {
    # operations.py — polygon cleaning
    "remove_slivers_erosion": operations.remove_slivers_erosion,
    "simplify_slivers": operations.simplify_slivers,
    "remove_degenerate_spikes": operations.remove_degenerate_spikes,
    "remove_protrusions": operations.remove_protrusions,
    # operations.py — line cleaning / construction / filter (single-input)
    "clean_line": operations.clean_line,
    "connect_points": operations.connect_points,
    "filter_by_column": operations.filter_by_column,
    # geometry.py — cleaning family (gdf-level)
    "make_valid_gdf": geometry.make_valid_gdf,
    "repair": geometry.make_valid_gdf,
    "morphological_filter": geometry.morphological_filter,
    "subtract_smaller_overlaps": geometry.subtract_smaller_overlaps,
}

# Built-in library makros. ``saeuberung_standard`` is the recurring polygon
# cleaning stack; each op runs with its own default parameters.
BUILTIN_MAKROS: dict[str, list[dict[str, Any]]] = {
    "saeuberung_standard": [
        {"op": "remove_degenerate_spikes"},
        {"op": "remove_protrusions"},
        {"op": "remove_slivers_erosion"},
        {"op": "repair"},
    ],
}


def op_names() -> list[str]:
    """Sorted names of all registered ops (for error messages / discovery)."""
    return sorted(_OPS)


def resolve_makros(project_makros: dict[str, Any] | None) -> dict[str, list]:
    """Merge built-in and project makros; a name collision is a hard error.

    Args:
        project_makros: The ``makros:`` mapping from a project workflow.yaml
            (name → op list), or ``None``.

    Returns:
        The merged ``{name: op_list}`` mapping.

    Raises:
        OpsPipelineError: if a project makro reuses a built-in makro's name, or
            the mapping is malformed.
    """
    project_makros = project_makros or {}
    if not isinstance(project_makros, dict):
        raise OpsPipelineError(f"makros must be a mapping, got {type(project_makros).__name__}")
    for name in project_makros:
        if name in BUILTIN_MAKROS:
            raise OpsPipelineError(
                f"makro {name!r} collides with a built-in makro; rename the "
                f"project makro (built-in makros are never silently overridden)"
            )
    merged: dict[str, list] = dict(BUILTIN_MAKROS)
    merged.update(project_makros)
    return merged


def expand_ops(ops: list, makros: dict[str, list]) -> list[dict[str, Any]]:
    """Expand ``{makro: name}`` elements inline into a flat op list.

    Order is preserved and a makro may be referenced more than once. A makro
    body that itself references a makro is rejected (no recursion).

    Args:
        ops: The step's op list (op-defs and/or ``{makro: name}`` refs).
        makros: Resolved ``{name: op_list}`` mapping (see :func:`resolve_makros`).

    Returns:
        A flat list of op-def dicts (no remaining makro references).

    Raises:
        OpsPipelineError: on a malformed element, an unknown makro, or a
            nested makro reference.
    """
    if not isinstance(ops, list):
        raise OpsPipelineError(f"ops must be a list, got {type(ops).__name__}")
    result: list[dict[str, Any]] = []
    for item in ops:
        if not isinstance(item, dict):
            raise OpsPipelineError(f"each ops element must be a mapping, got {item!r}")
        if "makro" in item:
            name = item["makro"]
            if name not in makros:
                raise OpsPipelineError(
                    f"unknown makro {name!r}; known: {sorted(makros)}"
                )
            for sub in makros[name]:
                if isinstance(sub, dict) and "makro" in sub:
                    raise OpsPipelineError(
                        f"makro {name!r} references makro {sub['makro']!r}; "
                        f"makro-in-makro (recursion) is not supported"
                    )
                result.append(sub)
        else:
            result.append(item)
    return result


def apply_op(gdf: gpd.GeoDataFrame, op_def: dict[str, Any]) -> gpd.GeoDataFrame:
    """Apply one op-def ``{op: name, **params}`` to *gdf* and return the result.

    Raises:
        OpsPipelineError: if the op key is missing or the name is unknown.
    """
    if not isinstance(op_def, dict):
        raise OpsPipelineError(f"op definition must be a mapping, got {op_def!r}")
    params = dict(op_def)
    name = params.pop("op", None)
    if name is None:
        raise OpsPipelineError(f"op definition missing 'op' key: {op_def!r}")
    func = _OPS.get(name)
    if func is None:
        raise OpsPipelineError(f"unknown op {name!r}; known: {op_names()}")
    return func(gdf, **params)


def run_ops(
    gdf: gpd.GeoDataFrame,
    ops: list,
    *,
    project_makros: dict[str, Any] | None = None,
) -> gpd.GeoDataFrame:
    """Run an ops chain over *gdf*, expanding makros first.

    Args:
        gdf: Input GeoDataFrame.
        ops: The op list (op-defs and/or ``{makro: name}`` refs).
        project_makros: Optional project-defined makros (top-level ``makros:``).

    Returns:
        The GeoDataFrame after the full chain.

    Raises:
        OpsPipelineError: on any malformed/unknown op or makro (see the module
            docstring for the exact rules).
    """
    makros = resolve_makros(project_makros)
    expanded = expand_ops(ops, makros)
    for op_def in expanded:
        gdf = apply_op(gdf, op_def)
    return gdf
