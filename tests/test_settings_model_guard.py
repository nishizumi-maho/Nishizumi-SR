"""The shipped model must win over parameters left by an older build.

    python tests/test_settings_model_guard.py

An upgrading user keeps their settings.json in %LOCALAPPDATA%. Without the
fingerprint stamp, a `model` block written by an older release would silently
keep running last release's constants forever - the overlay would look updated
and quietly compute the old answer.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings as cfg  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="nishizumi-settings-"))


def test_fingerprint_tracks_the_constants() -> None:
    """The stamp is derived from the defaults, so it cannot be forgotten."""
    defaults = asdict(cfg.ModelSettings())
    assert cfg.model_fingerprint() == hashlib.sha256(
        json.dumps(defaults, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]

    changed = dict(defaults, window_band1=defaults["window_band1"] + 1)
    other = hashlib.sha256(json.dumps(changed, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    assert other != cfg.model_fingerprint(), "changing a constant must move the stamp"


def test_unstamped_old_file_is_reset() -> None:
    path = TMP / "old.json"
    path.write_text(json.dumps({
        "overlay": {"x": 500, "y": 300, "locked": True, "mode": "compact"},
        "hotkeys": {"toggle_overlay": "F7"},
        # v4 as it stood a few releases ago: shorter window, no class factors.
        "model": {"window_band1": 1050.0, "window_band_ratio": 1.4142,
                  "window_factor_rookie": 1.0, "window_factor_class_d": 1.0,
                  "max_abs_session_delta": 3.0},
    }), encoding="utf-8")

    settings = cfg.load(path)

    shipped = cfg.ModelSettings()
    assert settings.model.window_band1 == shipped.window_band1, "stale window survived"
    assert settings.model.window_factor_rookie == shipped.window_factor_rookie, \
        "stale rookie factor survived"

    # Everything that is genuinely the user's stays untouched.
    assert settings.overlay.x == 500 and settings.overlay.y == 300
    assert settings.overlay.locked is True
    assert settings.overlay.mode == "compact"
    assert settings.hotkeys.toggle_overlay == "F7"

    # And the file is re-stamped, so the next launch is quiet.
    assert json.loads(path.read_text(encoding="utf-8"))["model_fingerprint"] == cfg.model_fingerprint()


def test_personal_calibration_survives_within_a_version() -> None:
    """`python -m sr.calibration` tells users to edit this; do not fight them."""
    path = TMP / "mine.json"
    mine = cfg.AppSettings()
    mine.model.window_band1 = 1650.0
    cfg.save(mine, path)

    assert cfg.load(path).model.window_band1 == 1650.0


def test_calibration_is_dropped_when_the_shipped_model_moves() -> None:
    path = TMP / "mine2.json"
    mine = cfg.AppSettings()
    mine.model.window_band1 = 1650.0
    cfg.save(mine, path)

    real = cfg.model_fingerprint
    try:
        cfg.model_fingerprint = lambda: "deadbeef0000"   # pretend the next release refits
        settings = cfg.load(path)
        assert settings.model.window_band1 == cfg.ModelSettings().window_band1, \
            "an old calibration must not survive a model change"
    finally:
        cfg.model_fingerprint = real


def test_a_fresh_install_is_stamped() -> None:
    path = TMP / "fresh.json"
    cfg.load(path)
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["model_fingerprint"] == cfg.model_fingerprint()


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {test.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok    {test.__name__}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
