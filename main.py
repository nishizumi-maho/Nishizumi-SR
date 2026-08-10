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

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
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

from app_paths import user_file

LOGS_DIR = user_file("logs", ".keep").parent
OVERLAY_MODES = ["compact", "normal", "detailed"]
SESSION_COMPLETE_DISPLAY_SECONDS = 8
# A tick that keeps failing would otherwise fill the disk with the same
# traceback ten times a second.
MAX_LOGGED_TICK_ERRORS = 5


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

    logger = logging.getLogger("sr_estimator")
    logger.setLevel(logging.INFO)
    logger.addHandler(file_handler)

    # A windowed PyInstaller build has no console: sys.stdout is None, and
    # StreamHandler(None) falls back to sys.stderr, which is also None. Every
    # single log call then raises inside logging and gets swallowed. Only
    # attach a console handler when there is actually a console to write to.
    if sys.stdout is not None:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    logger.info("Log file: %s", log_path)


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
        self.tick_errors: int = 0


def make_tray_icon() -> QIcon:
    """Green disc labelled "SR".

    Windows 11 hides unfamiliar tray icons behind the overflow chevron, and the
    tray is the recovery path when the overlay is locked or hidden - so it has
    to be identifiable once the user goes looking for it. Drawn in code so the
    build stays a single file with no external asset.
    """
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(76, 215, 135))
    painter.setPen(QColor(15, 17, 20))
    painter.drawEllipse(2, 2, 60, 60)

    font = QFont("Consolas")
    font.setPixelSize(30)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor(15, 17, 20))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "SR")
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

    # Stated up front so "which model is this actually running?" is answerable
    # from the log alone. config.settings resets a model block left behind by an
    # older build, so this line reflects what is really in use.
    logger.info(
        "Model v4 [%s]: window_band1=%.0f ratio=%.4f rookie=%.2f classD=%.2f",
        settings_module.model_fingerprint(),
        model_params.window_band1, model_params.window_band_ratio,
        model_params.window_factor_rookie, model_params.window_factor_class_d,
    )

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    window = OverlayWindow(
        opacity=app_settings.overlay.opacity,
        scale=app_settings.overlay.scale,
        locked=app_settings.overlay.locked,
    )
    window.set_content(format_waiting("Waiting for iRacing..."))
    # Position before showing, and only onto a screen that still exists - a
    # saved position from a monitor that has since been unplugged would put the
    # overlay somewhere the user cannot reach.
    window.move_onto_screen(app_settings.overlay.x, app_settings.overlay.y)
    window.show()

    mode_index = OVERLAY_MODES.index(app_settings.overlay.mode) if app_settings.overlay.mode in OVERLAY_MODES else 1

    def current_mode() -> str:
        return OVERLAY_MODES[mode_index]

    def persist_overlay_state() -> None:
        pos = window.pos()
        app_settings.overlay.x = pos.x()
        app_settings.overlay.y = pos.y()
        app_settings.overlay.mode = current_mode()
        app_settings.overlay.locked = window.locked
        try:
            settings_module.save(app_settings)
        except OSError as exc:
            # Losing the saved position is not worth taking the overlay down for.
            logger.error("Could not write settings.json: %s", exc)

    def on_cycle_mode() -> None:
        nonlocal mode_index
        mode_index = (mode_index + 1) % len(OVERLAY_MODES)
        logger.info("Overlay mode: %s", current_mode())
        persist_overlay_state()

    def reset_overlay() -> None:
        """Bring the overlay back from wherever it has got to.

        The one action that recovers every stuck state at once: hidden, locked,
        or parked off-screen on a monitor that is no longer there.
        """
        window.show()
        window.move_onto_screen(20, 20)
        window.set_locked(False)
        logger.info("Overlay reset to the top-left corner, unlocked")

    # The window only reports intent; what it means is decided here.
    window.close_requested.connect(app.quit)
    window.hide_requested.connect(window.hide)
    window.mode_cycle_requested.connect(on_cycle_mode)
    window.position_changed.connect(persist_overlay_state)
    window.lock_changed.connect(lambda _locked: persist_overlay_state())

    hotkeys: Optional[GlobalHotkeys] = None
    try:
        hotkeys = GlobalHotkeys(
            app_settings.hotkeys.toggle_overlay,
            app_settings.hotkeys.toggle_lock,
            app_settings.hotkeys.cycle_mode,
        )
        hotkeys.toggle_overlay.connect(window.toggle_visibility)
        hotkeys.toggle_lock.connect(window.toggle_lock)
        hotkeys.cycle_mode.connect(on_cycle_mode)
    except Exception as exc:
        logger.error("Global hotkeys are unavailable: %s", exc)
        hotkeys = None

    if hotkeys is None or not hotkeys.available:
        # Usually iRacing running elevated while this app is not: Windows will
        # not deliver a keyboard hook across privilege levels. Force the overlay
        # unlocked so its header bar - drag handle, lock and close buttons - is
        # on screen. Otherwise the user is left with a panel they can neither
        # move nor close.
        logger.warning(
            "No global hotkey could be registered. Leaving the overlay unlocked so it "
            "can still be moved and closed with the mouse; run this app as Administrator "
            "if iRacing is elevated."
        )
        if window.locked:
            window.set_locked(False)

    def hotkey_hint(key: str) -> str:
        """Only advertise a key in the menu if it actually bound."""
        return f"\t{key}" if hotkeys is not None and key in hotkeys.registered else ""

    # --- system tray: the recovery path, and the tidiest way to quit. The
    # overlay also carries its own close button whenever it is unlocked. ---
    tray: Optional[QSystemTrayIcon] = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = QSystemTrayIcon(make_tray_icon())
        tray.setToolTip("Nishizumi SR - estimated Safety Rating")
        menu = QMenu()
        actions = [
            (f"Show / hide overlay{hotkey_hint(app_settings.hotkeys.toggle_overlay)}",
             window.toggle_visibility),
            (f"Lock / unlock position{hotkey_hint(app_settings.hotkeys.toggle_lock)}",
             window.toggle_lock),
            (f"Cycle display mode{hotkey_hint(app_settings.hotkeys.cycle_mode)}",
             on_cycle_mode),
            ("Reset position", reset_overlay),
        ]
        for label, slot in actions:
            action = QAction(label, menu)
            action.triggered.connect(slot)
            menu.addAction(action)
        menu.addSeparator()
        action_quit = QAction("Quit Nishizumi SR", menu)
        action_quit.triggered.connect(app.quit)
        menu.addAction(action_quit)
        tray.setContextMenu(menu)

        def on_tray_activated(reason) -> None:
            # Left click or double click is the "I have lost the overlay" gesture.
            if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
                reset_overlay()

        tray.activated.connect(on_tray_activated)
        tray.show()
    else:
        # Rare, but it used to mean the app had no quit path at all.
        logger.warning(
            "No system tray is available. Use the close button on the unlocked "
            "overlay to quit."
        )
        if window.locked:
            window.set_locked(False)

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

    def safe_tick() -> None:
        """Contain anything the tick throws.

        PySide6 terminates the process on an unhandled exception raised inside
        a slot called from C++. In a windowed build that means the overlay just
        disappears and the traceback goes nowhere. One bad telemetry read is
        not worth losing the session over, so it is logged and polling
        continues.
        """
        try:
            tick()
        except Exception:
            state.tick_errors += 1
            if state.tick_errors <= MAX_LOGGED_TICK_ERRORS:
                logger.exception("Unhandled error while polling")
                if state.tick_errors == MAX_LOGGED_TICK_ERRORS:
                    logger.error("Further polling errors will not be logged")

    timer = QTimer()
    timer.setInterval(app_settings.poll_interval_ms)
    timer.timeout.connect(safe_tick)
    timer.start()

    def cleanup() -> None:
        if hotkeys is not None:
            hotkeys.shutdown()
        persist_overlay_state()
        sdk.shutdown()
        if tray is not None:
            tray.hide()   # otherwise Windows leaves a ghost icon until hovered
        if state.tick_errors:
            logger.warning("Polling errors this run: %s", state.tick_errors)
        logger.info("Application closed")

    app.aboutToQuit.connect(cleanup)

    logger.info(
        "Nishizumi SR started - overlay is %s. Close it with the X button when "
        "unlocked, or from the tray icon.",
        "locked (click-through)" if window.locked else "unlocked (draggable)",
    )
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
