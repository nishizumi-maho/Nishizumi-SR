"""Modelo v3 de estimativa de Safety Rating.

O QUE MUDOU EM RELACAO AO v2 (e por que)
O v2 era uma reta inventada (`delta = k * (curvas/target_cpi - incidentes)`)
com um unico coeficiente livre arbitrario. O v3 nao inventa a relacao entre
CPI e SR: ela foi **medida** a partir de 1.280 observacoes reais da API /data
do iRacing e reproduz o numero oficial com MAE de 0.007 SR
(ver sr/license_map.py e docs/RESEARCH.md).

O MODELO EM TRES PASSOS
1. **Inverter**: do SR atual (lido do SDK) tira-se o CPI que o iRacing tem
   por tras dele  ->  `license_map.cpi_from_sr`. Exato.
2. **Atualizar o CPI** com a sessao em andamento (o unico passo ainda
   aproximado - ver "A JANELA" abaixo):

       x = 1/CPI                        (incidentes por curva)
       alpha = min(1, curvas / janela)
       x_novo = x*(1 - alpha) + alpha * (pontos_de_incidente / curvas)

   Cada tipo de sessao entra com o peso do Sporting Code 3.6.1.1, aplicado
   **as curvas E aos incidentes** (o nome oficial e' "Corner and Incident
   Multipliers" - o v2 aplicava so' nos incidentes, o que inflava o CPI de
   qualify/TT indevidamente).
3. **Remapear para SR** com a regra de faixas sobrepostas (Sporting Code
   3.7.4) -> `license_map.remap_band`. O salto de ~0.40 ao cruzar 2.00/3.00
   sai sozinho da geometria, nao esta chumbado.

A JANELA (unica peca ainda nao exata - mas agora medida, nao chutada)
`old_cpi`/`new_cpi` reais permitem resolver a janela por corrida:

    janela = (incidentes - curvas/CPI_antigo) / (1/CPI_novo - 1/CPI_antigo)

Nas corridas desta conta isso deu ~1500-2000 curvas nas sessoes normais, mas
2726 e 4878 nas duas corridas muito longas de Nurburgring Combined (990 e
1485 curvas). Ou seja: **uma janela fixa sozinha nao explica os dados** -
sessoes grandes pesam menos do que ela preveria. Modelando o peso como

    alpha = min(alpha_max, curvas / janela)

o ajuste conjunto nas 8 corridas com curvas>0 da janela=1650, alpha_max=0.335
e **RMSE de 0.0205 SR (erro maximo 0.039)** - contra 0.106 sem o teto e 0.247
com o modelo v2.

O teto se sustenta em duas amostras independentes: 0.335 ajustando so' as 8
corridas desta conta e **0.34** ajustando 198 sessoes-piloto de outros pilotos.
Essa convergencia perto de 1/3 e' o achado; ela nao tem fonte oficial. Ja a
janela e' menos estavel (1650 pessoal vs 2150 populacao), por isso o padrao
enviado e' o de populacao e `python -m sr.calibration` personaliza.

Sessoes MUITO longas e de equipe (enduro) sao o pior caso: no recorte de 593
sessoes com mais de 800 curvas o RMSE sobe para ~0.19 SR, contra ~0.11 nas
sessoes normais. Provavelmente porque em evento de equipe as voltas por piloto
e o CPI do carro nao se alinham perfeitamente.

LIMITE CONHECIDO: curvas so' sao contadas por volta completa nesta validacao
retroativa (uma corrida com 0 voltas completas fica com 0 curvas e o modelo
nao ve o incidente). Rodando ao vivo o app tem LapDistPct e pode contar a
fracao de volta - ou seja, esse caso e' pior aqui do que sera em uso real.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

from sr.license_map import cpi_from_sr, remap_band

logger = logging.getLogger("sr_estimator.model")

# Sporting Code v2025.09.09, secao 3.6.1.1 - "Table of Corner and Incident
# Multipliers". Valem para curvas E incidentes.
SESSION_WEIGHTS = {
    "race": 1.00,
    "heat": 1.00,
    "qualify": 0.50,        # Qualify - Open (o comum em sessao oficial)
    "qualify_lone": 0.35,   # Qualify - Lone
    "warmup": 0.50,
    "practice": 0.50,       # Practice - Open dentro de um evento Ranked
    "practice_lone": 0.35,
    "time_trial": 0.35,
    "unknown": 0.00,        # "All Other Session Types" -> 0.00
}


@dataclass
class SRModelParams:
    # Ajustados em 198 sessoes-piloto independentes (sessoes "normais", ate 800
    # curvas) com CPI oficial da API /data: RMSE 0.109 SR. Nas 8 corridas desta
    # conta especificamente esses valores dao MAE 0.035 SR; um ajuste so' nelas
    # daria 1650 (MAE 0.013). O padrao aqui e' o de POPULACAO porque generaliza;
    # use `python -m sr.calibration` para personalizar com o seu historico.
    window_corners: float = 2150.0
    # Teto do peso de UMA sessao. Sem ele, corridas longas (Nurburgring
    # Combined, 990 e 1485 curvas) eram superestimadas em +0.45 a +0.79 SR.
    # Achado robusto: 0.335 ajustando so' as 8 corridas desta conta e 0.34
    # ajustando 198 sessoes de outros pilotos - duas amostras independentes
    # convergindo perto de 1/3. Nao ha fonte oficial para este teto.
    alpha_max: float = 0.34
    max_abs_session_delta: float = 3.0  # sanity clamp, briefing secao 26


DEFAULT_PARAMS = SRModelParams()


def session_weight(session_type: Optional[str]) -> float:
    """Peso do Sporting Code para o tipo de sessao (0.00 se desconhecido)."""
    return SESSION_WEIGHTS.get((session_type or "unknown").lower(), 0.00)


def update_cpi(
    cpi_old: float,
    corners: float,
    incident_points: float,
    params: SRModelParams = DEFAULT_PARAMS,
) -> float:
    """Aplica uma sessao ao CPI acumulado (passo 2 do modelo).

    `corners` e `incident_points` ja devem vir multiplicados pelo peso da
    sessao. Retorna o CPI novo; nunca divide por zero nem devolve infinito.
    """
    if corners <= 0 or cpi_old <= 0:
        return cpi_old
    window = max(1.0, params.window_corners)
    alpha = min(params.alpha_max, corners / window)
    x_old = 1.0 / cpi_old
    x_session = incident_points / corners
    x_new = x_old * (1.0 - alpha) + alpha * x_session
    if x_new <= 1e-9:
        # sessao perfeita ocupando a janela inteira: CPI enorme, mas finito.
        # O SR ja e' limitado a 4.99 no remapeamento, entao isto so' evita inf.
        return 1e9
    return 1.0 / x_new


def project_sr(
    sr_before: float,
    class_index: int,
    corners: float,
    incident_points: float,
    band: Optional[int] = None,
    params: SRModelParams = DEFAULT_PARAMS,
) -> tuple[float, float, float]:
    """Estimativa completa da sessao.

    Retorna (sr_projetado, delta, cpi_novo). `corners` e `incident_points` ja
    ponderados pelo peso da sessao.
    """
    if band is None:
        band = int(math.floor(sr_before))
    cpi_old = cpi_from_sr(sr_before, class_index, band)
    cpi_new = update_cpi(cpi_old, corners, incident_points, params)
    sr_new, _ = remap_band(cpi_new, class_index, band)

    delta = sr_new - sr_before
    bound = abs(params.max_abs_session_delta)
    if abs(delta) > bound:
        logger.error(
            "Delta de SR estimado (%+.2f) passou do limite de seguranca (%.2f); foi clampado",
            delta, bound,
        )
        delta = max(-bound, min(bound, delta))
        sr_new = sr_before + delta
    return sr_new, delta, cpi_new


def estimate_delta(
    sr_before: float,
    class_index: int,
    corners: float,
    incident_points: float,
    band: Optional[int] = None,
    params: SRModelParams = DEFAULT_PARAMS,
) -> float:
    """Atalho para quem so' quer o delta."""
    return project_sr(sr_before, class_index, corners, incident_points, band, params)[1]


def compute_cpi(corners: float, incident_points: float) -> Optional[float]:
    """CPI **da sessao atual** (nao o acumulado). None = limpo -> mostrar Clean."""
    if incident_points <= 0:
        return None
    return corners / incident_points
