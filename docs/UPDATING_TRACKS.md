# Keeping the track list current

`corners_per_lap` is the one input the SR model cannot derive: it comes from
iRacing. When a season adds tracks or reworks a layout, the shipped table needs
a refresh.

**Nothing breaks while it is stale.** That is deliberate, and it is what makes a
manual refresh acceptable rather than urgent:

| Consumer | Behaviour with an unknown track |
|---|---|
| **Overlay** | Learns it from the simulator automatically — `TrackNumTurns` in the session YAML *is* `corners_per_lap`, verified against every track where both were observable. A brand-new layout is scored correctly the first time it is driven. |
| **Web calculator** | The corners field is editable; type the number and it works. The page shows when its table was last refreshed. |

So the refresh below is housekeeping, not a fire drill.

---

## Why this is not a cron job

The endpoint that owns this table is authenticated. Since December 2025 iRacing
requires OAuth for the `/data` API and has paused new client registrations, so
there is no credential a CI job could legitimately hold. Anything claiming to
automate it would either be storing your iRacing password in a secret store or
quietly breaking the first time 2FA is enforced.

The honest design is: one click from a browser you are *already* logged into,
then a script does the rest.

---

## Refreshing it

**1. Export.** Log in to iRacing in a browser, open the developer console
(<kbd>F12</kbd>), and paste:

```js
(async () => {
  const r = await fetch('/bff/pub/proxy/data/track/get', {credentials:'include'});
  const j = await r.json();
  const data = j.link ? await (await fetch(j.link)).json() : j;
  const blob = new Blob([JSON.stringify(data)], {type:'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'iracing_tracks_export.json';
  document.body.appendChild(a); a.click(); a.remove();
  console.log('exported', (data.tracks || data).length, 'tracks');
})();
```

**2. Regenerate.**

```bash
python tools/update_tracks.py ~/Downloads/iracing_tracks_export.json
```

It prints a reviewable diff — tracks added, removed, or with a changed corner
count — and rewrites both `tracks/official_corners.json` (overlay) and
`docs/tracks.json` (website) from the same source, so the two can never drift
apart.

**3. Commit** if the diff looks right.

---

## Checking whether it is stale

```bash
python tools/update_tracks.py --check
```

is not needed — the CI workflow in `.github/workflows/track-table-age.yml` runs
monthly and opens an issue once the table passes 120 days old. That way the
reminder arrives on its own instead of depending on anyone remembering.
