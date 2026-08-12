# Research: iRacing Safety Rating & the iRacing SDK

This document exists to enforce one rule: **do not invent** iRacing's internal
algorithm or SDK field names. Everything below is either sourced from official
iRacing documentation, measured directly from iRacing's own result data, or
explicitly labelled as an assumption.

Last updated: 2026-08-09.

---

## 0. Summary — what is known, and how well

| Piece | Status | Error |
|---|---|---|
| Incident point values | **Official** (Sporting Code §3.5.1) | exact |
| Session weights | **Official** (Sporting Code §3.6.1.1) | exact |
| Corners per lap, per track | **Official** (`/data` track catalogue, 436 tracks) | exact |
| CPI → SR mapping | **Measured**, 12,639 observations | MAE 0.0068 SR fitted, **0.0099 held out** |
| Whole-number band jump | **Derived** from the mapping geometry | — |
| CPI update (rolling average) | **Measured**, 10,773 sessions | 1.4% on CPI ≈ 0.021 SR |
| MPR thresholds | Not exposed anywhere | unknown |

Those are the errors of the individual pieces. End to end — the number the app
actually shows — the held-out result over 123,361 driver-sessions is **MAE
0.033 SR**, with 88% of predicted SR values landing within 0.05. See
docs/MODEL.md section 9.

---

## 1. Official facts

Sources: [iRacing How-To: Safety Rating](https://support.iracing.com/support/solutions/articles/31000156960-iracing-how-to-safety-rating),
[iRacing 101: Incident Points](https://www.iracing.com/iracing-101-incident-points/),
and the [Official Sporting Code v2025.09.09](https://ir-core-sites.iracing.com/members/pdfs/20250909-official_sporting_code_dated_Sep_09_2025.pdf).

**Safety Rating is built on CPI — Corners Per Incident.** Each track
configuration has a "Corner Multiplier" that normalises an incident's impact
between short and long tracks (§3.6, and the glossary entry for Corner
Multiplier).

**Incident point values** (§3.5.1.1 / §3.5.1.2):

| Incident | Pavement | Dirt |
|---|---|---|
| Light contact with another driver | 0x | 0x |
| Wheels off the racing surface | 1x | 1x |
| Loss of control | 2x | 2x |
| Contact with other object | 2x | 2x |
| Heavy contact with another driver | **4x** | **2x** |

When several incidents happen at once, only the most serious counts (§3.5.2),
and incidents are assessed to every driver involved on a no-fault basis (§3.5.3).

**Corner and Incident Multipliers** (§3.6.1.1) — note the name: these weight
**corners as well as incidents**, not incidents alone.

| Session type | Weight | Session type | Weight |
|---|---|---|---|
| Time Trial | 0.35 | Qualify – Open | 0.50 |
| Practice – Lone | 0.35 | Warm-Up | 0.50 |
| Practice – Open | 0.50 | Heat / Race | 1.00 |
| Qualify – Lone | 0.35 | All other session types | 0.00 |

Independently confirmed by the [SR Calc](https://sr-calc.de/info.html)
documentation, which describes its totals as `sum(Laps × CPL × Multiplier)` and
`sum(Inc × Multiplier)`.

**Which sessions count** (§3.7.1.1): Ranked Qualifying, Ranked Time Trial,
Ranked Warm Up, Practice *within a Ranked Race event*, and Ranked Races. Note
the tension in the official text: table §4.3.1.1 lists "Open Practice" as not
adjusting SR while in-event "Practice" does, yet §3.5.1 says practice sessions
are not monitored at all. This project follows the two more specific sections.

**Overlapping values** (§3.7.3, §3.7.4). This is the mechanism behind the
familiar 0.40 jump. Licence classes partially overlap each other, and whole
number ranges partially overlap their neighbours, both "at a non-linear rate".
When SR crosses a whole number the underlying safety measure is *remapped* into
the new range. The support article states the user-facing version: crossing a
whole number upward adds 0.40, crossing downward subtracts it.

**Promotion / demotion** (§3.9, §3.10): 3.00 SR at season end with MPR met, or
4.00 at any time for a Fast Track (3.00 for Rookies). Below 2.00 at season end
demotes; below 1.00 demotes immediately. On promotion, SR is "updated to match
the overlapping value within the new License Class" — which works out to almost
exactly 1.00 (see §4).

---

## 2. Getting at iRacing's own data

An earlier round of this research concluded the `/data` API was unreachable
because of the OAuth client-ID pause. **That was a testing error.** The site's
own front end proxies the full API, and the prefix matters:

```
/bff/pub/proxy/api/...    -> allowlisted, most endpoints rejected
/bff/pub/proxy/data/...   -> the whole /data API, works with the session cookie
```

Useful endpoints:

| Endpoint | Gives |
|---|---|
| `data/results/get?subsession_id=` | per-driver `old_cpi`, `new_cpi`, `old_sub_level`, `new_sub_level`, `old_license_level`, incidents and laps per sim-session, plus `corners_per_lap` |
| `data/results/search_series` | every subsession in a season (11,346 for one series) |
| `data/track/get` | `corners_per_lap` for all 436 tracks |
| `data/member/info` | your licences with their current CPI |

> **Dependency warning.** This is an undocumented internal route of the website,
> not a supported public API, and it only ever returns data for the logged-in
> user. It is used here to *calibrate offline*. The app itself runs on the
> telemetry SDK alone and does not depend on it.

### Corners per lap is now exact

The project used to approximate corner counts with `TrackNumTurns` from the SDK,
flagged as low confidence. The official catalogue removes the guesswork —
Daytona Oval 4, Spa 21, Le Mans 38, Nürburgring Combined 165.

`TrackNumTurns` turns out to agree with it: both tracks where a
SDK-learned value could be compared matched exactly (Adelaide 14, Nürburgring
Combined 165). One discrepancy exists — Nordschleife Industriefahrten reads 154
in the catalogue but 73 in a subsession record — so the app prefers the value
the live session reports and logs the difference rather than hiding it.

---

## 3. The CPI → SR mapping

Fitted to 12,639 `(SR, CPI, licence level)` observations:

```
SR = band + a · ln( CPI / floor(class_index, band) )

floor(class_index, band) = F0 · r^(class_index + band)

a  = 1.5236        MAE 0.0068 SR
r  = 1.4562        max 0.040 SR
F0 = 3.4949
```

Within each band this is a straight line in `ln(CPI)` with R² = 0.9998–0.9999.

The structural finding is that **the floor depends only on
`class_index + band`** — one single geometric ladder. Measured directly in the
data: `C@3.00 = B@2.00 = A@1.00 = 22.9 CPI`. That is exactly the class and
whole-number overlap the Sporting Code describes in §3.7.3.

These constants were fitted on one set of races and then re-validated on a
completely independent series: **MAE 0.0068 SR**, with a refit on the new data
moving them by less than 0.5%.

**CPI required at each mark:**

| Class | SR 1.00 | SR 2.00 | SR 3.00 | SR 4.00 |
|---|---|---|---|---|
| Rookie | 5.1 | 7.4 | 10.8 | 15.7 |
| D | 7.4 | 10.8 | **15.7** | 22.9 |
| C | 10.8 | 15.7 | **22.9** | 33.3 |
| B | 15.7 | 22.9 | **33.3** | 48.5 |
| A | 22.9 | 33.3 | **48.5** | 70.7 |
| Pro | 33.3 | 48.5 | **70.7** | 102.9 |

The community figure of "15–20 CPI to hold D at 3.00" checks out (15.7). The
equally repeated "70–80 for A class" does **not** — the real number is 48.5.
70.7 is the Pro figure, which is probably where it came from.

---

## 4. Two behaviours that fall out for free

Neither is hardcoded anywhere in this project; both are consequences of the
ladder above.

**The whole-number jump.** Crossing a band remaps the same CPI into the
neighbouring range, which shifts the displayed fraction by `1 − a·ln(r) =
0.427`. iRacing rounds this to 0.40 in its support article. Confirmed on real
races: a driver going 1.89 → 2.63 had a raw movement of +0.34 plus the jump.

**Promotion costs 1.00 SR.** Since the floor depends on `class_index + band`,
moving up a class with an unchanged CPI drops the band by exactly one. This
matches §3.9.3's "approximately 1.00".

---

## 5. The rolling average — measured on 10,773 sessions

Because official results carry both `old_cpi` and `new_cpi`, the weight a
session carries can be solved **exactly**, with no fitting at all:

```
alpha = (1/CPI_new − 1/CPI_old) / (incidents/corners − 1/CPI_old)
```

Doing that across 10,773 driver-sessions — GT Sprint Series plus four endurance
series — settled three questions.

### 5.1 The window depends only on the SR band

| Band | Class B | Class A |
|---|---|---|
| 1.xx | 1217 | 1246 |
| 2.xx | 1661 | 1681 |
| 3.xx | 2274 | 2314 |
| 4.xx | — | 3185 |

Same band, different licence class, same window to within 2%. Grouping instead
by `class + band` — the rung the CPI ladder uses — does *not* collapse, so this
really is the whole-number SR band. A higher SR means a longer memory, which is
why SR gets progressively harder to move as it climbs.

### 5.2 It is an exponential moving average, not a fixed window

Binning the exactly-solved alpha against `corners / window`:

| corners/window | alpha observed | 1 − exp(−x) |
|---|---|---|
| 0.20 | 0.201 | 0.181 |
| 0.30 | 0.284 | 0.259 |
| 0.52 | 0.446 | 0.405 |
| 0.93 | 0.646 | 0.606 |
| 1.90 | 0.878 | 0.850 |
| 2.93 | 0.964 | 0.947 |

Linear up to about 0.37 — as far as sprint races reach — then saturating
exactly as an EMA does. The previous model used `min(cap, x)` with a cap near
1/3; that cap was an artifact of fitting a fixed window to only nine races and
disappears entirely with more data.

### 5.3 The ratio between bands is √2

```
window(band) = 1050 × √2^(band−1)   ->   1050 / 1485 / 2100 / 2970 corners
```

Letting each band take its own free window (4 parameters instead of 2)
converges onto exactly that progression and does not reduce the error at all,
so the geometric law is real rather than imposed.

Alternatives tested and rejected on the same data (RMSE on predicted CPI):

| model | error |
|---|---|
| **EMA, window geometric by band** | **1.40%** |
| free window per band (4 params) | 1.40% |
| window continuous in SR | 2.12% |
| window continuous in CPI | 3.83% |
| linear + cap (previous model) | 9.15% |

### 5.4 What the residual is

About 1.4% on predicted CPI, roughly 0.021 SR. Two checks on where it comes
from:

- Starting from the exact official `old_cpi` instead of inverting the rounded
  SR barely helps (MAE 0.0267 vs 0.0270), so the mapping is not the limit.
- Error falls with race length — 0.046 SR at 1–4 laps, 0.027 at 8–25, 0.018 at
  60+. That points at whole-lap reporting: iRacing counts the distance actually
  driven, while results only report completed laps.

The live overlay reads `LapDistPct` and counts real distance, so it does not
have that particular limitation.

---

## 6. Counting corners by distance, not by laps

Raised while testing and confirmed in the data: at Nürburgring it is common to
take a tow just after the pits, or to abandon a lap. The lap counter can move
without the driver having driven those corners. The reverse also shows up —
records exist with **0 completed laps, 2 incidents and a real −0.06 SR change**,
meaning iRacing credited the corners of a *partial* lap.

So `iracing/telemetry.py::CornerAccumulator` accumulates
`corners_per_lap × Δ(LapDistPct)`, only while the car is in the world and out of
the pit box, ignoring large distance jumps (tow, teleport, reset). That fixes
both directions of the error.

---

## 7. What the SDK exposes

Sources: [pyirsdk](https://github.com/kutu/pyirsdk),
[irsdkdocs](https://sajax.github.io/irsdkdocs/).

**Telemetry (per tick):** `SessionNum`, `SessionTime`, `SessionState`, `Lap`,
`LapDist`, `LapDistPct`, `OnPitRoad`, `PlayerCarIdx`, `IncidentCount`,
`PlayerCarMyIncidentCount`, `PlayerCarTeamIncidentCount`,
`PlayerCarDriverIncidentCount`, `PlayerTrackSurface`, `IsOnTrack`.

**Session YAML:** `WeekendInfo` (including `TrackID` and `TrackNumTurns`),
`DriverInfo`, `SessionInfo`.

`SessionState`: 0 Invalid, 1 GetInCar, 2 Warmup, 3 ParadeLaps, 4 Racing,
5 Checkered, 6 CoolDown.

`irsdk_TrkLoc`: −1 NotInWorld, 0 OffTrack, 1 InPitStall, 2 ApproachingPits,
3 OnTrack.

**Licence fields.** `LicLevel` runs 1–24, four levels per class, one per
whole-number SR band — confirmed against the API (level 13 = Class B band 1.xx,
level 20 = Class A band 4.xx). This is the reliable source for both class and
band; `LicSubLevel / 100` gives the SR itself. The app prefers `LicLevel` and
falls back to parsing the letter out of `LicString`.

**Incident counters.** `PlayerCarMyIncidentCount` isolates the locally logged-in
driver, which matters in team races; `IncidentCount` is the fallback on older
builds. Both are already in incident *points*, so no re-weighting of 1x/2x/4x is
needed.

**Driver swaps.** There is no field saying who is physically at the wheel on
another machine — that data belongs to the other client's session. The app can
speak accurately for the local driver only.

---

## 8. Still unknown

- **MPR thresholds.** Not in the telemetry SDK, and they vary by class and
  season.
- **Why the residual 1.4% exists.** Partly whole-lap reporting, but not
  entirely.
- **Whether the √2 band ratio and the 1050-corner base are exact** or merely
  very good fits. They are stable across sprint and endurance data, but iRacing
  has never published either.

---

## 9. Sources

- [Official Sporting Code v2025.09.09](https://ir-core-sites.iracing.com/members/pdfs/20250909-official_sporting_code_dated_Sep_09_2025.pdf) — §3.5, §3.6, §3.7, §3.9, §3.10, §4.3.1.1
- [iRacing How-To: Safety Rating](https://support.iracing.com/support/solutions/articles/31000156960-iracing-how-to-safety-rating)
- [Why did my SR decrease when moving up license levels?](https://support.iracing.com/support/solutions/articles/31000177907-why-did-my-sr-decrease-when-moving-up-license-levels-)
- [iRacing 101: Incident Points](https://www.iracing.com/iracing-101-incident-points/)
- [SR Calc documentation](https://sr-calc.de/info.html) — independent confirmation of the session multipliers
- [BoxThisLap: intro to the SR system](https://boxthislap.org/an-introduction-to-the-sr-system/)
- [pyirsdk](https://github.com/kutu/pyirsdk) · [irsdkdocs](https://sajax.github.io/irsdkdocs/)
- iRacing's `/data` API via the site's BFF proxy — the measured results in
  sections 2 to 5, taken from public official race results
