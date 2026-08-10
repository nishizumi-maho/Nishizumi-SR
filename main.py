"""
Nishizumi SR - entry point.

Wires together the iRacing SDK, the SR estimate, history/calibration and the
PySide6 overlay.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from config import settings as settings_module
from iracing.sdk import IRacingSDK
from iracing.session import (
    PlayerInfo,
    SessionMeta,
    TrackInfo,
    get_player_info,
    get_session_meta,
    get_track_info,
)
from iracing.telemetry import (
    SESSION_STATE_CHECKERED,
    SESSION_STATE_COOL_DOWN,
    LapDetector,
    read_snapshot,
    CornerAccumulator,
)
from overlay.widgets import (
    format_compact,
    format_detailed,
    format_normal,
    format_practice_notice,
    format_session_complete,
    format_waiting,
)
from overlay.window import GlobalHotkeys, OverlayWindow
from sr.calculator import SREstimate, SessionSRTracker
from sr.calibration import compute_stats, confidence_level
from sr.history import HistoryStore, build_record
from sr.model import SRModelParams, session_weight
from tracks.database import TrackDatabase

LOGS_DIR = Path(__file__).resolve().parent / "logs"
OVERLAY_MODES = ["compact", "normal", "detailed"]
SESSION_COMPLETE_DISPLAY_SECONDS = 8


class LogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%H:%M:%S")
        if record.levelno >= logging.WARNING:
            return f"[{ts}] [{record.levelname}] {record.getMessage()}"
        return f"[{ts}] {record.getMessage()}"


def setup_logging() -> None:
    LOGS_DIR.mkdir(exist_ok=True)
    log_path = LOGS_DIR / f"sr_estimator_{datetime.now():%Y%m%d_%H%M%S}.log"

    formatter = LogFormatter()
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    logger = logging.getLogger("sr_estimator")
    logger.setLevel(logging.INFO)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)


class AppState:
    def __init__(self) -> None:
        self.current_session_num: Optional[int] = None
        self.track_info: Optional[TrackInfo] = None
        self.player_info: Optional[PlayerInfo] = None
        self.session_meta: Optional[SessionMeta] = None
        self.session_finalized: bool = True
        self.session_complete_ticks_remaining: int = 0
        self.pending_complete_html: str = ""
        self.tick_count: int = 0


def make_tray_icon() -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(76, 215, 135))
    painter.setPen(QColor(15, 17, 20))
    painter.drawEllipse(1, 1, 30, 30)
    painter.end()
    return QIcon(pixmap)


def main() -> None:
    setup_logging()
    logger = logging.getLogger("sr_estimator.main")

    app_settings = settings_module.load()
    history = HistoryStore()
    track_db = TrackDatabase(fallback_corners_per_lap=app_settings.fallback_corners_per_lap)

    sdk = IRacingSDK()
    lap_detector = LapDetector()
    corner_accumulator = CornerAccumulator()
    model_params = SRModelParams(**asdict(app_settings.model))
    tracker = SessionSRTracker(model_params)
    state = AppState()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    window = OverlayWindow(opacity=app_settings.overlay.opacity, scale=app_settings.overlay.scale)
    window.move(app_settings.overlay.x, app_settings.overlay.y)
    window.set_locked(True)
    window.show()
    window.set_content(format_waiting("Waiting for iRacing..."))

    mode_index = OVERLAY_MODES.index(app_settings.overlay.mode) if app_settings.overlay.mode in OVERLAY_MODES else 1

    def current_mode() -> str:
        return OVERLAY_MODES[mode_index]

    def persist_overlay_state() -> None:
        pos = window.pos()
        app_settings.overlay.x = pos.x()
        app_settings.overlay.y = pos.y()
        app_settings.overlay.mode = current_mode()
        settings_module.save(app_settings)

    def on_toggle_lock() -> None:
        window.toggle_lock()
        persist_overlay_state()

    def on_cycle_mode() -> None:
        nonlocal mode_index
        mode_index = (mode_index + 1) % len(OVERLAY_MODES)
        logger.info("Overlay mode: %s", current_mode())
        persist_overlay_state()

    try:
        hotkeys: Optional[GlobalHotkeys] = GlobalHotkeys(
            app_settings.hotkeys.toggle_overlay,
            app_settings.hotkeys.toggle_lock,
            app_settings.hotkeys.cycle_mode,
        )
        hotkeys.toggle_overlay.connect(window.toggle_visibility)
        hotkeys.toggle_lock.connect(on_toggle_lock)
        hotkeys.cycle_mode.connect(on_cycle_mode)
    except Exception as exc:
        logger.error("Hotkeys globais indisponiveis (%s). Use o icone na bandeja para controlar o overlay.", exc)
        hotkeys = None

    # --- system tray: unica forma de fechar o app de forma limpa, ja que o
    # the overlay deliberately has no title bar or close button. ---
    tray = QSystemTrayIcon(make_tray_icon())
    tray.setToolTip("iRacing SR Estimator")
    menu = QMenu()
    action_toggle = QAction("Mostrar/Ocultar (F8)")
    action_toggle.triggered.connect(window.toggle_visibility)
    action_lock = QAction("Travar/Destravar posicao (F9)")
    action_lock.triggered.connect(on_toggle_lock)
    action_mode = QAction("Alternar modo (F10)")
    action_mode.triggered.connect(on_cycle_mode)
    action_quit = QAction("Sair")
    action_quit.triggered.connect(app.quit)
    menu.addAction(action_toggle)
    menu.addAction(action_lock)
    menu.addAction(action_mode)
    menu.addSeparator()
    menu.addAction(action_quit)
    tray.setContextMenu(menu)
    tray.show()

    def finalize_session(estimate: SREstimate) -> None:
        if state.session_meta is None or state.session_meta.session_type == "practice":
            state.session_finalized = True
            return
        if tracker.laps_completed == 0:
            state.session_finalized = True
            return

        record = build_record(
            track_display_name=state.track_info.display_name if state.track_info else "unknown",
            track_id=state.track_info.track_id if state.track_info else None,
            car_name=state.player_info.car_screen_name if state.player_info else "unknown",
            lic_string=state.player_info.lic_string if state.player_info else "",
            initial_sr=tracker.initial_sr,
            estimate=estimate,
            session_type=state.session_meta.session_type,
        )
        history.append(record)
        state.session_finalized = True
        state.pending_complete_html = format_session_complete(estimate.session_delta, None, None)
        ticks_per_second = max(1, 1000 // app_settings.poll_interval_ms)
        state.session_complete_ticks_remaining = SESSION_COMPLETE_DISPLAY_SECONDS * ticks_per_second

    def handle_new_session(session_num: int, current_incident_count: int, current_lap: int) -> None:
        # Close the previous session in case the transition skipped the
        # Cool Down/Checkered (comum entre Qualify -> Race).
        if not state.session_finalized:
            prev_multiplier = session_weight(state.session_meta.session_type) if state.session_meta else 0.0
            finalize_session(tracker.snapshot(prev_multiplier))

        track_info = get_track_info(sdk)
        player_info = get_player_info(sdk)
        session_meta = get_session_meta(sdk, session_num)

        if session_meta:
            logger.info("Session detected: %s", session_meta.session_type_raw or session_meta.session_type)
        if player_info:
            logger.info("PlayerCarIdx: %s", player_info.car_idx)

        # Reconcile the previous pending session against the fresh SR of this one
        # (ver sr/history.py para a justificativa desse mecanismo).
        if player_info and player_info.safety_rating is not None:
            history.reconcile_last_pending(player_info.safety_rating)
            logger.info("Initial SR: %.2f", player_info.safety_rating)
        elif player_info is not None:
            logger.warning("Could not read the current Safety Rating (see docs/RESEARCH.md section 7)")

        corners_per_lap = float(app_settings.fallback_corners_per_lap)
        if track_info:
            corners_per_lap, source, confidence = track_db.get_corners_per_lap(
                track_info.track_id, track_info.name, track_info.config_name, track_info.num_turns
            )
            logger.info(
                "Track: %s | corners/lap=%.1f (source=%s, confidence=%s)",
                track_info.display_name, corners_per_lap, source, confidence,
            )

        stats = compute_stats(history.all())
        conf_level = confidence_level(stats.n if stats else 0)

        tracker.start_session(
            initial_sr=player_info.safety_rating if player_info else None,
            initial_incident_count=current_incident_count,
            corners_per_lap=corners_per_lap,
            laps_total=session_meta.session_laps if session_meta else None,
            lic_string=player_info.lic_string if player_info else "",
            lic_level=getattr(player_info, "lic_level", None) if player_info else None,
            confidence=conf_level,
        )
        lap_detector.reset(session_num, current_lap)
        corner_accumulator.reset()

        state.track_info = track_info
        state.player_info = player_info
        state.session_meta = session_meta
        state.session_finalized = False

        # Only mark the session as handled once session_meta parsed successfully;
        # caso contrario tenta de novo no proximo tick em vez de travar
        # otherwise it could sit forever on "Waiting for session..." (the YAML
        # may not be fully populated at the exact moment a session changes).
        if session_meta is not None:
            state.current_session_num = session_num

    def refresh_session_laps() -> None:
        if state.session_meta is None:
            return
        meta = get_session_meta(sdk, state.session_meta.session_num)
        if meta is not None and meta.session_laps != state.session_meta.session_laps:
            state.session_meta = meta
            tracker.laps_total = meta.session_laps

    def render(estimate: SREstimate) -> str:
        mode = current_mode()
        session_type = state.session_meta.session_type if state.session_meta else "unknown"
        track_name = state.track_info.display_name if state.track_info else ""
        if mode == "compact":
            return format_compact(estimate)
        if mode == "detailed":
            return format_detailed(estimate, session_type, track_name)
        return format_normal(estimate)

    def tick() -> None:
        connected = sdk.poll()
        if not connected:
            window.set_content(format_waiting("Waiting for iRacing..."))
            return

        if state.session_complete_ticks_remaining > 0:
            state.session_complete_ticks_remaining -= 1
            window.set_content(state.pending_complete_html)
            return

        snap = read_snapshot(sdk)
        if snap is None:
            window.set_content(format_waiting("Waiting for session..."))
            return

        if snap.session_num != state.current_session_num:
            handle_new_session(snap.session_num, snap.incident_count, snap.lap)

        if state.session_meta is None:
            window.set_content(format_waiting("Waiting for session..."))
            return

        state.tick_count += 1
        refresh_every_ticks = max(1, app_settings.session_refresh_interval_ms // app_settings.poll_interval_ms)
        if state.tick_count % refresh_every_ticks == 0:
            refresh_session_laps()

        corner_accumulator.update(snap, tracker.corners_per_lap)
        tracker.set_corners(corner_accumulator.corners)
        lap_completed = lap_detector.update(snap.session_num, snap.lap)
        session_multiplier = session_weight(state.session_meta.session_type)

        if lap_completed:
            estimate = tracker.on_lap_completed(snap.incident_count, session_multiplier)
        else:
            estimate = tracker.snapshot(session_multiplier)

        if state.session_meta.session_type == "practice":
            window.set_content(format_practice_notice(estimate))
            return

        if snap.session_state in (SESSION_STATE_COOL_DOWN, SESSION_STATE_CHECKERED) and not state.session_finalized:
            finalize_session(estimate)
            window.set_content(state.pending_complete_html)
            return

        window.set_content(render(estimate))

    timer = QTimer()
    timer.setInterval(app_settings.poll_interval_ms)
    timer.timeout.connect(tick)
    timer.start()

    def cleanup() -> None:
        if hotkeys is not None:
            hotkeys.shutdown()
        persist_overlay_state()
        sdk.shutdown()
        logger.info("Aplicativo encerrado")

    app.aboutToQuit.connect(cleanup)

    logger.info("iRacing SR Estimator iniciado")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
