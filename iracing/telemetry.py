"""
Leitura de telemetria por tick e deteccao de volta completada.

Mantido deliberadamente simples/barato: nenhum calculo pesado acontece aqui.
O calculo da estimativa de SR so acontece quando uma volta e' completada
(ver sr/calculator.py), conforme pedido no briefing (secao 5 e 24).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from iracing.sdk import IRacingSDK

logger = logging.getLogger("sr_estimator.telemetry")

# irsdk_TrkLoc - constante estavel do SDK, ver RESEARCH.md 3.6
TRK_LOC_NOT_IN_WORLD = -1
TRK_LOC_OFF_TRACK = 0
TRK_LOC_IN_PIT_STALL = 1
TRK_LOC_APPROACHING_PITS = 2
TRK_LOC_ON_TRACK = 3

# irsdk_SessionState - confirmado no codigo-fonte do irsdk.py, ver RESEARCH.md 3.1
SESSION_STATE_INVALID = 0
SESSION_STATE_GET_IN_CAR = 1
SESSION_STATE_WARMUP = 2
SESSION_STATE_PARADE_LAPS = 3
SESSION_STATE_RACING = 4
SESSION_STATE_CHECKERED = 5
SESSION_STATE_COOL_DOWN = 6


@dataclass
class TelemetrySnapshot:
    session_num: int
    session_state: int
    session_time: float
    lap: int
    lap_dist_pct: float
    on_pit_road: bool
    player_track_surface: int
    incident_count: int
    incident_count_source: str


_incident_source_logged = False


def _pick_incident_count(sdk: IRacingSDK) -> tuple[int, str]:
    """PlayerCarMyIncidentCount isola o piloto local mesmo em trocas de piloto
    (ver RESEARCH.md secao 4). Cai para IncidentCount em builds antigas."""
    global _incident_source_logged
    value = sdk.get("PlayerCarMyIncidentCount")
    if isinstance(value, (int, float)):
        source = "PlayerCarMyIncidentCount"
    else:
        value = sdk.get("IncidentCount")
        source = "IncidentCount"

    if not _incident_source_logged:
        logger.info("Usando '%s' como fonte de incidentes", source)
        _incident_source_logged = True

    return int(value or 0), source


def read_snapshot(sdk: IRacingSDK) -> Optional[TelemetrySnapshot]:
    session_num = sdk.get("SessionNum")
    lap = sdk.get("Lap")
    if session_num is None or lap is None:
        return None

    incident_count, source = _pick_incident_count(sdk)

    return TelemetrySnapshot(
        session_num=int(session_num),
        session_state=int(sdk.get("SessionState", SESSION_STATE_INVALID)),
        session_time=float(sdk.get("SessionTime", 0.0)),
        lap=int(lap),
        lap_dist_pct=float(sdk.get("LapDistPct", 0.0) or 0.0),
        on_pit_road=bool(sdk.get("OnPitRoad", False)),
        player_track_surface=int(sdk.get("PlayerTrackSurface", TRK_LOC_NOT_IN_WORLD)),
        incident_count=incident_count,
        incident_count_source=source,
    )


class CornerAccumulator:
    """Conta curvas pela DISTANCIA realmente percorrida, nao por volta contada.

    POR QUE (caso real levantado pelo usuario, 2026-08-09): em Nurburgring
    e' comum o piloto pedir tow logo depois dos boxes ou simplesmente nao
    terminar a volta. O contador de voltas pode andar, mas o piloto nao
    dirigiu aquelas curvas - e o CPI do iRacing e' sobre curvas percorridas.
    Somar `corners_per_lap` a cada volta contada superestimaria as curvas
    exatamente nesses casos.

    O inverso tambem acontece e aparece nos dados reais desta conta: a
    subsession 87831243 registra 0 voltas completas, 2 incidentes e uma
    mudanca de SR de verdade (-0.06) - ou seja, o iRacing creditou as curvas
    de uma volta PARCIAL. Contar por distancia resolve os dois lados.

    Regras:
    - so' acumula quando o carro esta de fato no mundo e fora do box;
    - `LapDistPct` que volta a zero e' tratado como fim de volta;
    - um salto grande de distancia num unico tick (tow, teleporte, reset)
      nao e' creditado: apenas ressincroniza a referencia.
    """

    # Fracao maxima de volta plausivel em um unico tick. Com polling de 100ms,
    # ate um carro rapido numa pista curta faz muito menos que isso; um tow ou
    # teleporte faz muito mais. Valor folgado de proposito.
    MAX_TICK_FRACTION = 0.10

    def __init__(self) -> None:
        self._prev_pct: Optional[float] = None
        self.corners: float = 0.0
        self.distance_laps: float = 0.0
        self.skipped_jumps: int = 0

    def reset(self) -> None:
        self._prev_pct = None
        self.corners = 0.0
        self.distance_laps = 0.0
        self.skipped_jumps = 0

    def update(self, snap: "TelemetrySnapshot", corners_per_lap: float) -> float:
        """Acumula e retorna quantas curvas foram creditadas neste tick."""
        in_world = snap.player_track_surface not in (
            TRK_LOC_NOT_IN_WORLD, TRK_LOC_IN_PIT_STALL,
        )
        if not in_world:
            # No box / fora do mundo: nao dirige curvas e a posicao pode pular.
            self._prev_pct = None
            return 0.0

        pct = snap.lap_dist_pct
        if pct < 0.0 or pct > 1.0:
            self._prev_pct = None
            return 0.0

        if self._prev_pct is None:
            self._prev_pct = pct
            return 0.0

        delta = pct - self._prev_pct
        if delta < 0.0:
            delta += 1.0  # cruzou a linha de chegada

        if delta > self.MAX_TICK_FRACTION:
            # Tow, teleporte, reset de sessao ou app pausado: nao inventa curvas.
            self.skipped_jumps += 1
            logger.debug("Salto de distancia ignorado (%.3f de volta)", delta)
            self._prev_pct = pct
            return 0.0

        self._prev_pct = pct
        self.distance_laps += delta
        credited = corners_per_lap * delta
        self.corners += credited
        return credited


class LapDetector:
    """Detecta o momento em que uma volta e' completada, por sessao."""

    def __init__(self) -> None:
        self._last_lap: Optional[int] = None
        self._session_num: Optional[int] = None

    def reset(self, session_num: int, current_lap: int) -> None:
        self._session_num = session_num
        self._last_lap = current_lap

    def update(self, session_num: int, current_lap: int) -> bool:
        """Retorna True exatamente uma vez por incremento de volta."""
        if self._session_num != session_num or self._last_lap is None:
            self.reset(session_num, current_lap)
            return False

        if current_lap > self._last_lap:
            self._last_lap = current_lap
            return True

        if current_lap < self._last_lap:
            # sessao reiniciada/voltou (ex.: sair e reentrar nos boxes de forma
            # incomum) - nao trata como volta completada, apenas realinha.
            logger.warning("Numero de volta regrediu (%s -> %s); realinhando",
                            self._last_lap, current_lap)
            self._last_lap = current_lap
        return False
