"""
Configuracao persistente do aplicativo (config/settings.json).

Criado com valores padrao na primeira execucao caso o arquivo nao exista.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

logger = logging.getLogger("sr_estimator.config")

SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"


def _known_fields(cls, data: dict) -> dict:
    """Filtra `data` para as chaves que `cls` (um dataclass) realmente aceita.

    Necessario porque settings.json e' escrito em disco e sobrevive a
    mudancas de schema entre versoes (ex.: model v1 -> v2 trocou os
    coeficientes) - sem isso, uma chave antiga sobrando no arquivo salvo
    quebraria o load() com TypeError em vez de so ser ignorada."""
    valid = {f.name for f in fields(cls)}
    ignored = set(data) - valid
    if ignored:
        logger.warning("Ignorando chaves obsoletas em settings.json: %s", sorted(ignored))
    return {k: v for k, v in data.items() if k in valid}


@dataclass
class OverlaySettings:
    x: int = 20
    y: int = 20
    opacity: float = 0.85
    scale: float = 1.0
    mode: str = "normal"  # "compact" | "normal" | "detailed"


@dataclass
class HotkeySettings:
    toggle_overlay: str = "F8"
    toggle_lock: str = "F9"
    cycle_mode: str = "F10"


@dataclass
class ModelSettings:
    """Parametros do modelo v3 (ver sr/model.py).

    Nao ha mais `sensitivity_k`: a relacao CPI->SR nao e' mais um coeficiente
    livre, foi medida contra a API /data do iRacing (sr/license_map.py). O que
    sobra calibravel e' so' a regra da janela movel.
    """
    window_corners: float = 2150.0
    alpha_max: float = 0.34
    max_abs_session_delta: float = 3.0


@dataclass
class AppSettings:
    overlay: OverlaySettings = field(default_factory=OverlaySettings)
    hotkeys: HotkeySettings = field(default_factory=HotkeySettings)
    model: ModelSettings = field(default_factory=ModelSettings)
    # Tipos de sessao que alimentam a estimativa. Sporting Code 3.7.1.1 lista
    # "Practice within a Ranked Race event" como pontuavel, e a tabela 4.3.1.1
    # separa "Open Practice" (nao conta) de "Practice" dentro do evento (conta)
    # - por isso "practice" entra aqui, com peso 0.50/0.35 vindo de
    # sr.model.SESSION_WEIGHTS em vez de tudo-ou-nada.
    session_types_counted: list[str] = field(
        default_factory=lambda: ["race", "heat", "qualify", "warmup", "time_trial", "practice"]
    )
    poll_interval_ms: int = 100
    session_refresh_interval_ms: int = 1000
    fallback_corners_per_lap: int = 10


def load(path: Path = SETTINGS_PATH) -> AppSettings:
    if not path.exists():
        settings = AppSettings()
        save(settings, path)
        logger.info("settings.json nao encontrado, criado com valores padrao")
        return settings

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Falha ao ler settings.json (%s), usando padrao", exc)
        return AppSettings()

    settings = AppSettings()
    if "overlay" in raw:
        merged = _known_fields(OverlaySettings, {**asdict(settings.overlay), **raw["overlay"]})
        settings.overlay = OverlaySettings(**merged)
    if "hotkeys" in raw:
        merged = _known_fields(HotkeySettings, {**asdict(settings.hotkeys), **raw["hotkeys"]})
        settings.hotkeys = HotkeySettings(**merged)
    if "model" in raw:
        merged = _known_fields(ModelSettings, {**asdict(settings.model), **raw["model"]})
        settings.model = ModelSettings(**merged)
    if "session_types_counted" in raw:
        settings.session_types_counted = raw["session_types_counted"]
    if "poll_interval_ms" in raw:
        settings.poll_interval_ms = raw["poll_interval_ms"]
    if "session_refresh_interval_ms" in raw:
        settings.session_refresh_interval_ms = raw["session_refresh_interval_ms"]
    if "fallback_corners_per_lap" in raw:
        settings.fallback_corners_per_lap = raw["fallback_corners_per_lap"]
    return settings


def save(settings: AppSettings, path: Path = SETTINGS_PATH) -> None:
    payload = {
        "overlay": asdict(settings.overlay),
        "hotkeys": asdict(settings.hotkeys),
        "model": asdict(settings.model),
        "session_types_counted": settings.session_types_counted,
        "poll_interval_ms": settings.poll_interval_ms,
        "session_refresh_interval_ms": settings.session_refresh_interval_ms,
        "fallback_corners_per_lap": settings.fallback_corners_per_lap,
    }
    path.write_text(json.dumps(payload, indent=4, ensure_ascii=False), encoding="utf-8")
