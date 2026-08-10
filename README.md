# Nishizumi SR

**Live Safety Rating estimator for iRacing — desktop overlay + web calculator.**

- 🌐 **Web calculator:** <https://nishizumi-maho.github.io/Nishizumi-SR/>
- 🖥️ **Desktop overlay:** this repository (`python main.py`)

Both use **exactly the same formula** — verified case by case, the two
implementations agree to four decimal places.

> 📐 **[`docs/MODEL.md`](docs/MODEL.md) is the mathematical description of the
> model** — the equations, their derivation, and the measured constants.
>
> **Read [`docs/RESEARCH.md`](docs/RESEARCH.md) too**, especially **section 9**,
> which documents what was actually *measured* against iRacing's own data and
> what is still an approximation. iRacing has never published the formula; what
> this project has is a reconstruction from the official `old_cpi`/`new_cpi`
> fields that reproduces the CPI→SR mapping with a mean error of 0.007 SR. The
> part that remains approximate (the rolling window) is labelled as such — the
> app still calls its number an **Estimated** Safety Rating.

---

## 1. What it does

During an iRacing session the overlay shows a live estimate of how much Safety
Rating you are gaining or losing, based on the corners you have actually driven
and the incident points you have taken.

The web calculator answers the same question *before* you drive: pick your
licence, the track and the race length, then drag the incident slider to see
what 0x, 2x, 8x… would cost you.

## 2. Install

### Download (easiest)

Grab `NishizumiSR.exe` from the
[latest release](https://github.com/nishizumi-maho/Nishizumi-SR/releases/latest)
and run it. No Python needed.

Settings, learned tracks, session history and logs are written to
`%LOCALAPPDATA%\NishizumiSR` — never into the download itself.

### From source

Requires Windows, Python 3.10+, and iRacing.

```bash
pip install -r requirements.txt
```

| Package | Why |
|---|---|
| `pyirsdk` | reads iRacing's shared memory (telemetry + session YAML) |
| `PySide6` | the overlay window |
| `numpy` | least-squares fitting in `sr/calibration.py` |
| `keyboard` | global hotkeys that work while iRacing has focus |
| `PyYAML` | `pyirsdk` dependency for parsing session YAML |

## 3. Run

With iRacing open (any session, or even just the main menu):

```bash
python main.py
```

A semi-transparent panel appears at the top-left of the screen. While iRacing is
closed or no valid session exists, the overlay shows `Waiting for iRacing...` /
`Waiting for session...` rather than a made-up number.

There is no title bar by design, so close it from the **system tray icon** → Exit.

### Hotkeys

| Key | Action |
|---|---|
| `F8` | show / hide the overlay |
| `F9` | lock / unlock (unlocked = drag to reposition; locked = click-through) |
| `F10` | cycle the three modes: compact / normal / detailed |

Configurable in `config/settings.json` → `hotkeys`. If hotkey registration fails,
the app keeps working and the same controls remain on the tray icon.

> **Platform note:** if iRacing runs as Administrator, this app must too, or
> Windows will not deliver the global keyboard hook across privilege levels.

### Trying it in iRacing

1. Run `python main.py` with iRacing open.
2. Join a session (Practice, Qualify, Race or Time Trial).
3. Check `logs/sr_estimator_<timestamp>.log` — you should see `Connected to
   iRacing`, `Session detected: ...`, `PlayerCarIdx: ...`, `Initial SR: ...`.
4. Complete a lap — the log should show the lap, the corners accumulated by
   distance, incidents, CPI and the estimated SR, and the overlay should update.
5. When the session ends (checkered / cool down) the overlay shows
   "SESSION COMPLETE" briefly and the session is saved to `data/history.json`
   as pending. The next time a session starts, the fresh SR read at that moment
   reconciles the pending record and computes the estimate's real error.

## 4. The model (v4)

Three steps — `sr/license_map.py` + `sr/model.py`:

```
1. invert    current_cpi = cpi_from_sr(SR read from the SDK, class, band)   # exact
2. update    alpha  = 1 - exp(-corners / window(band))
             1/CPI' = (1-alpha)/current_cpi + alpha * (incidents/corners)
3. remap     SR' = band + a * ln(CPI' / floor(class, band))
             (moving to the neighbouring band when it leaves [band, band+1))

window(band, class) = 1060 x sqrt(2)^(band-1) x class_factor
```

Corners and incidents both arrive already multiplied by the session weight
(Sporting Code §3.6.1.1: Race 1.00, Warm-Up / Qualify-Open / Practice-Open 0.50,
Time Trial / Qualify-Lone / Practice-Lone 0.35). The official name is
"Corner **and** Incident Multipliers" — the weight applies to both sides, not
just to incidents as the old model assumed.

**The ±0.40 jump when crossing 2.00 or 3.00 is not hardcoded.** It emerges from
step 3, because whole-number bands overlap on a geometric ladder of ratio 1.4562.
The exact value the geometry produces is 0.427; iRacing rounds it to 0.40 in its
support article. The same geometry is why a class promotion costs almost exactly
1.00 SR.

### The rolling average

Your accumulated CPI is an **exponentially weighted moving average** over corners.
Two facts here were measured against 10,773 driver-sessions, not assumed:

- **Within Class C, B, A and Pro the window depends only on the SR band, not on
  the class.** At band 3 the medians are C 2451, B 2342, A 2448 corners. Rookie and
  Class D are genuinely shorter, so new drivers' SR moves faster. A higher SR means
  a longer memory, which is why SR gets harder to move the higher it climbs.

  | Band | Rookie | Class D | C / B / A / Pro |
  |---|---|---|---|
  | 1.xx | 731 | 880 | 1060 |
  | 2.xx | 1034 | 1244 | 1499 |
  | 3.xx | 1463 | 1760 | 2120 |
  | 4.xx | 2069 | 2488 | 2998 |
- **It is an EMA, not a fixed window with a cap.** Solving `alpha` exactly for every
  session and binning it reproduces `1 - exp(-x)` across the whole observed range
  (x from 0.06 to 2.9). Letting each band take its own free window converges onto
  exactly the √2 progression above, so the law is real rather than imposed.

Validated against **123,361 driver-sessions** from 7,598 official races, covering
every series in the season with official racing, in all five licence categories.
Fitted on a random half, scored on the other half:

| metric | held-out result |
|---|---|
| CPI prediction error | **2.90%** |
| SR mean absolute error | **0.033** |
| within 0.02 SR | **64%** |
| within 0.05 SR | **88%** |

Re-fitting every constant on the training half did **not** beat the shipped
values, and left completely free the band ratio converged to **1.4141** — √2 to
four decimals, from data never used to choose it.

The residual is dominated by one thing, and it is not the algorithm: results
pages report whole laps while iRacing scores the distance actually driven, worth
roughly half a lap to a lap. The overlay integrates `LapDistPct` and so avoids it
entirely; if you compare the *calculator* against a past result on a road course,
adding half a lap closes most of the gap. Full detail in
[`docs/MODEL.md`](docs/MODEL.md) §9.

### Corners per lap

The **official** iRacing value is used: `tracks/official_corners.json` holds
`corners_per_lap` for 436 tracks, taken from the `/data` track catalogue and
keyed by the same `TrackID` the SDK exposes. Examples: Daytona Oval 4, Spa 21,
Nordschleife Industriefahrten 73, Nürburgring Combined 165.

At runtime the value reported by the live session (`TrackNumTurns`) wins if it
disagrees, since that is the number that session actually used; the difference is
logged rather than silently swallowed.

**A season update is a non-event for the overlay.** `TrackNumTurns` *is*
`corners_per_lap`, so a track added tomorrow is scored correctly the first time
it is driven, with no table refresh. Refreshing the shipped table only matters
for the web calculator — see [`docs/UPDATING_TRACKS.md`](docs/UPDATING_TRACKS.md):
one browser export plus `python tools/update_tracks.py`. A monthly CI job opens
an issue once the table passes 120 days old.

Corners are counted by **distance driven** (`corners_per_lap × Δ LapDistPct`),
not by lap count. A tow just after the pits, or an abandoned lap, therefore does
not credit corners the driver never drove — and a partial lap still credits the
corners they did drive. See `iracing/telemetry.py::CornerAccumulator`.

### CPI needed to hold 3.00 SR (measured, not community estimates)

| Rookie | D | C | B | A | Pro |
|---|---|---|---|---|---|
| 10.8 | 15.7 | 22.9 | 33.3 | 48.5 | 70.7 |

The widely repeated "70–80 CPI for A class" is wrong — measured, it is about
48.5. The 70.7 figure belongs to Pro, which likely explains where it came from.

## 5. Project layout

```
main.py                   # orchestration: polling, event detection, overlay

iracing/
    sdk.py                # thin wrapper over pyirsdk
    session.py            # parses WeekendInfo / DriverInfo / SessionInfo
    telemetry.py          # per-tick reads, lap detection, corner accumulation

sr/
    license_map.py        # CPI <-> SR mapping MEASURED against the /data API (core)
    model.py              # rolling window + band remap + session weights
    calculator.py         # per-session corner / incident / CPI accumulator
    history.py            # persistence + reconciliation against real SR
    calibration.py        # error stats + window calibration from real history
    license_targets.py    # DEPRECATED shim -> license_map.py

tracks/
    database.py           # resolves corners per lap (TrackID -> corners)
    official_corners.json # 436 tracks, official iRacing values
    track_data.json       # local cache, editable

overlay/                  # PySide6 window, per-mode formatting, styles
config/                   # settings load/save
docs/
    MODEL.md              # the mathematics: equations, derivation, constants
    RESEARCH.md           # facts vs. hypotheses, and how the data was obtained
    index.html            # the web calculator
    tracks.json           # track data for the web page

tools/
    fit_model.py          # offline refit against the calibration set

data/
    calibration_set.json  # 13,849 anonymous driver-sessions (no identifiers)
```

## 6. Calibrating against your own results

Every scoreable session ends up as a pending record in `data/history.json`.
When a fresh SR is read at the start of the next session, that record is
reconciled: `actual_delta`, `final_sr` and `error` get filled in.

After accumulating reconciled sessions:

```bash
python -m sr.calibration
```

This reports mean error, mean absolute error and a confidence level, and
suggests a new `window_corners`. It never recalibrates itself silently during a
session — that is deliberate, so the estimate stays auditable.

The shipped default (`window_corners = 2150`, `alpha_max = 0.34`) comes from 198
independent driver-sessions. Fitting a single driver's own races tends to land
lower (around 1650), which is exactly what this command personalises.

## 7. Known limitations

- **The rolling window is not solved.** A fixed window fails on long races, so a
  single session's weight is capped at `alpha_max ≈ 1/3`. That cap replicated
  across two independent samples but has no official source. Expect roughly
  ±0.11 SR on normal sessions and ±0.19 on very long or team endurance races.
- **Team endurance events** are the weakest case: per-driver laps and the car's
  CPI do not line up perfectly.
- **MPR is not shown.** It is not exposed by the telemetry SDK.
- **VR is not supported.** The overlay is desktop-only; calculation (`sr/`,
  `iracing/`) is kept separate from presentation (`overlay/`) so a VR layer could
  be swapped in later.
- **Driver swaps across machines.** `PlayerCarMyIncidentCount` already isolates
  the local driver's incidents, but the SDK cannot tell you who is physically at
  the wheel on another machine, so the app only speaks for the local driver.
- **History reconciliation ignores category** (oval / road / dirt). Accurate if
  you race one category per day; it can reconcile against the wrong session if
  you alternate.

## 8. Performance

Telemetry is read every 100 ms, but the estimate is only recomputed on lap
completion; each tick does a cheap snapshot to keep the UI current. The session
YAML (the expensive parse) is only re-read when `SessionNum` changes. Measured
locally: ~73 MB RAM and negligible CPU.

## 9. Disclaimer

Not affiliated with iRacing. Every number this project produces is an
**estimate** — the official result is always the source of truth.
