"""
Acumulador de estimativa de SR para a sessao atual.

Fluxo (briefing secao 5): captura SR/incidentes iniciais -> a cada volta
completada soma curvas -> recalcula incidentes acumulados -> recalcula CPI ->
recalcula estimativa. A UI pode atualizar continuamente, mas o calculo pesado
so roda em on_lap_completed().
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sr.license_map import (class_and_band_from_license_level,
                            class_index_from_string, target_cpi_to_hold)
from sr.model import SRModelParams, compute_cpi, project_sr, session_weight

logger = logging.getLogger("sr_estimator.calculator")

SR_HARD_MIN = 0.0
SR_HARD_MAX = 5.0


@dataclass
class SREstimate:
    initial_sr: Optional[float]
    session_delta: float
    projected_sr: Optional[float]
    incidents: int
    corners: float
    cpi: Optional[float]
    target_cpi: float
    laps_completed: int
    laps_total: Optional[int]
    confidence: str


class SessionSRTracker:
    def __init__(self, params: SRModelParams) -> None:
        self._params = params
        self.initial_sr: Optional[float] = None
        self._initial_incident_count: int = 0
        self.corners_per_lap: float = 0.0
        self.laps_total: Optional[int] = None
        self.confidence: str = "Low"
        self.target_cpi: float = 0.0
        self._target_cpi_confidence: str = "fallback"
        self.class_index: int = 2   # C ate a licenca real ser lida
        self.band: int = 2

        self.accumulated_corners: float = 0.0
        self.laps_completed: int = 0
        self._last_incident_count: int = 0

    def start_session(
        self,
        initial_sr: Optional[float],
        initial_incident_count: int,
        corners_per_lap: float,
        laps_total: Optional[int],
        lic_string: str = "",
        confidence: str = "Low",
        lic_level: Optional[int] = None,
    ) -> None:
        self.initial_sr = initial_sr
        self._initial_incident_count = initial_incident_count
        self._last_incident_count = initial_incident_count
        self.corners_per_lap = corners_per_lap
        self.laps_total = laps_total
        self.confidence = confidence
        self.accumulated_corners = 0.0
        self.laps_completed = 0

        # LicLevel (1..24) carrega classe E faixa de SR; e' a fonte preferida.
        # LicString ("B 3.54") so' da a classe, entao a banda vem do SR.
        if lic_level:
            self.class_index, self.band = class_and_band_from_license_level(lic_level)
            self._target_cpi_confidence = "sdk_lic_level"
        else:
            idx = class_index_from_string(lic_string)
            if idx is None:
                idx = 2  # C: meio da tabela, so' pra nao travar
                self._target_cpi_confidence = "fallback"
                logger.warning("Classe de licenca desconhecida em lic_string=%r; usando C", lic_string)
            else:
                self._target_cpi_confidence = "lic_string"
            self.class_index = idx
            self.band = int(initial_sr) if initial_sr is not None else 2

        self.target_cpi = target_cpi_to_hold(self.class_index)
        logger.info(
            "Licenca: classe_idx=%s banda=%s (fonte=%s) | CPI para segurar 3.00 = %.1f",
            self.class_index, self.band, self._target_cpi_confidence, self.target_cpi,
        )

    def set_corners(self, corners: float) -> None:
        """Curvas percorridas ate agora, medidas por DISTANCIA.

        Vem de iracing.telemetry.CornerAccumulator, que conta fracao de volta
        real - assim um tow logo apos os boxes ou uma volta abandonada nao
        credita curvas que o piloto nao dirigiu, e uma volta parcial credita
        as curvas que ele dirigiu de fato.
        """
        self.accumulated_corners = max(0.0, float(corners))

    def on_lap_completed(self, current_incident_count: int, session_multiplier: float = 1.0) -> SREstimate:
        """Unico ponto onde o calculo 'pesado' roda e e' logado (briefing secao 24:
        UI pode atualizar continuamente, mas o calculo principal e' por volta).

        As curvas NAO sao somadas aqui - elas ja vem de set_corners(), por
        distancia percorrida."""
        self.laps_completed += 1
        self._last_incident_count = current_incident_count
        logger.info("Lap completed: %s", self.laps_completed)
        logger.info("Corners (por distancia): %.1f", self.accumulated_corners)

        estimate = self._compute(session_multiplier)

        logger.info("Incidents: %s", estimate.incidents)
        logger.info("CPI: %s", "inf" if estimate.cpi is None else f"{estimate.cpi:.1f}")
        logger.info("Estimated SR: %+.2f", estimate.session_delta)
        return estimate

    def snapshot(self, session_multiplier: float = 1.0) -> SREstimate:
        """Recalcula sem logar - usado para refrescar a UI a cada tick sem
        gerar log/CPU extra (briefing secao 24)."""
        return self._compute(session_multiplier)

    def _compute(self, session_multiplier: float) -> SREstimate:
        raw_incidents = self._last_incident_count - self._initial_incident_count
        incidents = max(0, raw_incidents)

        # Sporting Code 3.6.1.1: o multiplicador de sessao e' de "Corner AND
        # Incident" - pondera curvas E incidentes. Ponderar so' os incidentes
        # (como fazia o v2) inflava o CPI de qualify/TT indevidamente.
        weighted_incidents = incidents * session_multiplier
        weighted_corners = self.accumulated_corners * session_multiplier

        cpi = compute_cpi(self.accumulated_corners, incidents)

        projected: Optional[float] = None
        delta = 0.0
        if self.initial_sr is not None:
            projected, delta, _ = project_sr(
                self.initial_sr, self.class_index, weighted_corners,
                weighted_incidents, band=self.band, params=self._params,
            )
            projected = max(SR_HARD_MIN, min(SR_HARD_MAX, projected))

        return SREstimate(
            initial_sr=self.initial_sr,
            session_delta=delta,
            projected_sr=projected,
            incidents=incidents,
            corners=self.accumulated_corners,
            cpi=cpi,
            target_cpi=self.target_cpi,
            laps_completed=self.laps_completed,
            laps_total=self.laps_total,
            confidence=self.confidence,
        )
