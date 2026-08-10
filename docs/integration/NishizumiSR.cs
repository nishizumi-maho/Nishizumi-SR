// Nishizumi SR - estimated iRacing Safety Rating.
// Reference implementation for third-party overlays. No dependencies.
//
//   Guide:   docs/INTEGRATION.md
//   Vectors: docs/integration/vectors.json  (this file is verified against them)
//   Model:   docs/MODEL.md
//   Source:  https://github.com/nishizumi-maho/Nishizumi-SR
//
// A port of the Python in sr/ and iracing/telemetry.py, kept deliberately
// literal so the two can be diffed. Every number it produces is an ESTIMATE -
// see "What to tell your users" in the guide.
//
// MIT licensed, like the project it comes from.
// Targets C# 5 upwards, so it drops into .NET Framework, .NET and Unity alike.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace NishizumiSR
{
    /// <summary>Result of projecting one session onto a Safety Rating.</summary>
    public struct SrProjection
    {
        public double Sr;      // projected, already clamped to the displayed range
        public double Delta;   // Sr - srBefore
        public double Cpi;     // the new accumulated CPI
        public int Band;       // whole-number band the projection landed in
    }

    /// <summary>Licence class and SR band, as carried by the SDK's LicLevel.</summary>
    public struct LicenseClassBand
    {
        public int ClassIndex; // 0=R 1=D 2=C 3=B 4=A 5=Pro
        public int Band;       // 1..4
    }

    /// <summary>The estimator. Pure functions; nothing here holds state.</summary>
    public static class SafetyRating
    {
        // ── measured constants ───────────────────────────────────────────────
        // a, r and F0 are a least-squares fit to 12,639 real (SR, CPI, licence)
        // observations from iRacing's own results: mean error 0.007 SR. They are
        // not tuning knobs - changing them stops the mapping matching iRacing.
        // The window constants come from 123,361 driver-sessions. See MODEL.md.
        public const double ASlope = 1.5236;
        public const double RRatio = 1.4562;
        public const double F0Cpi = 3.4949;

        public const double WindowBand1 = 1060.0;
        public const double WindowBandRatio = 1.4142;   // sqrt(2), to the fitted precision
        public const double WindowFactorRookie = 0.69;  // Rookie and D move measurably faster
        public const double WindowFactorClassD = 0.83;
        public const double MaxAbsSessionDelta = 3.0;   // sanity clamp

        public const int BandMin = 0;
        public const int BandMax = 4;
        public const double SrDisplayMin = 0.0;
        public const double SrDisplayMax = 4.99;        // Sporting Code 3.7.2

        // irsdk_TrkLoc
        public const int TrkLocNotInWorld = -1;
        public const int TrkLocInPitStall = 1;

        // irsdk_SessionState
        public const int SessionStateCheckered = 5;
        public const int SessionStateCoolDown = 6;

        /// <summary>
        /// Sporting Code 3.6.1.1, "Table of Corner and Incident Multipliers".
        /// The weight applies to corners AND incidents - both sides.
        /// </summary>
        public static readonly Dictionary<string, double> SessionWeights =
            new Dictionary<string, double>(StringComparer.OrdinalIgnoreCase)
            {
                { "race", 1.00 },
                { "heat", 1.00 },
                { "qualify", 0.50 },        // Qualify - Open, the usual official case
                { "qualify_lone", 0.35 },
                { "warmup", 0.50 },
                { "practice", 0.50 },       // Practice - Open inside a Ranked event
                { "practice_lone", 0.35 },
                { "time_trial", 0.35 },
                { "unknown", 0.00 }         // "All Other Session Types"
            };

        private static readonly Dictionary<string, int> ClassIndexByLetter =
            new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase)
            {
                { "R", 0 }, { "ROOKIE", 0 }, { "D", 1 }, { "C", 2 },
                { "B", 3 }, { "A", 4 }, { "P", 5 }, { "PRO", 5 }
            };

        private static readonly Regex LeadingLetters = new Regex("^([A-Za-z]+)", RegexOptions.Compiled);

        private static double Clamp(double value, double low, double high)
        {
            return Math.Max(low, Math.Min(high, value));
        }

        private static int ClampInt(int value, int low, int high)
        {
            return Math.Max(low, Math.Min(high, value));
        }

        // ── licence ──────────────────────────────────────────────────────────

        /// <summary>
        /// SDK <c>LicLevel</c> (1..24) to class and band.
        ///
        /// The single most useful field, and the one ports most often miss: it
        /// carries the licence class AND the whole-number SR band together, which
        /// is exactly what the model needs. Level 13 is Class B band 1; level 20
        /// is Class A band 4. Prefer it over parsing LicString.
        /// </summary>
        public static LicenseClassBand ClassAndBandFromLicenseLevel(int licLevel)
        {
            LicenseClassBand result;
            result.ClassIndex = (licLevel - 1) / 4;
            result.Band = ((licLevel - 1) % 4) + 1;
            return result;
        }

        /// <summary>"B 3.54" / "Class B" to 3. null when unrecognisable.</summary>
        public static int? ClassIndexFromString(string licString)
        {
            string s = (licString ?? string.Empty).Trim().ToUpperInvariant().Replace("CLASS", string.Empty).Trim();
            Match match = LeadingLetters.Match(s);
            if (!match.Success) return null;
            int index;
            if (!ClassIndexByLetter.TryGetValue(match.Groups[1].Value, out index)) return null;
            return index;
        }

        // ── steps 1 and 3: the exact CPI <-> SR mapping ──────────────────────

        /// <summary>CPI at the floor of a whole band - the exact CPI of e.g. "3.00".</summary>
        public static double BandFloorCpi(int classIndex, int band)
        {
            return F0Cpi * Math.Pow(RRatio, classIndex + band);
        }

        /// <summary>Displayed SR for a CPI inside a specific band (no remapping).</summary>
        public static double SrFromCpi(double cpi, int classIndex, int band)
        {
            if (cpi <= 0) return band;
            return band + ASlope * Math.Log(cpi / BandFloorCpi(classIndex, band));
        }

        /// <summary>The CPI sitting behind a displayed SR. Step 1 of the model.</summary>
        public static double CpiFromSr(double sr, int classIndex, int? band = null)
        {
            int b = ClampInt(band ?? (int)Math.Floor(sr), BandMin, BandMax);
            return BandFloorCpi(classIndex, b) * Math.Exp((sr - b) / ASlope);
        }

        /// <summary>
        /// The overlapping-band rule, Sporting Code 3.7.4. Step 3 of the model.
        ///
        /// While the computed SR falls outside [band, band+1) the same CPI is
        /// remapped into the neighbouring band. iRacing's documented "+0.40 when
        /// you cross a whole number" is NOT hardcoded - it falls out of this
        /// geometry, which produces 0.427. Do not add a bonus on top of it.
        /// </summary>
        public static SrProjection RemapBand(double cpi, int classIndex, int band)
        {
            int b = ClampInt(band, BandMin, BandMax);
            SrProjection result = new SrProjection();
            for (int i = 0; i < 8; i++)   // convergence is guaranteed; the bound only avoids a hang
            {
                double sr = SrFromCpi(cpi, classIndex, b);
                if (sr >= b + 1.0 && b < BandMax) { b++; continue; }
                if (sr < b && b > BandMin) { b--; continue; }
                result.Sr = Clamp(sr, SrDisplayMin, SrDisplayMax);
                result.Band = b;
                result.Cpi = cpi;
                return result;
            }
            result.Sr = Clamp(SrFromCpi(cpi, classIndex, b), SrDisplayMin, SrDisplayMax);
            result.Band = b;
            result.Cpi = cpi;
            return result;
        }

        /// <summary>The real jump when crossing a whole number (documented as "0.40").</summary>
        public static double BandCrossingBonus()
        {
            return 1.0 - ASlope * Math.Log(RRatio);
        }

        /// <summary>CPI required to hold a given SR in that class. 3.00 by default.</summary>
        public static double TargetCpiToHold(int classIndex, double sr = 3.00)
        {
            return CpiFromSr(sr, classIndex, (int)Math.Floor(sr));
        }

        // ── step 2: the rolling average ──────────────────────────────────────

        /// <summary>
        /// EMA decay constant, in corners, for this band and class. Higher SR
        /// means a longer memory - which is why SR gets progressively harder to
        /// move as it climbs.
        /// </summary>
        public static double WindowCorners(int band, int classIndex)
        {
            double factor = 1.0;
            if (classIndex <= 0) factor = WindowFactorRookie;
            else if (classIndex == 1) factor = WindowFactorClassD;
            return WindowBand1 * Math.Pow(WindowBandRatio, Math.Max(0, band - 1)) * factor;
        }

        /// <summary>
        /// The weight this session carries: alpha = 1 - exp(-corners / window).
        /// An exponentially weighted moving average, not a fixed window with a cap.
        /// </summary>
        public static double SessionAlpha(double corners, int band, int classIndex)
        {
            if (corners <= 0) return 0.0;
            return 1.0 - Math.Exp(-corners / Math.Max(1.0, WindowCorners(band, classIndex)));
        }

        /// <summary>
        /// Apply one session to the accumulated CPI. <paramref name="corners"/>
        /// and <paramref name="incidentPoints"/> must ALREADY carry the session weight.
        /// </summary>
        public static double UpdateCpi(double cpiOld, double corners, double incidentPoints,
                                       int band, int classIndex)
        {
            if (corners <= 0 || cpiOld <= 0) return cpiOld;
            double alpha = SessionAlpha(corners, band, classIndex);
            double xOld = 1.0 / cpiOld;
            double xSession = incidentPoints / corners;
            double xNew = xOld * (1.0 - alpha) + alpha * xSession;
            if (xNew <= 1e-9) return 1e9;   // a perfect session filling the window: huge, but finite
            return 1.0 / xNew;
        }

        // ── the whole pipeline ───────────────────────────────────────────────

        /// <summary>
        /// Full estimate for a session.
        /// </summary>
        /// <param name="srBefore">SR at the start, as read from the SDK.</param>
        /// <param name="classIndex">0=R 1=D 2=C 3=B 4=A 5=Pro.</param>
        /// <param name="corners">Corners driven, ALREADY weighted.</param>
        /// <param name="incidentPoints">Incident points taken, ALREADY weighted.</param>
        /// <param name="band">Whole-number band; defaults to floor(srBefore).</param>
        public static SrProjection ProjectSr(double srBefore, int classIndex, double corners,
                                             double incidentPoints, int? band = null)
        {
            int b = band ?? (int)Math.Floor(srBefore);
            double cpiOld = CpiFromSr(srBefore, classIndex, b);
            double cpiNew = UpdateCpi(cpiOld, corners, incidentPoints, b, classIndex);

            SrProjection result = RemapBand(cpiNew, classIndex, b);
            result.Cpi = cpiNew;
            result.Delta = result.Sr - srBefore;
            if (Math.Abs(result.Delta) > MaxAbsSessionDelta)
            {
                result.Delta = Clamp(result.Delta, -MaxAbsSessionDelta, MaxAbsSessionDelta);
                result.Sr = srBefore + result.Delta;
            }
            return result;
        }

        /// <summary>Sporting Code weight for a session type. 0 for anything unrecognised.</summary>
        public static double SessionWeight(string sessionType)
        {
            double weight;
            if (SessionWeights.TryGetValue(sessionType ?? "unknown", out weight)) return weight;
            return 0.0;
        }

        /// <summary>
        /// Classify the raw SDK <c>SessionType</c>. Substring matching on purpose:
        /// there is no closed official list of the values it can take.
        /// </summary>
        public static string ClassifySessionType(string raw)
        {
            string s = (raw ?? string.Empty).ToLowerInvariant();
            if (s.Contains("time trial")) return "time_trial";
            if (s.Contains("practice") || s.Contains("testing")) return "practice";
            if (s.Contains("qualify")) return "qualify";
            if (s.Contains("warmup")) return "warmup";
            if (s.Contains("race")) return "race";
            return "unknown";
        }

        /// <summary>CPI of the CURRENT session. null means clean (no incidents).</summary>
        public static double? SessionCpi(double corners, double incidentPoints)
        {
            if (incidentPoints <= 0) return null;
            return corners / incidentPoints;
        }

        internal static string ToInvariant(double value)
        {
            return value.ToString("R", CultureInfo.InvariantCulture);
        }
    }

    /// <summary>
    /// Counts corners by DISTANCE actually driven, not by laps completed.
    ///
    /// The piece ports get wrong most often, and it matters: iRacing scores the
    /// distance driven, so cornersPerLap * lapsCompleted is wrong in both
    /// directions. A tow out of the pits moves the lap counter without the driver
    /// having driven those corners; an abandoned lap still earns the corners that
    /// were driven. Feed it every telemetry tick.
    /// </summary>
    public class CornerAccumulator
    {
        /// <summary>Largest plausible fraction of a lap in one tick. A tow covers far more.</summary>
        public const double MaxTickFraction = 0.10;

        public double Corners { get; private set; }
        public double DistanceLaps { get; private set; }
        public int SkippedJumps { get; private set; }

        private double? _prevPct;

        public void Reset()
        {
            Corners = 0.0;
            DistanceLaps = 0.0;
            SkippedJumps = 0;
            _prevPct = null;
        }

        /// <param name="lapDistPct">iRacing <c>LapDistPct</c>, 0..1.</param>
        /// <param name="trackSurface">iRacing <c>PlayerTrackSurface</c> (irsdk_TrkLoc).</param>
        /// <param name="cornersPerLap">Corners on this layout.</param>
        /// <returns>Corners credited by this tick.</returns>
        public double Update(double lapDistPct, int trackSurface, double cornersPerLap)
        {
            bool inWorld = trackSurface != SafetyRating.TrkLocNotInWorld
                        && trackSurface != SafetyRating.TrkLocInPitStall;
            if (!inWorld)
            {
                // In the pit box or out of the world: no corners, and position may jump.
                _prevPct = null;
                return 0.0;
            }
            if (!(lapDistPct >= 0.0) || lapDistPct > 1.0)   // also catches NaN
            {
                _prevPct = null;
                return 0.0;
            }
            if (!_prevPct.HasValue)
            {
                _prevPct = lapDistPct;
                return 0.0;
            }

            double delta = lapDistPct - _prevPct.Value;
            if (delta < 0.0) delta += 1.0;   // crossed the start/finish line

            if (delta > MaxTickFraction)
            {
                // Tow, teleport, session reset or a stalled app: invent no corners.
                SkippedJumps++;
                _prevPct = lapDistPct;
                return 0.0;
            }

            _prevPct = lapDistPct;
            DistanceLaps += delta;
            double credited = cornersPerLap * delta;
            Corners += credited;
            return credited;
        }
    }

    /// <summary>What an overlay reads once per repaint.</summary>
    public struct SrEstimate
    {
        public double? InitialSr;
        public double? ProjectedSr;
        public double Delta;
        public double Corners;
        public int Incidents;
        public double? Cpi;         // null = clean
        public double TargetCpi;    // CPI needed to hold 3.00 in this class
        public int LapsCompleted;
        public int? LapsTotal;
    }

    /// <summary>
    /// Per-session tracker: start it when <c>SessionNum</c> changes, feed it
    /// telemetry, read <see cref="Estimate"/> whenever you paint. See
    /// docs/INTEGRATION.md for the five SDK fields it needs.
    /// </summary>
    public class SessionTracker
    {
        public double? InitialSr { get; private set; }
        public int ClassIndex { get; private set; }
        public int Band { get; private set; }
        public double CornersPerLap { get; private set; }
        public double Weight { get; private set; }
        public int? LapsTotal { get; private set; }
        public int LapsCompleted { get; private set; }
        public int Incidents { get; private set; }

        private int _initialIncidentCount;
        private int? _lastLap;
        private readonly CornerAccumulator _accumulator = new CornerAccumulator();

        public SessionTracker() { Reset(); }

        public void Reset()
        {
            InitialSr = null;
            ClassIndex = 2;      // C until the real licence is read
            Band = 2;
            CornersPerLap = 10;
            Weight = 0.0;
            LapsTotal = null;
            LapsCompleted = 0;
            Incidents = 0;
            _initialIncidentCount = 0;
            _lastLap = null;
            _accumulator.Reset();
        }

        /// <param name="safetyRating">LicSubLevel / 100. null if unknown.</param>
        /// <param name="incidentCount">PlayerCarMyIncidentCount right now.</param>
        /// <param name="cornersPerLap">TrackNumTurns, or your own table.</param>
        /// <param name="sessionType">Raw SDK SessionType.</param>
        /// <param name="licLevel">SDK LicLevel (1..24). Preferred over licString.</param>
        /// <param name="licString">Fallback, e.g. "C 2.99".</param>
        /// <param name="lapsTotal">SessionLaps, null when the session is timed.</param>
        public void StartSession(double? safetyRating, int incidentCount, double cornersPerLap,
                                 string sessionType, int? licLevel = null, string licString = null,
                                 int? lapsTotal = null)
        {
            Reset();
            InitialSr = safetyRating;

            if (licLevel.HasValue && licLevel.Value > 0)
            {
                LicenseClassBand parsed = SafetyRating.ClassAndBandFromLicenseLevel(licLevel.Value);
                ClassIndex = parsed.ClassIndex;
                Band = parsed.Band;
            }
            else
            {
                int? index = SafetyRating.ClassIndexFromString(licString);
                ClassIndex = index ?? 2;
                Band = safetyRating.HasValue ? (int)Math.Floor(safetyRating.Value) : 2;
            }

            _initialIncidentCount = incidentCount;
            CornersPerLap = cornersPerLap;
            LapsTotal = lapsTotal;
            Weight = SafetyRating.SessionWeight(SafetyRating.ClassifySessionType(sessionType));
        }

        /// <summary>One telemetry tick. Call it as often as you poll; 10 Hz is plenty.</summary>
        public void Update(double lapDistPct, int trackSurface, int incidentCount, int? lap = null)
        {
            _accumulator.Update(lapDistPct, trackSurface, CornersPerLap);
            Incidents = Math.Max(0, incidentCount - _initialIncidentCount);

            if (lap.HasValue)
            {
                if (!_lastLap.HasValue || lap.Value < _lastLap.Value) _lastLap = lap.Value;
                else if (lap.Value > _lastLap.Value)
                {
                    LapsCompleted += lap.Value - _lastLap.Value;
                    _lastLap = lap.Value;
                }
            }
        }

        public SrEstimate Estimate()
        {
            double corners = _accumulator.Corners;

            // Sporting Code 3.6.1.1 weights corners AND incidents. Weighting only
            // the incidents inflates CPI in qualifying and time trial.
            double weightedCorners = corners * Weight;
            double weightedIncidents = Incidents * Weight;

            SrEstimate estimate = new SrEstimate();
            estimate.InitialSr = InitialSr;
            estimate.Corners = corners;
            estimate.Incidents = Incidents;
            estimate.Cpi = SafetyRating.SessionCpi(corners, Incidents);
            estimate.TargetCpi = SafetyRating.TargetCpiToHold(ClassIndex);
            estimate.LapsCompleted = LapsCompleted;
            estimate.LapsTotal = LapsTotal;

            if (InitialSr.HasValue)
            {
                SrProjection projection = SafetyRating.ProjectSr(
                    InitialSr.Value, ClassIndex, weightedCorners, weightedIncidents, Band);
                estimate.ProjectedSr = Math.Max(0.0, Math.Min(5.0, projection.Sr));
                estimate.Delta = projection.Delta;
            }
            return estimate;
        }
    }
}
