"""
Wrapper fino sobre o pyirsdk (pacote `irsdk`).

Isola as particularidades da biblioteca (startup/shutdown, checagem de conexao,
leitura defensiva de chaves que podem nao existir em builds mais antigas do
simulador) do resto do aplicativo. Ver docs/RESEARCH.md secao 3 para as fontes
de cada variavel usada aqui.
"""

from __future__ import annotations

import logging
from typing import Any

import irsdk

logger = logging.getLogger("sr_estimator.sdk")


class IRacingSDK:
    """Conexao com o iRacing via memoria compartilhada (irsdk.IRSDK)."""

    def __init__(self) -> None:
        self._ir = irsdk.IRSDK()
        self._was_connected = False

    @property
    def raw(self) -> irsdk.IRSDK:
        return self._ir

    def poll(self) -> bool:
        """Chamar uma vez por tick. Retorna True se ha uma sessao viva do iRacing."""
        if not self._ir.is_initialized:
            try:
                self._ir.startup()
            except Exception as exc:  # a lib pode lancar se o simulador nao estiver de pe
                logger.debug("startup() falhou: %s", exc)

        is_connected = bool(getattr(self._ir, "is_initialized", False) and self._ir.is_connected)

        if is_connected and not self._was_connected:
            logger.info("Connected to iRacing")
        elif not is_connected and self._was_connected:
            logger.info("Disconnected from iRacing")
            try:
                self._ir.shutdown()
            except Exception as exc:
                logger.debug("shutdown() falhou: %s", exc)

        self._was_connected = is_connected
        return is_connected

    def get(self, key: str, default: Any = None) -> Any:
        """Leitura defensiva de uma chave de telemetria ou de YAML de sessao."""
        if not getattr(self._ir, "is_initialized", False):
            return default
        try:
            value = self._ir[key]
        except Exception as exc:
            logger.debug("Falha ao ler '%s': %s", key, exc)
            return default
        return default if value is None else value

    def shutdown(self) -> None:
        try:
            self._ir.shutdown()
        except Exception:
            pass
