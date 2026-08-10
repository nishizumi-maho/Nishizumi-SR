# The Model

A complete mathematical description of how Nishizumi SR predicts an iRacing
Safety Rating change.

iRacing has never published its formula. What follows is a *reconstruction*,
derived from public official race results and validated against **123,361
driver-sessions** drawn from 7,598 official races across every series in the
season, in all five licence categories, scored on a held-out split. Where a
quantity is exact it is marked as such; where it is fitted, the residual error
is stated.

---

## 1. Notation

| Symbol | Meaning |
|---|---|
| $S$ | displayed Safety Rating, $0.00 \le S \le 4.99$ |
| $b = \lfloor S \rfloor$ | the **band** — the whole-number part of $S$, $b \in \{0,1,2,3,4\}$ |
| $\kappa$ | licence **class index**: $0=$ Rookie, $1=$ D, $2=$ C, $3=$ B, $4=$ A, $5=$ Pro |
| $P$ | accumulated **CPI** (corners per incident) — the hidden state |
| $c$ | corners driven in a session, session-weighted |
| $x$ | incident points in a session, session-weighted |

The SDK exposes $b$ and $\kappa$ together in one integer, `LicLevel` $\in [1,24]$:

$$\kappa = \left\lfloor \frac{\text{LicLevel}-1}{4} \right\rfloor, \qquad
b = \big((\text{LicLevel}-1) \bmod 4\big) + 1$$

---

## 2. The state variable is CPI, not SR

Safety Rating is a *display* of an underlying quantity. The Sporting Code calls
it "the measure of a driver's overall safety record" (§3.7.4); the results API
exposes it directly as `old_cpi` / `new_cpi`.

Everything the algorithm does happens to $P$. $S$ is only a rendering of it.
So the model is a three-step pipeline:

$$S \;\xrightarrow{\;\text{invert}\;}\; P \;\xrightarrow{\;\text{update}\;}\; P' \;\xrightarrow{\;\text{render}\;}\; S'$$

Steps 1 and 3 are exact inverses of one another. Only step 2 is fitted.

---

## 3. The rendering map $P \mapsto S$

### 3.1 The law

Within a band, $S$ is affine in $\ln P$:

$$\boxed{\; S \;=\; b \;+\; a \ln\!\left(\frac{P}{\Phi(\kappa, b)}\right) \;}$$

where $\Phi(\kappa,b)$ is the CPI at the *floor* of that band — the CPI for which
the band displays exactly $b.00$.

$$a = 1.5236$$

Held-out mean absolute error **0.0098 SR** over 58,842 observations. For context
the site only displays two decimals, so ~0.005 is the observational floor.

These constants are not merely tuned: re-fitting them on a training half and
scoring on the held-out half produces a *worse* result (0.0101 against 0.0098).
A refit chases noise the shipped values do not.

### 3.2 The floor is a single geometric ladder

The structural result — and the one that makes everything else fall out — is
that $\Phi$ does not depend on $\kappa$ and $b$ separately, but only on their
**sum**:

$$\boxed{\; \Phi(\kappa, b) \;=\; \Phi_0 \, r^{\,\kappa + b} \;}$$

$$\Phi_0 = 3.4949, \qquad r = 1.4562$$

Measured directly in the data, and visible as an identity:

$$\Phi(\text{C},3) = \Phi(\text{B},2) = \Phi(\text{A},1) = 22.9 \text{ CPI}$$

This is exactly the "overlapping values" the Sporting Code describes in §3.7.3,
made quantitative. Writing $n = \kappa + b$ for the rung on the ladder:

| $n$ | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|
| $\Phi$ | 10.8 | 15.7 | 22.9 | 33.3 | 48.5 | 70.7 |

### 3.3 CPI required at each licence

Substituting $S = 3.00$:

| Class | SR 1.00 | SR 2.00 | SR 3.00 | SR 4.00 |
|---|---|---|---|---|
| Rookie | 5.1 | 7.4 | 10.8 | 15.7 |
| D | 7.4 | 10.8 | **15.7** | 22.9 |
| C | 10.8 | 15.7 | **22.9** | 33.3 |
| B | 15.7 | 22.9 | **33.3** | 48.5 |
| A | 22.9 | 33.3 | **48.5** | 70.7 |
| Pro | 33.3 | 48.5 | **70.7** | 102.9 |

The widely repeated community figure of "15–20 CPI to hold D at 3.00" is
correct. The equally repeated "70–80 for A class" is not: it is 48.5. The value
70.7 belongs to **Pro**, which is the likely origin of the error.

### 3.4 Inversion

Step 1 of the pipeline is just the algebraic inverse:

$$P \;=\; \Phi_0\, r^{\,\kappa+b} \, e^{(S-b)/a}$$

---

## 4. Two documented behaviours, derived rather than assumed

Neither of the following is hardcoded anywhere in this project. Both are
consequences of §3.2.

### 4.1 The $\pm 0.40$ whole-number jump

Bands overlap. When $P$ grows past the top of band $b$, the *same* $P$ is
re-rendered in band $b+1$. At the moment of crossing, $S = b+1$ in the old band,
so $P = \Phi_0 r^{\kappa+b} e^{1/a}$. Rendering that in the new band:

$$S' = (b+1) + a\ln\!\left(\frac{\Phi_0 r^{\kappa+b} e^{1/a}}{\Phi_0 r^{\kappa+b+1}}\right)
     = (b+1) + a\left(\tfrac{1}{a} - \ln r\right)$$

$$\boxed{\;\Delta_{\text{jump}} \;=\; 1 - a\ln r \;=\; 0.4274\;}$$

iRacing's support article rounds this to **0.40** — "crossing 1.99 nets an extra
0.40, giving 2.41". The model produces 2.43 from first principles.

### 4.2 Promotion costs exactly 1.00 SR

On promotion $\kappa \to \kappa+1$ while $P$ is unchanged. Since $\Phi$ depends
only on $\kappa+b$, holding $P$ fixed forces $b \to b-1$:

$$S' = S - 1$$

The Sporting Code says "approximately 1.00" (§3.9.3). The model says exactly
1.00, and explains *why* it is not a subtraction but a re-rendering.

---

## 5. The update $P \mapsto P'$

This is the only fitted part of the pipeline.

### 5.1 Incidents per corner is the natural coordinate

Work in the reciprocal $\lambda = 1/P$, the **incident rate per corner**. It is
additive in a way $P$ is not, and it handles a clean session ($x = 0$) without
dividing by zero.

A session contributes its own rate $\lambda_{\text{sess}} = x/c$, blended into
the accumulated rate:

$$\boxed{\;\lambda' \;=\; (1-\alpha)\,\lambda \;+\; \alpha\,\frac{x}{c}\;}\qquad P' = 1/\lambda'$$

### 5.2 $\alpha$ can be solved exactly, not fitted

Because official results carry both `old_cpi` and `new_cpi`, rearranging gives
$\alpha$ for every real session with no free parameters at all:

$$\alpha \;=\; \frac{\lambda' - \lambda}{\dfrac{x}{c} - \lambda}
          \;=\; \frac{1/P' - 1/P}{x/c - 1/P}$$

This is what turns the update from guesswork into measurement.

### 5.3 The weighting is exponential

Binning the solved $\alpha$ against $c/W$ over the 102,787 sessions where it is
solvable:

| $c/W$ | n | $\alpha$ measured | $1 - e^{-c/W}$ |
|---|---|---|---|
| 0.09 | 64,675 | 0.086 | 0.087 |
| 0.18 | 20,677 | 0.175 | 0.168 |
| 0.29 | 5,666 | 0.259 | 0.251 |
| 0.42 | 4,334 | 0.350 | 0.343 |
| 0.61 | 4,049 | 0.467 | 0.458 |
| 0.89 | 2,308 | 0.601 | 0.590 |
| 1.43 | 801 | 0.764 | 0.760 |
| 2.53 | 277 | 0.920 | 0.920 |

The law is not approximately right over part of the range — it tracks the
measurement across a factor of thirty in $c/W$.

Linear for small $c/W$, saturating smoothly — the signature of an exponentially
weighted moving average over corners, not a fixed window:

$$\boxed{\;\alpha \;=\; 1 - e^{-c/W}\;}$$

An earlier version of this project used $\alpha = \min(\tfrac13, c/W)$. That cap
was an artifact of fitting a fixed window to nine races, and it disappears
entirely once there is enough data to see the curve above.

### 5.4 The decay constant $W$

$$\boxed{\; W(\kappa, b) \;=\; W_1 \cdot \sqrt{2}^{\,b-1} \cdot f(\kappa) \;}$$

$$W_1 = 1060 \text{ corners}, \qquad
f(\kappa) = \begin{cases}
0.69 & \kappa = 0 \ (\text{Rookie})\\
0.83 & \kappa = 1 \ (\text{Class D})\\
1 & \kappa \ge 2
\end{cases}$$

| Band | Rookie | Class D | C / B / A / Pro |
|---|---|---|---|
| 1.xx | 731 | 880 | 1060 |
| 2.xx | 1034 | 1244 | 1499 |
| 3.xx | 1463 | 1760 | 2120 |
| 4.xx | 2069 | 2488 | 2998 |

Three measured facts sit behind this:

1. **Within C, B, A and Pro, $W$ depends only on the band, not the class.** At
   band 3 the medians are C 2451, B 2342, A 2448 corners. Grouping instead by
   the ladder rung $\kappa+b$ does *not* collapse, so it is genuinely the band.
2. **Rookie and D are shorter.** At band 2: R ≈ 1024, D ≈ 1367, versus ≈ 1750
   for C/B/A. New drivers' SR is deliberately more responsive.
3. **The ratio is $\sqrt{2}$.** Letting each band take its own free window (four
   parameters instead of two) converges onto exactly this geometric progression
   and does not reduce the error. Pinning the ratio to $\sqrt2$ instead of
   fitting it freely costs 0.005 percentage points.

The consequence is that SR becomes progressively harder to move as it climbs —
a driver at 4.xx has roughly three times the memory of one at 1.xx.

---

## 6. Session weighting

Corners *and* incidents are both scaled before entering the update. The official
name in Sporting Code §3.6.1.1 is "Corner **and** Incident Multipliers":

$$c = w \sum_j \ell_j \cdot \text{CPL}, \qquad x = w \sum_j x_j$$

| Session | $w$ | Session | $w$ |
|---|---|---|---|
| Race / Heat | 1.00 | Qualify – Open | 0.50 |
| Warm-Up | 0.50 | Qualify – Lone | 0.35 |
| Practice – Open | 0.50 | Practice – Lone | 0.35 |
| Time Trial | 0.35 | All other | 0.00 |

Because the same $w$ multiplies both $c$ and $x$, the session's own CPI
$c/x$ is unchanged by it — the weight controls only **how much the session
counts**, not how good it looks. Weighting incidents alone, as an earlier
version did, wrongly inflated CPI in qualifying and time trials.

Incident point values (§3.5.1): off-track 1×, loss of control 2×, contact with
an object 2×, heavy contact with another driver 4× on tarmac and 2× on dirt.
Simultaneous incidents count only the most severe.

---

## 7. Corners

$\text{CPL}$, the corners per lap, is **exact**: iRacing publishes it per track
configuration, and this project ships all 436 values in
`tracks/official_corners.json`, keyed by the same `TrackID` the SDK exposes.

Corners are accumulated by **distance driven**, not by laps counted:

$$c \;=\; \text{CPL} \sum_{\text{ticks}} \Delta(\text{LapDistPct})$$

summed only while the car is in the world and outside the pit box, discarding
single-tick jumps larger than 10% of a lap (tow, teleport, reset). This matters
in both directions: a tow just after the pits would otherwise credit corners
never driven, and a driver who retires mid-lap genuinely *is* credited for the
partial lap — records exist with 0 completed laps, 2 incidents and a real
−0.06 SR change.

---

## 8. The complete algorithm

Given $S$, $\kappa$, laps $\ell$, incident points $x_{\text{raw}}$, session
weight $w$, and track corners-per-lap:

$$
\begin{aligned}
b &= \lfloor S \rfloor \\
c &= w \cdot \ell \cdot \text{CPL}, \qquad x = w \cdot x_{\text{raw}} \\
P &= \Phi_0\, r^{\,\kappa+b}\, e^{(S-b)/a} \\
\alpha &= 1 - \exp\!\big(-c / (W_1 \sqrt2^{\,b-1} f(\kappa))\big) \\
\lambda' &= (1-\alpha)/P + \alpha\, x/c \\
S' &= b + a \ln\!\big(1/(\lambda' \Phi_0 r^{\kappa+b})\big) \\
&\quad \text{then re-render into the neighbouring band while } S' \notin [b, b+1)
\end{aligned}
$$

All constants in one place:

$$a = 1.5236,\quad r = 1.4562,\quad \Phi_0 = 3.4949,\quad
W_1 = 1060,\quad \rho = \sqrt2,\quad f = (0.69, 0.83, 1, 1, 1, 1)$$

---

## 9. Accuracy

Validated against **123,361 driver-sessions** from 7,598 official races, sampled
across every series in the season that had official races, in all five licence
categories. The model was fitted on a random half and scored on the other half —
so nothing below is a model grading its own homework.

| Quantity | Held-out result |
|---|---|
| Rendering map $P \mapsto S$ | MAE **0.0099 SR**, p99 0.048 |
| Predicted CPI $P'$ | RMSE **2.90%** |
| Predicted SR $S'$ | MAE **0.0329 SR** |
| Exact to 2 decimals | 20.1% |
| Within 0.01 SR | 38.0% |
| Within 0.02 SR | 64.2% |
| Within 0.05 SR | 88.4% |

### The constants are already optimal

Re-fitting every constant on the training half and scoring on the held-out half
did **not** beat the shipped values:

| Model | Held-out CPI RMSE |
|---|---|
| **Shipped** $W_1{=}1060$, ratio $\sqrt2$, $f{=}(0.69,0.83,1)$ | **2.899%** |
| Refit, ratio free | 2.899% |
| Refit, ratio pinned to $\sqrt2$ | 2.899% |
| No class factor ($f \equiv 1$) | 2.946% |
| No band scaling (flat window) | 5.388% |

Two things are worth drawing out. Left completely free, the band ratio converged
to **1.4141** — $\sqrt2$ to four decimals, from 123k sessions that had never
been used to choose it. And the rendering map's shipped constants *beat* a fresh
refit on held-out data (0.0099 vs 0.0101 MAE), which is the behaviour of
constants that are right rather than merely tuned.

Dropping band scaling nearly doubles the error, so that part of the structure is
carrying real weight, not decoration.

### Where the remaining error lives

The residual is **not** randomly distributed — it is dominated by one thing, and
it is an artifact of the data source rather than of iRacing's algorithm.

Every bucket showed the same small negative bias (about −0.9% on predicted CPI),
regardless of category, licence class or strength of field. A uniform bias like
that is a missing constant, so it can be chased down. Correcting the corner count
multiplicatively helped a little; correcting it *additively* helped much more and
almost eliminated the bias (−0.84% → −0.09%). Splitting that offset by track
type showed what it really is:

| corners/lap | best offset | in laps |
|---|---|---|
| 6–9 | +9 | 1.17 |
| 10–13 | +13 | 1.13 |
| 14–17 | +15.5 | 0.99 |
| 18–23 | +19 | 0.94 |
| 4–5 (ovals) | −4 | −1.0 |

**It is one lap.** Results report `laps_complete` as a whole number, while
iRacing scores the distance actually driven — the part-lap past the line, the
run to the checkered, the out-lap. On road courses that is worth roughly half a
lap to a lap more than the reported count; on ovals the bookkeeping runs the
other way.

This is deliberately **not** compensated for in the model:

* The **overlay** never has the problem. It integrates `LapDistPct` and counts
  the distance actually driven, so it needs no correction — the correction exists
  only to undo a limitation of reading whole laps off a results page.
* Fitting per-category lap offsets does improve the offline numbers (MAE 0.0329 →
  0.0279, within-0.02 64% → 72%), but the fitted values are incoherent as physics
  — Oval **+2.45** laps against Dirt Oval **−2.0**. That is lap-count bookkeeping,
  not a property of the algorithm, and baking it in would also break the
  guarantee that the overlay and the web calculator agree exactly.

If you are comparing the calculator against a past result page, adding roughly
half a lap on a road course closes most of the gap.

### What is still unknown

- Whether $W_1 = 1060$ and the $\sqrt2$ ratio are exact or very good fits.
  Twelve times the data has not moved either.
- Why Rookie and Class D windows are shorter — a deliberate choice, or simply
  that those drivers have less history for the average to work with.
- Dirt Oval carries about five times the residual scatter of every other category
  (6.6% against 1.2–1.5%). Heat-race formats aggregate differently, which is the
  most likely cause, but it is unconfirmed.
- MPR thresholds, which are not exposed anywhere.

## 10. Reproducing this

```bash
python tools/analyse.py      # the full held-out analysis behind section 9
python tools/fit_model.py    # the narrower earlier fit
```

Both read the anonymous datasets shipped in `data/`:

| file | rows | format |
|---|---|---|
| `calibration_set_123k.json.gz` | 123,361 | columnar, gzipped (2.2 MB) |
| `calibration_set.json` | 13,849 | one object per row |

They contain only the physical quantities the model needs — corners, incident
points, CPI before and after, SR before and after, band, licence class, licence
category, strength of field, a team-event flag, corners per lap and lap count.

No customer IDs, driver names, subsession IDs, series IDs, track IDs, dates or
finishing positions. Strength of field is rounded to the nearest 50 so it cannot
serve as a join key back to a specific race. `tools/dataset.py` reads either
file; the assembly script asserts the field list before writing, so an
identifying column cannot be added by accident.
