#!/usr/bin/env python3
"""
Generate static HTML weather pages (Finnish) from FMI open data, one per
target e-ink device. Meant to be run on a schedule (e.g. GitHub Actions)
and the output published as static files (e.g. GitHub Pages) -- each
device's own screenshot/webhook mechanism (TRMNL's Screenshot plugin,
LaraPaper's Screenshot handler, an Inkplate image pipeline, etc.) then
periodically captures the relevant URL. No server, no device-specific
markup beyond plain HTML/CSS.

Uses only the Python standard library (zoneinfo needs the system tz
database, present by default on Ubuntu GitHub Actions runners).

Env vars:
  FMI_PLACE     - Finnish place name (default: Helsinki)
  OUTPUT_DIR    - directory to write the per-device HTML files into
                  (default: docs)
"""

import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

NS = {"BsWfs": "http://xml.fmi.fi/schema/wfs/2.0"}
PARAMETERS = "Temperature,FeelsLike,WindSpeedMS,WindDirection,Precipitation1h,WeatherSymbol3"
HELSINKI_TZ = ZoneInfo("Europe/Helsinki")
TARGET_HOURS = (3, 9, 15, 21)  # local Helsinki checkpoints shown in the table

# Finnish descriptions per FMI's own reference
# (ilmatieteenlaitos.fi/latauspalvelun-pikaohje), cross-checked against
# fmidev/opendata-resources filenames.
WEATHER_SYMBOLS_FI = {
    1: "Selkeää", 2: "Puolipilvistä", 3: "Pilvistä",
    21: "Heikkoja sadekuuroja", 22: "Sadekuuroja", 23: "Voimakkaita sadekuuroja",
    31: "Heikkoa vesisadetta", 32: "Vesisadetta", 33: "Voimakasta vesisadetta",
    41: "Heikkoja lumikuuroja", 42: "Lumikuuroja", 43: "Voimakkaita lumikuuroja",
    51: "Heikkoa lumisadetta", 52: "Lumisadetta", 53: "Voimakasta lumisadetta",
    61: "Ukkoskuuroja", 62: "Voimakkaita ukkoskuuroja",
    63: "Ukkosta", 64: "Voimakasta ukkosta",
    71: "Heikkoja räntäkuuroja", 72: "Räntäkuuroja", 73: "Voimakkaita räntäkuuroja",
    81: "Heikkoa räntäsadetta", 82: "Räntäsadetta", 83: "Voimakasta räntäsadetta",
    91: "Utua", 92: "Sumua",
}


# ============================================================================
# Device profiles
# ============================================================================
# Each output device gets its own file, sized and styled to its exact panel.
# The data (fetch_forecast) and row-selection logic are shared -- only the
# page's pixel dimensions and CSS sizing differ per device. To add a new
# device: add a DeviceProfile below and it's picked up automatically by
# main() at the bottom of this file.

@dataclass(frozen=True)
class DeviceProfile:
    key: str            # short id, used in log output
    label: str           # human-readable name, used in log output
    filename: str        # output file, written under OUTPUT_DIR
    width: int            # css px, must match the physical panel exactly
    height: int
    # CSS sizing -- tuned per device's height budget. See generate_weather_page.py
    # revision history / conversation notes if these ever need re-tuning after
    # a real screenshot shows over/underflow (as happened for TRMNL originally).
    page_padding: str
    place_font: int
    updated_font: int
    header_pad_bottom: int
    header_margin_bottom: int
    now_gap: int
    now_margin_bottom: int
    temp_font: int
    now_details_font: float
    now_details_line_height: float
    table_font: int
    cell_padding: str


DEVICES = [
    DeviceProfile(
        key="trmnl",
        label="TRMNL OG (via Screenshot plugin)",
        filename="index.html",
        width=800, height=480,
        page_padding="26px 34px",
        place_font=36, updated_font=20,
        header_pad_bottom=12, header_margin_bottom=20,
        now_gap=44, now_margin_bottom=29,
        temp_font=104, now_details_font=26, now_details_line_height=1.55,
        table_font=22, cell_padding="7px 8px",
    ),
    DeviceProfile(
        key="inkplate",
        label="Inkplate 6 V2 (800x600, 3-bit grayscale)",
        filename="inkplate.html",
        width=800, height=600,
        # 120px taller than the TRMNL panel -- sized up proportionally to
        # use the extra vertical room rather than just leaving it blank.
        page_padding="32px 34px",
        place_font=40, updated_font=22,
        header_pad_bottom=12, header_margin_bottom=24,
        now_gap=44, now_margin_bottom=36,
        temp_font=120, now_details_font=28, now_details_line_height=1.55,
        table_font=24, cell_padding="9px 8px",
    ),
]


# ============================================================================
# Fetching FMI data (shared by all devices)
# ============================================================================

def fetch_forecast(place: str) -> list[dict]:
    url = (
        "https://opendata.fmi.fi/wfs?service=WFS&version=2.0.0&request=getFeature"
        "&storedquery_id=fmi::forecast::harmonie::surface::point::simple"
        f"&place={urllib.parse.quote(place)}"
        f"&parameters={urllib.parse.quote(PARAMETERS)}"
    )
    with urllib.request.urlopen(url, timeout=15) as resp:
        xml_bytes = resp.read()

    root = ET.fromstring(xml_bytes)
    elements = root.findall(".//BsWfs:BsWfsElement", NS)
    if not elements:
        raise RuntimeError(f"FMI returned no forecast elements for place={place!r}")

    by_time: dict[str, dict] = {}
    for el in elements:
        time = el.find("BsWfs:Time", NS).text
        name = el.find("BsWfs:ParameterName", NS).text
        value = el.find("BsWfs:ParameterValue", NS).text
        row = by_time.setdefault(time, {"time": time})
        try:
            row[name] = float(value)
        except (TypeError, ValueError):
            row[name] = None

    return [by_time[t] for t in sorted(by_time)]


def to_helsinki(iso_time: str) -> datetime:
    dt_utc = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
    return dt_utc.astimezone(HELSINKI_TZ)


def weather_text(code: float | None) -> str:
    if code is None:
        return "—"
    return WEATHER_SYMBOLS_FI.get(int(code), f"Koodi {int(code)}")


def fmt(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def select_checkpoint_rows(forecast: list[dict], now_local: datetime,
                            target_hours=TARGET_HOURS, count: int = 4) -> list[tuple[datetime, dict]]:
    """Pick the next occurrence of each target local hour, in chronological order."""
    candidates = []
    for f in forecast:
        dt_local = to_helsinki(f["time"])
        if dt_local.hour in target_hours and dt_local > now_local:
            candidates.append((dt_local, f))
    candidates.sort(key=lambda pair: pair[0])
    return candidates[:count]


# ============================================================================
# Rendering (one call per device, using its DeviceProfile for sizing)
# ============================================================================

def render_html(place: str, forecast: list[dict], device: DeviceProfile) -> str:
    now_entry = forecast[0]
    now_local = to_helsinki(now_entry["time"])
    checkpoints = select_checkpoint_rows(forecast, now_local)

    rows = "\n".join(
        f"""
        <tr>
          <td>{dt_local.strftime('%H:%M')}</td>
          <td>{fmt(f.get('Temperature'))}°</td>
          <td>{weather_text(f.get('WeatherSymbol3'))}</td>
          <td>{fmt(f.get('WindSpeedMS'))} m/s</td>
          <td>{fmt(f.get('Precipitation1h'))} mm</td>
        </tr>"""
        for dt_local, f in checkpoints
    )

    updated_local = datetime.now(HELSINKI_TZ).strftime("%H:%M")

    return f"""<!DOCTYPE html>
<html lang="fi">
<head>
<meta charset="utf-8">
<title>{place} sää ({device.label})</title>
<style>
  html, body {{
    margin: 0; padding: 0;
    width: {device.width}px; height: {device.height}px;
    background: #ffffff; color: #000000;
    font-family: 'DejaVu Sans', Arial, sans-serif;
    overflow: hidden;
  }}
  .page {{ padding: {device.page_padding}; box-sizing: border-box; width: 100%; height: 100%; }}
  .header {{
    display: flex; justify-content: space-between; align-items: baseline;
    border-bottom: 3px solid #000;
    padding-bottom: {device.header_pad_bottom}px;
    margin-bottom: {device.header_margin_bottom}px;
  }}
  .place {{ font-size: {device.place_font}px; font-weight: 700; }}
  .updated {{ font-size: {device.updated_font}px; }}
  .now {{
    display: flex; align-items: center;
    gap: {device.now_gap}px;
    margin-bottom: {device.now_margin_bottom}px;
  }}
  .temp {{ font-size: {device.temp_font}px; font-weight: 700; line-height: 1; }}
  .now-details {{ font-size: {device.now_details_font}px; line-height: {device.now_details_line_height}; }}
  table {{ width: 100%; border-collapse: collapse; font-size: {device.table_font}px; }}
  th {{ text-align: left; border-bottom: 2px solid #000; padding: {device.cell_padding}; }}
  td {{ padding: {device.cell_padding}; border-bottom: 1px solid #666; }}
</style>
</head>
<body>
  <div class="page">
    <div class="header">
      <span class="place">{place}</span>
      <span class="updated">Päivitetty {updated_local}</span>
    </div>

    <div class="now">
      <div class="temp">{fmt(now_entry.get('Temperature'))}°</div>
      <div class="now-details">
        {weather_text(now_entry.get('WeatherSymbol3'))}<br>
        Tuntuu kuin {fmt(now_entry.get('FeelsLike'))}°<br>
        Tuuli {fmt(now_entry.get('WindSpeedMS'))} m/s &nbsp;·&nbsp; Sade {fmt(now_entry.get('Precipitation1h'))} mm
      </div>
    </div>

    <table>
      <thead>
        <tr><th>Aika</th><th>Lämpötila</th><th>Sää</th><th>Tuuli</th><th>Sade</th></tr>
      </thead>
      <tbody>{rows}
      </tbody>
    </table>
  </div>
</body>
</html>
"""


# ============================================================================
# Entry point -- fetch once, render once per device
# ============================================================================

def main() -> None:
    place = os.environ.get("FMI_PLACE", "Helsinki")
    output_dir = os.environ.get("OUTPUT_DIR", "docs")

    forecast = fetch_forecast(place)
    os.makedirs(output_dir, exist_ok=True)

    for device in DEVICES:
        html = render_html(place, forecast, device)
        output_path = os.path.join(output_dir, device.filename)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[{device.key}] wrote {output_path} ({device.label}) for {place}, "
              f"{len(forecast)} forecast steps")


if __name__ == "__main__":
    main()
