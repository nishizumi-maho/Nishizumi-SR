// Runs NishizumiSR.cs against docs/integration/vectors.json.
//
//   mcs -out:verify.exe NishizumiSR.cs VerifyVectors.cs && mono verify.exe
//   (or drop both files into a console project and `dotnet run`)
//
// Port the library to your own language, make it produce the same numbers, and
// you have the same estimator. Compare with a tolerance, never for equality:
// Log() and Exp() are allowed to differ by an ulp between runtimes.
//
// The JSON reader below is deliberately minimal and self-contained so this
// verifier pulls in no NuGet packages and runs on any runtime.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using NishizumiSR;

internal static class Json
{
    public static object Parse(string text)
    {
        int i = 0;
        object value = ParseValue(text, ref i);
        SkipWhitespace(text, ref i);
        return value;
    }

    private static void SkipWhitespace(string s, ref int i)
    {
        while (i < s.Length && char.IsWhiteSpace(s[i])) i++;
    }

    private static object ParseValue(string s, ref int i)
    {
        SkipWhitespace(s, ref i);
        char c = s[i];
        if (c == '{') return ParseObject(s, ref i);
        if (c == '[') return ParseArray(s, ref i);
        if (c == '"') return ParseString(s, ref i);
        if (s.Length - i >= 4 && s.Substring(i, 4) == "true") { i += 4; return true; }
        if (s.Length - i >= 5 && s.Substring(i, 5) == "false") { i += 5; return false; }
        if (s.Length - i >= 4 && s.Substring(i, 4) == "null") { i += 4; return null; }
        return ParseNumber(s, ref i);
    }

    private static Dictionary<string, object> ParseObject(string s, ref int i)
    {
        var result = new Dictionary<string, object>();
        i++;                                    // '{'
        SkipWhitespace(s, ref i);
        if (s[i] == '}') { i++; return result; }
        while (true)
        {
            SkipWhitespace(s, ref i);
            string key = ParseString(s, ref i);
            SkipWhitespace(s, ref i);
            i++;                                // ':'
            result[key] = ParseValue(s, ref i);
            SkipWhitespace(s, ref i);
            if (s[i] == ',') { i++; continue; }
            i++;                                // '}'
            return result;
        }
    }

    private static List<object> ParseArray(string s, ref int i)
    {
        var result = new List<object>();
        i++;                                    // '['
        SkipWhitespace(s, ref i);
        if (s[i] == ']') { i++; return result; }
        while (true)
        {
            result.Add(ParseValue(s, ref i));
            SkipWhitespace(s, ref i);
            if (s[i] == ',') { i++; continue; }
            i++;                                // ']'
            return result;
        }
    }

    private static string ParseString(string s, ref int i)
    {
        var sb = new StringBuilder();
        i++;                                    // opening quote
        while (s[i] != '"')
        {
            if (s[i] == '\\')
            {
                i++;
                char e = s[i++];
                if (e == 'n') sb.Append('\n');
                else if (e == 't') sb.Append('\t');
                else if (e == 'r') sb.Append('\r');
                else if (e == 'b') sb.Append('\b');
                else if (e == 'f') sb.Append('\f');
                else if (e == 'u')
                {
                    sb.Append((char)Convert.ToInt32(s.Substring(i, 4), 16));
                    i += 4;
                }
                else sb.Append(e);
            }
            else sb.Append(s[i++]);
        }
        i++;                                    // closing quote
        return sb.ToString();
    }

    private static double ParseNumber(string s, ref int i)
    {
        int start = i;
        while (i < s.Length && (char.IsDigit(s[i]) || s[i] == '-' || s[i] == '+' ||
                                s[i] == '.' || s[i] == 'e' || s[i] == 'E')) i++;
        return double.Parse(s.Substring(start, i - start), CultureInfo.InvariantCulture);
    }
}

internal static class VerifyVectors
{
    private const double SrTol = 1e-9;
    private const double RelTol = 1e-9;

    private static int _checks;
    private static readonly List<string> Failures = new List<string>();

    private static Dictionary<string, object> Obj(object o) { return (Dictionary<string, object>)o; }
    private static List<object> Arr(object o) { return (List<object>)o; }
    private static double Num(object o) { return (double)o; }
    private static int Int(object o) { return (int)(double)o; }

    private static void Eq(string label, double actual, double expected, double tol, bool relative = false)
    {
        _checks++;
        double diff = Math.Abs(actual - expected);
        double limit = relative ? tol * Math.Max(1.0, Math.Abs(expected)) : tol;
        if (!(diff <= limit))
        {
            Failures.Add(string.Format(CultureInfo.InvariantCulture,
                "{0}: got {1:R}, expected {2:R}", label, actual, expected));
        }
    }

    private static void EqExact(string label, double actual, double expected)
    {
        _checks++;
        if (actual != expected)
        {
            Failures.Add(string.Format(CultureInfo.InvariantCulture,
                "{0}: got {1:R}, expected {2:R}", label, actual, expected));
        }
    }

    private static void EqNullable(string label, int? actual, object expected)
    {
        _checks++;
        int? want = expected == null ? (int?)null : Int(expected);
        if (actual != want)
        {
            Failures.Add(label + ": got " + (actual.HasValue ? actual.Value.ToString() : "null")
                         + ", expected " + (want.HasValue ? want.Value.ToString() : "null"));
        }
    }

    public static int Main(string[] args)
    {
        string path = args.Length > 0
            ? args[0]
            : Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "vectors.json");
        if (!File.Exists(path)) path = "vectors.json";

        var vectors = Obj(Json.Parse(File.ReadAllText(path)));

        // constants ---------------------------------------------------------
        var c = Obj(vectors["constants"]);
        EqExact("ASlope", SafetyRating.ASlope, Num(c["a_slope"]));
        EqExact("RRatio", SafetyRating.RRatio, Num(c["r_ratio"]));
        EqExact("F0Cpi", SafetyRating.F0Cpi, Num(c["f0_cpi"]));
        EqExact("WindowBand1", SafetyRating.WindowBand1, Num(c["window_band1"]));
        EqExact("WindowBandRatio", SafetyRating.WindowBandRatio, Num(c["window_band_ratio"]));
        EqExact("WindowFactorRookie", SafetyRating.WindowFactorRookie, Num(c["window_factor_rookie"]));
        EqExact("WindowFactorClassD", SafetyRating.WindowFactorClassD, Num(c["window_factor_class_d"]));
        EqExact("MaxAbsSessionDelta", SafetyRating.MaxAbsSessionDelta, Num(c["max_abs_session_delta"]));
        EqExact("SrDisplayMax", SafetyRating.SrDisplayMax, Num(c["sr_display_max"]));
        Eq("BandCrossingBonus", SafetyRating.BandCrossingBonus(), Num(c["band_crossing_bonus"]), SrTol);

        // session weights ---------------------------------------------------
        foreach (var pair in Obj(vectors["session_weights"]))
            EqExact("SessionWeights." + pair.Key, SafetyRating.SessionWeights[pair.Key], Num(pair.Value));
        foreach (var item in Arr(vectors["session_weight_lookups"]))
        {
            var v = Obj(item);
            EqExact("SessionWeight(" + v["session_type"] + ")",
                SafetyRating.SessionWeight((string)v["session_type"]), Num(v["weight"]));
        }

        // licence -----------------------------------------------------------
        foreach (var item in Arr(vectors["license_levels"]))
        {
            var v = Obj(item);
            LicenseClassBand got = SafetyRating.ClassAndBandFromLicenseLevel(Int(v["lic_level"]));
            EqExact("licLevel " + Int(v["lic_level"]) + " class", got.ClassIndex, Num(v["class_index"]));
            EqExact("licLevel " + Int(v["lic_level"]) + " band", got.Band, Num(v["band"]));
        }
        foreach (var item in Arr(vectors["license_strings"]))
        {
            var v = Obj(item);
            EqNullable("licString \"" + v["lic_string"] + "\"",
                SafetyRating.ClassIndexFromString((string)v["lic_string"]), v["class_index"]);
        }
        foreach (var item in Arr(vectors["target_cpi_to_hold_3_00"]))
        {
            var v = Obj(item);
            Eq("TargetCpiToHold(" + Int(v["class_index"]) + ")",
                SafetyRating.TargetCpiToHold(Int(v["class_index"])), Num(v["target_cpi"]), RelTol, true);
        }

        // mapping -----------------------------------------------------------
        foreach (var item in Arr(vectors["band_floors"]))
        {
            var v = Obj(item);
            Eq("BandFloorCpi", SafetyRating.BandFloorCpi(Int(v["class_index"]), Int(v["band"])),
                Num(v["floor_cpi"]), RelTol, true);
        }
        foreach (var item in Arr(vectors["sr_cpi"]))
        {
            var v = Obj(item);
            Eq("CpiFromSr", SafetyRating.CpiFromSr(Num(v["sr"]), Int(v["class_index"]), Int(v["band"])),
                Num(v["cpi"]), RelTol, true);
            Eq("SrFromCpi", SafetyRating.SrFromCpi(Num(v["cpi"]), Int(v["class_index"]), Int(v["band"])),
                Num(v["sr_round_trip"]), SrTol);
        }
        foreach (var item in Arr(vectors["remap"]))
        {
            var v = Obj(item);
            SrProjection got = SafetyRating.RemapBand(Num(v["cpi"]), Int(v["class_index"]), Int(v["band"]));
            Eq("RemapBand sr", got.Sr, Num(v["sr"]), SrTol);
            EqExact("RemapBand band", got.Band, Num(v["final_band"]));
        }

        // rolling average ---------------------------------------------------
        foreach (var item in Arr(vectors["windows"]))
        {
            var v = Obj(item);
            Eq("WindowCorners", SafetyRating.WindowCorners(Int(v["band"]), Int(v["class_index"])),
                Num(v["window"]), RelTol, true);
        }
        foreach (var item in Arr(vectors["alphas"]))
        {
            var v = Obj(item);
            Eq("SessionAlpha", SafetyRating.SessionAlpha(Num(v["corners"]), Int(v["band"]), Int(v["class_index"])),
                Num(v["alpha"]), SrTol);
        }
        foreach (var item in Arr(vectors["update_cpi"]))
        {
            var v = Obj(item);
            Eq("UpdateCpi", SafetyRating.UpdateCpi(Num(v["cpi_old"]), Num(v["corners"]),
                    Num(v["incident_points"]), Int(v["band"]), Int(v["class_index"])),
                Num(v["cpi_new"]), RelTol, true);
        }
        foreach (var item in Arr(vectors["session_cpi"]))
        {
            var v = Obj(item);
            double? got = SafetyRating.SessionCpi(Num(v["corners"]), Num(v["incident_points"]));
            _checks++;
            if (v["cpi"] == null)
            {
                if (got.HasValue) Failures.Add("SessionCpi: expected null, got " + got.Value);
            }
            else if (!got.HasValue) Failures.Add("SessionCpi: expected " + Num(v["cpi"]) + ", got null");
            else Eq("SessionCpi", got.Value, Num(v["cpi"]), RelTol, true);
        }

        // the whole pipeline ------------------------------------------------
        foreach (var item in Arr(vectors["project"]))
        {
            var v = Obj(item);
            SrProjection got = SafetyRating.ProjectSr(Num(v["sr_before"]), Int(v["class_index"]),
                Num(v["corners"]), Num(v["incident_points"]));
            string label = " [" + v["comment"] + "]";
            Eq("ProjectSr sr" + label, got.Sr, Num(v["projected_sr"]), SrTol);
            Eq("ProjectSr delta" + label, got.Delta, Num(v["delta"]), SrTol);
            Eq("ProjectSr cpi" + label, got.Cpi, Num(v["cpi_new"]), RelTol, true);
        }

        // corners by distance -----------------------------------------------
        foreach (var item in Arr(vectors["corner_accumulator"]))
        {
            var v = Obj(item);
            var accumulator = new CornerAccumulator();
            foreach (var sample in Arr(v["samples"]))
            {
                var s = Obj(sample);
                accumulator.Update(Num(s["lap_dist_pct"]), Int(s["track_surface"]), Num(v["corners_per_lap"]));
            }
            Eq("CornerAccumulator [" + v["comment"] + "]", accumulator.Corners, Num(v["corners"]), RelTol, true);
        }

        if (Failures.Count > 0)
        {
            Console.Error.WriteLine("FAILED: " + Failures.Count + " of " + _checks + " checks\n");
            for (int i = 0; i < Math.Min(25, Failures.Count); i++)
                Console.Error.WriteLine("  - " + Failures[i]);
            if (Failures.Count > 25)
                Console.Error.WriteLine("  ... and " + (Failures.Count - 25) + " more");
            return 1;
        }
        Console.WriteLine("OK: all " + _checks + " checks passed against vectors.json");
        return 0;
    }
}
