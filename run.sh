#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is required." >&2
    exit 1
fi

if ! python3 -c 'import tkinter' >/dev/null 2>&1; then
    echo "Tkinter is required. On Raspberry Pi OS: sudo apt install python3-tk" >&2
    exit 1
fi

if [[ ! -f config.json ]]; then
    cp config.example.json config.json
    echo "Created config.json from config.example.json."
fi

if ! python3 -c 'import json; assert json.load(open("config.json", encoding="utf-8")).get("tracked_stops")' >/dev/null 2>&1; then
    echo "No tracked stops configured. Run: python3 route_and_stop_finder_tool.py" >&2
    echo "Then run ./run.sh again." >&2
    exit 1
fi

exec python3 hk_bus_display.py
