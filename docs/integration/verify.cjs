/*
 * Runs docs/integration/nishizumi-sr.js against docs/integration/vectors.json.
 *
 *     node docs/integration/verify.cjs
 *
 * Port the library to your own language, make it produce the same numbers, and
 * you have the same estimator. Compare with a tolerance, never for equality:
 * log() and exp() are allowed to differ by an ulp between runtimes.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const SR = require("./nishizumi-sr.js");

const vectors = JSON.parse(fs.readFileSync(path.join(__dirname, "vectors.json"), "utf8"));

const SR_TOL = 1e-9;
const REL_TOL = 1e-9;

let checks = 0;
const failures = [];

function close(actual, expected, tol, relative) {
  if (expected === null || actual === null) return actual === expected;
  if (!Number.isFinite(actual) || !Number.isFinite(expected)) return actual === expected;
  const diff = Math.abs(actual - expected);
  return relative ? diff <= tol * Math.max(1, Math.abs(expected)) : diff <= tol;
}

function eq(label, actual, expected, tol, relative) {
  checks++;
  const ok = tol === undefined ? actual === expected : close(actual, expected, tol, relative);
  if (!ok) failures.push(`${label}: got ${actual}, expected ${expected}`);
}

/* constants ------------------------------------------------------------- */
const c = vectors.constants;
eq("A_SLOPE", SR.A_SLOPE, c.a_slope);
eq("R_RATIO", SR.R_RATIO, c.r_ratio);
eq("F0_CPI", SR.F0_CPI, c.f0_cpi);
eq("WINDOW_BAND1", SR.WINDOW_BAND1, c.window_band1);
eq("WINDOW_BAND_RATIO", SR.WINDOW_BAND_RATIO, c.window_band_ratio);
eq("WINDOW_FACTOR_ROOKIE", SR.WINDOW_FACTOR_ROOKIE, c.window_factor_rookie);
eq("WINDOW_FACTOR_CLASS_D", SR.WINDOW_FACTOR_CLASS_D, c.window_factor_class_d);
eq("MAX_ABS_SESSION_DELTA", SR.MAX_ABS_SESSION_DELTA, c.max_abs_session_delta);
eq("SR_DISPLAY_MAX", SR.SR_DISPLAY_MAX, c.sr_display_max);
eq("bandCrossingBonus", SR.bandCrossingBonus(), c.band_crossing_bonus, SR_TOL);

/* session weights ------------------------------------------------------- */
for (const [name, weight] of Object.entries(vectors.session_weights)) {
  eq(`SESSION_WEIGHTS.${name}`, SR.SESSION_WEIGHTS[name], weight);
}
for (const v of vectors.session_weight_lookups) {
  eq(`sessionWeight(${JSON.stringify(v.session_type)})`,
     SR.sessionWeight(v.session_type), v.weight);
}

/* licence --------------------------------------------------------------- */
for (const v of vectors.license_levels) {
  const got = SR.classAndBandFromLicenseLevel(v.lic_level);
  eq(`licLevel ${v.lic_level} class`, got.classIndex, v.class_index);
  eq(`licLevel ${v.lic_level} band`, got.band, v.band);
}
for (const v of vectors.license_strings) {
  eq(`licString ${JSON.stringify(v.lic_string)}`,
     SR.classIndexFromString(v.lic_string), v.class_index);
}
for (const v of vectors.target_cpi_to_hold_3_00) {
  eq(`targetCpiToHold(${v.class_index})`,
     SR.targetCpiToHold(v.class_index), v.target_cpi, REL_TOL, true);
}

/* mapping --------------------------------------------------------------- */
for (const v of vectors.band_floors) {
  eq(`bandFloorCpi(${v.class_index},${v.band})`,
     SR.bandFloorCpi(v.class_index, v.band), v.floor_cpi, REL_TOL, true);
}
for (const v of vectors.sr_cpi) {
  eq(`cpiFromSr(${v.sr},${v.class_index},${v.band})`,
     SR.cpiFromSr(v.sr, v.class_index, v.band), v.cpi, REL_TOL, true);
  eq(`srFromCpi round trip ${v.sr}`,
     SR.srFromCpi(v.cpi, v.class_index, v.band), v.sr_round_trip, SR_TOL);
}
for (const v of vectors.remap) {
  const got = SR.remapBand(v.cpi, v.class_index, v.band);
  eq(`remapBand sr (${v.class_index},${v.band})`, got.sr, v.sr, SR_TOL);
  eq(`remapBand band (${v.class_index},${v.band})`, got.band, v.final_band);
}

/* rolling average ------------------------------------------------------- */
for (const v of vectors.windows) {
  eq(`windowCorners(${v.band},${v.class_index})`,
     SR.windowCorners(v.band, v.class_index), v.window, REL_TOL, true);
}
for (const v of vectors.alphas) {
  eq(`sessionAlpha(${v.corners},${v.band},${v.class_index})`,
     SR.sessionAlpha(v.corners, v.band, v.class_index), v.alpha, SR_TOL);
}
for (const v of vectors.update_cpi) {
  eq(`updateCpi(${v.cpi_old},${v.corners},${v.incident_points})`,
     SR.updateCpi(v.cpi_old, v.corners, v.incident_points, v.band, v.class_index),
     v.cpi_new, REL_TOL, true);
}
for (const v of vectors.session_cpi) {
  eq(`sessionCpi(${v.corners},${v.incident_points})`,
     SR.sessionCpi(v.corners, v.incident_points), v.cpi, v.cpi === null ? undefined : REL_TOL, true);
}

/* the whole pipeline ---------------------------------------------------- */
for (const v of vectors.project) {
  const got = SR.projectSr(v.sr_before, v.class_index, v.corners, v.incident_points);
  eq(`projectSr sr [${v.comment}]`, got.sr, v.projected_sr, SR_TOL);
  eq(`projectSr delta [${v.comment}]`, got.delta, v.delta, SR_TOL);
  eq(`projectSr cpi [${v.comment}]`, got.cpi, v.cpi_new, REL_TOL, true);
}

/* corners by distance --------------------------------------------------- */
for (const v of vectors.corner_accumulator) {
  const acc = new SR.CornerAccumulator();
  for (const s of v.samples) acc.update(s.lap_dist_pct, s.track_surface, v.corners_per_lap);
  eq(`CornerAccumulator [${v.comment}]`, acc.corners, v.corners, REL_TOL, true);
}

/* ----------------------------------------------------------------------- */
if (failures.length) {
  console.error(`FAILED: ${failures.length} of ${checks} checks\n`);
  for (const f of failures.slice(0, 25)) console.error("  - " + f);
  if (failures.length > 25) console.error(`  ... and ${failures.length - 25} more`);
  process.exit(1);
}
console.log(`OK: all ${checks} checks passed against ${path.basename("vectors.json")}`);
