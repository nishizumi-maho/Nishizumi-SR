"""
DEPRECATED - kept only as a bridge to sr/license_map.py.

This module used to hold the COMMUNITY estimates of target CPI per licence
class ("~15-20 corners per incident at D", "~70-80 at A"), linearly
interpolated for C and B. Those numbers were replaced by direct measurement
against iRacing's own result data - see sr/license_map.py.

What actually changed:

    class    community (v2)   measured (v3)
    D             17.5            15.7      <- the "15-20" range held up
    C             36.7            22.9
    B             55.8            33.3
    A             75.0            48.5      <- "70-80" was well over

The progression between classes is also geometric (ratio ~1.456), not linear
as v2 assumed.

Nothing in the project imports this any more. It stays so an old import does
not break, and it delegates so the numbers can never diverge.
"""

from __future__ import annotations

from typing import Optional

from sr.license_map import class_index_from_string, target_cpi_to_hold


def target_cpi_for_license(lic_string: Optional[str]) -> tuple[float, str]:
    """Assinatura antiga, valores novos (medidos). Ver sr/license_map.py."""
    index = class_index_from_string(lic_string)
    if index is None:
        return target_cpi_to_hold(2), "fallback"  # C = meio da tabela
    return target_cpi_to_hold(index), "measured_from_iracing_data_api"
