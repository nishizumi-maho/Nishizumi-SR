"""End-to-end run of the whole application against a scripted fake session.

Boots main.py for real - polling timer, session detection, corner accumulation,
the SR estimate, history and the overlay - with pyirsdk replaced by
tests/fake_irsdk.py. Nothing here needs iRacing, a display or a network:

    python tests/test_app_smoke.py

It checks the things a user would notice: that the panel says it is waiting
before the sim is up, that practice is called out as not counting, that a race
with incidents shows a negative delta, that the session is written to history,
and that the close button actually ends the process.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.fake_irsdk import FakeSim, install  # noqa: E402

SIM = install(FakeSim())

# Writable state must not land in the checkout while tests run.
import app_paths  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="nishizumi-test-"))
app_paths.user_data_dir = lambda: _TMP  # type: ignore[assignment]

import json  # noqa: E402
import re  # noqa: E402

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QSystemTrayIcon  # noqa: E402

SETTINGS = _TMP / "config" / "settings.json"
SETTINGS.parent.mkdir(parents=True, exist_ok=True)
SETTINGS.write_text(json.dumps({
    "overlay": {"x": 20, "y": 20, "opacity": 0.85, "scale": 1.0,
                "mode": "detailed", "locked": True},
    # Poll as fast as the event loop allows so the run takes under a second.
    "poll_interval_ms": 1,
    "session_refresh_interval_ms": 5,
}), encoding="utf-8")

import main as app_main  # noqa: E402

FAILURES: list[str] = []
NOTES: list[str] = []


def check(condition: bool, message: str) -> None:
    if condition:
        NOTES.append(f"ok    {message}")
    else:
        FAILURES.append(message)
        NOTES.append(f"FAIL  {message}")


def plain(html: str) -> str:
    """The panel text as a user reads it, with the markup taken out."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = (text.replace("&nbsp;", " ").replace("&middot;", "-")
                .replace("&#8594;", "->").replace("&amp;", "&"))
    return re.sub(r"\s+", " ", text).strip()


class Script:
    """Drives the fake sim and inspects the overlay.

    Steps run one per beat, but a step may park the script on a condition -
    "keep beating until the panel says X" - so the test never depends on how
    many polls the event loop manages to fit into a given wall-clock interval.
    """

    STEP_MS = 10
    WAIT_BEATS = 400

    def __init__(self, window) -> None:
        self.window = window
        self.steps = [
            self.expect_waiting,
            self.connect_practice,
            self.expect_practice_notice,
            self.start_race,
            self.expect_clean_race,
            self.take_incidents,
            self.expect_penalty,
            self.check_overlay_controls,
            self.wave_checkered,
            self.expect_session_complete,
            self.expect_history_written,
            self.close_via_button,
        ]
        self.index = 0
        self.pending = None
        self.timer = QTimer()
        self.timer.setInterval(self.STEP_MS)
        self.timer.timeout.connect(self.beat)
        self.timer.start()

        # Nothing in this test may hang CI.
        self.guard = QTimer()
        self.guard.setSingleShot(True)
        self.guard.timeout.connect(self.timeout)
        self.guard.start(30_000)

    @property
    def text(self) -> str:
        return plain(self.window._label.text())

    def wait_for(self, predicate, description: str) -> None:
        self.pending = [predicate, description, self.WAIT_BEATS]

    def stop(self) -> None:
        self.timer.stop()
        self.guard.stop()

    def beat(self) -> None:
        if self.pending is not None:
            predicate, description, remaining = self.pending
            if predicate():
                self.pending = None
            else:
                remaining -= 1
                if remaining <= 0:
                    FAILURES.append(f"timed out waiting for {description}")
                    NOTES.append(f"FAIL  timed out waiting for {description}")
                    self.stop()
                    QApplication.instance().quit()
                    return
                self.pending[2] = remaining
                return

        if self.index >= len(self.steps):
            return
        step = self.steps[self.index]
        self.index += 1
        try:
            step()
        except Exception as exc:  # noqa: BLE001
            FAILURES.append(f"{step.__name__} raised {type(exc).__name__}: {exc}")
            NOTES.append(f"ERROR {step.__name__}: {exc}")
            self.stop()
            QApplication.instance().quit()

    def timeout(self) -> None:
        FAILURES.append(f"the script stalled at step {self.index}")
        NOTES.append(f"FAIL  the script stalled at step {self.index}")
        self.timer.stop()
        QApplication.instance().quit()

    # -- steps --------------------------------------------------------------
    def expect_waiting(self) -> None:
        check("Waiting for iRacing" in self.text,
              "shows 'Waiting for iRacing...' before the sim is up")

    def connect_practice(self) -> None:
        SIM.session_type = "Practice"
        SIM.auto_drive = 0.02          # 2% of a lap per tick, well under the jump guard
        SIM.connected = True
        self.wait_for(lambda: "PRACTICE" in self.text, "the practice notice")

    def expect_practice_notice(self) -> None:
        check("does not count towards SR" in self.text,
              "calls out practice as not counting towards SR")
        check("Curvas" not in self.text and "Incidentes" not in self.text,
              "the practice notice is in English")

    def start_race(self) -> None:
        SIM.session_num = 1
        SIM.session_type = "Race"
        SIM.session_laps = 10
        SIM.lap = 0
        SIM.lap_dist_pct = 0.0
        SIM.incidents = 0
        self.wait_for(lambda: SIM.lap >= 3 and "ESTIMATED SAFETY RATING" in self.text,
                      "three racing laps to be completed")

    def expect_clean_race(self) -> None:
        text = self.text
        check("Road America" in text and "Race" in text,
              "names the track and the session type")
        check("2.99" in text, "shows the SR read from the SDK (2.99)")
        check("Clean" in text, "reports a clean run as 'Clean', not infinity")

        corners = self.number(text, r"Corners\s+([0-9.]+)")
        check(corners is not None and corners >= 40,
              f"credits corners driven by distance (got {corners}, expect ~42 for 3 laps)")

        delta = self.number(text, r"Session\s+([+-][0-9.]+)")
        check(delta is not None and delta > 0,
              f"a clean race projects an SR gain (got {delta})")

        # Starting SR is 2.99, so a clean run crosses a whole number and picks
        # up the band-remap bonus iRacing documents as "+0.40". Nothing in the
        # code hardcodes it - it falls out of the overlapping-band geometry.
        projected = self.number(text, r"Projected\s+([0-9.]+)")
        check(projected is not None and projected >= 3.0,
              f"crossing 3.00 applies the band-remap bonus (2.99 -> {projected})")
        check(delta is not None and 0.40 <= delta <= 0.55,
              f"the crossing bonus lands near the documented +0.40 (got {delta})")

    def take_incidents(self) -> None:
        SIM.incidents = 8
        self.wait_for(lambda: "8x" in self.text and SIM.lap >= 5,
                      "the incidents to register over five laps")

    def expect_penalty(self) -> None:
        text = self.text
        delta = self.number(text, r"Session\s+([+-][0-9.]+)")
        check(delta is not None and delta < 0,
              f"8x in a short race costs SR (got {delta})")
        cpi = self.number(text, r"CPI\s+([0-9.]+)")
        check(cpi is not None and cpi > 0, f"reports the session CPI (got {cpi})")
        check("Target CPI" in text, "shows the CPI needed to hold 3.00")

    def check_overlay_controls(self) -> None:
        window = self.window
        if QSystemTrayIcon.isSystemTrayAvailable():
            check(window.locked is True, "honours locked:true from settings.json")
        else:
            # The offscreen platform has no tray. That used to leave the app with
            # no quit path at all, so it must now refuse to stay click-through.
            check(window.locked is False,
                  "with no system tray, refuses to stay locked so it can still be closed")

        window.set_locked(False)
        check(window._header.isVisible(), "unlocking reveals the header bar")
        check(window._close_button.isVisible(), "the close button is on screen")
        start = window.pos()
        window.move(start.x() + 40, start.y() + 25)
        check(window.pos() != start, "the window can be repositioned while unlocked")

    def wave_checkered(self) -> None:
        SIM.session_state = 5          # irsdk_SessionState: Checkered
        self.wait_for(lambda: "SESSION COMPLETE" in self.text, "the end-of-session summary")

    def expect_session_complete(self) -> None:
        check("SESSION COMPLETE" in self.text, "shows the summary when the session ends")
        check("Estimated" in self.text, "the summary reports the estimate")

    def expect_history_written(self) -> None:
        path = _TMP / "data" / "history.json"
        check(path.exists(), "writes the session to history.json")
        if not path.exists():
            return
        records = json.loads(path.read_text(encoding="utf-8"))
        check(len(records) == 1, f"exactly one record was stored (got {len(records)})")
        if not records:
            return
        record = records[-1]
        check(record["incidents"] == 8, "the record keeps the incident count")
        check(record["corners"] > 0, f"the record keeps the corners driven ({record['corners']})")
        check(record["estimated_delta"] < 0,
              f"the stored estimate is a loss ({record['estimated_delta']})")
        check(record["reconciled"] is False, "the record is pending reconciliation")
        check(record["track"] == "Road America", "the record names the track")

    def close_via_button(self) -> None:
        self.stop()
        check(self.window._close_button.isVisible(), "the close button is reachable")
        self.window._close_button.click()

    @staticmethod
    def number(text: str, pattern: str):
        match = re.search(pattern, text)
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None


def run() -> int:
    """Patch the overlay so the script can attach, then run the real main()."""
    real_overlay = app_main.OverlayWindow
    holder: dict = {}

    class ScriptedOverlay(real_overlay):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            # A QApplication exists by now, so a timer can be started.
            holder["script"] = Script(self)

    app_main.OverlayWindow = ScriptedOverlay
    try:
        app_main.main()
    except SystemExit as exc:
        check(exc.code in (0, None), f"the app exited cleanly (code {exc.code})")
    finally:
        app_main.OverlayWindow = real_overlay

    check("script" in holder, "the overlay was created")
    if "script" in holder:
        script = holder["script"]
        check(script.index == len(script.steps),
              f"every step ran ({script.index}/{len(script.steps)})")

    print("\n".join(NOTES))
    if FAILURES:
        print("\nFAILURES:")
        for failure in FAILURES:
            print(f"  - {failure}")
        print(f"\n{len(NOTES) - len(FAILURES)}/{len(NOTES)} checks passed")
        return 1
    print(f"\nall {len(NOTES)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
