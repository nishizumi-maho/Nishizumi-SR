/*!
 * Nishizumi SR - estimated iRacing Safety Rating.
 * Reference implementation for third-party overlays. No dependencies.
 *
 * Guide:    docs/INTEGRATION.md
 * Vectors:  docs/integration/vectors.json   (this file is verified against them)
 * Model:    docs/MODEL.md
 * Source:   https://github.com/nishizumi-maho/Nishizumi-SR
 *
 * This is a port of the Python in sr/ and iracing/telemetry.py, kept
 * deliberately literal so the two can be diffed. Every number a user sees from
 * it is an ESTIMATE - see "What to tell your users" in the guide.
 *
 * MIT licensed, like the project it comes from.
 *
 * Works as CommonJS (require), as a browser global (window.NishizumiSR) and
 * through the default import in bundlers and Node ESM.
 */
(function (root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.NishizumiSR = factory();
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  /* ── measured constants ──────────────────────────────────────────────────
   * a, r and F0 come from a least-squares fit to 12,639 real (SR, CPI, licence)
   * observations taken from iRacing's own results: mean error 0.007 SR. They
   * are not tuning knobs - changing them stops the mapping matching iRacing.
   * The window constants come from 123,361 driver-sessions. See docs/MODEL.md.
   */
  var A_SLOPE = 1.5236;
  var R_RATIO = 1.4562;
  var F0_CPI = 3.4949;

  var WINDOW_BAND1 = 1060.0;
  var WINDOW_BAND_RATIO = 1.4142;      // sqrt(2) to the precision that was fitted
  var WINDOW_FACTOR_ROOKIE = 0.69;     // Rookie and Class D move measurably faster
  var WINDOW_FACTOR_CLASS_D = 0.83;
  var MAX_ABS_SESSION_DELTA = 3.0;     // sanity clamp, never reached in practice

  var BAND_MIN = 0;
  var BAND_MAX = 4;
  var SR_DISPLAY_MIN = 0.0;
  var SR_DISPLAY_MAX = 4.99;           // Sporting Code 3.7.2

  /* Sporting Code 3.6.1.1, "Table of Corner and Incident Multipliers".
   * The weight applies to corners AND incidents - both sides. */
  var SESSION_WEIGHTS = {
    race: 1.0,
    heat: 1.0,
    qualify: 0.5,          // Qualify - Open, the usual case in official events
    qualify_lone: 0.35,
    warmup: 0.5,
    practice: 0.5,         // Practice - Open inside a Ranked event
    practice_lone: 0.35,
    time_trial: 0.35,
    unknown: 0.0           // "All Other Session Types"
  };

  var CLASS_INDEX = { R: 0, ROOKIE: 0, D: 1, C: 2, B: 3, A: 4, P: 5, PRO: 5 };

  /* irsdk_TrkLoc */
  var TRK_LOC_NOT_IN_WORLD = -1;
  var TRK_LOC_IN_PIT_STALL = 1;

  /* irsdk_SessionState */
  var SESSION_STATE_CHECKERED = 5;
  var SESSION_STATE_COOL_DOWN = 6;

  function clamp(value, low, high) {
    return Math.max(low, Math.min(high, value));
  }

  /* ── licence ─────────────────────────────────────────────────────────── */

  /**
   * SDK `LicLevel` (1..24) -> { classIndex, band }.
   *
   * This is the single most useful field and the one ports most often miss:
   * it carries the licence class AND the whole-number SR band together, which
   * is exactly what the model needs. Level 13 is Class B band 1; level 20 is
   * Class A band 4. Prefer it over parsing `LicString`.
   */
  function classAndBandFromLicenseLevel(licLevel) {
    var level = Math.trunc(licLevel);
    return { classIndex: Math.floor((level - 1) / 4), band: ((level - 1) % 4) + 1 };
  }

  /** "B 3.54" / "Class B" -> 3. null when unrecognisable. */
  function classIndexFromString(licString) {
    var s = String(licString == null ? "" : licString).trim().toUpperCase().replace("CLASS", "").trim();
    var match = /^([A-Z]+)/.exec(s);
    if (!match) return null;
    var index = CLASS_INDEX[match[1]];
    return index === undefined ? null : index;
  }

  /* ── step 1 and 3: the exact CPI <-> SR mapping ──────────────────────── */

  /** CPI at the floor of a whole SR band - the exact CPI of e.g. "3.00". */
  function bandFloorCpi(classIndex, band) {
    return F0_CPI * Math.pow(R_RATIO, classIndex + band);
  }

  /** Displayed SR for a CPI inside a specific band (no remapping). */
  function srFromCpi(cpi, classIndex, band) {
    if (cpi <= 0) return band;
    return band + A_SLOPE * Math.log(cpi / bandFloorCpi(classIndex, band));
  }

  /** The CPI sitting behind a displayed SR. Step 1 of the model. */
  function cpiFromSr(sr, classIndex, band) {
    var b = band === undefined || band === null ? Math.floor(sr) : band;
    b = clamp(b, BAND_MIN, BAND_MAX);
    return bandFloorCpi(classIndex, b) * Math.exp((sr - b) / A_SLOPE);
  }

  /**
   * The overlapping-band rule, Sporting Code 3.7.4. Step 3 of the model.
   *
   * While the computed SR falls outside [band, band+1) the same CPI is remapped
   * into the neighbouring band. iRacing's documented "+0.40 when you cross a
   * whole number" is NOT hardcoded anywhere - it falls out of this geometry,
   * which produces 0.427. Do not add a bonus on top of this.
   */
  function remapBand(cpi, classIndex, band) {
    var b = clamp(Math.trunc(band), BAND_MIN, BAND_MAX);
    for (var i = 0; i < 8; i++) {   // convergence is guaranteed; the bound only avoids a hang
      var sr = srFromCpi(cpi, classIndex, b);
      if (sr >= b + 1.0 && b < BAND_MAX) { b += 1; continue; }
      if (sr < b && b > BAND_MIN) { b -= 1; continue; }
      return { sr: clamp(sr, SR_DISPLAY_MIN, SR_DISPLAY_MAX), band: b };
    }
    return { sr: clamp(srFromCpi(cpi, classIndex, b), SR_DISPLAY_MIN, SR_DISPLAY_MAX), band: b };
  }

  /** The real jump when crossing a whole number (iRacing documents "0.40"). */
  function bandCrossingBonus() {
    return 1.0 - A_SLOPE * Math.log(R_RATIO);
  }

  /** CPI required to hold a given SR in that class. 3.00 by default. */
  function targetCpiToHold(classIndex, sr) {
    var target = sr === undefined ? 3.0 : sr;
    return cpiFromSr(target, classIndex, Math.floor(target));
  }

  /* ── step 2: the rolling average ─────────────────────────────────────── */

  /**
   * EMA decay constant, in corners, for this band and licence class.
   * Higher SR means a longer memory - which is why SR gets progressively
   * harder to move as it climbs.
   */
  function windowCorners(band, classIndex) {
    var factor = 1.0;
    if (classIndex <= 0) factor = WINDOW_FACTOR_ROOKIE;
    else if (classIndex === 1) factor = WINDOW_FACTOR_CLASS_D;
    return WINDOW_BAND1 * Math.pow(WINDOW_BAND_RATIO, Math.max(0, Math.trunc(band) - 1)) * factor;
  }

  /**
   * The weight this session carries in the rolling average.
   *
   *     alpha = 1 - exp(-corners / window)
   *
   * An exponentially weighted moving average, not a fixed window with a cap.
   */
  function sessionAlpha(corners, band, classIndex) {
    if (corners <= 0) return 0.0;
    return 1.0 - Math.exp(-corners / Math.max(1.0, windowCorners(band, classIndex)));
  }

  /**
   * Apply one session to the accumulated CPI.
   * `corners` and `incidentPoints` must ALREADY carry the session weight.
   */
  function updateCpi(cpiOld, corners, incidentPoints, band, classIndex) {
    if (corners <= 0 || cpiOld <= 0) return cpiOld;
    var alpha = sessionAlpha(corners, band, classIndex);
    var xOld = 1.0 / cpiOld;
    var xSession = incidentPoints / corners;
    var xNew = xOld * (1.0 - alpha) + alpha * xSession;
    if (xNew <= 1e-9) return 1e9;   // a perfect session filling the window: huge, but finite
    return 1.0 / xNew;
  }

  /* ── the whole pipeline ──────────────────────────────────────────────── */

  /**
   * Full estimate for a session.
   *
   * @param {number} srBefore        SR at the start, as read from the SDK.
   * @param {number} classIndex      0=R 1=D 2=C 3=B 4=A 5=Pro.
   * @param {number} corners         Corners driven, ALREADY weighted.
   * @param {number} incidentPoints  Incident points taken, ALREADY weighted.
   * @param {number} [band]          Whole-number SR band; defaults to floor(srBefore).
   * @returns {{sr: number, delta: number, cpi: number, band: number}}
   */
  function projectSr(srBefore, classIndex, corners, incidentPoints, band) {
    var b = band === undefined || band === null ? Math.floor(srBefore) : band;
    var cpiOld = cpiFromSr(srBefore, classIndex, b);
    var cpiNew = updateCpi(cpiOld, corners, incidentPoints, b, classIndex);
    var remapped = remapBand(cpiNew, classIndex, b);

    var sr = remapped.sr;
    var delta = sr - srBefore;
    if (Math.abs(delta) > MAX_ABS_SESSION_DELTA) {
      delta = clamp(delta, -MAX_ABS_SESSION_DELTA, MAX_ABS_SESSION_DELTA);
      sr = srBefore + delta;
    }
    return { sr: sr, delta: delta, cpi: cpiNew, band: remapped.band };
  }

  /** Sporting Code weight for a session type. 0 for anything unrecognised. */
  function sessionWeight(sessionType) {
    var key = String(sessionType == null ? "unknown" : sessionType).toLowerCase();
    var weight = SESSION_WEIGHTS[key];
    return weight === undefined ? 0.0 : weight;
  }

  /**
   * Classify the raw SDK `SessionType` string. Substring matching on purpose:
   * there is no closed official list of the values it can take.
   */
  function classifySessionType(raw) {
    var s = String(raw == null ? "" : raw).toLowerCase();
    if (s.indexOf("time trial") >= 0) return "time_trial";
    if (s.indexOf("practice") >= 0 || s.indexOf("testing") >= 0) return "practice";
    if (s.indexOf("qualify") >= 0) return "qualify";
    if (s.indexOf("warmup") >= 0) return "warmup";
    if (s.indexOf("race") >= 0) return "race";
    return "unknown";
  }

  /** CPI of the CURRENT session. null means clean (no incidents). */
  function sessionCpi(corners, incidentPoints) {
    if (incidentPoints <= 0) return null;
    return corners / incidentPoints;
  }

  /* ── corners driven, by distance ─────────────────────────────────────── */

  /**
   * Counts corners by DISTANCE actually driven, not by laps completed.
   *
   * This is the piece ports get wrong most often, and it matters: iRacing
   * scores the distance driven, so `cornersPerLap * lapsCompleted` is wrong in
   * both directions. A tow out of the pits moves the lap counter without the
   * driver having driven those corners; an abandoned lap still earns the
   * corners that were driven. Feed it every telemetry tick.
   */
  function CornerAccumulator() {
    this.corners = 0.0;
    this.distanceLaps = 0.0;
    this.skippedJumps = 0;
    this._prevPct = null;
  }

  /** Largest plausible fraction of a lap in one tick. A tow covers far more. */
  CornerAccumulator.MAX_TICK_FRACTION = 0.10;

  CornerAccumulator.prototype.reset = function () {
    this.corners = 0.0;
    this.distanceLaps = 0.0;
    this.skippedJumps = 0;
    this._prevPct = null;
  };

  /**
   * @param {number} lapDistPct     iRacing `LapDistPct`, 0..1.
   * @param {number} trackSurface   iRacing `PlayerTrackSurface` (irsdk_TrkLoc).
   * @param {number} cornersPerLap  Corners on this layout.
   * @returns {number} corners credited by this tick.
   */
  CornerAccumulator.prototype.update = function (lapDistPct, trackSurface, cornersPerLap) {
    var inWorld = trackSurface !== TRK_LOC_NOT_IN_WORLD && trackSurface !== TRK_LOC_IN_PIT_STALL;
    if (!inWorld) {
      // In the pit box or out of the world: no corners, and the position may jump.
      this._prevPct = null;
      return 0.0;
    }
    var pct = lapDistPct;
    if (!(pct >= 0.0) || pct > 1.0) {   // also catches NaN
      this._prevPct = null;
      return 0.0;
    }
    if (this._prevPct === null) {
      this._prevPct = pct;
      return 0.0;
    }

    var delta = pct - this._prevPct;
    if (delta < 0.0) delta += 1.0;      // crossed the start/finish line

    if (delta > CornerAccumulator.MAX_TICK_FRACTION) {
      // Tow, teleport, session reset or a stalled app: invent no corners.
      this.skippedJumps += 1;
      this._prevPct = pct;
      return 0.0;
    }

    this._prevPct = pct;
    this.distanceLaps += delta;
    var credited = cornersPerLap * delta;
    this.corners += credited;
    return credited;
  };

  /* ── the bit an overlay actually calls ───────────────────────────────── */

  /**
   * Per-session tracker: start it when `SessionNum` changes, feed it telemetry,
   * read `estimate()` whenever you paint. See docs/INTEGRATION.md for the
   * five SDK fields it needs.
   */
  function SessionTracker() {
    this.reset();
  }

  SessionTracker.prototype.reset = function () {
    this.initialSr = null;
    this.classIndex = 2;          // C until the real licence is read
    this.band = 2;
    this.cornersPerLap = 10;
    this.sessionWeight = 0.0;
    this.lapsTotal = null;
    this.lapsCompleted = 0;
    this.incidents = 0;
    this._initialIncidentCount = 0;
    this._lastLap = null;
    this._accumulator = new CornerAccumulator();
  };

  /**
   * @param {object} options
   * @param {number|null} options.safetyRating  LicSubLevel / 100. null if unknown.
   * @param {number} [options.licLevel]         SDK LicLevel (1..24). Preferred.
   * @param {string} [options.licString]        Fallback, e.g. "C 2.99".
   * @param {number} options.incidentCount      PlayerCarMyIncidentCount right now.
   * @param {number} options.cornersPerLap      TrackNumTurns, or your own table.
   * @param {string} options.sessionType        Raw SDK SessionType, or a classified one.
   * @param {number|null} [options.lapsTotal]   SessionLaps, null when timed.
   */
  SessionTracker.prototype.startSession = function (options) {
    var o = options || {};
    this.reset();

    this.initialSr = (typeof o.safetyRating === "number") ? o.safetyRating : null;

    if (o.licLevel) {
      var parsed = classAndBandFromLicenseLevel(o.licLevel);
      this.classIndex = parsed.classIndex;
      this.band = parsed.band;
    } else {
      var index = classIndexFromString(o.licString);
      this.classIndex = index === null ? 2 : index;
      this.band = this.initialSr === null ? 2 : Math.floor(this.initialSr);
    }

    this._initialIncidentCount = o.incidentCount || 0;
    this.cornersPerLap = o.cornersPerLap || 10;
    this.lapsTotal = (o.lapsTotal === undefined) ? null : o.lapsTotal;
    this.sessionWeight = sessionWeight(classifySessionType(o.sessionType) === "unknown"
      ? o.sessionType
      : classifySessionType(o.sessionType));
    return this;
  };

  /**
   * One telemetry tick. Call it as often as you poll - 10 Hz is plenty.
   *
   * @param {object} telemetry
   * @param {number} telemetry.lapDistPct       LapDistPct
   * @param {number} telemetry.trackSurface     PlayerTrackSurface
   * @param {number} telemetry.incidentCount    PlayerCarMyIncidentCount
   * @param {number} [telemetry.lap]            Lap, only used for the lap counter
   */
  SessionTracker.prototype.update = function (telemetry) {
    var t = telemetry || {};
    this._accumulator.update(t.lapDistPct, t.trackSurface, this.cornersPerLap);

    if (typeof t.incidentCount === "number") {
      this.incidents = Math.max(0, t.incidentCount - this._initialIncidentCount);
    }
    if (typeof t.lap === "number") {
      if (this._lastLap === null || t.lap < this._lastLap) this._lastLap = t.lap;
      else if (t.lap > this._lastLap) { this.lapsCompleted += t.lap - this._lastLap; this._lastLap = t.lap; }
    }
    return this;
  };

  /**
   * The current estimate.
   *
   * @returns {{initialSr: number|null, projectedSr: number|null, delta: number,
   *            corners: number, incidents: number, cpi: number|null,
   *            targetCpi: number, lapsCompleted: number, lapsTotal: number|null}}
   */
  SessionTracker.prototype.estimate = function () {
    var corners = this._accumulator.corners;

    // Sporting Code 3.6.1.1 weights corners AND incidents. Weighting only the
    // incidents inflates CPI in qualifying and time trial.
    var weightedCorners = corners * this.sessionWeight;
    var weightedIncidents = this.incidents * this.sessionWeight;

    var projected = null;
    var delta = 0.0;
    if (this.initialSr !== null) {
      var result = projectSr(this.initialSr, this.classIndex, weightedCorners, weightedIncidents, this.band);
      projected = clamp(result.sr, 0.0, 5.0);
      delta = result.delta;
    }

    return {
      initialSr: this.initialSr,
      projectedSr: projected,
      delta: delta,
      corners: corners,
      incidents: this.incidents,
      cpi: sessionCpi(corners, this.incidents),
      targetCpi: targetCpiToHold(this.classIndex),
      lapsCompleted: this.lapsCompleted,
      lapsTotal: this.lapsTotal
    };
  };

  return {
    // constants
    A_SLOPE: A_SLOPE, R_RATIO: R_RATIO, F0_CPI: F0_CPI,
    WINDOW_BAND1: WINDOW_BAND1, WINDOW_BAND_RATIO: WINDOW_BAND_RATIO,
    WINDOW_FACTOR_ROOKIE: WINDOW_FACTOR_ROOKIE, WINDOW_FACTOR_CLASS_D: WINDOW_FACTOR_CLASS_D,
    MAX_ABS_SESSION_DELTA: MAX_ABS_SESSION_DELTA,
    SR_DISPLAY_MIN: SR_DISPLAY_MIN, SR_DISPLAY_MAX: SR_DISPLAY_MAX,
    SESSION_WEIGHTS: SESSION_WEIGHTS,
    SESSION_STATE_CHECKERED: SESSION_STATE_CHECKERED,
    SESSION_STATE_COOL_DOWN: SESSION_STATE_COOL_DOWN,
    // licence
    classAndBandFromLicenseLevel: classAndBandFromLicenseLevel,
    classIndexFromString: classIndexFromString,
    // mapping
    bandFloorCpi: bandFloorCpi, srFromCpi: srFromCpi, cpiFromSr: cpiFromSr,
    remapBand: remapBand, bandCrossingBonus: bandCrossingBonus,
    targetCpiToHold: targetCpiToHold,
    // rolling average
    windowCorners: windowCorners, sessionAlpha: sessionAlpha, updateCpi: updateCpi,
    // pipeline
    projectSr: projectSr, sessionWeight: sessionWeight,
    classifySessionType: classifySessionType, sessionCpi: sessionCpi,
    // helpers
    CornerAccumulator: CornerAccumulator, SessionTracker: SessionTracker
  };
});
