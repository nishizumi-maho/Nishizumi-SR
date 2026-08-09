"""Mapeamento exato CPI <-> Safety Rating do iRacing.

DE ONDE VEM ISTO (importante - nao e' mais "estimativa da comunidade"):
Em 2026-08-09 conseguimos ler a API /data do iRacing atraves do proxy BFF do
site (ver docs/RESEARCH.md secao 5, reescrita). Os resultados oficiais de cada
subsession expoem, por piloto, os campos `old_cpi`/`new_cpi` (o CPI real usado
pelo iRacing) junto de `old_sub_level`/`new_sub_level` (SR*100) e
`old_license_level`/`new_license_level`.

Com 1.280 pares reais (SR, CPI, nivel de licenca) extraidos de 9 subsessions,
o mapeamento se revelou uma reta perfeita em ln(CPI) dentro de cada faixa:

    SR = banda + a * ln( CPI / piso_cpi(classe, banda) )

    piso_cpi(classe, banda) = F0 * r ** (indice_classe + banda)

Ajuste global (minimos quadrados) sobre os 1.280 pares:
    a  = 1.5236      MAE = 0.0072 SR
    r  = 1.4562      erro maximo = 0.0374 SR
    F0 = 3.4949

Validado contra as 5 licencas reais desta conta (erro <= 0.011 SR, e o proprio
site so' mostra 2 casas):

    oval        B 2.06  CPI 23.978  -> previsto 2.071
    sports_car  B 3.54  CPI 47.202  -> previsto 3.530
    formula_car B 3.60  CPI 49.242  -> previsto 3.595
    dirt_oval   C 3.36  CPI 29.108  -> previsto 3.367
    dirt_road   C 3.69  CPI 36.140  -> previsto 3.696

DUAS CONSEQUENCIAS QUE CAEM DE GRACA DESSA GEOMETRIA
1. O "+0.40 ao cruzar um numero inteiro" documentado pelo iRacing NAO precisa
   ser codificado a mao: ele e' o resultado de remapear o mesmo CPI para a
   banda vizinha. O salto implicito e' 1 - a*ln(r) = 0.427, e o artigo oficial
   arredonda para 0.40. Ver `remap_band`.
2. A promocao de classe custa exatamente 1.00 de SR: como o piso depende de
   (indice_classe + banda), subir uma classe com o mesmo CPI reduz a banda em
   exatamente 1. Bate com o Sporting Code 3.9.3 ("aproximadamente 1.00").

O QUE AINDA NAO E' EXATO: a regra de atualizacao do CPI entre sessoes
(a janela movel) - ver sr/model.py. O mapeamento CPI->SR aqui, esse sim,
reproduz o numero do iRacing.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Optional

logger = logging.getLogger("sr_estimator.license_map")

# Parametros ajustados aos 1.280 pares reais (ver docstring).
A_SLOPE = 1.5236
R_RATIO = 1.4562
F0_CPI = 3.4949

BAND_MIN = 0
BAND_MAX = 4          # SR exibido vai ate 4.99 (Sporting Code 3.7.2)
SR_DISPLAY_MAX = 4.99
SR_DISPLAY_MIN = 0.00

CLASS_INDEX = {
    "R": 0, "ROOKIE": 0,
    "D": 1,
    "C": 2,
    "B": 3,
    "A": 4,
    "P": 5, "PRO": 5,
}
_CLASS_BY_INDEX = {0: "R", 1: "D", 2: "C", 3: "B", 4: "A", 5: "P"}

_LIC_LETTER_RE = re.compile(r"^([A-Za-z]+)")


def class_and_band_from_license_level(lic_level: int) -> tuple[int, int]:
    """`LicLevel` do SDK (1..24) -> (indice_da_classe, banda).

    Confirmado nos dados reais: nivel 13 -> classe B banda 1; nivel 20 ->
    classe A banda 4. Cada classe ocupa 4 niveis consecutivos, um por faixa
    inteira de SR.
    """
    idx = (int(lic_level) - 1) // 4
    band = ((int(lic_level) - 1) % 4) + 1
    return idx, band


def class_index_from_string(lic_string: Optional[str]) -> Optional[int]:
    """"B 3.54" / "Class B" -> 3. Retorna None se nao der pra reconhecer."""
    s = (lic_string or "").strip().upper()
    s = s.replace("CLASS", "").strip()
    match = _LIC_LETTER_RE.match(s)
    if not match:
        return None
    return CLASS_INDEX.get(match.group(1))


def band_floor_cpi(class_index: int, band: int) -> float:
    """CPI no piso de uma faixa inteira de SR (ex.: o CPI exato de "3.00")."""
    return F0_CPI * (R_RATIO ** (class_index + band))


def sr_from_cpi(cpi: float, class_index: int, band: int) -> float:
    """SR exibido para um CPI, dentro de uma banda especifica (sem remapear)."""
    if cpi <= 0:
        return float(band)
    return band + A_SLOPE * math.log(cpi / band_floor_cpi(class_index, band))


def cpi_from_sr(sr: float, class_index: int, band: Optional[int] = None) -> float:
    """Inverso de sr_from_cpi: o CPI que o iRacing tem por tras de um SR.

    E' isto que permite ao app partir do SR real lido do SDK e trabalhar na
    mesma grandeza que o algoritmo de verdade usa.
    """
    if band is None:
        band = int(math.floor(sr))
    band = max(BAND_MIN, min(BAND_MAX, band))
    return band_floor_cpi(class_index, band) * math.exp((sr - band) / A_SLOPE)


def remap_band(cpi: float, class_index: int, band: int) -> tuple[float, int]:
    """Aplica a regra de faixas sobrepostas do Sporting Code 3.7.4.

    Enquanto o SR calculado sair da faixa [banda, banda+1), o mesmo CPI e'
    remapeado para a faixa vizinha. E' dai que sai o "+0.40 / -0.40" do artigo
    oficial: aqui ele vale 1 - a*ln(r) ~= 0.427, e nao e' um numero chumbado -
    e' consequencia da geometria (ver band_crossing_bonus()).

    Retorna (sr_exibido, banda_final).
    """
    band = max(BAND_MIN, min(BAND_MAX, int(band)))
    for _ in range(8):  # convergencia garantida; o laco so' evita loop infinito
        sr = sr_from_cpi(cpi, class_index, band)
        if sr >= band + 1.0 and band < BAND_MAX:
            band += 1
            continue
        if sr < band and band > BAND_MIN:
            band -= 1
            continue
        return max(SR_DISPLAY_MIN, min(SR_DISPLAY_MAX, sr)), band
    return max(SR_DISPLAY_MIN, min(SR_DISPLAY_MAX, sr_from_cpi(cpi, class_index, band))), band


def band_crossing_bonus() -> float:
    """O valor real do salto ao cruzar um inteiro (o iRacing documenta 0.40)."""
    return 1.0 - A_SLOPE * math.log(R_RATIO)


def target_cpi_to_hold(class_index: int, sr: float = 3.00) -> float:
    """CPI necessario pra segurar um SR (por padrao 3.00) naquela classe.

    Substitui as estimativas da comunidade que o projeto usava antes. Os
    numeros medidos sao bem mais baixos que os citados por sites de terceiros
    para as classes altas (A: ~48.5 medido vs "70-80" repetido pela comunidade).
    """
    band = int(math.floor(sr))
    return cpi_from_sr(sr, class_index, band)


def describe_ladder() -> str:
    """Tabela legivel dos pisos de CPI - util em log e para conferencia."""
    lines = ["classe  SR1.00   SR2.00   SR3.00   SR4.00"]
    for idx in range(6):
        floors = "  ".join(f"{band_floor_cpi(idx, b):7.2f}" for b in (1, 2, 3, 4))
        lines.append(f"  {_CLASS_BY_INDEX[idx]:<5} {floors}")
    return "\n".join(lines)
