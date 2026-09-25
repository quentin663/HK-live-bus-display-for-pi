import json
import math
import os
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import font as tkfont

# Hong Kong Timezone (UTC+8)
HK_TZ = timezone(timedelta(hours=8))

# Thread-safe global caches for HKO icon raw bytes and Tkinter PhotoImages
HKO_ICON_BYTES_CACHE = {}
HKO_PHOTOIMAGE_CACHE = {}

# Weather condition text map for fallback text display
WEATHER_TEXT_MAP = {
    50: "SUNNY",
    51: "SUNNY",
    52: "PARTLY CLOUDY",
    53: "SHOWERS",
    54: "SHOWERS",
    60: "CLOUDY",
    61: "OVERCAST",
    62: "LIGHT RAIN",
    63: "HEAVY RAIN",
    64: "STORM",
    65: "THUNDERSTORM",
    70: "FINE",
    71: "FINE",
    76: "CLOUDY",
    77: "FINE",
    80: "WINDY",
    81: "DRY",
    82: "HUMID",
    83: "FOG",
    84: "MIST",
    85: "HAZE",
    90: "HOT",
    91: "WARM",
    92: "COOL",
    93: "COLD"
}

DEFAULT_CONFIG = {
    "display": {
        "fullscreen": True,
        "width": 1920,
        "height": 1080,
        "carousel_interval_seconds": 12,
        "api_refresh_seconds": 20,
        "weather_refresh_seconds": 180,
        "hide_cursor": False
    },
    "tracked_stops": []
}


class HKBusAPI:
    """Handles network requests and ETA parsing for HK Transport operators (KMB/LWB & Citybus)."""

    KMB_STOP_ETA_URL = "https://data.etabus.gov.hk/v1/transport/kmb/stop-eta/{stop_id}"
    KMB_ROUTE_ETA_URL = "https://data.etabus.gov.hk/v1/transport/kmb/eta/{stop_id}/{route}/{service_type}"
    KMB_STOP_INFO_URL = "https://data.etabus.gov.hk/v1/transport/kmb/stop/{stop_id}"
    
    CTB_ETA_URL = "https://rt.data.gov.hk/v2/transport/citybus/eta/CTB/{stop_id}/{route}"
    CTB_STOP_INFO_URL = "https://rt.data.gov.hk/v2/transport/citybus/stop/{stop_id}"

    @staticmethod
    def _fetch_json(url, timeout=5):
        """Helper to fetch and decode JSON with explicit timeout and exception guards."""
        headers = {"User-Agent": "Mozilla/5.0 (RaspberryPi; Linux) HKBusPi/2.0 (1080p)"}
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if response.status == 200:
                    data = response.read().decode("utf-8")
                    return json.loads(data)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, json.JSONDecodeError):
            return None
        except Exception as e:
            print(f"[HTTP Error] {url}: {e}")
            return None
        return None

    @classmethod
    def get_kmb_stop_info(cls, stop_id):
        """Fetch KMB stop name in Chinese and English."""
        url = cls.KMB_STOP_INFO_URL.format(stop_id=stop_id)
        data = cls._fetch_json(url)
        if data and "data" in data and data["data"]:
            d = data["data"]
            return {
                "name_en": d.get("name_en", stop_id),
                "name_tc": d.get("name_tc", stop_id)
            }
        return {"name_en": stop_id, "name_tc": stop_id}

    @classmethod
    def get_ctb_stop_info(cls, stop_id):
        """Fetch Citybus stop info."""
        url = cls.CTB_STOP_INFO_URL.format(stop_id=stop_id)
        data = cls._fetch_json(url)
        if data and "data" in data and data["data"]:
            d = data["data"]
            return {
                "name_en": d.get("name_en", stop_id),
                "name_tc": d.get("name_tc", stop_id)
            }
        return {"name_en": stop_id, "name_tc": stop_id}

    @classmethod
    def fetch_etas_for_target(cls, target_config):
        """Fetch and normalize ETAs for KMB/LWB or Citybus with deduplication and filtering."""
        operator = target_config.get("operator", "KMB").upper()
        route = target_config.get("route", "").strip().upper()
        stop_id = target_config.get("stop_id", "").strip()
        service_type = str(target_config.get("service_type", "1"))
        direction = target_config.get("direction", "").lower()
        
        # Map direction config string to KMB bound code ('O' or 'I')
        bound_code = "O" if direction in ["outbound", "o"] else "I" if direction in ["inbound", "i"] else None

        now_hk = datetime.now(HK_TZ)
        results = []
        seen_timestamps = set()

        if operator in ["KMB", "LWB"]:
            url = cls.KMB_STOP_ETA_URL.format(stop_id=stop_id)
            raw = cls._fetch_json(url)
            
            if raw is None or not raw.get("data"):
                url = cls.KMB_ROUTE_ETA_URL.format(
                    stop_id=stop_id,
                    route=route,
                    service_type=service_type
                )
                raw = cls._fetch_json(url)
                if raw is None:
                    return None

            if "data" not in raw:
                return None

            for item in raw.get("data", []):
                if item.get("route", "").upper() != route:
                    continue

                # Filter by service_type (e.g. '1' main line vs '2' special trips)
                item_st = str(item.get("service_type", "1"))
                if service_type and item_st != service_type:
                    continue

                # Filter by direction if provided ('O' outbound vs 'I' inbound)
                item_dir = item.get("dir") or item.get("bound")
                if bound_code and item_dir and item_dir.upper() != bound_code:
                    continue

                eta_str = item.get("eta")

                # Deduplicate identical ETA departure timestamps
                if eta_str and eta_str in seen_timestamps:
                    continue
                if eta_str:
                    seen_timestamps.add(eta_str)

                dest_en = item.get("dest_en", "")
                dest_tc = item.get("dest_tc", "")
                rmk_en = item.get("rmk_en", "")
                rmk_tc = item.get("rmk_tc", "")

                minutes_left = None
                eta_dt = None
                if eta_str:
                    try:
                        eta_dt = datetime.fromisoformat(eta_str)
                        diff = (eta_dt - now_hk).total_seconds()
                        minutes_left = max(0, int(math.ceil(diff / 60.0)))
                    except Exception:
                        pass

                results.append({
                    "route": route,
                    "dest_en": dest_en,
                    "dest_tc": dest_tc,
                    "eta_time": eta_dt,
                    "minutes_left": minutes_left,
                    "rmk_en": rmk_en,
                    "rmk_tc": rmk_tc,
                    "is_realtime": bool(eta_str and not rmk_en.lower().startswith("scheduled"))
                })

        elif operator == "CTB":
            url = cls.CTB_ETA_URL.format(stop_id=stop_id, route=route)
            raw = cls._fetch_json(url)
            if raw is None or "data" not in raw:
                return None

            for item in raw.get("data", []):
                eta_str = item.get("eta")

                if eta_str and eta_str in seen_timestamps:
                    continue
                if eta_str:
                    seen_timestamps.add(eta_str)

                dest_en = item.get("dest_en", "")
                dest_tc = item.get("dest_tc", "")
                rmk_en = item.get("rmk_en", "")
                rmk_tc = item.get("rmk_tc", "")

                minutes_left = None
                eta_dt = None
                if eta_str:
                    try:
                        eta_dt = datetime.fromisoformat(eta_str)
                        diff = (eta_dt - now_hk).total_seconds()
                        minutes_left = max(0, int(math.ceil(diff / 60.0)))
                    except Exception:
                        pass

                results.append({
                    "route": route,
                    "dest_en": dest_en,
                    "dest_tc": dest_tc,
                    "eta_time": eta_dt,
                    "minutes_left": minutes_left,
                    "rmk_en": rmk_en,
                    "rmk_tc": rmk_tc,
                    "is_realtime": bool(eta_str and "schedule" not in rmk_en.lower())
                })

        # Efficient sorting by minutes left
        results.sort(key=lambda x: (x["minutes_left"] is None, x["minutes_left"] if x["minutes_left"] is not None else 9999))
        return results


class HKWeatherAPI:
    """Fetches real-time regional weather and multi-day forecast from HK Observatory with concurrent icon prefetching."""

    HKO_URL_EN = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=rhrread&lang=en"
    HKO_URL_TC = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=rhrread&lang=tc"
    HKO_FND_EN = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=fnd&lang=en"
    HKO_FND_TC = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=fnd&lang=tc"

    @classmethod
    def fetch_hko_icon_bytes(cls, icon_code):
        """Fetch raw PNG image bytes from HKO with memory caching."""
        if not icon_code:
            return None
        icon_str = str(icon_code)
        if icon_str in HKO_ICON_BYTES_CACHE:
            return HKO_ICON_BYTES_CACHE[icon_str]

        candidate_urls = [
            f"https://www.hko.gov.hk/images/wxicon/pic{icon_str}.png",
            f"https://www.hko.gov.hk/images/HKO_m_head/pic{icon_str}.png"
        ]
        headers = {"User-Agent": "Mozilla/5.0 (RaspberryPi; Linux) HKBusPi/2.0"}

        for url in candidate_urls:
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=3) as resp:
                    if resp.status == 200:
                        png_data = resp.read()
                        if png_data.startswith(b"\x89PNG"):
                            HKO_ICON_BYTES_CACHE[icon_str] = png_data
                            return png_data
            except Exception:
                continue

        return None

    @classmethod
    def fetch_polam_weather(cls):
        """Fetch current weather and 7-day forecast with concurrent HTTP requests."""
        headers = {"User-Agent": "Mozilla/5.0 (RaspberryPi; Linux) HKBusPi/2.0"}

        def fetch_url(url):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=4) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                print(f"[Weather Request Error] {url}: {e}")
            return None

        # These four endpoints are independent, so fetch them concurrently.
        urls = {
            "current_en": cls.HKO_URL_EN,
            "current_tc": cls.HKO_URL_TC,
            "forecast_en": cls.HKO_FND_EN,
            "forecast_tc": cls.HKO_FND_TC,
        }
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {name: executor.submit(fetch_url, url) for name, url in urls.items()}
            data = {name: future.result() for name, future in futures.items()}

        data_en = data.get("current_en") or {}
        data_tc = data.get("current_tc") or {}
        fnd_en = data.get("forecast_en") or {}
        fnd_tc = data.get("forecast_tc") or {}

        current_data = {"temp": None, "humidity": None, "warning": "", "icon_code": None, "icon_bytes": None}
        forecast_list = []

        temps_en = data_en.get("temperature", {}).get("data", [])
        for t in temps_en:
            place = t.get("place", "")
            if "Tseung Kwan O" in place or "Po Lam" in place:
                current_data["temp"] = t.get("value")
                break
        if current_data["temp"] is None and temps_en:
            current_data["temp"] = temps_en[0].get("value")

        hum_data = data_en.get("humidity", {}).get("data", [])
        if hum_data:
            current_data["humidity"] = hum_data[0].get("value")

        warnings = data_tc.get("warningMessage", [])
        if warnings:
            current_data["warning"] = warnings[0].split("\n")[0]

        icon_list = data_en.get("icon", [])
        if icon_list:
            current_data["icon_code"] = icon_list[0]
            current_data["icon_bytes"] = cls.fetch_hko_icon_bytes(icon_list[0])

        list_tc = fnd_tc.get("weatherForecast", [])
        list_en = fnd_en.get("weatherForecast", [])
        raw_forecast_items = []
        icon_codes_to_fetch = set()

        def extract_val(data_dict, key):
            val = data_dict.get(key)
            if isinstance(val, dict):
                return val.get("value", "--")
            if val is not None:
                return str(val)
            return "--"

        for i in range(min(7, len(list_en))):
            item_en = list_en[i]
            item_tc = list_tc[i] if i < len(list_tc) else {}
            date_str = item_en.get("forecastDate", "")
            formatted_date = f"{date_str[6:8]}/{date_str[4:6]}" if len(date_str) == 8 else date_str
            icon_code = item_en.get("ForecastIcon") or item_tc.get("ForecastIcon") or item_en.get("forecastIcon")
            if icon_code:
                icon_codes_to_fetch.add(icon_code)
            raw_forecast_items.append({
                "date": formatted_date,
                "week_en": item_en.get("week", "").upper()[:3],
                "min_temp": extract_val(item_en, "forecastMintemp"),
                "max_temp": extract_val(item_en, "forecastMaxtemp"),
                "hum_min": extract_val(item_en, "forecastMinrh"),
                "hum_max": extract_val(item_en, "forecastMaxrh"),
                "icon_code": icon_code,
            })

        icon_bytes_map = {}
        if icon_codes_to_fetch:
            with ThreadPoolExecutor(max_workers=min(7, len(icon_codes_to_fetch))) as executor:
                future_to_code = {executor.submit(cls.fetch_hko_icon_bytes, code): code for code in icon_codes_to_fetch}
                for future in as_completed(future_to_code):
                    code = future_to_code[future]
                    try:
                        icon_bytes_map[code] = future.result()
                    except Exception:
                        icon_bytes_map[code] = None

        for item in raw_forecast_items:
            item["icon_bytes"] = icon_bytes_map.get(item["icon_code"])
            forecast_list.append(item)

        return {"current": current_data, "forecast": forecast_list}


class HKBusApp(tk.Tk):
    """Main Tkinter Dashboard Application displaying stacked multi-route layout for 1080p."""

    def __init__(self, config_file="config.json"):
        super().__init__()
        self.config_path = config_file
        self.config = self.load_config(config_file)
        
        self.display_cfg = self.config.get("display", {})
        self.tracked_stops = self.config.get("tracked_stops", [])
        
        # Application State
        self.cached_eta_data = {}
        self.stop_meta_cache = {}
        self.weather_info = None
        self.weather_full_text = ""
        self.weather_ticker_idx = 0
        self.last_sync_time = None
        self.is_fullscreen = self.display_cfg.get("fullscreen", True)

        # Thread safety locks
        self.refresh_lock = threading.Lock()
        self.refresh_in_progress = False
        self.stop_executor = ThreadPoolExecutor(max_workers=8)
        
        # 1080p Window setup
        self.title("Hong Kong Bus Transit Multi-Route Board (1080p)")
        self.configure(bg="#0B0F17")
        
        width = self.display_cfg.get("width", 1920)
        height = self.display_cfg.get("height", 1080)
        self.geometry(f"{width}x{height}")
        
        if self.is_fullscreen:
            self.attributes("-fullscreen", True)
        
        if self.display_cfg.get("hide_cursor", False):
            self.config(cursor="none")

        # Initialize Fonts
        self.init_fonts()

        # Keyboard & Touch bindings
        self.bind("<F11>", self.toggle_fullscreen)
        self.bind("<Escape>", lambda e: self.exit_fullscreen())
        self.bind("<q>", lambda e: self.destroy())
        self.bind("<space>", lambda e: self.trigger_manual_refresh())

        # Build Stacked 1080p UI Layout
        self.create_ui()

        # Initial render
        self.render_all_routes()

        # Start timers & background fetching
        self.update_clock()
        self.update_eta_countdowns()
        self.animate_weather_ticker()
        self.start_background_sync()

    def load_config(self, filepath):
        """Load JSON configuration with robust defaults fallback."""
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[Warning] Could not parse {filepath}: {e}. Using defaults.")
        else:
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
            except Exception:
                pass
        return DEFAULT_CONFIG

    def init_fonts(self):
        """High-legibility font sizes scaled for stacked multi-route layout on 1080p screens."""
        available = tkfont.families(self)
        primary_tc = "Noto Sans CJK TC" if "Noto Sans CJK TC" in available else "WenQuanYi Micro Hei" if "WenQuanYi Micro Hei" in available else "Helvetica"
        primary_num = "DejaVu Sans" if "DejaVu Sans" in available else "Arial"

        self.f_clock = tkfont.Font(family=primary_num, size=32, weight="bold")
        self.f_date = tkfont.Font(family=primary_tc, size=15)
        
        # Route badge font scale levels
        self.f_route_badge_lg = tkfont.Font(family=primary_num, size=34, weight="bold")
        self.f_route_badge_md = tkfont.Font(family=primary_num, size=28, weight="bold")
        self.f_route_badge_sm = tkfont.Font(family=primary_num, size=22, weight="bold")
        
        self.f_op_tag = tkfont.Font(family=primary_tc, size=13, weight="bold")
        
        self.f_dest_tc = tkfont.Font(family=primary_tc, size=24, weight="bold")
        self.f_dest_en = tkfont.Font(family=primary_num, size=14, weight="bold")
        self.f_dest_prefix = tkfont.Font(family=primary_tc, size=12)
        self.f_stop_info = tkfont.Font(family=primary_tc, size=13, weight="bold")
        
        self.f_weather = tkfont.Font(family=primary_tc, size=15, weight="bold")
        
        # Countdown & arrival time fonts
        self.f_eta_min = tkfont.Font(family=primary_num, size=42, weight="bold")
        self.f_eta_arriving = tkfont.Font(family=primary_tc, size=30, weight="bold")
        self.f_eta_unit = tkfont.Font(family=primary_tc, size=13, weight="bold")
        self.f_eta_sub = tkfont.Font(family=primary_num, size=15, weight="bold")
        self.f_card_header = tkfont.Font(family=primary_tc, size=13, weight="bold")
        
        self.f_status = tkfont.Font(family=primary_tc, size=12)
        
        # 7-Day Forecast Widget Fonts
        self.f_forecast_text = tkfont.Font(family=primary_num, size=12, weight="bold")
        self.f_forecast_day = tkfont.Font(family=primary_num, size=14, weight="bold")
        self.f_forecast_temp = tkfont.Font(family=primary_num, size=14, weight="bold")
        self.f_forecast_rh = tkfont.Font(family=primary_num, size=12)


    def create_ui(self):
        """Constructs top header, middle multi-route grid container, and bottom footer/weather widgets."""
        # Top Header Bar (Height: 80px)
        self.header = tk.Frame(self, bg="#161B26", height=80)
        self.header.pack(fill="x", side="top")

        # Left Header Title
        self.header_left = tk.Frame(self.header, bg="#161B26")
        self.header_left.pack(side="left", padx=28, pady=8)

        self.lbl_board_title = tk.Label(self.header_left, text="HK BUS LIVE PASSENGER BOARD  即時巴士到站資訊", font=self.f_status, fg="#8F9CAE", bg="#161B26")
        self.lbl_board_title.pack(anchor="w")

        # Right Header: Weather Ticker, Date, Clock
        self.header_right = tk.Frame(self.header, bg="#161B26")
        self.header_right.pack(side="right", padx=28, pady=8)

        self.lbl_clock = tk.Label(self.header_right, text="--:--:--", font=self.f_clock, fg="#00E5FF", bg="#161B26")
        self.lbl_clock.pack(side="right")

        self.lbl_date = tk.Label(self.header_right, text="----/--/--", font=self.f_date, fg="#94A3B8", bg="#161B26")
        self.lbl_date.pack(side="right", padx=(16, 20))

        # 60-character wide scrolling ticker label
        self.lbl_cur_weather = tk.Label(self.header_right, text="寶琳 Po Lam --°C | RH: --%", font=self.f_weather, fg="#38BDF8", bg="#161B26", width=60, anchor="w")
        self.lbl_cur_weather.pack(side="right", padx=(0, 6))

        # Current weather PNG icon label
        self.lbl_cur_weather_icon = tk.Label(self.header_right, text="", bg="#161B26")
        self.lbl_cur_weather_icon.pack(side="right", padx=(0, 10))

        # CRITICAL PACKING ORDER: Pack footer AT THE BOTTOM BEFORE main_container expand=True
        self.footer = tk.Frame(self, bg="#070A0F", height=32)
        self.footer.pack(fill="x", side="bottom")

        self.lbl_sync_info = tk.Label(self.footer, text="Connecting to HK Open Data...", font=self.f_status, fg="#64748B", bg="#070A0F")
        self.lbl_sync_info.pack(side="left", padx=28, pady=2)

        self.lbl_hint = tk.Label(self.footer, text="[Space] Refresh  •  [F11] Fullscreen  •  [Q] Exit", font=self.f_status, fg="#475569", bg="#070A0F")
        self.lbl_hint.pack(side="right", padx=28, pady=2)

        # Main Container packed LAST with expand=True to guarantee footer is never cut off
        self.main_container = tk.Frame(self, bg="#0B0F17")
        self.main_container.pack(fill="both", expand=True, padx=28, pady=(8, 4))

        # Main Grid Proportions:
        self.main_container.grid_columnconfigure(0, weight=3, uniform="main_col")
        self.main_container.grid_columnconfigure(1, weight=2, uniform="main_col")
        self.main_container.grid_columnconfigure(2, weight=2, uniform="main_col")
        self.main_container.grid_columnconfigure(3, weight=2, uniform="main_col")

        # Build vertical route rows for each tracked stop
        self.route_rows = []
        num_stops = len(self.tracked_stops)

        for route_idx, target in enumerate(self.tracked_stops):
            self.main_container.grid_rowconfigure(route_idx, weight=1)
            row_widgets = self.create_route_row(self.main_container, route_idx)
            self.route_rows.append(row_widgets)

        # Forecast Row
        weather_row_idx = max(num_stops, 1)
        self.main_container.grid_rowconfigure(weather_row_idx, weight=0)

        self.forecast_row = tk.Frame(self.main_container, bg="#0B0F17")
        self.forecast_row.grid(row=weather_row_idx, column=0, columnspan=4, sticky="ew", padx=3, pady=(8, 4))

        for j in range(7):
            self.forecast_row.grid_columnconfigure(j, weight=1, uniform="forecast_col")

        self.forecast_cards = []
        for i in range(7):
            f_card = self.create_forecast_card(self.forecast_row, index=i)
            self.forecast_cards.append(f_card)

    def create_route_row(self, parent, row_idx):
        """Creates a horizontal route row: [Route Status & Info Panel] + [3 ETA Cards]."""
        info_panel = tk.Frame(parent, bg="#131924", highlightthickness=2, highlightbackground="#1E293B")
        info_panel.grid(row=row_idx, column=0, sticky="nsew", padx=4, pady=2)

        # Badge Box
        badge_frame = tk.Frame(info_panel, bg="#DC2626", padx=8, pady=2, width=145)
        badge_frame.pack_propagate(False)
        badge_frame.pack(side="left", fill="both")

        lbl_route_num = tk.Label(badge_frame, text="--", font=self.f_route_badge_lg, fg="#FFFFFF", bg="#DC2626")
        lbl_route_num.pack(anchor="center", expand=True)

        lbl_op_tag = tk.Label(badge_frame, text="KMB", font=self.f_op_tag, fg="#FEE2E2", bg="#DC2626")
        lbl_op_tag.pack(anchor="center", pady=(0, 2))

        # Details Box
        details_frame = tk.Frame(info_panel, bg="#131924", padx=10, pady=2)
        details_frame.pack(side="left", fill="both", expand=True)

        lbl_dest_prefix = tk.Label(details_frame, text="往 DESTINATION", font=self.f_dest_prefix, fg="#94A3B8", bg="#131924")
        lbl_dest_prefix.pack(anchor="w")

        lbl_dest_tc = tk.Label(details_frame, text="載入中...", font=self.f_dest_tc, fg="#FFFFFF", bg="#131924")
        lbl_dest_tc.pack(anchor="w", pady=(0, 0))

        lbl_dest_en = tk.Label(details_frame, text="Loading...", font=self.f_dest_en, fg="#CBD5E1", bg="#131924")
        lbl_dest_en.pack(anchor="w", pady=(0, 0))

        lbl_stop_title = tk.Label(details_frame, text="", font=self.f_stop_info, fg="#38BDF8", bg="#131924")
        lbl_stop_title.pack(anchor="w", pady=(1, 0))

        eta_boxes = []
        for i in range(3):
            box = self.create_eta_box(parent, row_idx=row_idx, col_idx=i + 1)
            eta_boxes.append(box)

        return {
            "info_panel": info_panel,
            "badge_frame": badge_frame,
            "lbl_route_num": lbl_route_num,
            "lbl_op_tag": lbl_op_tag,
            "lbl_dest_prefix": lbl_dest_prefix,
            "lbl_dest_tc": lbl_dest_tc,
            "lbl_dest_en": lbl_dest_en,
            "lbl_stop_title": lbl_stop_title,
            "eta_boxes": eta_boxes
        }

    def create_eta_box(self, parent, row_idx, col_idx):
        """Create a single ETA card for a route row."""
        card = tk.Frame(parent, bg="#111723", highlightthickness=2, highlightbackground="#1E293B")
        card.grid(row=row_idx, column=col_idx, sticky="nsew", padx=4, pady=2)

        header = tk.Frame(card, bg="#1E293B", pady=2)
        header.pack(fill="x")

        labels = ["NEXT BUS 下班車", "2ND BUS 第二班", "3RD BUS 第三班"]
        card_index = col_idx - 1
        lbl_order = tk.Label(header, text=labels[card_index], font=self.f_card_header, fg="#38BDF8" if card_index == 0 else "#94A3B8", bg="#1E293B")
        lbl_order.pack(anchor="center")

        content = tk.Frame(card, bg="#111723", pady=2)
        content.pack(fill="both", expand=True)

        lbl_min = tk.Label(content, text="--", font=self.f_eta_min, fg="#22C55E" if card_index == 0 else "#38BDF8", bg="#111723")
        lbl_min.pack(anchor="center", pady=(1, 0))

        lbl_unit = tk.Label(content, text="分鐘 MIN", font=self.f_eta_unit, fg="#94A3B8", bg="#111723")
        lbl_unit.pack(anchor="center", pady=(0, 0))

        # Clock arrival time label (e.g. 14:25)
        lbl_time = tk.Label(content, text="", font=self.f_eta_sub, fg="#38BDF8" if card_index == 0 else "#E2E8F0", bg="#111723")
        lbl_time.pack(anchor="center", pady=(1, 0))

        return {
            "card": card,
            "lbl_min": lbl_min,
            "lbl_unit": lbl_unit,
            "lbl_time": lbl_time,
            "lbl_order": lbl_order
        }

    def create_forecast_card(self, parent, index):
        """Create a simplified 7-day forecast pill card with weather icon container above date."""
        card = tk.Frame(parent, bg="#111723", highlightthickness=2, highlightbackground="#1E293B", padx=6, pady=8)
        card.grid(row=0, column=index, sticky="nsew", padx=3)

        # Weather Icon Container above date (Height: 54px)
        icon_container = tk.Frame(card, bg="#111723", height=54)
        icon_container.pack_propagate(False)
        icon_container.pack(fill="x", anchor="center", pady=(2, 1))

        lbl_icon = tk.Label(icon_container, text="", font=self.f_forecast_text, fg="#F59E0B", bg="#111723")
        lbl_icon.pack(anchor="center", expand=True)

        lbl_day = tk.Label(card, text="--- --/--", font=self.f_forecast_day, fg="#38BDF8", bg="#111723")
        lbl_day.pack(anchor="center", pady=(2, 1))

        lbl_temp = tk.Label(card, text="--° - --°C", font=self.f_forecast_temp, fg="#F8FAFC", bg="#111723")
        lbl_temp.pack(anchor="center", pady=(2, 1))

        lbl_rh = tk.Label(card, text="RH --%", font=self.f_forecast_rh, fg="#94A3B8", bg="#111723")
        lbl_rh.pack(anchor="center", pady=(1, 2))

        return {
            "card": card,
            "lbl_icon": lbl_icon,
            "lbl_day": lbl_day,
            "lbl_temp": lbl_temp,
            "lbl_rh": lbl_rh
        }


    def update_clock(self):
        """Updates top right clock label every 1000ms formatted as DD-MM-YYYY."""
        now_hk = datetime.now(HK_TZ)
        self.lbl_clock.config(text=now_hk.strftime("%H:%M:%S"))
        self.lbl_date.config(text=now_hk.strftime("%d-%m-%Y  (%a)"))
        self.after(1000, self.update_clock)

    def update_eta_countdowns(self):
        """Recalculate displayed ETA minutes from cached timestamps every second.

        This avoids hitting the transport API just to keep the countdown moving.
        """
        if self.tracked_stops and self.cached_eta_data:
            self.render_all_routes()
        self.after(1000, self.update_eta_countdowns)

    def animate_weather_ticker(self):
        """Scrolls the current weather information right-to-left continuously."""
        if self.weather_full_text:
            padded_text = self.weather_full_text + "               "
            idx = self.weather_ticker_idx % len(padded_text)
            display_str = padded_text[idx:] + padded_text[:idx]
            self.lbl_cur_weather.config(text=display_str[:60])
            self.weather_ticker_idx += 1
        self.after(280, self.animate_weather_ticker)

    def start_background_sync(self):
        """Run periodic network synchronization without blocking Tkinter."""
        def fetch_metadata_for_target(idx, target):
            op = target.get("operator", "KMB").upper()
            stop_id = target.get("stop_id", "")
            try:
                if op in ["KMB", "LWB"]:
                    return idx, HKBusAPI.get_kmb_stop_info(stop_id)
                if op == "CTB":
                    return idx, HKBusAPI.get_ctb_stop_info(stop_id)
            except Exception as e:
                print(f"[Meta Fetch Error] stop {idx}: {e}")
            return idx, None

        def sync_worker():
            last_weather_check = 0.0
            while True:
                cycle_start = time.monotonic()
                now_ts = time.time()
                weather_interval = max(30, self.display_cfg.get("weather_refresh_seconds", 180))

                if now_ts - last_weather_check >= weather_interval:
                    try:
                        w_data = HKWeatherAPI.fetch_polam_weather()
                        if w_data:
                            self.after(0, lambda data=w_data: self._apply_weather(data))
                    except Exception as e:
                        print(f"[Weather Error]: {e}")
                    finally:
                        last_weather_check = time.time()

                if self.tracked_stops:
                    fetched_eta_data = {}
                    fetched_meta = {}

                    # Fetch uncached stop metadata concurrently.
                    missing_meta = [
                        (idx, target) for idx, target in enumerate(self.tracked_stops)
                        if idx not in self.stop_meta_cache
                    ]
                    if missing_meta:
                        futures = [self.stop_executor.submit(fetch_metadata_for_target, idx, target) for idx, target in missing_meta]
                        for future in as_completed(futures):
                            idx, meta = future.result()
                            if meta:
                                fetched_meta[idx] = meta

                    # Fetch all route ETAs concurrently.
                    futures = {
                        self.stop_executor.submit(HKBusAPI.fetch_etas_for_target, target): idx
                        for idx, target in enumerate(self.tracked_stops)
                    }
                    for future in as_completed(futures):
                        idx = futures[future]
                        try:
                            result = future.result()
                            if result is not None:
                                fetched_eta_data[idx] = result
                        except Exception as e:
                            # Do not replace valid cached data with [] on transient API errors.
                            print(f"[Parallel Fetch Error] stop {idx}: {e}")

                    sync_time = datetime.now(HK_TZ)

                    def apply_bus_data(eta_data=fetched_eta_data, meta_data=fetched_meta, synced_at=sync_time):
                        self.cached_eta_data.update(eta_data)
                        self.stop_meta_cache.update(meta_data)
                        self.last_sync_time = synced_at
                        self.render_all_routes()

                    self.after(0, apply_bus_data)

                interval = max(10, self.display_cfg.get("api_refresh_seconds", 20))
                elapsed = time.monotonic() - cycle_start
                time.sleep(max(0.5, interval - elapsed))

        threading.Thread(target=sync_worker, daemon=True, name="HKBusSync").start()

    def _apply_weather(self, weather_data):
        self.weather_info = weather_data
        self.render_weather()


    def render_weather(self):
        """Format current weather badge and 7-day forecast cards using downloaded PNG icons or text fallbacks."""
        if self.weather_info:
            current = self.weather_info.get("current", {})
            temp = current.get("temp")
            hum = current.get("humidity")
            warn = current.get("warning", "")

            temp_str = f"{temp}°C" if temp is not None else "--°C"
            hum_str = f"RH: {hum}%" if hum is not None else "RH: --%"
            
            cur_text = f"寶琳 Po Lam {temp_str}  |  {hum_str}"
            if warn:
                cur_text += f"  [{warn}]"
            self.weather_full_text = cur_text

            # Render current weather icon next to weather bar
            cur_icon_code = current.get("icon_code")
            cur_icon_bytes = current.get("icon_bytes")
            cur_photo_img = None
            if cur_icon_code:
                icon_str = str(cur_icon_code)
                if icon_str in HKO_PHOTOIMAGE_CACHE:
                    cur_photo_img = HKO_PHOTOIMAGE_CACHE[icon_str]
                elif cur_icon_bytes:
                    try:
                        cur_photo_img = tk.PhotoImage(data=cur_icon_bytes)
                        HKO_PHOTOIMAGE_CACHE[icon_str] = cur_photo_img
                    except Exception:
                        cur_photo_img = None

            if cur_photo_img:
                self.lbl_cur_weather_icon.config(image=cur_photo_img, text="")
                self.lbl_cur_weather_icon.image = cur_photo_img
            else:
                self.lbl_cur_weather_icon.config(image="", text="")

            forecasts = self.weather_info.get("forecast", [])
            for i in range(7):
                f_box = self.forecast_cards[i]
                if i < len(forecasts):
                    f_item = forecasts[i]
                    day_str = f"{f_item['week_en']} {f_item['date']}"  # DD/MM
                    temp_str = f"{f_item['min_temp']}° - {f_item['max_temp']}°C"
                    rh_str = f"RH {f_item['hum_min']}-{f_item['hum_max']}%"
                    icon_code = f_item.get("icon_code")
                    icon_bytes = f_item.get("icon_bytes")

                    photo_img = None
                    if icon_code:
                        icon_str = str(icon_code)
                        if icon_str in HKO_PHOTOIMAGE_CACHE:
                            photo_img = HKO_PHOTOIMAGE_CACHE[icon_str]
                        elif icon_bytes:
                            try:
                                photo_img = tk.PhotoImage(data=icon_bytes)
                                HKO_PHOTOIMAGE_CACHE[icon_str] = photo_img
                            except Exception:
                                photo_img = None

                    if photo_img:
                        f_box["lbl_icon"].config(image=photo_img, text="")
                        f_box["lbl_icon"].image = photo_img
                    else:
                        code_int = int(icon_code) if icon_code else 0
                        fallback_text = WEATHER_TEXT_MAP.get(code_int, "WEATHER")
                        f_box["lbl_icon"].config(text=fallback_text, image="", fg="#F59E0B")

                    f_box["lbl_day"].config(text=day_str)
                    f_box["lbl_temp"].config(text=temp_str)
                    f_box["lbl_rh"].config(text=rh_str)
                else:
                    f_box["lbl_icon"].config(text="--", image="")
                    f_box["lbl_day"].config(text="--- --/--")
                    f_box["lbl_temp"].config(text="--° - --°C")
                    f_box["lbl_rh"].config(text="RH --%")

    def render_all_routes(self):
        """Renders ETA data for ALL tracked routes simultaneously in stacked rows."""
        if not self.tracked_stops:
            return

        for idx, target in enumerate(self.tracked_stops):
            if idx >= len(self.route_rows):
                break

            row_widgets = self.route_rows[idx]
            operator = target.get("operator", "KMB").upper()
            route = target.get("route", "--").upper()
            custom_title = target.get("custom_title", "")

            # Operator branding
            if operator in ["KMB", "LWB"]:
                badge_bg = "#DC2626" if operator == "KMB" else "#EA580C"
                badge_fg = "#FFFFFF"
                op_label = "九巴 KMB" if operator == "KMB" else "龍運 LWB"
            elif operator == "CTB":
                badge_bg = "#EAB308"
                badge_fg = "#0F172A"
                op_label = "城巴 Citybus"
            else:
                badge_bg = "#2563EB"
                badge_fg = "#FFFFFF"
                op_label = operator

            # Font scaling for route number length
            if len(route) <= 2:
                f_badge = self.f_route_badge_lg
            elif len(route) in [3, 4]:
                f_badge = self.f_route_badge_md
            else:
                f_badge = self.f_route_badge_sm

            row_widgets["badge_frame"].config(bg=badge_bg)
            row_widgets["lbl_route_num"].config(text=route, bg=badge_bg, fg=badge_fg, font=f_badge)
            row_widgets["lbl_op_tag"].config(text=op_label, bg=badge_bg, fg=badge_fg)

            stop_info = self.stop_meta_cache.get(idx, {})
            stop_display = custom_title or f"📍 {stop_info.get('name_tc', '')} {stop_info.get('name_en', '')}"
            row_widgets["lbl_stop_title"].config(text=stop_display)

            etas = self.cached_eta_data.get(idx, None)

            # Recalculate countdowns locally from the API timestamps. This keeps the
            # display accurate between network refreshes and naturally removes buses
            # once their ETA has passed.
            now_hk = datetime.now(HK_TZ)
            valid_etas = []
            if etas:
                for eta in etas:
                    eta_dt = eta.get("eta_time")
                    if eta_dt is not None:
                        diff_seconds = (eta_dt - now_hk).total_seconds()
                        if diff_seconds >= -30:
                            eta["minutes_left"] = max(0, int(math.ceil(diff_seconds / 60.0)))
                            valid_etas.append(eta)
            no_remaining_buses = (etas is not None and len(valid_etas) == 0)

            if etas is None:
                row_widgets["lbl_dest_prefix"].config(text="狀態 STATUS")
                row_widgets["lbl_dest_tc"].config(text="正在獲取資料...", font=self.f_dest_en)
                row_widgets["lbl_dest_en"].config(text="Contacting transport API...", font=self.f_dest_en)
            elif no_remaining_buses:
                row_widgets["lbl_dest_prefix"].config(text="狀態 STATUS")
                row_widgets["lbl_dest_tc"].config(text="尾班車已駛離此站", font=self.f_dest_tc)
                row_widgets["lbl_dest_en"].config(text="Final bus has departed from this stop", font=self.f_dest_en)
            else:
                row_widgets["lbl_dest_prefix"].config(text="往 DESTINATION")
                first_eta = valid_etas[0] if valid_etas else etas[0]

                dest_tc = target.get("destination_tc") or first_eta.get("dest_tc") or "---"
                dest_en = target.get("destination_en") or first_eta.get("dest_en") or "---"

                row_widgets["lbl_dest_tc"].config(text=dest_tc, font=self.f_dest_tc)
                row_widgets["lbl_dest_en"].config(text=dest_en, font=self.f_dest_en)

            # Update the 3 ETA boxes for this route
            for i in range(3):
                box = row_widgets["eta_boxes"][i]
                if not no_remaining_buses and valid_etas and i < len(valid_etas):
                    item = valid_etas[i]
                    mins = item.get("minutes_left")
                    eta_dt = item.get("eta_time")

                    if mins is not None:
                        if mins == 0:
                            box["lbl_min"].config(text="即將抵達", font=self.f_eta_arriving, fg="#EF4444")
                            box["lbl_unit"].config(text="ARRIVING NOW", font=self.f_eta_unit, fg="#EF4444")
                        else:
                            box["lbl_min"].config(text=str(mins), font=self.f_eta_min, fg="#22C55E" if i == 0 else "#38BDF8")
                            box["lbl_unit"].config(text="分鐘 MIN", font=self.f_eta_unit, fg="#94A3B8")
                    else:
                        box["lbl_min"].config(text="--", font=self.f_eta_min, fg="#64748B")
                        box["lbl_unit"].config(text="無時間", font=self.f_eta_unit, fg="#64748B")

                    # Clock Arrival Time under countdown minutes (e.g., ETA 14:25)
                    box["lbl_time"].config(text=f"ETA {eta_dt.strftime('%H:%M')}" if eta_dt else "")
                else:
                    box["lbl_time"].config(text="")

                    if no_remaining_buses:
                        if i == 0:
                            box["lbl_min"].config(
                                text="尾班車已開出",
                                font=self.f_eta_arriving,
                                fg="#F59E0B"
                            )
                            box["lbl_unit"].config(
                                text="Final bus has departed",
                                font=self.f_eta_unit,
                                fg="#CBD5E1"
                            )
                        else:
                            box["lbl_min"].config(
                                text="服務已結束",
                                font=self.f_eta_arriving,
                                fg="#475569"
                            )
                            box["lbl_unit"].config(
                                text="Service ended",
                                font=self.f_eta_unit,
                                fg="#475569"
                            )
                    elif etas is not None and valid_etas:
                        box["lbl_min"].config(
                            text="沒有更多班次",
                            font=self.f_eta_arriving,
                            fg="#64748B"
                        )
                        box["lbl_unit"].config(
                            text="No more buses",
                            font=self.f_eta_unit,
                            fg="#64748B"
                        )
                    else:
                        box["lbl_min"].config(
                            text="--",
                            font=self.f_eta_min,
                            fg="#334155"
                        )
                        box["lbl_unit"].config(
                            text="載入中...",
                            font=self.f_eta_unit,
                            fg="#475569"
                        )

        if self.last_sync_time:
            self.lbl_sync_info.config(text=f"Last synced: {self.last_sync_time.strftime('%H:%M:%S')}")
        else:
            self.lbl_sync_info.config(text="Connecting to HK Open Data...")


    def trigger_manual_refresh(self):
        """Triggers asynchronous manual data refresh with concurrency guard."""
        with self.refresh_lock:
            if self.refresh_in_progress:
                return
            self.refresh_in_progress = True

        self.lbl_sync_info.config(text="Refreshing data now...")
        threading.Thread(target=self._manual_refresh_worker, daemon=True, name="HKBusManualRefresh").start()

    def _manual_refresh_worker(self):
        """Worker thread for parallel manual refresh."""
        new_eta_data = {}

        try:
            future_to_idx = {
                self.stop_executor.submit(HKBusAPI.fetch_etas_for_target, target): idx
                for idx, target in enumerate(self.tracked_stops)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    result = future.result()
                    if result is not None:
                        new_eta_data[idx] = result
                except Exception as e:
                    print(f"[Manual Parallel Fetch Error] stop {idx}: {e}")

            sync_time = datetime.now(HK_TZ)

            def apply_refresh():
                self.cached_eta_data.update(new_eta_data)
                self.last_sync_time = sync_time
                self.render_all_routes()
                with self.refresh_lock:
                    self.refresh_in_progress = False

            self.after(0, apply_refresh)
        except Exception as e:
            print(f"[Manual Refresh Error]: {e}")

            def finish_refresh():
                with self.refresh_lock:
                    self.refresh_in_progress = False
                self.render_all_routes()

            self.after(0, finish_refresh)

    def toggle_fullscreen(self, event=None):
        self.is_fullscreen = not self.is_fullscreen
        self.attributes("-fullscreen", self.is_fullscreen)

    def exit_fullscreen(self):
        self.is_fullscreen = False
        self.attributes("-fullscreen", False)


if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    app = HKBusApp(config_file=config_path)
    app.mainloop()