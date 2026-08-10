# Adding the Safety Rating estimator to your overlay

A guide for people who already ship an iRacing overlay — RaceLab, Kapps, iRon,
SimHub gauges, a bespoke Electron HUD — and want a live **estimated Safety
Rating** in it.

You do not need to read the research to use this. You need five fields from the
SDK, about eighty lines of arithmetic, and one rule about counting corners that
is easy to get wrong. Everything here is MIT licensed and copy-paste-able.

| | |
|---|---|
| **Reference ports** | [`integration/nishizumi-sr.js`](integration/nishizumi-sr.js) · [`integration/NishizumiSR.cs`](integration/NishizumiSR.cs) |
| **Golden vectors** | [`integration/vectors.json`](integration/vectors.json) — 441 cases |
| **Verifiers** | [`integration/verify.cjs`](integration/verify.cjs) · [`integration/VerifyVectors.cs`](integration/VerifyVectors.cs) |
| **The mathematics** | [`MODEL.md`](MODEL.md) |
| **Where the data came from** | [`RESEARCH.md`](RESEARCH.md) |

---

## 1. What you are implementing

iRacing has never published its Safety Rating formula. This is a reconstruction
measured against iRacing's own result data, which exposes `old_cpi`/`new_cpi`
alongside the SR they produced.

Two of the three steps are essentially exact. Held out against **123,361
driver-sessions** from 7,598 official races, across all five licence classes:

| metric | held-out result |
|---|---|
| CPI prediction error | 2.90% |
| SR mean absolute error | **0.033** |
| within 0.02 SR | 64% |
| within 0.05 SR | 88% |

That is the accuracy you inherit. It is good enough to tell a driver "this race
is costing you about 0.15 SR" and be right; it is not good enough to be called
the official number, and you should not present it as one. See
[§10](#10-what-to-tell-your-users).

---

## 2. Pick your path

**Vendor a reference port.** Drop in one file, no dependencies:

- JavaScript / TypeScript / Electron → [`integration/nishizumi-sr.js`](integration/nishizumi-sr.js)
  (CommonJS, browser global and bundler-friendly)
- C# / .NET / Unity → [`integration/NishizumiSR.cs`](integration/NishizumiSR.cs)
  (targets C# 5 upwards, so .NET Framework works)

**Port it yourself.** Read [§4](#4-the-algorithm)–[§6](#6-session-weights), then
make your port reproduce [`integration/vectors.json`](integration/vectors.json).
Both reference ports are verified against exactly those vectors, so if you match
them you match this project.

Either way, read [§5](#5-counting-corners-the-part-that-is-easy-to-get-wrong).
It is the one piece that is not arithmetic, and it is where ports go wrong.

---

## 3. What you need from the SDK

Five things. Everything else is derived.

| You need | Read | Notes |
|---|---|---|
| Safety Rating now | `DriverInfo:Drivers[PlayerCarIdx]:LicSubLevel` | SR × 100, so `299` → `2.99` |
| Licence class **and** band | `DriverInfo:Drivers[PlayerCarIdx]:LicLevel` | 1..24. **Use this one** — see below |
| Incident points | `PlayerCarMyIncidentCount` | Not `IncidentCount`; see below |
| Distance driven | `LapDistPct` + `PlayerTrackSurface` | Per tick, 0..1 — see [§5](#5-counting-corners-the-part-that-is-easy-to-get-wrong) |
| Corners on this layout | `WeekendInfo:TrackNumTurns` | See [§7](#7-corners-per-lap) |

Plus two for bookkeeping: `SessionNum` (start a new session when it changes) and
`SessionInfo:Sessions[N]:SessionType` (which weight applies, [§6](#6-session-weights)).

### `LicLevel` is the field to use

`LicLevel` is an integer 1..24 that carries **both** the licence class and the
whole-number SR band, which is exactly the pair the model needs:

```
class_index = (LicLevel - 1) / 4        // integer division. 0=R 1=D 2=C 3=B 4=A 5=Pro
band        = ((LicLevel - 1) % 4) + 1  // 1..4
```

Confirmed against the `/data` API: level 13 is Class B band 1, level 20 is Class
A band 4. Parsing the letter out of `LicString` ("B 3.54") gives you the class
but not the band, so you then have to guess the band from the SR — which is
wrong for anyone sitting exactly on a boundary. Use `LicLevel`; keep `LicString`
as a fallback.

### `PlayerCarMyIncidentCount`, not `IncidentCount`

`PlayerCarMyIncidentCount` isolates the local driver's own incidents, which
matters in team races with driver swaps. Fall back to `IncidentCount` only if
the former is missing on an older build.

Take a baseline when the session starts and use the difference:

```
incidents_this_session = max(0, current_count - count_at_session_start)
```

### If SR cannot be read

`LicSubLevel` is occasionally not populated the instant a session changes. Show
"—" and wait. Do not invent a starting SR: every number downstream depends on it,
and a fabricated baseline produces a confidently wrong delta.

---

## 4. The algorithm

Three steps. The quantity iRacing actually tracks is **CPI** — corners per
incident — and SR is a display transform of it.

### Step 1 — invert the SR you read into a CPI

```
floor_cpi(class, band) = 3.4949 * 1.4562^(class_index + band)
cpi_old                = floor_cpi(class, band) * exp((SR - band) / 1.5236)
```

### Step 2 — update the CPI with the session driven so far

```
window = 1060 * 1.4142^(band - 1) * class_factor
alpha  = 1 - exp(-corners / window)

x_new  = (1 - alpha) / cpi_old  +  alpha * (incident_points / corners)
cpi_new = 1 / x_new
```

`class_factor` is **0.69** for Rookie, **0.83** for Class D, and **1.0** for C, B,
A and Pro. Within C/B/A/Pro the window depends only on the SR band, never on the
class — that was measured, not assumed.

| window (corners) | band 1.xx | 2.xx | 3.xx | 4.xx |
|---|---|---|---|---|
| Rookie | 731 | 1034 | 1463 | 2069 |
| Class D | 880 | 1244 | 1760 | 2488 |
| C / B / A / Pro | 1060 | 1499 | 2120 | 2998 |

This is an **exponentially weighted moving average**, not a fixed window with a
cap. If you find yourself writing `min(cap, corners/window)`, you have the old
and wrong form: it only matches for small `corners` and needs an arbitrary cap
near 1/3 to stop long races blowing up.

A higher SR means a longer memory, which is why SR gets progressively harder to
move as it climbs — and why the same 4x costs a 4.20 driver less than a 2.20 one.

### Step 3 — map the new CPI back to a displayed SR

```
sr = band + 1.5236 * ln( cpi_new / floor_cpi(class, band) )
```

then apply the **overlapping-band rule**: while `sr` falls outside
`[band, band+1)`, move `band` one step toward it and recompute. Clamp the result
to `0.00 … 4.99`.

> **Do not add a "+0.40 bonus" for crossing a whole number.** iRacing documents
> that jump, but it is not a separate rule — it falls out of this remap, because
> the bands sit on a geometric ladder of ratio 1.4562. The exact value the
> geometry produces is **0.427**; the support article rounds it. If you add it on
> top you will double-count it. The same geometry is why a class promotion costs
> almost exactly 1.00 SR.

### Worked example

A Class C driver on **2.99**, three clean laps at Road America (14 corners/lap):

```
band            = 2
floor_cpi(C,2)  = 15.7152
cpi_old         = 30.0963          <- 2.99 inverted
window(band 2)  = 1499.05
corners         = 42
alpha           = 0.027629         <- 1 - exp(-42/1499.05)
x_session       = 0.0              <- 0 incidents
cpi_new         = 30.9515
sr in band 2    = 3.0327           <- outside [2,3), so remap
floor_cpi(C,3)  = 22.8844
sr in band 3    = 3.4601           <- final

result: 2.99 -> 3.4601   (delta +0.4701)
```

Three clean laps moved the raw number by +0.04; the other +0.43 is the band
crossing. That is correct, and it is why testing your port against the vectors
matters — a plausible-looking implementation that skips the remap is out by ten
times the real effect at exactly the moment your users care most.

---

## 5. Counting corners: the part that is easy to get wrong

**iRacing scores the distance actually driven, not laps completed.** So:

```
corners = corners_per_lap * laps_completed        // WRONG
```

is wrong in both directions. A tow out of the pits advances the lap counter
without the driver having driven those corners. An abandoned lap still earns the
corners that *were* driven — real results exist with 0 completed laps, 2
incidents and a genuine −0.06 SR change.

Integrate `LapDistPct` instead, every tick:

```
on each telemetry tick:
    in_world = PlayerTrackSurface not in (NotInWorld = -1, InPitStall = 1)
    if not in_world:            prev = null; return          # position may jump
    if LapDistPct outside 0..1: prev = null; return
    if prev is null:            prev = LapDistPct; return

    delta = LapDistPct - prev
    if delta < 0:  delta += 1                                # crossed start/finish

    if delta > 0.10:                                         # tow, teleport, reset
        prev = LapDistPct
        return                                               # credit nothing

    prev = LapDistPct
    corners += corners_per_lap * delta
```

Three rules doing the work:

1. **Not in the world or sitting in the pit box** → no corners, and reset the
   reference point, because the next position can be anywhere.
2. **A wrap past the start/finish line** is a lap boundary, not a 99% jump
   backwards.
3. **A single-tick jump over 10% of a lap** is a tow, a teleport or your app
   having been starved of CPU. Resynchronise, credit nothing. At 10 Hz even a
   fast car on a short track covers far less than 10% in one tick, so the
   threshold is generous.

Polling at 10 Hz is plenty. The estimate is cheap; you can recompute it on every
frame if you want.

---

## 6. Session weights

Sporting Code §3.6.1.1, the "Table of Corner **and** Incident Multipliers". The
name matters: the weight multiplies **corners as well as incidents**.

| Session type | Weight |
|---|---|
| Race, Heat | 1.00 |
| Qualify (open), Warm-Up, Practice (open, in a Ranked event) | 0.50 |
| Qualify (lone), Practice (lone), Time Trial | 0.35 |
| Anything else | 0.00 |

Apply it to both sides before calling the projection:

```
weighted_corners   = corners   * weight
weighted_incidents = incidents * weight
```

Weighting only the incidents is a common mistake and it inflates CPI in
qualifying and time trial. The same lap, 21 corners and 4x:

| | result |
|---|---|
| in a race (1.00) | 2.75 → 2.6696 (**−0.0804**) |
| in open qualifying (0.50) | 2.75 → 2.7091 (**−0.0409**) |

`SessionType` has no closed official list of values, so match on substrings:
`"time trial"` → time_trial, `"practice"`/`"testing"` → practice, `"qualify"` →
qualify, `"warmup"` → warmup, `"race"` → race, otherwise unknown (weight 0).

---

## 7. Corners per lap

**`WeekendInfo:TrackNumTurns` from the live session is the number to use.** It
*is* corners-per-lap, so a layout iRacing adds tomorrow is scored correctly the
first time it is driven, with no table to maintain on your side.

If you want a table anyway — to show a projection before the driver is on
track — this repo ships the official values for 436 layouts, keyed by the same
`TrackID` the SDK reports:

- [`../tracks/official_corners.json`](../tracks/official_corners.json)
- [`tracks.json`](tracks.json) — the same data, shaped for the web calculator

Examples: Daytona Oval 4, Spa 21, Nordschleife Industriefahrten 73, Nürburgring
Combined 165.

---

## 8. Using the reference ports

### JavaScript

```js
const SR = require("./nishizumi-sr.js");   // or: import SR from "./nishizumi-sr.js"

const tracker = new SR.SessionTracker();

// when SessionNum changes
tracker.startSession({
  safetyRating: driver.LicSubLevel / 100,   // 2.99
  licLevel:     driver.LicLevel,            // 10
  incidentCount: telemetry.PlayerCarMyIncidentCount,
  cornersPerLap: weekend.TrackNumTurns,
  sessionType:   session.SessionType,       // "Race"
  lapsTotal:     session.SessionLaps
});

// every telemetry tick
tracker.update({
  lapDistPct:   telemetry.LapDistPct,
  trackSurface: telemetry.PlayerTrackSurface,
  incidentCount: telemetry.PlayerCarMyIncidentCount,
  lap:          telemetry.Lap
});

// whenever you paint
const e = tracker.estimate();
// { initialSr: 2.99, projectedSr: 3.46, delta: 0.47, corners: 42,
//   incidents: 0, cpi: null, targetCpi: 22.88, lapsCompleted: 3, lapsTotal: 10 }
render(`SR ${e.delta >= 0 ? "+" : ""}${e.delta.toFixed(2)}`);
```

The individual functions are exported too, if you would rather keep your own
session state: `projectSr`, `cpiFromSr`, `remapBand`, `sessionAlpha`,
`windowCorners`, `sessionWeight`, `targetCpiToHold`, `CornerAccumulator`.

### C#

```csharp
using NishizumiSR;

var tracker = new SessionTracker();

// when SessionNum changes
tracker.StartSession(
    safetyRating:  driver.LicSubLevel / 100.0,
    incidentCount: telemetry.PlayerCarMyIncidentCount,
    cornersPerLap: weekend.TrackNumTurns,
    sessionType:   session.SessionType,
    licLevel:      driver.LicLevel,
    lapsTotal:     session.SessionLaps);

// every telemetry tick
tracker.Update(telemetry.LapDistPct, telemetry.PlayerTrackSurface,
               telemetry.PlayerCarMyIncidentCount, telemetry.Lap);

// whenever you paint
SrEstimate e = tracker.Estimate();
Render($"SR {e.Delta:+0.00;-0.00}");
```

Or call `SafetyRating.ProjectSr(...)` directly and keep your own state.

---

## 9. Verifying your port

[`integration/vectors.json`](integration/vectors.json) holds 441 cases generated
by running the shipped Python implementation — the same functions the desktop
overlay itself calls. It covers every step, plus the corner accumulator,
including the tow and pit-box cases.

```bash
node docs/integration/verify.cjs                                   # JavaScript

mcs -out:verify.exe NishizumiSR.cs VerifyVectors.cs && mono verify.exe   # C#
# or drop both .cs files into a console project and `dotnet run`
```

Both print `OK: all N checks passed`. Point the same vectors at your own port.

**Compare with a tolerance, never for equality.** `log()` and `exp()` are allowed
to differ by an ulp between runtimes; `1e-9` on SR values and a `1e-9` relative
tolerance on CPI is plenty, and both reference ports pass at that.

Regenerate the vectors after any model change with `python tools/gen_vectors.py`,
and your port fails loudly instead of drifting quietly.

---

## 10. What to tell your users

Please do not present this as iRacing's own number.

- Label it **"Estimated SR"** or "SR (est.)". The desktop app says
  `ESTIMATED SAFETY RATING` in full in its detailed mode.
- The official result is always the source of truth.
- Neither this project nor your overlay is affiliated with iRacing.

Expect roughly ±0.03 SR on a normal session. The weakest cases are team
endurance events, where per-driver laps and the car's CPI do not line up
perfectly, and very long races.

Two things the SDK cannot give you, so do not promise them: **MPR** (minimum
participation requirement) is not exposed by the telemetry SDK at all, and the
SDK cannot tell you who is physically at the wheel on another machine in a team
event.

---

## 11. Checklist before you ship

- [ ] Class **and band** come from `LicLevel`, not from parsing `LicString`
- [ ] Corners come from integrating `LapDistPct`, not `laps × corners_per_lap`
- [ ] The 10%-per-tick jump guard is in, and so is the pit-box / not-in-world reset
- [ ] The session weight multiplies **corners and incidents**, both
- [ ] The band remap is implemented — and you did **not** also add a +0.40 bonus
- [ ] Incidents are counted as a delta from a baseline taken at session start
- [ ] A new `SessionNum` resets everything, including the corner accumulator
- [ ] SR that cannot be read shows "—" rather than a made-up baseline
- [ ] Your port passes `vectors.json`
- [ ] The UI says **estimated**

## 12. Licence and credit

MIT, same as the project. Attribution is not legally required by the licence, but
a line like

> Estimated SR model by [Nishizumi SR](https://github.com/nishizumi-maho/Nishizumi-SR)

is appreciated, and it gives your users somewhere to go when they want to know
how the number is produced.

If you find a case where the estimate is consistently off, please open an issue
with the session details — the model is measured against real results, so
counter-examples are how it improves.

## 13. Staying up to date

Watch [`MODEL.md`](MODEL.md) and `vectors.json`. Model constants change rarely
and only with evidence; when they do, the vectors change in the same commit, so
diffing that one file tells you whether you need to update.

A footnote if you compare against the web calculator: it uses `Math.SQRT2` for
the band ratio where the shipped model uses the fitted `1.4142`. The difference
is one part in 10⁵ of the window and below 10⁻⁶ SR — far under the model's own
error — but it is why the two can disagree in the last decimal.
