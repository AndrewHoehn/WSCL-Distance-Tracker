#!/usr/bin/env python3
"""
Youth Bike League Distance Calculator (Venues/Events + Time)
- Geocodes teams (ZIP→City fallback) and unique venues (once)
- Infers season from month (Mar–Jun = Spring, Sep–Nov = Fall)
- Computes round-trip miles AND hours per Team × Venue (once)
- Builds dated Events referencing Venues
- Precomputes:
    • Per-team totals (overall + by season) [miles, hours]
    • Per-venue totals (overall + by season) [miles, hours]
- Fixes Ephrata '(local venue)' to geocode in Ephrata (not Spokane)
- Cleans duplicated team/city strings like "Anacortes Composite  Anacortes"
"""

import os, csv, json, time, argparse, re
from typing import Dict, List, Tuple, Optional
from collections import Counter, defaultdict
import requests

# ────────────────────────────────────────────────────────────────────────────────
# Configuration
# ────────────────────────────────────────────────────────────────────────────────

GOOGLE_API_KEY = "API_KEY_HERE"
TEAMS_CSV = "wscl_teams_from_map.csv"  # Team,City,State,Zip
RACES_CSV = "wscl_races_2023_2025.csv"  # Year,Date,City,Venue  (Season optional)
OUTPUT_JSON = "bike_league_data.json"

# WA/ID-ish bounds to catch wayward geocodes
LAT_MIN, LAT_MAX = 45.0, 49.5
LNG_MIN, LNG_MAX = -125.0, -116.0

GEOCODE_DELAY_SEC = 0.10
DISTANCE_DELAY_SEC = 0.10

# Venue normalization
LOCATION_STANDARDIZATION = {
    "360 Trails": "360 Trails",
    "360 Trails Park": "360 Trails",
    "360 Trails (State Championship and Relay)": "360 Trails",
    "Squilchuck (Race + Camping)": "Squilchuck State Park",
    "Squilchuck (State Park)": "Squilchuck State Park",
    "(local venue)": "Riverside State Park",  # generic default; refined per-city below
    "Riverside (State Park)": "Riverside State Park",
    "Liberty Bell High School": "Liberty Bell High School",
    "Roslyn High School": "Roslyn High School",
    "Cle Elum-Roslyn High School": "Roslyn High School",
}

# City-specific overrides for '(local venue)'
CITY_LOCAL_VENUE_OVERRIDES = {
    "Spokane": "Riverside State Park",
    "Winthrop": "Liberty Bell High School",
    # --- Ephrata fix ---
    # If we don't know the exact venue, geocode the city center (better than Spokane!)
    "Ephrata": "Ephrata, WA",
    # Add more city→venue mappings as needed:
    # "Leavenworth": "Ski Hill",
}

# ────────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────────


def infer_season(month: int) -> str:
    if 3 <= month <= 6:
        return "Spring"
    if 9 <= month <= 11:
        return "Fall"
    return "Other"


def parse_mm_dd(date_str: str) -> Tuple[int, int]:
    m, d = date_str.split("/")
    return int(m), int(d)


def standardize_venue_name(city: str, venue: str) -> str:
    venue = venue.strip()
    if venue == "(local venue)":
        # City-specific fixes first
        for k, v in CITY_LOCAL_VENUE_OVERRIDES.items():
            if k.lower() in city.lower():
                return v
    # Generic normalization
    return LOCATION_STANDARDIZATION.get(venue, venue)


def clean_city_field(team_name: str, city: str) -> str:
    """Remove team name from city field if it was accidentally concatenated."""
    if not city:
        return city
    c = city
    # Remove the team name (case-insensitive) if it's embedded in 'city'
    pattern = re.escape(team_name)
    c = re.sub(pattern, "", c, flags=re.IGNORECASE).strip()
    # Collapse multiple spaces
    c = re.sub(r"\s{2,}", " ", c)
    # If we nuked everything, leave original city
    return c or city


# ────────────────────────────────────────────────────────────────────────────────
# Core
# ────────────────────────────────────────────────────────────────────────────────


class DistanceCalculator:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.geocode_cache: Dict[str, Optional[Dict]] = {}
        self.distance_cache: Dict[str, Tuple[float, float]] = {}  # miles, hours

    # ---------- Google APIs ----------

    def geocode_location(self, query: str, force_state: str = "WA") -> Optional[Dict]:
        cache_key = f"{query}|{force_state}"
        if cache_key in self.geocode_cache:
            return self.geocode_cache[cache_key]

        # If query already includes ', WA' or similar, don't double-append state
        full_query = (
            query
            if re.search(r",\s*[A-Z]{2}\b", query)
            else f"{query}, {force_state}, USA"
        )

        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"address": full_query, "key": self.api_key}

        print(f"Geocoding: {full_query}")
        r = requests.get(url, params=params, timeout=30)
        data = r.json()

        if data.get("status") != "OK" or not data.get("results"):
            print(f"  ⚠️  Geocoding failed: {full_query} -> {data.get('status')}")
            self.geocode_cache[cache_key] = None
            return None

        res = data["results"][0]
        lat = res["geometry"]["location"]["lat"]
        lng = res["geometry"]["location"]["lng"]
        formatted = res["formatted_address"]

        if not (LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX):
            print(f"  ⚠️  Outside WA/ID bounds: {formatted} ({lat:.4f},{lng:.4f})")
            self.geocode_cache[cache_key] = None
            return None

        geocoded = {
            "query": query,
            "formatted_address": formatted,
            "lat": lat,
            "lng": lng,
        }
        self.geocode_cache[cache_key] = geocoded
        time.sleep(GEOCODE_DELAY_SEC)
        print(f"  ✓ {formatted} ({lat:.4f}, {lng:.4f})")
        return geocoded

    def distance_and_time(self, origin: Dict, destination: Dict) -> Tuple[float, float]:
        """
        Return one-way (miles, hours) between two geocoded points via Distance Matrix.
        """
        cache_key = f"{origin['lat']:.6f},{origin['lng']:.6f}|{destination['lat']:.6f},{destination['lng']:.6f}"
        if cache_key in self.distance_cache:
            return self.distance_cache[cache_key]

        url = "https://maps.googleapis.com/maps/api/distancematrix/json"
        params = {
            "origins": f"{origin['lat']},{origin['lng']}",
            "destinations": f"{destination['lat']},{destination['lng']}",
            "units": "imperial",
            "key": self.api_key,
        }

        olabel = origin.get("query") or origin.get("formatted_address", "origin")
        dlabel = destination.get("query") or destination.get(
            "formatted_address", "dest"
        )
        print(f"Distance: {olabel} → {dlabel}")

        r = requests.get(url, params=params, timeout=30)
        data = r.json()

        if data.get("status") != "OK":
            print(f"  ⚠️  Matrix error: {data.get('status')}")
            self.distance_cache[cache_key] = (0.0, 0.0)
            return 0.0, 0.0

        element = data["rows"][0]["elements"][0]
        if element.get("status") != "OK":
            print(f"  ⚠️  No route found ({element.get('status')})")
            self.distance_cache[cache_key] = (0.0, 0.0)
            return 0.0, 0.0

        miles = element["distance"]["value"] / 1609.34
        hours = element["duration"]["value"] / 3600
        print(f"  ✓ {miles:.1f} miles ({hours:.1f} h)")

        self.distance_cache[cache_key] = (miles, hours)
        time.sleep(DISTANCE_DELAY_SEC)
        return miles, hours

    # ---------- Pipeline steps ----------

    def process_teams(self, csv_path: str) -> List[Dict]:
        print("\n" + "=" * 60)
        print("PROCESSING TEAMS")
        print("=" * 60)
        teams: List[Dict] = []
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                team = (row.get("Team") or "").strip()
                city = (row.get("City") or "").strip()
                state = (row.get("State") or "WA").strip()
                zipc = (row.get("Zip") or "").strip()

                if not team:
                    continue

                # Clean duplicated "Team TeamCity" style strings in city
                city = clean_city_field(team, city)

                geocoded = None
                if zipc:
                    geocoded = self.geocode_location(zipc, force_state=state)
                if not geocoded and city:
                    geocoded = self.geocode_location(city, force_state=state)

                if geocoded:
                    teams.append(
                        {
                            "name": team,
                            "city": city,
                            "state": state,
                            "zip": zipc,
                            "geocoded": geocoded,
                        }
                    )
                else:
                    print(f"  ⚠️  SKIP TEAM (no geocode): {team} ({city}, {state})")

        print(f"\n✓ Geocoded teams: {len(teams)}")
        return teams

    def process_races(self, csv_path: str) -> Tuple[List[Dict], List[Dict]]:
        print("\n" + "=" * 60)
        print("PROCESSING RACES → VENUES & EVENTS")
        print("=" * 60)
        venues: Dict[str, Dict] = {}
        events: List[Dict] = []

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                city = (row.get("City") or "").strip()
                venue_in = (row.get("Venue") or "").strip()
                date = (row.get("Date") or "").strip()  # "4/13"
                year = (row.get("Year") or "").strip()

                if not (city and venue_in and date and year):
                    continue

                venue = standardize_venue_name(city, venue_in)
                month, day = parse_mm_dd(date)
                season = infer_season(month)

                location_key = f"{city}, {venue}"

                # Geocode each unique venue once
                if location_key not in venues:
                    query = f"{venue}, {city}"
                    geo = self.geocode_location(query, force_state="WA")
                    if not geo:
                        print(f"  ⚠️  SKIP VENUE (no geocode): {location_key}")
                        continue
                    venues[location_key] = {
                        "location_key": location_key,
                        "city": city,
                        "venue": venue,
                        "geocoded": geo,
                    }

                safe_venue = re.sub(r"\s+", "_", venue)
                event_id = f"{year}-{month:02d}-{day:02d}_{city}_{safe_venue}"
                events.append(
                    {
                        "event_id": event_id,
                        "year": year,
                        "month": month,
                        "day": day,
                        "season": season,
                        "date": date,
                        "location_key": location_key,
                    }
                )

        print(f"\n✓ Unique venues: {len(venues)}   ✓ Events: {len(events)}")
        return list(venues.values()), events

    def compute_team_venue_distances(
        self, teams: List[Dict], venues: List[Dict]
    ) -> Dict:
        print("\n" + "=" * 60)
        print("CALCULATING DISTANCES (Team × Venue, RT miles & hours)")
        print("=" * 60)
        distances: Dict[str, Dict] = {}
        total_pairs = len(teams) * len(venues)
        i = 0
        for t in teams:
            tname = t["name"]
            distances[tname] = {
                "venues": {},  # {location_key: {"miles": x, "hours": y}}
                "total_miles_events": 0.0,  # filled later
                "total_hours_events": 0.0,  # filled later
                "season_totals": {  # filled later
                    "Spring": {"miles": 0.0, "hours": 0.0},
                    "Fall": {"miles": 0.0, "hours": 0.0},
                    "Other": {"miles": 0.0, "hours": 0.0},
                },
            }
            for v in venues:
                i += 1
                print(f"\n[{i}/{total_pairs}] {tname} → {v['location_key']}")
                one_way_miles, one_way_hours = self.distance_and_time(
                    t["geocoded"], v["geocoded"]
                )
                rt_miles = round(one_way_miles * 2, 1)
                rt_hours = round(one_way_hours * 2, 2)
                if one_way_miles > 500:
                    print(f"  ⚠️  Very long one-way ({one_way_miles:.1f} mi)")
                distances[tname]["venues"][v["location_key"]] = {
                    "miles": rt_miles,
                    "hours": rt_hours,
                }
        return distances


# ────────────────────────────────────────────────────────────────────────────────
# Aggregation
# ────────────────────────────────────────────────────────────────────────────────


def derive_aggregates(distances: Dict, events: List[Dict]) -> Dict:
    # Event counts per venue overall & per-season
    venue_event_counts = Counter(e["location_key"] for e in events)
    venue_event_counts_by_season: Dict[str, Counter] = defaultdict(Counter)
    for e in events:
        venue_event_counts_by_season[e["season"]][e["location_key"]] += 1

    # Initialize venue totals (miles/hours)
    venue_total_miles = defaultdict(float)
    venue_total_hours = defaultdict(float)
    venue_total_miles_by_season: Dict[str, Dict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    venue_total_hours_by_season: Dict[str, Dict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )

    # Per-team totals (overall + season) by summing each event's venue distance
    for team, tdata in distances.items():
        total_miles = total_hours = 0.0
        # reset season totals
        for s in tdata["season_totals"].keys():
            tdata["season_totals"][s]["miles"] = 0.0
            tdata["season_totals"][s]["hours"] = 0.0

        for e in events:
            vkey = e["location_key"]
            rt = tdata["venues"].get(vkey, {"miles": 0.0, "hours": 0.0})
            total_miles += rt["miles"]
            total_hours += rt["hours"]
            tdata["season_totals"][e["season"]]["miles"] += rt["miles"]
            tdata["season_totals"][e["season"]]["hours"] += rt["hours"]

        tdata["total_miles_events"] = round(total_miles, 1)
        tdata["total_hours_events"] = round(total_hours, 2)
        for s, vals in tdata["season_totals"].items():
            vals["miles"] = round(vals["miles"], 1)
            vals["hours"] = round(vals["hours"], 2)

    # Venue totals across all teams, weighted by number of events at each venue
    # Explanation: for each venue v, each team travels RT miles/time once per event held there.
    for vkey, count in venue_event_counts.items():
        for team, tdata in distances.items():
            rt = tdata["venues"].get(vkey, {"miles": 0.0, "hours": 0.0})
            venue_total_miles[vkey] += rt["miles"] * count
            venue_total_hours[vkey] += rt["hours"] * count

    # Seasonal venue totals
    for season, counts in venue_event_counts_by_season.items():
        for vkey, count in counts.items():
            for team, tdata in distances.items():
                rt = tdata["venues"].get(vkey, {"miles": 0.0, "hours": 0.0})
                venue_total_miles_by_season[season][vkey] += rt["miles"] * count
                venue_total_hours_by_season[season][vkey] += rt["hours"] * count

    # Round
    venue_total_miles = {k: round(v, 1) for k, v in venue_total_miles.items()}
    venue_total_hours = {k: round(v, 2) for k, v in venue_total_hours.items()}
    venue_total_miles_by_season = {
        s: {k: round(v, 1) for k, v in d.items()}
        for s, d in venue_total_miles_by_season.items()
    }
    venue_total_hours_by_season = {
        s: {k: round(v, 2) for k, v in d.items()}
        for s, d in venue_total_hours_by_season.items()
    }

    return {
        "venue_event_counts": dict(venue_event_counts),
        "venue_event_counts_by_season": {
            s: dict(c) for s, c in venue_event_counts_by_season.items()
        },
        "venue_total_miles": venue_total_miles,
        "venue_total_hours": venue_total_hours,
        "venue_total_miles_by_season": venue_total_miles_by_season,
        "venue_total_hours_by_season": venue_total_hours_by_season,
    }


# ────────────────────────────────────────────────────────────────────────────────
# Orchestration
# ────────────────────────────────────────────────────────────────────────────────


def main():
    if not GOOGLE_API_KEY:
        print("ERROR: Please set GOOGLE_API_KEY (env var) or hardcode it at top.")
        return

    calc = DistanceCalculator(GOOGLE_API_KEY)

    teams = calc.process_teams(TEAMS_CSV)
    if not teams:
        print("ERROR: No teams loaded")
        return

    venues, events = calc.process_races(RACES_CSV)
    if not venues or not events:
        print("ERROR: No venues/events loaded")
        return

    distances = calc.compute_team_venue_distances(teams, venues)
    aggregates = derive_aggregates(distances, events)

    data = {
        "teams": teams,
        "venues": venues,
        "events": events,
        "distances": distances,  # team -> {venues{...}, total_*_events, season_totals}
        "aggregates": aggregates,  # venue totals & counts (overall + by season)
        "metadata": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_teams": len(teams),
            "total_venues": len(venues),
            "total_events": len(events),
        },
    }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    # Console summary
    print(f"\n✓ Saved: {OUTPUT_JSON}")
    print(f"Teams: {len(teams)} | Venues: {len(venues)} | Events: {len(events)}")

    # Top venues (miles)
    top_v = sorted(
        aggregates["venue_total_miles"].items(), key=lambda kv: kv[1], reverse=True
    )[:5]
    print("\nTop Venues by Total Miles:")
    for i, (vkey, miles) in enumerate(top_v, 1):
        print(f"  {i}. {vkey}: {miles:.1f} mi")

    # Top teams (miles)
    top_t = sorted(
        ((t, d["total_miles_events"]) for t, d in distances.items()),
        key=lambda kv: kv[1],
        reverse=True,
    )[:5]
    print("\nTop Teams by Total Miles:")
    for i, (tname, miles) in enumerate(top_t, 1):
        print(f"  {i}. {tname}: {miles:.1f} mi")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    args, _ = parser.parse_known_args()
    main()
