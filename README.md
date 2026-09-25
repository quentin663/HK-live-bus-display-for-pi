# HK Live Bus Display for Raspberry Pi

A fullscreen Raspberry Pi / Tkinter passenger information display for Hong Kong public transport. The app shows real-time bus ETAs for multiple stops and routes alongside current Hong Kong Observatory weather and a 7-day forecast.

> **Display target:** This project is currently designed and tuned for a **1920×1080 (1080p) landscape display**. Other resolutions may work, but the layout and scaling may require adjustment.

The project currently supports **KMB**, **LWB**, and **Citybus** data using Hong Kong government open-data APIs. No API keys or account authentication are required.

## Features

- Real-time ETA display for multiple configured routes and stops
- KMB and LWB support
- Citybus support
- Up to three upcoming ETAs per tracked stop
- Automatic ETA countdown updates between API refreshes
- Stop-name metadata lookup
- Current Hong Kong weather
- 7-day Hong Kong Observatory forecast
- Weather icon caching
- Bilingual Chinese / English interface
- Fullscreen 1920×1080 display layout
- Background API refreshes so the UI remains responsive
- Manual refresh using the keyboard
- Route and stop search helper for editing `config.json`

## Repository Structure

```text
.
├── config.example.json
├── run.sh
├── hk_bus_display.py
├── route_and_stop_finder_tool.py
├── LICENSE
└── README.md
```

### `hk_bus_display.py`

Main Tkinter application. It handles:

- KMB/LWB and Citybus ETA requests
- Stop metadata requests
- Hong Kong Observatory weather requests
- Background refresh threads
- ETA countdown calculation
- Weather/icon caching
- Fullscreen passenger-board UI rendering

### `route_and_stop_finder_tool.py`

Interactive command-line helper for finding routes and stop IDs. It can also add or remove tracked stops in `config.json`.

### `config.example.json`

Template for your local `config.json`, which stores display settings and tracked stops. `config.json` is ignored by Git to keep your chosen stops private.

## Requirements

- Python 3
- Tkinter
- Internet access
- A 1920×1080 (1080p) landscape display is the current design target

Other resolutions may work, but the UI is not currently optimized for them and may need layout or scaling changes.

The application otherwise uses Python standard-library modules and does not require a third-party Python package for its API requests.

On Raspberry Pi OS, Tkinter may need to be installed separately:

```bash
sudo apt update
sudo apt install python3-tk
```

## Quick start

On a Raspberry Pi with a desktop session and Tkinter installed, run:

```bash
git clone https://github.com/quentin663/HK-live-bus-display-for-pi.git
cd HK-live-bus-display-for-pi
./run.sh
```

The first run creates a local `config.json` from the example. Add a stop with `python3 route_and_stop_finder_tool.py` or edit the file, then run `./run.sh` again. Your chosen stops stay in the ignored local config.

For later updates from a clone of the **current history**:

```bash
git pull --ff-only
./run.sh
```

Because the repository history was replaced in September 2026, clones made before that replacement cannot use a normal `git pull` for their first update. Back up their `config.json`, clone the repository into a new directory, then copy the backed-up config into the new clone. Subsequent pulls work normally.

## Configuration

Example configuration before adding stops:

```json
{
  "display": {
    "fullscreen": true,
    "width": 1920,
    "height": 1080,
    "language": "bilingual",
    "theme": "dark",
    "carousel_interval_seconds": 12,
    "api_refresh_seconds": 25,
    "show_clock": true,
    "hide_cursor": false,
    "max_eta_entries": 3
  },
  "tracked_stops": []
}
```

Use `route_and_stop_finder_tool.py` to select a route and stop, or add an entry manually using these fields:

### Tracked stop fields

| Field | Description |
| --- | --- |
| `operator` | Transport operator, such as `KMB`, `LWB`, or `CTB` |
| `route` | Route number |
| `stop_id` | Operator stop identifier |
| `direction` | Usually `outbound` or `inbound` |
| `service_type` | KMB/LWB service variation, normally `1` |
| `custom_title` | Optional display label for the stop |

## Finding Routes and Stop IDs

Run the included helper:

```bash
python3 route_and_stop_finder_tool.py
```

The utility can:

- search KMB/LWB routes
- display route direction and service variations
- list stop sequences
- search Citybus route stops
- add a selected stop to `config.json`
- remove configured stops

## Controls

While the display is running:

| Key | Action |
| --- | --- |
| `Space` | Refresh bus data immediately |
| `F11` | Toggle fullscreen |
| `Esc` | Exit fullscreen |
| `Q` | Close the application |

## Data Sources

The project retrieves data directly from Hong Kong public open-data services.

### KMB / LWB

```text
https://data.etabus.gov.hk/v1/transport/kmb/
```

Used for route, stop, and ETA information.

### Citybus

```text
https://rt.data.gov.hk/v2/transport/citybus/
```

Used for Citybus route, stop, and ETA information.

### Hong Kong Observatory

```text
https://data.weather.gov.hk/weatherAPI/opendata/
```

Used for current weather and forecast information.

Weather icons are retrieved from Hong Kong Observatory web resources.

## Authentication and API Keys

No application credentials are currently required.

Requests are normal HTTPS GET requests with a `User-Agent` header. The application does **not** currently use:

- usernames or passwords
- API keys
- OAuth
- bearer tokens
- refresh tokens
- cookies or application sessions

This means there are no secrets that need to be added to `config.json` for the existing data sources.

## How Data Refresh Works

The dashboard performs network requests in background threads so Tkinter's UI thread remains responsive.

A simplified request flow is:

```text
config.json
    │
    ▼
HKBusApp
    │
    ├── HKBusAPI
    │     ├── KMB / LWB API
    │     └── Citybus API
    │
    └── HKWeatherAPI
          └── Hong Kong Observatory API
```

Bus API results are cached in memory. The displayed minute countdowns are recalculated locally every second from the returned ETA timestamps, so the application does not need to contact the transport API every second.

Weather data is refreshed on a separate interval and weather icons are cached in memory after download.

## Notes

- Hong Kong time is handled explicitly as UTC+8.
- API failures are handled defensively so temporary request errors do not immediately overwrite previously valid ETA data.
- Public API availability and response formats are controlled by the respective transport operators and Hong Kong government data services.
- The current UI is primarily designed around a 1920×1080 landscape display.

## License

This project is licensed under the MIT License. See [`LICENSE`](LICENSE) for details.
