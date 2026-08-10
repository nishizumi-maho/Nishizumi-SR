"""Advanced analysis of the Nishizumi SR calibration set.

    python tools/analyse.py [path/to/calibration_set.json]

Everything here is fitted on a training split and scored on a held-out test
split, so a model that merely memorises the data cannot win. Previous rounds of
this project fitted and scored on the same rows, which flatters complex models.

Sections:
  1  data shape
  2  the rendering map  P -> S            (re-derive a, r, F0)
  3  the window law     W(band, class)    (model selection, held out)
  4  session weights                      (fitted, vs the Sporting Code table)
  5  effects not yet in the model         (category, SoF, team events)
  6  residual structure                   (what is left, and what predicts it)
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DEFAULT = ROOT / "data" / "calibration_set_full.json"

SQRT2 = math.sqrt(2.0)


# ---------------------------------------------------------------- loading
def load(path: Path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw["rows"]
    d = {}
    for k in ("c", "i", "oc", "nc", "s0", "s1", "b", "k"):
        d[k] = np.array([r[k] for r in rows], dtype=float)
    for k in ("cat", "sof", "team"):
        d[k] = np.array([r.get(k, -1) for r in rows], dtype=float)
    d["s0"] /= 100.0
    d["s1"] /= 100.0
    d["b"] = d["b"].astype(int)
    d["k"] = d["k"].astype(int)
    return d, raw.get("_meta", {})


def split(d, frac=0.5, seed=7):
    n = len(d["c"])
    rng = np.random.default_rng(seed)
    idx = rng.permutation(n)
    cut = int(n * frac)
    tr, te = idx[:cut], idx[cut:]
    sub = lambda s: {k: v[s] for k, v in d.items()}
    return sub(tr), sub(te)


# ------------------------------------------------------- rendering map
def fit_map(d):
    """SR - band = a*ln(P) - a*ln(F0) - a*ln(r)*rung   ->  linear least squares."""
    ok = (d["s0"] > 0.05) & (d["s0"] < 4.95) & (d["oc"] > 0)
    rung = d["k"][ok] + d["b"][ok]
    X = np.column_stack([np.log(d["oc"][ok]), rung, np.ones(ok.sum())])
    y = d["s0"][ok] - d["b"][ok]
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    a = coef[0]
    r = math.exp(-coef[1] / a)
    f0 = math.exp(-coef[2] / a)
    return a, r, f0


def map_err(d, a, r, f0):
    ok = (d["s0"] > 0.05) & (d["s0"] < 4.95) & (d["oc"] > 0)
    rung = d["k"][ok] + d["b"][ok]
    pred = d["b"][ok] + a * np.log(d["oc"][ok] / (f0 * r ** rung))
    return np.abs(pred - d["s0"][ok])


# ------------------------------------------------------------- window
def alpha_exact(d):
    """alpha solved from the data, no fitting at all."""
    xo, xn = 1.0 / d["oc"], 1.0 / d["nc"]
    xs = d["i"] / np.maximum(d["c"], 1e-9)
    den = xs - xo
    with np.errstate(divide="ignore", invalid="ignore"):
        a = (xn - xo) / den
    ok = np.isfinite(a) & (np.abs(den) > 1e-7) & (d["c"] > 40) & (a > 0.001) & (a < 1.3)
    return a, ok


def window_of(b, k, w1, ratio, kr, kd):
    f = np.where(k == 0, kr, np.where(k == 1, kd, 1.0))
    return w1 * ratio ** (b - 1) * f


def cpi_err(d, w1, ratio, kr, kd):
    W = window_of(d["b"], d["k"], w1, ratio, kr, kd)
    al = 1.0 - np.exp(-d["c"] / W)
    x = (1.0 / d["oc"]) * (1 - al) + al * (d["i"] / d["c"])
    pred = np.where(x > 1e-9, 1.0 / np.maximum(x, 1e-9), 1e9)
    return np.sqrt(np.mean(((pred - d["nc"]) / d["nc"]) ** 2)) * 100


def fit_window(d, ratio=None):
    best = None
    ratios = [ratio] if ratio else [x / 100 for x in range(130, 156, 1)]
    for rr in ratios:
        for w1 in range(850, 1451, 25):
            for kr in [x / 100 for x in range(55, 101, 3)]:
                for kd in [x / 100 for x in range(65, 106, 3)]:
                    e = cpi_err(d, w1, rr, kr, kd)
                    if best is None or e < best[0]:
                        best = (e, w1, rr, kr, kd)
    return best


# ------------------------------------------------------------------ main
def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    d, meta = load(path)
    n = len(d["c"])
    tr, te = split(d)

    print("=" * 76)
    print(f"1  DATA   {n:,} driver-sessions   ({path.name})")
    print("=" * 76)
    for k, name in enumerate("RDCBAP"):
        m = (d["k"] == k).sum()
        if m:
            print(f"   class {name}: {m:>7,}")
    print(f"   corners  median {np.median(d['c']):.0f}   p95 {np.percentile(d['c'],95):.0f}")
    print(f"   incidents median {np.median(d['i']):.0f}   clean sessions "
          f"{(d['i']==0).sum():,} ({100*(d['i']==0).mean():.0f}%)")
    print(f"   train {len(tr['c']):,} / test {len(te['c']):,}")

    print("\n" + "=" * 76)
    print("2  RENDERING MAP  P -> S")
    print("=" * 76)
    a, r, f0 = fit_map(tr)
    print(f"   refit on train : a={a:.4f}  r={r:.4f}  F0={f0:.4f}")
    print(f"   shipped        : a=1.5236  r=1.4562  F0=3.4949")
    e_new = map_err(te, a, r, f0)
    e_old = map_err(te, 1.5236, 1.4562, 3.4949)
    print(f"   held-out MAE   : refit {e_new.mean():.4f}   shipped {e_old.mean():.4f}")
    print(f"   held-out p99   : refit {np.percentile(e_new,99):.4f}   "
          f"shipped {np.percentile(e_old,99):.4f}")

    print("\n" + "=" * 76)
    print("3  WINDOW LAW  (fitted on train, scored on test)")
    print("=" * 76)
    al, ok = alpha_exact(d)
    print(f"   alpha solvable for {ok.sum():,} sessions")
    trw = {k: v[ok[: len(v)]] if False else v for k, v in tr.items()}
    cands = {}
    best_free = fit_window(tr)
    cands["EMA, ratio free"] = best_free[1:]
    best_s2 = fit_window(tr, ratio=SQRT2)
    cands["EMA, ratio sqrt2"] = best_s2[1:]
    cands["shipped"] = (1060, SQRT2, 0.69, 0.83)
    cands["no class factor"] = (best_s2[1], SQRT2, 1.0, 1.0)
    print(f"   {'model':<24}{'W1':>6}{'ratio':>7}{'kR':>6}{'kD':>6}"
          f"{'train%':>9}{'TEST%':>9}")
    for name, p in cands.items():
        print(f"   {name:<24}{p[0]:>6.0f}{p[1]:>7.3f}{p[2]:>6.2f}{p[3]:>6.2f}"
              f"{cpi_err(tr,*p):>9.3f}{cpi_err(te,*p):>9.3f}")

    print("\n   observed alpha vs corners/window (shipped window):")
    W = window_of(d["b"], d["k"], 1060, SQRT2, 0.69, 0.83)
    ratio_x = d["c"] / W
    print(f"   {'c/W':>8}{'n':>8}{'alpha obs':>11}{'1-exp(-x)':>11}")
    for lo, hi in [(0,.15),(.15,.25),(.25,.35),(.35,.5),(.5,.75),(.75,1.2),(1.2,2),(2,10)]:
        m = ok & (ratio_x >= lo) & (ratio_x < hi)
        if m.sum() > 60:
            x = np.median(ratio_x[m])
            print(f"   {x:>8.2f}{m.sum():>8,}{np.median(al[m]):>11.3f}"
                  f"{1-math.exp(-x):>11.3f}")

    print("\n" + "=" * 76)
    print("4  EFFECTS NOT IN THE MODEL")
    print("=" * 76)
    w1, ratio, kr, kd = cands["shipped"]
    W = window_of(d["b"], d["k"], w1, ratio, kr, kd)
    alm = 1.0 - np.exp(-d["c"] / W)
    x = (1.0 / d["oc"]) * (1 - alm) + alm * (d["i"] / d["c"])
    pred = 1.0 / np.maximum(x, 1e-9)
    rel = (pred - d["nc"]) / d["nc"]

    def by(mask_fn, label, vals):
        print(f"\n   by {label}:")
        print(f"     {'group':<16}{'n':>9}{'bias%':>9}{'RMSE%':>9}")
        for v in vals:
            m = mask_fn(v)
            if m.sum() > 200:
                print(f"     {str(v):<16}{m.sum():>9,}{100*rel[m].mean():>9.2f}"
                      f"{100*np.sqrt((rel[m]**2).mean()):>9.2f}")

    cats = sorted(set(d["cat"][d["cat"] >= 0].astype(int).tolist()))
    if cats:
        by(lambda v: d["cat"] == v, "licence category id", cats)
    if (d["team"] >= 0).any():
        by(lambda v: d["team"] == v, "team event", [0, 1])
    if (d["sof"] > 0).any():
        qs = np.percentile(d["sof"][d["sof"] > 0], [25, 50, 75])
        by(lambda v: (d["sof"] >= v[0]) & (d["sof"] < v[1]), "strength of field",
           [(0, qs[0]), (qs[0], qs[1]), (qs[1], qs[2]), (qs[2], 1e9)])

    print("\n" + "=" * 76)
    print("5  RESIDUAL STRUCTURE")
    print("=" * 76)
    print(f"   {'bucket':<22}{'n':>9}{'bias%':>9}{'RMSE%':>9}")
    for lo, hi, lab in [(0,100,'corners <100'),(100,300,'100-300'),(300,600,'300-600'),
                        (600,1200,'600-1200'),(1200,1e9,'>1200')]:
        m = (d["c"] >= lo) & (d["c"] < hi)
        if m.sum() > 200:
            print(f"   {lab:<22}{m.sum():>9,}{100*rel[m].mean():>9.2f}"
                  f"{100*np.sqrt((rel[m]**2).mean()):>9.2f}")
    for lo, hi, lab in [(0,1,'clean (0 inc)'),(1,4,'1-3 inc'),(4,9,'4-8 inc'),
                        (9,20,'9-19 inc'),(20,1e9,'20+ inc')]:
        m = (d["i"] >= lo) & (d["i"] < hi)
        if m.sum() > 200:
            print(f"   {lab:<22}{m.sum():>9,}{100*rel[m].mean():>9.2f}"
                  f"{100*np.sqrt((rel[m]**2).mean()):>9.2f}")


if __name__ == "__main__":
    main()
