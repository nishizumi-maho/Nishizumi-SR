# The Model

A complete mathematical description of how Nishizumi SR predicts an iRacing
Safety Rating change.

iRacing has never published its formula. What follows is a *reconstruction*,
fitted to **13,849 anonymous driver-sessions** taken from public official race
results. Where a quantity is exact it is marked as such; where it is fitted, the
residual error is stated.

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

Fitted over 14,231 observations:

$$a = 1.5236$$

Mean absolute error **0.0093 SR**. For context the site only displays two
decimals, so ~0.005 is the observational floor. Re-fitting freely across the
whole licence range moves $a$ by less than 0.5% and *improves* MAE only to
0.0091, so these constants are effectively at the optimum.

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

Binning the solved $\alpha$ against $c/W$ over 13,849 sessions:

| $c/W$ | $\alpha$ measured | $1 - e^{-c/W}$ |
|---|---|---|
| 0.20 | 0.201 | 0.181 |
| 0.30 | 0.284 | 0.259 |
| 0.52 | 0.446 | 0.405 |
| 0.93 | 0.646 | 0.606 |
| 1.90 | 0.878 | 0.850 |
| 2.93 | 0.964 | 0.947 |

Linear for small $c/W$, saturating smoothly — the signature of an exponentially
weighted moving average over corners, not a fixed window:

$$\boxed{\;\alpha \;=\; 1 - e^{-c/W}\;}$$

An earlier version of this project used $\alpha = \min(\tfrac13, c/W)$. That cap
was an artifact of fitting a fixed window to nine races; on the full data it
scores 9.2% CPI error against 1.21% for the exponential form.

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

Against 13,849 anonymous driver-sessions from public official results:

| Quantity | Error |
|---|---|
| Rendering map $P \mapsto S$ | MAE **0.0093 SR** |
| Predicted CPI $P'$ | RMSE **1.21%** |
| Predicted SR $S'$ | MAE **0.0233 SR**, RMSE 0.064 |
| Within 0.01 SR | 44.9% |
| Within 0.02 SR | 72.4% |

By licence class:

| Class | n | MAE | within 0.02 |
|---|---|---|---|
| Rookie | 445 | 0.0161 | 84.3% |
| D | 1139 | 0.0234 | 72.1% |
| C | 1169 | 0.0227 | 75.4% |
| B | 3962 | 0.0253 | 66.7% |
| A | 7134 | 0.0227 | 74.3% |

### Where the residual comes from

- **Not** the rendering map: starting from the exact official `old_cpi` instead
  of inverting the rounded SR barely helps (0.0267 → 0.0270 MAE).
- **Whole-lap reporting.** Error falls monotonically with race length — 0.046 SR
  at 1–4 laps, 0.027 at 8–25, 0.018 at 60+. Results report completed laps, while
  iRacing scores the distance actually driven. The live overlay reads
  `LapDistPct` and does not have this limitation; the web calculator does,
  because you type whole laps into it.

### What remains unknown

- Whether $W_1 = 1060$ and the $\sqrt2$ ratio are exact or merely very good fits.
- Why Rookie and Class D windows are shorter — a deliberate design choice, or an
  artifact of those drivers simply having less history for the average to work
  with.
- MPR thresholds, which are not exposed anywhere.

---

## 10. Reproducing this

```bash
python tools/fit_model.py
```

Reads `data/calibration_set.json` — the anonymous dataset behind every number
above — and re-runs the fit. The file contains only the physical quantities the
model needs: corners, incident points, CPI before and after, SR before and
after, band and class index. No customer IDs, driver names, subsession IDs,
track IDs, series IDs or dates.
