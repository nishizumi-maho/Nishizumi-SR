"""OBSOLETO - mantido apenas como ponte para sr/license_map.py.

Este modulo continha as estimativas da COMUNIDADE para o CPI alvo por classe
("~15-20 corners per incident em D", "~70-80 em A"), interpoladas linearmente
para C e B. Em 2026-08-09 esses numeros foram substituidos por medicao direta:
lendo a API /data do iRacing (campos oficiais `old_cpi`/`new_cpi` junto de
`old_sub_level`/`old_license_level`), 1.280 pares reais (SR, CPI, licenca)
revelaram o mapeamento exato - ver sr/license_map.py.

O que mudou na pratica:

    classe   comunidade (v2)   medido (v3)
    D             17.5            15.7      <- a faixa "15-20" batia
    C             36.7            22.9
    B             55.8            33.3
    A             75.0            48.5      <- "70-80" estava bem alto

Alem disso a progressao entre classes nao e' linear (como o v2 assumia) e sim
geometrica, de razao ~1.456 por degrau.

Nada no projeto importa mais este arquivo. Ele so' continua aqui para que um
import antigo nao quebre - e delegando, para que os numeros nunca divirjam.
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
