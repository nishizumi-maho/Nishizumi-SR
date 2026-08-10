"""Refresh the official corners-per-lap table after an iRacing season update.

    python tools/update_tracks.py path/to/iracing_tracks_export.json

Regenerates both copies that ship with the project:

    tracks/official_corners.json   the overlay reads this
    docs/tracks.json               the web calculator reads this

and prints a diff so a season update is reviewable before it is committed.

WHY THIS IS NOT FULLY AUTOMATIC
The `/data` endpoint that owns this table is authenticated. Since December 2025
iRacing has required OAuth for it and has paused new client registrations, so
there is no credential a CI job could legitimately hold. The practical path is
therefore: a human who is already logged in exports the list in one click, and
this script does everything else.

Neither consumer actually breaks while the table is stale, which is what makes
the manual step acceptable:

* The overlay learns any unknown track from the simulator itself. `TrackNumTurns`
  in the session YAML matches `corners_per_lap` exactly, verified against every
  track where both values were available, so a brand-new layout is scored
  correctly the first time it is driven.
* The web calculator lets you type the corner count in by hand, and says when
  its table was last refreshed.

GETTING THE EXPORT
Open iRacing in a logged-in browser, open the developer console, and run the
snippet in docs/UPDATING_TRACKS.md. It downloads the JSON this script expects.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = [ROOT / "tracks" / "official_corners.json", ROOT / "docs" / "tracks.json"]

# Layouts iRacing keeps listed but that can no longer be raced.
RETIRED_PREFIX = "[Retired]"


def normalise(raw) -> list[dict]:
    """Accept either the raw /data/track/get array or an already-wrapped file."""
    rows = raw.get("tracks", raw) if isinstance(raw, dict) else raw
    out = []
    for t in rows:
        cpl = t.get("corners_per_lap")
        name = (t.get("track_name") or "").strip()
        if not cpl or not name or name.startswith(RETIRED_PREFIX):
            continue
        out.append({
            "track_id": int(t["track_id"]),
            "corners_per_lap": int(cpl),
            "category": t.get("category", "road"),
            "track_name": name,
            "config_name": (t.get("config_name") or "").strip(),
            "length_km": t.get("track_config_length"),
        })
    out.sort(key=lambda x: x["track_id"])
    return out


def load_existing(path: Path) -> dict[int, dict]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {int(t["track_id"]): t for t in raw.get("tracks", [])}


def report(old: dict[int, dict], new: list[dict]) -> bool:
    new_by_id = {t["track_id"]: t for t in new}
    added = [t for i, t in new_by_id.items() if i not in old]
    removed = [t for i, t in old.items() if i not in new_by_id]
    changed = [
        (old[i], t) for i, t in new_by_id.items()
        if i in old and old[i]["corners_per_lap"] != t["corners_per_lap"]
    ]

    def label(t):
        return f"{t['track_name']}{' - ' + t['config_name'] if t['config_name'] else ''}"

    print(f"  existing {len(old)} tracks -> incoming {len(new)}")
    for t in added:
        print(f"  + {t['track_id']:>4}  {label(t):<58} {t['corners_per_lap']:>4} corners")
    for t in removed:
        print(f"  - {t['track_id']:>4}  {label(t):<58} (gone from the export)")
    for a, b in changed:
        print(f"  ~ {b['track_id']:>4}  {label(b):<58} "
              f"{a['corners_per_lap']} -> {b['corners_per_lap']} corners")
    if not (added or removed or changed):
        print("  no changes")
    return bool(added or removed or changed)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1])
    if not src.exists():
        print(f"export not found: {src}")
        return 1

    tracks = normalise(json.loads(src.read_text(encoding="utf-8")))
    if len(tracks) < 100:
        print(f"refusing to write: only {len(tracks)} tracks parsed, the export looks wrong")
        return 1

    changed = report(load_existing(TARGETS[0]), tracks)

    payload = {
        "_meta": {
            "source": "iRacing /data API - track/get endpoint",
            "captured_utc": date.today().isoformat(),
            "note": "Official iRacing corners_per_lap. Key = track_id "
                    "(same TrackID the SDK exposes).",
            "excluded": "tracks prefixed [Retired] (no longer raced)",
        },
        "tracks": tracks,
    }
    body = json.dumps(payload, indent=1, ensure_ascii=False)
    for path in TARGETS:
        path.write_text(body, encoding="utf-8")
        print(f"  wrote {path.relative_to(ROOT)}")

    print("\n  done." + ("  Review the diff above, then commit." if changed else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
