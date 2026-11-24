# Valorant Split scout

This CLI uses local GRID compressed JSONL feeds to locate DGZ's recurring early damage-contact area on Split, then scores the Sentinels match against that fixed area.

## What Junie built

Junie built the GRID analysis CLI, a Streamlit dashboard with a reference-only 15 m area and reveal-gated Sentinels results, a validated demo-feed downloader, and regression tests. The MIT license applies to the project's original code; third-party GRID feeds and the optional Split minimap are separately sourced and are not covered by it.

## Run

Install the dependency and run from the folder containing the feeds:

```powershell
python -m pip install -r requirements.txt
python scout.py
```

## Dashboard

Fetch the three pinned GRID demo feeds into the current folder, then start the one-screen dashboard:

```powershell
python -m pip install -r requirements.txt
python fetch_demo_data.py
python -m streamlit run dashboard.py
```

`fetch_demo_data.py` downloads `events_2748743_grid.jsonl.zst`, `events_2748747_grid.jsonl.zst`, and `events_2748757_grid.jsonl.zst` from the [Cloud9 GRID starter repository](https://github.com/cloud9esports/stl-hackathon-2026-grid-starter), pinned to commit `af2355877299e99b27e247951415e6fb5a40fc82`. It verifies each file against the pinned Git blob SHA-1 and size, reuses valid local copies without network access, and replaces missing or invalid copies only after a complete download passes validation. The dashboard lists any missing required feed and displays the fetch command; the downloaded feeds remain local and are ignored by Git.

Initially, it shows only the EG / KRÜ Split damage-contact evidence and the locked 15 m circle. Press **Reveal Sentinels match** to show the eligible-round headline and Sentinels results for rounds 5–10; selecting a round such as **Round 9** highlights its contact on the same plot and shows its timestamp, DGZ position, and time after barrier drop. Sentinels data is only scored after the reference area is fitted and never affects its center.

The plot optionally uses `split_minimap.png`, Split's `displayIcon` from the [Valorant API map metadata](https://valorant-api.com/v1/maps/d960549e-485c-e861-8d71-aa9d1aed12a2). If the image is absent, the dashboard still shows the evidence, locked 15 m circle, reveal button, and selected Sentinels contact on a coordinate plot. GRID coordinates use the same projection in either view: `u = 0.842188 + y * 0.000078` and `v = 0.697578 - x * 0.000078`; the fitted circle is transformed without clipping and can extend beyond the image edges.

The default input is every `events_*_grid.jsonl.zst` file in the current folder. You can also pass explicit feed paths:

```powershell
python scout.py events_2748743_grid.jsonl.zst events_2748747_grid.jsonl.zst events_2748757_grid.jsonl.zst
```

The CLI fits a 15-meter radius (1,500 map-unit) circle to eligible reference contacts and does not use Sentinels data when choosing its center. `--radius-m` and `--window-s` can change the radius and early-contact window.

## Scoring definition

- The reference set is Split rounds against Evil Geniuses or KRÜ where DGZ is defending and has an Operator in his game-level inventory at `round-ended-freezetime` (barrier drop).
- First contact means DGZ's earliest enemy `player-damaged-player` event at or after barrier drop and no more than 20 seconds later, whether DGZ deals or receives the damage. DGZ's position in that event is the contact point.
- The CLI finds a maximum-coverage circle of the requested radius among reference points only. Ties use the lexicographically smallest rounded center `(x, y)` in map units.
- Sentinels rounds are reported individually. An eligible round is a Split round where DGZ defends with an Operator at barrier drop. A located early contact within the fixed circle is a hit; one outside is a miss. No observed damage contact and contacts lacking coordinates are listed separately, not counted as misses.
- Hit rate uses only eligible rounds with a located early contact. Round winners and non-eligible Sentinels rounds are still shown for context.

GRID position coordinates are reported in map units; this analysis uses 100 map units per meter. Damage events are only an observable proxy for contact: the feed cannot establish visual encounters that caused no damage. The compressed source feeds are ignored by Git via `*.jsonl.zst` and should remain local.