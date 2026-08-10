"""
Frameless, semi-transparent PySide6 overlay window.

Click-through by default so it never blocks the mouse in iRacing, and
repositionable in "unlock" mode. Click-through uses the Qt window flags that
map to WS_EX_TRANSPARENT / WS_EX_LAYERED on Windows, so no native calls are
needed. Updates arrive from the polling thread through Qt's normal event
queue, which is thread-safe.
"""

from __future__ import annotations

import logging

import keyboard
from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from overlay.styles import BASE_STYLESHEET

logger = logging.getLogger("sr_estimator.overlay")


class GlobalHotkeys(QObject):
    toggle_overlay = Signal()
    toggle_lock = Signal()
    cycle_mode = Signal()

    def __init__(self, toggle_overlay_key: str, toggle_lock_key: str, cycle_mode_key: str) -> None:
        super().__init__()
        self._registered: list[str] = []
        self._bind(toggle_overlay_key, self.toggle_overlay.emit)
        self._bind(toggle_lock_key, self.toggle_lock.emit)
        self._bind(cycle_mode_key, self.cycle_mode.emit)

    def _bind(self, key: str, callback) -> None:
        try:
            keyboard.add_hotkey(key, callback)
            self._registered.append(key)
            logger.info("Hotkey registered: %s", key)
        except Exception as exc:
            logger.error("Could not register hotkey '%s': %s", key, exc)

    def shutdown(self) -> None:
        for key in self._registered:
            try:
                keyboard.remove_hotkey(key)
            except Exception:
                pass


class OverlayWindow(QWidget):
    def __init__(self, opacity: float, scale: float) -> None:
        super().__init__()
        self._locked = True
        self._drag_offset: QPoint | None = None

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setWindowOpacity(opacity)

        self._label = QLabel(self)
        self._label.setObjectName("panel")
        self._label.setTextFormat(Qt.RichText)
        self._label.setWordWrap(False)
        self.set_scale(scale)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)
        self.setStyleSheet(BASE_STYLESHEET)

    def set_scale(self, scale: float) -> None:
        font = QFont("Consolas")
        font.setPointSizeF(max(6.0, 10.0 * scale))
        self._label.setFont(font)

    def set_content(self, html: str) -> None:
        self._label.setText(html)
        self.adjustSize()

    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        self.setAttribute(Qt.WA_TransparentForMouseEvents, locked)
        border = "1px dashed rgba(255,255,255,120)" if not locked else "1px solid rgba(255,255,255,40)"
        self.setStyleSheet(BASE_STYLESHEET.replace("1px solid rgba(255, 255, 255, 40)", border))
        logger.info("Overlay %s", "locked (click-through)" if locked else "unlocked (draggable)")

    def toggle_lock(self) -> None:
        self.set_locked(not self._locked)

    def toggle_visibility(self) -> None:
        self.setVisible(not self.isVisible())
        logger.info("Overlay %s", "visivel" if self.isVisible() else "oculto")

    # --- drag when unlocked ---
    def mousePressEvent(self, event) -> None:
        if not self._locked and event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event) -> None:
        if not self._locked and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
