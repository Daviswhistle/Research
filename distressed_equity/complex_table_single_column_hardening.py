from __future__ import annotations

from typing import Any

from . import complex_table_debt_extraction as _complex_table


def install_single_instrument_transposed_table_hardening() -> None:
    """Allow field-label + one-instrument transposed tables.

    The established transposed parser required three columns because its first
    regression used two instruments. A valid transposed debt table only needs two:
    one semantic field-label column and one instrument column. Pad that shape with
    an inert provenance cell and delegate to the already-tested parser so all
    identity/field/footnote safeguards stay unchanged.
    """

    if getattr(_complex_table, "_single_instrument_transposed_hardening_installed", False):
        return
    original = _complex_table._transposed_spec

    def hardened_transposed_spec(module: Any, grid: tuple[tuple[Any, ...], ...]):
        if grid and len(grid[0]) == 2:
            padded = tuple(
                tuple(row) + (
                    _complex_table._GridCell(
                        text="",
                        is_header=False,
                        origin_row=row_index,
                        origin_cell=-1,
                    ),
                )
                for row_index, row in enumerate(grid)
            )
            return original(module, padded)
        return original(module, grid)

    _complex_table._transposed_spec = hardened_transposed_spec
    _complex_table._single_instrument_transposed_hardening_installed = True
