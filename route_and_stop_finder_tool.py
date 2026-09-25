#!/usr/bin/env python3
"""
HK Bus Route & Stop Search Utility
==================================
Easily look up stop IDs and directions for KMB, LWB, and Citybus,
and optionally add them directly to your `config.json`.
"""

import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

KMB_BASE = "https://data.etabus.gov.hk/v1/transport/kmb"
CTB_BASE = "https://rt.data.gov.hk/v2/transport/citybus"

# In-memory cache to avoid re-fetching details for repeated stops
STOP_CACHE = {}


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "HKBusHelper/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[Error] Failed to fetch {url}: {e}")
    return None


def search_kmb_route(route_name):
    print(f"\nSearching KMB for route: {route_name.upper()}...")
    url = f"{KMB_BASE}/route/"
    data = fetch_json(url)
    if not data or "data" not in data:
        print("Could not retrieve KMB route database.")
        return []

    matches = [r for r in data["data"] if r.get("route", "").upper() == route_name.upper()]
    if not matches:
        print(f"No KMB route found matching '{route_name}'.")
        return []

    print(f"Found {len(matches)} direction/service variations:")
    for idx, m in enumerate(matches, 1):
        direction = "Outbound (去程)" if m.get("bound") == "O" else "Inbound (回程)"
        service = m.get("service_type")
        orig_tc = m.get("orig_tc", "")
        dest_tc = m.get("dest_tc", "")
        orig_en = m.get("orig_en", "")
        dest_en = m.get("dest_en", "")
        print(f"[{idx}] {m.get('route')} ({direction}, Type {service}): {orig_tc} ({orig_en}) -> {dest_tc} ({dest_en})")

    sel = input("\nSelect variation number (or press Enter to cancel): ").strip()
    if not sel.isdigit() or int(sel) < 1 or int(sel) > len(matches):
        return []

    chosen = matches[int(sel) - 1]
    bound_code = "outbound" if chosen.get("bound") == "O" else "inbound"
    service_type = chosen.get("service_type", "1")

    # Fetch stop sequence for this route
    stop_url = f"{KMB_BASE}/route-stop/{chosen['route']}/{bound_code}/{service_type}"
    stop_data = fetch_json(stop_url)
    if not stop_data or "data" not in stop_data:
        print("Could not fetch stop list for this route.")
        return []

    stops = stop_data["data"]
    print(f"\nFetching stop details for {len(stops)} stops in parallel...")

    def fetch_kmb_stop_info(s):
        try:
            seq = int(s.get("seq", 0))
        except (ValueError, TypeError):
            seq = 0
        s_id = s.get("stop")

        if s_id not in STOP_CACHE:
            s_info = fetch_json(f"{KMB_BASE}/stop/{s_id}")
            if s_info and "data" in s_info:
                STOP_CACHE[s_id] = {
                    "name_tc": s_info["data"].get("name_tc", ""),
                    "name_en": s_info["data"].get("name_en", "")
                }
            else:
                STOP_CACHE[s_id] = {"name_tc": "Unknown", "name_en": "Unknown"}

        info = STOP_CACHE[s_id]
        return {"seq": seq, "stop_id": s_id, "name_tc": info["name_tc"], "name_en": info["name_en"]}

    # Multi-threaded parallel fetching across 16 worker threads
    with ThreadPoolExecutor(max_workers=16) as executor:
        stop_list = list(executor.map(fetch_kmb_stop_info, stops))

    stop_list.sort(key=lambda x: x["seq"])

    print("\n--- Stop Sequence ---")
    for item in stop_list:
        print(f"{item['seq']:2d}. [ID: {item['stop_id']}] {item['name_tc']} ({item['name_en']})")

    stop_sel = input("\nEnter the Sequence Number or Stop ID to track: ").strip()
    selected_stop_id = None
    stop_name = ""
    for item in stop_list:
        if str(item["seq"]) == stop_sel or item["stop_id"] == stop_sel:
            selected_stop_id = item["stop_id"]
            stop_name = f"{item['name_tc']} / {item['name_en']}"
            break

    if not selected_stop_id:
        print("Stop not found.")
        return []

    return [{
        "operator": "KMB",
        "route": chosen["route"],
        "stop_id": selected_stop_id,
        "direction": bound_code,
        "service_type": service_type,
        "custom_title": f"{chosen['route']} @ {stop_name}"
    }]


def search_ctb_route(route_name):
    print(f"\nSearching Citybus (CTB) for route: {route_name.upper()}...")
    url = f"{CTB_BASE}/route/CTB/{route_name.upper()}"
    data = fetch_json(url)
    if not data or "data" not in data:
        print(f"No Citybus route found matching '{route_name}'.")
        return []

    r_info = data["data"]
    print(f"Route {r_info.get('route')}: {r_info.get('orig_tc')} -> {r_info.get('dest_tc')}")

    def fetch_ctb_stop_info(s):
        try:
            seq = int(s.get("seq", 0))
        except (ValueError, TypeError):
            seq = 0
        s_id = s.get("stop")

        if s_id not in STOP_CACHE:
            info = fetch_json(f"{CTB_BASE}/stop/{s_id}")
            if info and "data" in info:
                STOP_CACHE[s_id] = {
                    "name_tc": info["data"].get("name_tc", ""),
                    "name_en": info["data"].get("name_en", "")
                }
            else:
                STOP_CACHE[s_id] = {"name_tc": "", "name_en": ""}

        c_info = STOP_CACHE[s_id]
        return {"seq": seq, "stop_id": s_id, "name_tc": c_info["name_tc"], "name_en": c_info["name_en"]}

    # Fetch stop list in parallel
    for direction in ["outbound", "inbound"]:
        print(f"\n--- {direction.upper()} Stops ---")
        stop_url = f"{CTB_BASE}/route-stop/CTB/{route_name.upper()}/{direction}"
        s_data = fetch_json(stop_url)
        if not s_data or "data" not in s_data:
            continue

        stops = s_data["data"]
        with ThreadPoolExecutor(max_workers=16) as executor:
            stop_list = list(executor.map(fetch_ctb_stop_info, stops))

        stop_list.sort(key=lambda x: x["seq"])
        for item in stop_list:
            print(f"{item['seq']:2d}. [ID: {item['stop_id']}] {item['name_tc']} ({item['name_en']})")

    chosen_stop_id = input("\nEnter Stop ID to track: ").strip()
    if not chosen_stop_id:
        return []

    return [{
        "operator": "CTB",
        "route": route_name.upper(),
        "stop_id": chosen_stop_id,
        "custom_title": f"CTB {route_name.upper()} @ Stop {chosen_stop_id}"
    }]


def append_to_config(new_entry, config_file="config.json"):
    config = {}
    if os.path.exists(config_file):
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)

    if "tracked_stops" not in config:
        config["tracked_stops"] = []

    config["tracked_stops"].append(new_entry)
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
    print(f"\n[Success] Added to {config_file}!")


def delete_stop_from_config(config_file="config.json"):
    """Lists current tracked routes and allows single or bulk deletion."""
    if not os.path.exists(config_file):
        print(f"\n[Error] Config file '{config_file}' not found.")
        return

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception as e:
        print(f"\n[Error] Could not read {config_file}: {e}")
        return

    stops = config.get("tracked_stops", [])
    if not stops:
        print("\nNo tracked stops found in config.json.")
        return

    print("\n========================================")
    print("      Current Tracked Stops in Config")
    print("========================================")
    for idx, s in enumerate(stops, 1):
        op = s.get("operator", "KMB")
        route = s.get("route", "")
        stop_id = s.get("stop_id", "")
        title = s.get("custom_title", f"{op} {route} @ {stop_id}")
        print(f"[{idx}] [{op}] Route {route} (Stop: {stop_id}) - {title}")

    print("\nDelete options:")
    print(" - Enter item number to delete (e.g. 1)")
    print(" - Enter 'all' to delete all routes")
    print(" - Press Enter to cancel and return to menu")
    choice = input("\nSelect option: ").strip().lower()

    if choice == "all":
        confirm = input("Are you sure you want to delete ALL routes? (y/n): ").strip().lower()
        if confirm == "y":
            config["tracked_stops"] = []
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            print("\n[Success] All tracked stops deleted from config.json!")
        return

    if choice.isdigit():
        num = int(choice)
        if 1 <= num <= len(stops):
            removed = stops.pop(num - 1)
            config["tracked_stops"] = stops
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            route_name = removed.get("route", "")
            title_str = removed.get("custom_title", "")
            print(f"\n[Success] Removed route '{route_name}' ({title_str}) from {config_file}!")
        else:
            print("\n[Error] Invalid item number.")


def main():
    while True:
        print("\n========================================")
        print("    Hong Kong Bus Stop Finder & Config Manager")
        print("========================================")
        print("1. Search & Add KMB / LWB (九巴 / 龍運)")
        print("2. Search & Add Citybus (城巴)")
        print("3. View / Delete tracked routes (檢視 / 刪除路線)")
        print("4. Exit")
        choice = input("Select option (1-4): ").strip()

        if choice == "1":
            route = input("\nEnter bus route (e.g. 1A, 968, 104): ").strip()
            if route:
                res = search_kmb_route(route)
                if res:
                    print("\nConfig entry generated:")
                    print(json.dumps(res[0], indent=2, ensure_ascii=False))
                    save = input("\nSave this stop directly to config.json? (y/n): ").strip().lower()
                    if save == "y":
                        append_to_config(res[0])
        elif choice == "2":
            route = input("\nEnter bus route (e.g. 102, A21, E21): ").strip()
            if route:
                res = search_ctb_route(route)
                if res:
                    print("\nConfig entry generated:")
                    print(json.dumps(res[0], indent=2, ensure_ascii=False))
                    save = input("\nSave this stop directly to config.json? (y/n): ").strip().lower()
                    if save == "y":
                        append_to_config(res[0])
        elif choice == "3":
            delete_stop_from_config()
        elif choice == "4" or choice.lower() in ["q", "exit", "quit"]:
            print("Exiting tool. Goodbye!")
            break
        else:
            print("Invalid choice. Please select 1, 2, 3, or 4.")


if __name__ == "__main__":
    main()