#!/usr/bin/env python3
"""
Fetch upcoming NYC Subway arrivals (grouped) and PATH Newark trains at WTC.

Uses the MTA GTFS-realtime feeds (no API key required for subway) and the
official PANYNJ PATH JSON feed.

    python mta_fulton.py
"""

import time
from collections import defaultdict

import requests
from google.transit import gtfs_realtime_pb2

PATH_URL = "https://www.panynj.gov/bin/portauthority/ridepath.json"

# Feeds (%2F must stay encoded in URLs)
FEEDS = {
    "A/C": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-ace",
    "2/3/4/5": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs",
    "J/Z": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-jz",
    "N/R/W": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-nqrw",
}

GROUP_ORDER = ["A/C", "2/3", "4/5", "J", "W/R"]

ROUTE_GROUPS = {
    "A/C": ("A", "C"),
    "2/3": ("2", "3"),
    "4/5": ("4", "5"),
    "J": ("J",),
    "W/R": ("N", "R", "W"),
}

GROUP_COLORS = {
    "A/C": "0039A6",
    "2/3": "EE352E",
    "4/5": "00933C",
    "J": "996633",
    "W/R": "FCCC0A",
    "PATH": "D93A30",
}

# Per-route colors (legacy / debugging)
ROUTE_COLORS = {
    "2": "EE352E",
    "3": "EE352E",
    "4": "00933C",
    "5": "00933C",
    "A": "0039A6",
    "C": "0039A6",
    "J": "996633",
    "N": "FCCC0A",
    "R": "FCCC0A",
    "W": "FCCC0A",
}


def should_include(stop_id, route_id):
    """
    Filter rules:
      - Fulton (A38/229/418): uptown only (stop suffix N)
      - Fulton J (M22): J trains toward Brooklyn/Queens only (suffix S)
      - WTC Cortlandt (R25): N, R, W uptown from World Trade Center (suffix N)
    """
    if stop_id.startswith("M22"):
        return route_id == "J" and stop_id.endswith("S")

    if stop_id.startswith(("A38", "229", "418")):
        return stop_id.endswith("N") and route_id in {"2", "3", "4", "5", "A", "C"}

    if stop_id.startswith("R25"):
        return stop_id.endswith("N") and route_id in {"N", "R", "W"}

    return False


def fetch_feed(url):
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(r.content)
    return feed


def fetch_route_arrivals():
    """
    Return {route_id: [minutes, ...]} sorted soonest-first.

    Fulton St: uptown 2/3/4/5/A/C; J to Brooklyn/Queens.
    WTC Cortlandt St: uptown N/R/W.
    """
    now = int(time.time())
    seen = set()
    route_minutes = defaultdict(list)

    for url in FEEDS.values():
        feed = fetch_feed(url)
        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue
            trip = entity.trip_update.trip
            route_id = trip.route_id
            trip_id = trip.trip_id or entity.id

            for stu in entity.trip_update.stop_time_update:
                if not should_include(stu.stop_id, route_id):
                    continue

                arrival_ts = None
                if stu.HasField("arrival") and stu.arrival.time:
                    arrival_ts = stu.arrival.time
                elif stu.HasField("departure") and stu.departure.time:
                    arrival_ts = stu.departure.time
                if arrival_ts is None:
                    continue

                minutes = max(0, (arrival_ts - now) // 60)
                dedupe_key = (route_id, trip_id, minutes)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                route_minutes[route_id].append(minutes)

    for route in route_minutes:
        route_minutes[route].sort()

    return dict(route_minutes)


def group_arrivals(route_minutes):
    """
    Merge per-route arrivals into display groups (A/C, 2/3, 4/5, J, W/R).

    Each group is a list of (route_id, minutes) tuples sorted soonest-first,
    e.g. {"A/C": [("A", 0), ("C", 4), ("A", 1), ...]}.
    """
    grouped = {}
    for label in GROUP_ORDER:
        arrivals = []
        for route_id in ROUTE_GROUPS[label]:
            for minutes in route_minutes.get(route_id, []):
                arrivals.append((route_id, minutes))
        if arrivals:
            grouped[label] = sorted(arrivals, key=lambda t: t[1])
    return grouped


def fetch_grouped_arrivals():
    """Return {group_label: [(route_id, minutes), ...]} for MTA lines."""
    return group_arrivals(fetch_route_arrivals())


def fetch_fulton_arrivals():
    """Backward-compatible alias for fetch_route_arrivals()."""
    return fetch_route_arrivals()


def fetch_path_wtc_newark():
    """
    PATH trains at World Trade Center bound for Newark (ToNJ).

    Returns (route_trains, route_colors) in the same shape as path_sign_wide:
      route_trains: {"Newark": [(train_message, minutes), ...]}
      route_colors: {"Newark": "D93A30"}
    """
    data = requests.get(PATH_URL, timeout=10).json()

    station = next(s for s in data["results"] if s["consideredStation"] == "WTC")
    destination = next(d for d in station["destinations"] if d["label"] == "ToNJ")

    route_trains = defaultdict(list)
    for train in destination["messages"]:
        if train["headSign"] == "Newark":
            route_trains["Newark"].append(
                (train, int(train["secondsToArrival"]) // 60)
            )
    for route in route_trains:
        route_trains[route].sort(key=lambda t: int(t[0]["secondsToArrival"]))

    route_colors = {}
    for train, _ in route_trains.get("Newark", []):
        route_colors["Newark"] = train["lineColor"]
        break

    return dict(route_trains), route_colors


def fetch_path_wtc_newark_minutes():
    """Return [minutes, ...] for Newark-bound PATH trains at WTC."""
    route_trains, _ = fetch_path_wtc_newark()
    return [minutes for _, minutes in route_trains.get("Newark", [])]


def main():
    grouped = fetch_grouped_arrivals()
    path_trains, path_colors = fetch_path_wtc_newark()
    path_minutes = [m for _, m in path_trains.get("Newark", [])]

    if not grouped and not path_minutes:
        print("No upcoming arrivals found.")
        return

    print("Fulton St (uptown) + WTC (N/R/W uptown)\n")
    for label in GROUP_ORDER:
        if label in grouped:
            times = ", ".join(f"{route} {m} min" for route, m in grouped[label])
            print(f"  {label}: {times}")

    if path_minutes:
        times = ", ".join(f"{m} min" for m in path_minutes)
        color = path_colors.get("Newark", GROUP_COLORS["PATH"])
        print(f"\nPATH WTC → Newark ({color})\n  {times}")


if __name__ == "__main__":
    main()
