from __future__ import annotations

from eva.adapters.postgres.foreign_key_catalog import expected_foreign_key_shapes


def test_current_catalog_has_one_complete_named_foreign_key_graph() -> None:
    shapes = expected_foreign_key_shapes()

    assert len(shapes) == 119
    assert len({(shape.source_schema, shape.source_table, shape.name) for shape in shapes}) == len(
        shapes
    )
    assert all(
        shape.source_columns
        and shape.target_columns
        and len(shape.source_columns) == len(shape.target_columns)
        for shape in shapes
    )
