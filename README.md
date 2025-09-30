# WSCL Road Warrior — Distance & Time Tracker

A tiny data pipeline + static web app that celebrates Washington Student Cycling League teams that go the extra mile. It geocodes teams and race venues, uses the Google Distance Matrix API to compute **round-trip miles and drive time** for every team→venue pair, aggregates by season and venue, then renders a fun, filterable leaderboard.

---

## What’s in this repo

```

.
├── calculator.py            # Python ETL: geocode, distance matrix, aggregates → JSON
├── wscl_teams_from_map.csv  # Team names + city/state/zip (input)
├── wscl_races_2023_2025.csv # Race “events” (year/month/day + city/venue) (input)
├── bike_league_data.json    # Output data consumed by the web app (generated)
└── webapp-manager.html      # Tailwind-powered static UI & leaderboards

````

---

## How it works

1. **Load teams** from `wscl_teams_from_map.csv`. Cleans team/city strings, geocodes by ZIP or city (constrained to WA/ID), caches results.
2. **Load races** from `wscl_races_2023_2025.csv`. Splits into:
   - **Venues**: unique `location_key` like `"Lakewood, Fort Steilacoom"`.
   - **Events**: dated occurrences (year/month/day/season/date + `location_key`).
3. **Compute distances & times** with the Google Distance Matrix API for every team × venue pair. Stores **round-trip miles and hours**.
4. **Aggregate**:
   - Per-team totals across all events and by season (`Spring`, `Fall`, `Other`).
   - Per-venue totals across the league (overall and by season).
5. **Write JSON** (`bike_league_data.json`) with teams, venues, events, distances, aggregates, and metadata.
6. **Visualize** with `webapp-manager.html`: fetches the JSON, displays fun stat cards, and supports filters for overall, season, race, or team views.

---

## Data model (output)

The generated `bike_league_data.json` includes:

- **events**: dated race occurrences with `{year, month, day, season, date, location_key}`.
- **distances** (per team):
  - `venues[location_key] = { miles, hours }` (round-trip).
  - `total_miles_events`, `total_hours_events`.
  - `season_totals = { Spring, Fall, Other }` each with `{ miles, hours }`.
- **aggregates**:
  - `venue_event_counts` (+ per season).
  - `venue_total_miles` / `venue_total_hours` (+ per season).

---

## Prerequisites

- **Python 3.9+**
- **requests** library
- **Google Maps API key** with Geocoding + Distance Matrix enabled

---

## Setup & Running

1. Install deps:
   ```bash
   pip install requests
```'

2. Configure your API key:
   Edit the top of `calculator.py` and set `GOOGLE_API_KEY`.

3. Prepare input CSVs:

   * `wscl_teams_from_map.csv` — teams, cities, zips.
   * `wscl_races_2023_2025.csv` — races for 2023–2025.

4. Run the ETL:

   ```bash
   python3 calculator.py
   ```

   On success you’ll see a summary and `bike_league_data.json` will be written.

5. Open the UI:
   Place `webapp-manager.html` alongside `bike_league_data.json` and open it in your browser (via a local server, e.g. `python3 -m http.server`).

---

## Features (UI)

* **Road Warrior Stats**: top/bottom teams, longest trek, average miles.
* **Leaderboard**: ranks all teams by total miles (with medals 🥇🥈🥉).
* **Season view**: compare totals across Spring/Fall seasons.
* **Race view**: see travel to a selected venue.
* **Team view**: drill into one team’s per-event trips.

---

## Implementation notes

* **Caching**: geocoding and distance calls cached in-memory to reduce API calls.
* **Round-trip units**: miles/hours doubled from one-way results.
* **Season classification**: derived from event month (`Spring` = Mar–Jun, `Fall` = Sep–Nov).
* **Aggregates**: JSON includes league-wide totals to make venue/season rollups instant.

---

## Extending

* Add cost/CO₂ calculators (miles → $ and kg CO₂).
* Show maps (pins for venues).
* Add CSV export of leaderboards.
* Persist caches for repeat runs.

---

## License

MIT — use, remix, improve.

---

## Acknowledgments

Thanks to the Washington Student Cycling League. Team and race data come from their public site.


