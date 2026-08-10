"""Behavioural tests for the overlay window.

Runs headless (QT_QPA_PLATFORM=offscreen is set below), so it works in CI and
on a machine without a display:

    python tests/test_overlay_window.py

These cover the two things the released .exe got wrong - a panel that could not
be dragged and could not be closed - plus the recovery paths around them.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from overlay.window import OverlayWindow

app = QApplication.instance() or QApplication([])


def _mouse(kind: QEvent.Type, pos: QPoint, button: Qt.MouseButton = Qt.LeftButton) -> QMouseEvent:
    """A mouse event whose global position is the same as its local one.

    The window is at the origin in these tests, so local and global coincide and
    the drag arithmetic stays readable.
    """
    buttons = Qt.LeftButton if kind != QEvent.MouseButtonRelease else Qt.NoButton
    return QMouseEvent(kind, QPointF(pos), QPointF(pos), button, buttons, Qt.NoModifier)


def _drag(window: OverlayWindow, start: QPoint, end: QPoint) -> None:
    app.sendEvent(window, _mouse(QEvent.MouseButtonPress, start))
    app.sendEvent(window, _mouse(QEvent.MouseMove, end))
    app.sendEvent(window, _mouse(QEvent.MouseButtonRelease, end))


def _new_window(locked: bool) -> OverlayWindow:
    window = OverlayWindow(opacity=0.85, scale=1.0, locked=locked)
    window.set_content("SR +0.00")
    window.show()
    window.move(0, 0)
    app.processEvents()
    return window


def test_starts_unlocked_with_visible_chrome() -> None:
    window = _new_window(locked=False)
    assert window.locked is False
    assert window._header.isVisible(), "the header must be on screen when unlocked"
    assert window._close_button.isVisible(), "a user must be able to see how to close it"
    assert not window.testAttribute(Qt.WA_TransparentForMouseEvents)
    window.close()


def test_locked_hides_chrome_and_goes_click_through() -> None:
    window = _new_window(locked=True)
    assert window.locked is True
    assert not window._header.isVisible(), "no chrome while locked - it is a HUD"
    assert window.testAttribute(Qt.WA_TransparentForMouseEvents)
    window.close()


def test_toggle_lock_round_trips() -> None:
    window = _new_window(locked=True)
    seen: list[bool] = []
    window.lock_changed.connect(seen.append)

    window.toggle_lock()
    assert window.locked is False
    assert window._header.isVisible()
    assert not window.testAttribute(Qt.WA_TransparentForMouseEvents)

    window.toggle_lock()
    assert window.locked is True
    assert not window._header.isVisible()
    assert window.testAttribute(Qt.WA_TransparentForMouseEvents)

    assert seen == [False, True], f"lock_changed should report each transition, got {seen}"
    window.close()


def test_drag_moves_the_window_when_unlocked() -> None:
    """The headline bug: this was impossible in the released .exe."""
    window = _new_window(locked=False)
    moved: list[int] = []
    window.position_changed.connect(lambda: moved.append(1))

    _drag(window, QPoint(10, 5), QPoint(210, 155))

    assert window.pos() == QPoint(200, 150), f"expected (200, 150), got {window.pos()}"
    assert moved, "releasing the drag must announce the new position so it gets saved"
    window.close()


def test_drag_is_ignored_when_locked() -> None:
    window = _new_window(locked=True)
    _drag(window, QPoint(10, 5), QPoint(210, 155))
    assert window.pos() == QPoint(0, 0), "a locked overlay must never move"
    window.close()


def test_right_button_does_not_drag() -> None:
    window = _new_window(locked=False)
    app.sendEvent(window, _mouse(QEvent.MouseButtonPress, QPoint(10, 5), Qt.RightButton))
    app.sendEvent(window, _mouse(QEvent.MouseMove, QPoint(210, 155)))
    assert window.pos() == QPoint(0, 0)
    window.close()


def test_close_button_asks_to_quit() -> None:
    window = _new_window(locked=False)
    asked: list[int] = []
    window.close_requested.connect(lambda: asked.append(1))
    window._close_button.click()
    assert asked, "the X button must request a quit"
    window.close()


def test_mode_button_asks_to_cycle() -> None:
    window = _new_window(locked=False)
    asked: list[int] = []
    window.mode_cycle_requested.connect(lambda: asked.append(1))
    window._mode_button.click()
    assert asked
    window.close()


def test_lock_button_locks() -> None:
    window = _new_window(locked=False)
    window._lock_button.click()
    assert window.locked is True, "the header lock button must work without any hotkey"
    window.close()


def test_window_close_means_quit_not_disappear() -> None:
    """Alt+F4 must not leave a headless process holding the hotkeys."""
    window = _new_window(locked=False)
    asked: list[int] = []
    window.close_requested.connect(lambda: asked.append(1))
    closed = window.close()
    assert asked, "closing the window must request an application quit"
    assert closed is False, "the close is intercepted; main.py decides what happens"
    window.hide()


def test_off_screen_position_is_pulled_back() -> None:
    window = _new_window(locked=False)
    screen = window.screen().availableGeometry()

    window.move_onto_screen(99_999, 99_999)
    pos = window.pos()
    assert screen.contains(pos), f"{pos} should have been clamped into {screen}"

    window.move_onto_screen(-5_000, -5_000)
    pos = window.pos()
    assert pos.x() >= screen.left() and pos.y() >= screen.top(), f"{pos} is still off-screen"

    window.move_onto_screen(120, 90)
    assert window.pos() == QPoint(120, 90), "a valid position must be left alone"
    window.close()


def test_content_updates_and_skips_identical_repaints() -> None:
    window = _new_window(locked=False)
    window.set_content("first")
    assert window._label.text() == "first"
    window.set_content("second")
    assert window._label.text() == "second"
    window.set_content("second")  # no-op path
    assert window._label.text() == "second"
    window.close()


def test_visibility_toggles() -> None:
    window = _new_window(locked=True)
    assert window.isVisible()
    window.toggle_visibility()
    assert not window.isVisible()
    window.toggle_visibility()
    assert window.isVisible()
    window.close()


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - a crash is a failure too
            failures += 1
            print(f"ERROR {test.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok    {test.__name__}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
