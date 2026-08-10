"""
Persistent application configuration (config/settings.json).

Created with defaults on first run if the file does not exist.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

logger = logging.getLogger("sr_estimator.config")

from app_paths import user_file

# Writable: lives beside the code from source, in %LOCALAPPDATA% when frozen.
SETTINGS_PATH = user_file("config", "settings.json")


def _known_fields(cls, data: dict) -> dict:
    """Filter `data` down to the keys `cls` (a dataclass) actually accepts.

    Needed because settings.json lives on disk and survives schema changes
    between versions - without this, a leftover key from an older model would
    break load() with a TypeError instead of just being ignored."""
    valid = {f.name for f in fields(cls)}
    ignored = set(data) - valid
    if ignored:
        logger.warning("Ignoring obsolete keys in settings.json: %s", sorted(ignored))
    return {k: v for k, v in data.items() if k in valid}


@dataclass
class OverlaySettings:
    x: int = 20
    y: int = 20
    opacity: float = 0.85
    scale: float = 1.0
    mode: str = "normal"  # "compact" | "normal" | "detailed"
    # Starts UNLOCKED on a fresh install: unlocked is the only state that shows
    # the header bar, so a first-time user can drag the panel and close it
    # without having to discover a hotkey or hunt for the tray icon. The choice
    # is remembered from then on.
    locked: bool = False


@dataclass
class HotkeySettings:
    toggle_overlay: str = "F8"
    toggle_lock: str = "F9"
    cycle_mode: str = "F10"


@dataclass
class ModelSettings:
    """Model parameters (see sr/model.py).

    The CPI->SR relationship is not a free coefficient - it was measured
    against iRacing's own result data (sr/license_map.py). What remains
    calibratable is only the rolling-average window.
    """
    window_band1: float = 1060.0
    window_band_ratio: float = 1.4142
    window_factor_rookie: float = 0.69
    window_factor_class_d: float = 0.83
    max_abs_session_delta: float = 3.0


@dataclass
class AppSettings:
    overlay: OverlaySettings = field(default_factory=OverlaySettings)
    hotkeys: HotkeySettings = field(default_factory=HotkeySettings)
    model: ModelSettings = field(default_factory=ModelSettings)
    # Session types that feed the estimate. Sporting Code 3.7.1.1 lists
    # "Practice within a Ranked Race event" as scoreable, and table 4.3.1.1
    # separates "Open Practice" (does not count) from in-event "Practice"
    # (does) - hence "practice" is here, weighted 0.50/0.35 via
    # sr.model.SESSION_WEIGHTS rather than being all-or-nothing.
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
        logger.info("settings.json not found; created with defaults")
        return settings

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Could not read settings.json (%s); using defaults", exc)
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
