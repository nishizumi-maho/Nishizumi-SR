"""Generate docs/integration/vectors.json from the shipped Python implementation.

The vectors are the contract for anyone porting the estimator. They are
produced by calling the same functions the overlay itself calls, so a port that
reproduces them is reproducing this project, not a paraphrase of it.

    python tools/gen_vectors.py

Re-run it whenever a model constant changes, and the ports will fail loudly
instead of drifting quietly.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# iracing.telemetry imports iracing.sdk, which imports pyirsdk purely to talk to
# the simulator. Nothing here touches the sim, so an empty stand-in is enough to
# let the CornerAccumulator - the piece ports most often get wrong - be exercised.
sys.modules.setdefault("irsdk", types.ModuleType("irsdk"))

from iracing.telemetry import CornerAccumulator, TelemetrySnapshot  # noqa: E402
from sr.license_map import (  # noqa: E402
    A_SLOPE,
    F0_CPI,
    R_RATIO,
    band_crossing_bonus,
    band_floor_cpi,
    class_and_band_from_license_level,
    class_index_from_string,
    cpi_from_sr,
    remap_band,
    sr_from_cpi,
    target_cpi_to_hold,
)
from sr.model import (  # noqa: E402
    DEFAULT_PARAMS,
    SESSION_WEIGHTS,
    compute_cpi,
    project_sr,
    session_alpha,
    session_weight,
    update_cpi,
    window_corners,
)

OUT = ROOT / "docs" / "integration" / "vectors.json"

CLASSES = [0, 1, 2, 3, 4, 5]
BANDS = [1, 2, 3, 4]


def license_level_vectors() -> list[dict]:
    return [
        {"lic_level": level, "class_index": ci, "band": band}
        for level in range(1, 25)
        for ci, band in [class_and_band_from_license_level(level)]
    ]


def license_string_vectors() -> list[dict]:
    samples = ["A 4.99", "B 3.54", "C 2.99", "D 1.20", "R 0.80", "Rookie 2.50",
               "Class B", "P 4.10", "", "nonsense"]
    return [{"lic_string": s, "class_index": class_index_from_string(s)} for s in samples]


def band_floor_vectors() -> list[dict]:
    return [
        {"class_index": ci, "band": band, "floor_cpi": band_floor_cpi(ci, band)}
        for ci in CLASSES for band in BANDS
    ]


def sr_cpi_vectors() -> list[dict]:
    out = []
    for ci in CLASSES:
        for band in BANDS:
            for offset in (0.0, 0.01, 0.25, 0.5, 0.75, 0.99):
                sr = band + offset
                cpi = cpi_from_sr(sr, ci, band)
                out.append({
                    "sr": sr, "class_index": ci, "band": band,
                    "cpi": cpi,
                    "sr_round_trip": sr_from_cpi(cpi, ci, band),
                })
    return out


def remap_vectors() -> list[dict]:
    out = []
    for ci in (0, 2, 4):
        for band in BANDS:
            for factor in (0.4, 0.9, 1.0, 1.5, 2.2, 4.0, 12.0):
                cpi = band_floor_cpi(ci, band) * factor
                sr, final_band = remap_band(cpi, ci, band)
                out.append({
                    "cpi": cpi, "class_index": ci, "band": band,
                    "sr": sr, "final_band": final_band,
                })
    return out


def window_vectors() -> list[dict]:
    return [
        {"band": band, "class_index": ci, "window": window_corners(band, ci)}
        for ci in CLASSES for band in BANDS
    ]


def alpha_vectors() -> list[dict]:
    return [
        {"corners": corners, "band": band, "class_index": ci,
         "alpha": session_alpha(corners, band, ci)}
        for corners in (0.0, 1.0, 50.0, 200.0, 1060.0, 2500.0, 9000.0)
        for band in (1, 3)
        for ci in (0, 1, 2, 4)
    ]


def update_cpi_vectors() -> list[dict]:
    out = []
    for cpi_old in (10.0, 22.9, 48.5):
        for corners in (0.0, 40.0, 300.0, 2000.0):
            for incidents in (0.0, 1.0, 8.0):
                out.append({
                    "cpi_old": cpi_old, "corners": corners,
                    "incident_points": incidents, "band": 2, "class_index": 2,
                    "cpi_new": update_cpi(cpi_old, corners, incidents, 2, 2),
                })
    return out


def project_vectors() -> list[dict]:
    """The headline function, over cases that exercise every branch."""
    cases = [
        # (sr_before, class_index, corners, incident_points, comment)
        (2.99, 2, 42.0, 0.0, "clean laps crossing 3.00 - the band-remap bonus"),
        (3.46, 2, 70.0, 8.0, "8x in a short race"),
        (4.99, 4, 300.0, 0.0, "already at the display ceiling"),
        (1.00, 0, 150.0, 4.0, "rookie, short window"),
        (2.50, 1, 500.0, 2.0, "class D"),
        (3.00, 3, 1200.0, 6.0, "class B, endurance stint"),
        (4.20, 4, 2500.0, 12.0, "class A, long race"),
        (2.00, 2, 0.0, 0.0, "no corners driven - nothing may change"),
        (2.00, 2, 100.0, 0.0, "perfectly clean"),
        (0.50, 0, 200.0, 20.0, "disaster near the floor"),
        (3.99, 5, 800.0, 1.0, "pro, one incident"),
        (2.75, 2, 21.0, 4.0, "one bad lap at Spa (21 corners, weight 1.00)"),
        # Qualifying: corners AND incidents are both weighted by 0.50.
        (2.75, 2, 21.0 * 0.5, 4.0 * 0.5, "the same lap in open qualifying"),
    ]
    out = []
    for sr, ci, corners, incidents, comment in cases:
        projected, delta, cpi_new = project_sr(sr, ci, corners, incidents)
        out.append({
            "comment": comment,
            "sr_before": sr, "class_index": ci,
            "corners": corners, "incident_points": incidents,
            "projected_sr": projected, "delta": delta, "cpi_new": cpi_new,
        })
    return out


def corner_accumulator_vectors() -> list[dict]:
    """Sequences of telemetry samples and the corners they must credit.

    ON_TRACK = 3, IN_PIT_STALL = 1, NOT_IN_WORLD = -1 (irsdk_TrkLoc).
    """
    def run(samples: list[tuple[float, int]], corners_per_lap: float) -> float:
        acc = CornerAccumulator()
        for pct, surface in samples:
            acc.update(
                TelemetrySnapshot(
                    session_num=0, session_state=4, session_time=0.0, lap=0,
                    lap_dist_pct=pct, on_pit_road=False,
                    player_track_surface=surface, incident_count=0,
                    incident_count_source="test",
                ),
                corners_per_lap,
            )
        return acc.corners

    steady = [(round(i * 0.02, 4) % 1.0, 3) for i in range(51)]        # exactly one lap
    wrap = [(0.90, 3), (0.95, 3), (0.99, 3), (0.03, 3), (0.08, 3)]      # crosses the line
    teleport = [(0.10, 3), (0.15, 3), (0.80, 3), (0.85, 3)]             # a tow: 0.65 jump
    pits = [(0.10, 3), (0.15, 1), (0.20, 1), (0.25, 3), (0.30, 3)]      # sits in the box
    not_in_world = [(0.10, 3), (0.15, -1), (0.40, 3), (0.45, 3)]        # left the world

    cases = [
        ("one full lap in 2% steps", steady, 14.0),
        ("crossing start/finish", wrap, 20.0),
        ("a tow: the jump itself credits nothing", teleport, 20.0),
        ("time spent in the pit box", pits, 20.0),
        ("out of the world and back", not_in_world, 20.0),
    ]
    return [
        {"comment": comment, "corners_per_lap": cpl,
         "samples": [{"lap_dist_pct": p, "track_surface": s} for p, s in samples],
         "corners": run(samples, cpl)}
        for comment, samples, cpl in cases
    ]


def main() -> int:
    payload = {
        "_meta": {
            "what": "Golden test vectors for the Nishizumi SR estimator.",
            "generated_by": "tools/gen_vectors.py",
            "guide": "docs/INTEGRATION.md",
            "tolerance": {
                "sr": 1e-9,
                "cpi_relative": 1e-9,
                "note": "log/exp differ by an ulp between runtimes; compare with a tolerance, never for equality.",
            },
        },
        "constants": {
            "a_slope": A_SLOPE,
            "r_ratio": R_RATIO,
            "f0_cpi": F0_CPI,
            "window_band1": DEFAULT_PARAMS.window_band1,
            "window_band_ratio": DEFAULT_PARAMS.window_band_ratio,
            "window_factor_rookie": DEFAULT_PARAMS.window_factor_rookie,
            "window_factor_class_d": DEFAULT_PARAMS.window_factor_class_d,
            "max_abs_session_delta": DEFAULT_PARAMS.max_abs_session_delta,
            "sr_display_max": 4.99,
            "band_crossing_bonus": band_crossing_bonus(),
        },
        "session_weights": SESSION_WEIGHTS,
        "session_weight_lookups": [
            {"session_type": name, "weight": session_weight(name)}
            for name in ["race", "heat", "qualify", "qualify_lone", "warmup",
                         "practice", "practice_lone", "time_trial", "unknown",
                         "Race", "something else"]
        ],
        "target_cpi_to_hold_3_00": [
            {"class_index": ci, "target_cpi": target_cpi_to_hold(ci)} for ci in CLASSES
        ],
        "license_levels": license_level_vectors(),
        "license_strings": license_string_vectors(),
        "band_floors": band_floor_vectors(),
        "sr_cpi": sr_cpi_vectors(),
        "remap": remap_vectors(),
        "windows": window_vectors(),
        "alphas": alpha_vectors(),
        "update_cpi": update_cpi_vectors(),
        "session_cpi": [
            {"corners": c, "incident_points": i, "cpi": compute_cpi(c, i)}
            for c, i in [(100.0, 0.0), (100.0, 4.0), (0.0, 3.0), (42.0, 8.0)]
        ],
        "project": project_vectors(),
        "corner_accumulator": corner_accumulator_vectors(),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    count = sum(len(v) for v in payload.values() if isinstance(v, list))
    print(f"Wrote {OUT.relative_to(ROOT)} ({count} vectors, {OUT.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
