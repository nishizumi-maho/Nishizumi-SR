"""A stand-in for pyirsdk that replays a scripted session.

Lets the whole application - polling, session detection, corner accumulation,
the estimate and the overlay - run end to end on a machine with no iRacing and
no shared memory. Install it with :func:`install` *before* importing anything
that imports ``irsdk``.
"""

from __future__ import annotations

import sys
import types
from typing import Any, Optional


class FakeSim:
    """Mutable state a test drives directly, exposed the way the SDK exposes it."""

    def __init__(self) -> None:
        self.connected = False
        self.session_num = 0
        self.session_type = "Practice"
        self.session_laps = 0            # 0 -> "unlimited" (a timed session)
        self.session_state = 4           # irsdk_SessionState: Racing
        self.session_time = 0.0
        self.lap = 0
        self.lap_dist_pct = 0.0
        self.on_pit_road = False
        self.track_surface = 3           # irsdk_TrkLoc: OnTrack
        self.incidents = 0

        # Fraction of a lap covered per application tick. The real accumulator
        # integrates LapDistPct and deliberately refuses to credit a jump larger
        # than 10% of a lap (tow, teleport, reset), so a test that moved the car
        # in one leap would score zero corners - as a real tow does.
        self.auto_drive = 0.0

        self.track_id = 18               # Road America in the official table
        self.track_name = "roadamerica"
        self.track_config = "Full Course"
        self.track_display = "Road America"
        self.track_num_turns = 14

        self.lic_string = "C 2.99"
        self.lic_level = 10              # class C, band 2 - matches the SR below
        self.lic_sub_level = 299         # SR * 100

    # -- convenience used by the tests --------------------------------------
    def advance_tick(self) -> None:
        """One application tick's worth of movement."""
        self.session_time += 0.1
        if self.auto_drive:
            self.drive(self.auto_drive)

    def drive(self, fraction: float) -> None:
        """Advance the car by a fraction of a lap, wrapping the lap counter."""
        self.lap_dist_pct += fraction
        while self.lap_dist_pct >= 1.0:
            self.lap_dist_pct -= 1.0
            self.lap += 1

    def weekend_info(self) -> dict:
        return {
            "TrackID": self.track_id,
            "TrackName": self.track_name,
            "TrackConfigName": self.track_config,
            "TrackDisplayName": self.track_display,
            "TrackNumTurns": self.track_num_turns,
        }

    def driver_info(self) -> dict:
        return {
            "DriverCarIdx": 0,
            "Drivers": [{
                "CarIdx": 0,
                "UserID": 123456,
                "UserName": "Test Driver",
                "CarScreenName": "Ferrari 296 GT3",
                "LicString": self.lic_string,
                "LicLevel": self.lic_level,
                "LicSubLevel": self.lic_sub_level,
            }],
        }

    def session_info(self) -> dict:
        sessions = []
        for num in range(self.session_num + 1):
            is_current = num == self.session_num
            sessions.append({
                "SessionNum": num,
                "SessionType": self.session_type if is_current else "Practice",
                "SessionName": (self.session_type or "").upper(),
                "SessionLaps": self.session_laps if is_current else "unlimited",
            })
        return {"Sessions": sessions}


class FakeIRSDK:
    """The subset of ``irsdk.IRSDK`` that ``iracing/sdk.py`` actually touches."""

    def __init__(self, sim: Optional[FakeSim] = None) -> None:
        self._sim = sim if sim is not None else FakeIRSDK.sim
        self.is_initialized = False

    # class-level default so `irsdk.IRSDK()` with no arguments finds the sim
    sim: FakeSim

    @property
    def is_connected(self) -> bool:
        return bool(self._sim.connected)

    def startup(self) -> bool:
        if self._sim.connected:
            self.is_initialized = True
        return self.is_initialized

    def shutdown(self) -> None:
        self.is_initialized = False

    def __getitem__(self, key: str) -> Any:
        if not self.is_initialized or not self._sim.connected:
            return None
        sim = self._sim
        # "SessionNum" is the first key read_snapshot() asks for, exactly once
        # per tick, so it is the natural place to move the car forward. Lap and
        # LapDistPct are then read from a state that is internally consistent.
        if key == "SessionNum":
            sim.advance_tick()
        table = {
            "WeekendInfo": sim.weekend_info(),
            "DriverInfo": sim.driver_info(),
            "SessionInfo": sim.session_info(),
            "PlayerCarIdx": 0,
            "SessionNum": sim.session_num,
            "SessionState": sim.session_state,
            "SessionTime": sim.session_time,
            "Lap": sim.lap,
            "LapDistPct": sim.lap_dist_pct,
            "OnPitRoad": sim.on_pit_road,
            "PlayerTrackSurface": sim.track_surface,
            "PlayerCarMyIncidentCount": sim.incidents,
            "IncidentCount": sim.incidents,
        }
        if key not in table:
            raise KeyError(key)
        return table[key]


def install(sim: FakeSim) -> FakeSim:
    """Register a fake ``irsdk`` module (and a no-op ``keyboard``) in sys.modules."""
    FakeIRSDK.sim = sim

    module = types.ModuleType("irsdk")
    module.IRSDK = FakeIRSDK           # type: ignore[attr-defined]
    sys.modules["irsdk"] = module

    # The hotkey library needs a real OS hook, which CI does not have. A no-op
    # stand-in keeps the "hotkeys registered" path under test; without it the
    # app takes its no-hotkeys fallback instead.
    keyboard_module = types.ModuleType("keyboard")
    keyboard_module.add_hotkey = lambda *a, **k: None      # type: ignore[attr-defined]
    keyboard_module.remove_hotkey = lambda *a, **k: None   # type: ignore[attr-defined]
    sys.modules["keyboard"] = keyboard_module
    return sim
