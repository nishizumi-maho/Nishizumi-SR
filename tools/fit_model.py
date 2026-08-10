"""Refit the Nishizumi SR model against the anonymous calibration set.

Usage:  python tools/fit_model.py

Reads data/calibration_set.json (13,849 anonymous driver-sessions taken from
public official race results) and reports, for a range of candidate models,
how well each reproduces iRacing's own new_cpi and the resulting Safety Rating.

Nothing here runs at app time - this is the offline experiment that produced
the constants in sr/license_map.py and sr/model.py.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sr.license_map import A_SLOPE, F0_CPI, R_RATIO, cpi_from_sr, remap_band

DATA = Path(__file__).resolve().parent.parent / "data" / "calibration_set.json"
SQRT2 = math.sqrt(2.0)


def load():
    raw = json.loads(DATA.read_text(encoding="utf-8"))
    return [
        dict(c=r["c"], i=r["i"], oc=r["oc"], nc=r["nc"],
             sr0=r["s0"] / 100.0, sr1=r["s1"] / 100.0, band=r["b"], cls=r["k"])
        for r in raw["rows"]
    ]


def window(band, cls, w1, rho, kr, kd):
    k = kr if cls == 0 else (kd if cls == 1 else 1.0)
    return w1 * (rho ** (band - 1)) * k


def err_cpi(rows, w1, rho, kr, kd):
    """RMSE (%) on iRacing's own new_cpi - the cleanest target, no band noise."""
    s = 0.0
    for d in rows:
        a = 1.0 - math.exp(-d["c"] / window(d["band"], d["cls"], w1, rho, kr, kd))
        x = (1.0 / d["oc"]) * (1 - a) + a * (d["i"] / d["c"])
        pred = 1.0 / x if x > 1e-9 else 1e9
        rel = (pred - d["nc"]) / d["nc"]
        s += rel * rel
    return math.sqrt(s / len(rows)) * 100.0


def err_sr(rows, w1, rho, kr, kd):
    """Error on the displayed SR, which is what a user actually compares."""
    tot = sq = 0.0
    within01 = within02 = 0
    for d in rows:
        a = 1.0 - math.exp(-d["c"] / window(d["band"], d["cls"], w1, rho, kr, kd))
        x = (1.0 / cpi_from_sr(d["sr0"], d["cls"])) * (1 - a) + a * (d["i"] / d["c"])
        cpi = 1.0 / x if x > 1e-9 else 1e9
        pred, _ = remap_band(cpi, d["cls"], d["band"])
        e = abs(pred - d["sr1"])
        tot += e
        sq += e * e
        within01 += e <= 0.01
        within02 += e <= 0.02
    n = len(rows)
    return dict(MAE=tot / n, RMSE=math.sqrt(sq / n),
                within_0_01=100 * within01 / n, within_0_02=100 * within02 / n)


def main():
    rows = load()
    print(f"calibration set: {len(rows)} anonymous driver-sessions\n")

    print("=" * 74)
    print("Coarse grid, ratio free")
    print("=" * 74)
    best = None
    for w1 in range(950, 1301, 25):
        for rho in [x / 100 for x in range(130, 151, 2)]:
            for kr in [x / 100 for x in range(55, 91, 5)]:
                for kd in [x / 100 for x in range(65, 101, 5)]:
                    e = err_cpi(rows, w1, rho, kr, kd)
                    if best is None or e < best[0]:
                        best = (e, w1, rho, kr, kd)
    print(f"  best: W1={best[1]} rho={best[2]:.2f} kR={best[3]:.2f} kD={best[4]:.2f}"
          f"  -> {best[0]:.3f}% CPI")

    print("\n" + "=" * 74)
    print("Ratio pinned to sqrt(2), refine the rest")
    print("=" * 74)
    best2 = None
    for w1 in range(950, 1301, 10):
        for kr in [x / 100 for x in range(55, 91, 2)]:
            for kd in [x / 100 for x in range(65, 101, 2)]:
                e = err_cpi(rows, w1, SQRT2, kr, kd)
                if best2 is None or e < best2[0]:
                    best2 = (e, w1, kr, kd)
    print(f"  best: W1={best2[1]} rho=sqrt2 kR={best2[2]:.2f} kD={best2[3]:.2f}"
          f"  -> {best2[0]:.3f}% CPI")

    print("\n" + "=" * 74)
    print("Candidate comparison")
    print("=" * 74)
    cands = [
        ("shipped v4 (no class factor)", 1050, SQRT2, 1.0, 1.0),
        ("ratio free", best[1], best[2], best[3], best[4]),
        ("sqrt2 pinned", best2[1], SQRT2, best2[2], best2[3]),
    ]
    print(f"  {'model':<32}{'CPI%':>7}{'MAE':>8}{'RMSE':>8}{'<=0.01':>8}{'<=0.02':>8}")
    for name, w1, rho, kr, kd in cands:
        e = err_cpi(rows, w1, rho, kr, kd)
        s = err_sr(rows, w1, rho, kr, kd)
        print(f"  {name:<32}{e:>7.2f}{s['MAE']:>8.4f}{s['RMSE']:>8.4f}"
              f"{s['within_0_01']:>7.1f}%{s['within_0_02']:>7.1f}%")

    w1, rho, kr, kd = best2[1], SQRT2, best2[2], best2[3]
    print("\n" + "=" * 74)
    print(f"Windows for W1={w1}, ratio=sqrt(2), kR={kr:.2f}, kD={kd:.2f}")
    print("=" * 74)
    print(f"  {'band':<6}{'R':>8}{'D':>8}{'C/B/A/Pro':>12}")
    for b in (1, 2, 3, 4):
        print(f"  {b:<6}{window(b,0,w1,rho,kr,kd):>8.0f}"
              f"{window(b,1,w1,rho,kr,kd):>8.0f}{window(b,2,w1,rho,kr,kd):>12.0f}")

    print("\n" + "=" * 74)
    print("Accuracy by licence class (best model)")
    print("=" * 74)
    print(f"  {'class':<8}{'n':>7}{'MAE':>9}{'<=0.02':>9}")
    for k, name in enumerate("RDCBAP"):
        sel = [r for r in rows if r["cls"] == k]
        if len(sel) < 50:
            continue
        s = err_sr(sel, w1, rho, kr, kd)
        print(f"  {name:<8}{len(sel):>7}{s['MAE']:>9.4f}{s['within_0_02']:>8.1f}%")


if __name__ == "__main__":
    main()
