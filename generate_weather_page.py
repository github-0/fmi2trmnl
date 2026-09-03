#!/usr/bin/env python3
"""
Generate a static HTML weather page (Finnish) from FMI open data, sized
for a TRMNL e-ink display. Meant to be run on a schedule (e.g. GitHub
Actions) and the output published as a static file (e.g. GitHub Pages)
-- TRMNL's Screenshot plugin (or LaraPaper's Screenshot handler) then
periodically screenshots that URL. No server, no TRMNL-specific markup.

Uses only the Python standard library (zoneinfo needs the system tz
database, present by default on Ubuntu GitHub Actions runners).

Env vars:
  FMI_PLACE     - Finnish place name (default: Helsinki)
  OUTPUT_PATH   - where to write the HTML (default: docs/index.html)
"""

import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
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


def render_html(place: str, forecast: list[dict]) -> str:
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
<title>{place} sää</title>
<style>
  html, body {{
    margin: 0; padding: 0;
    width: 800px; height: 480px;
    background: #ffffff; color: #000000;
    font-family: 'DejaVu Sans', Arial, sans-serif;
    overflow: hidden;
  }}
  .page {{ padding: 26px 34px; box-sizing: border-box; width: 100%; height: 100%; }}
  .header {{
    display: flex; justify-content: space-between; align-items: baseline;
    border-bottom: 3px solid #000; padding-bottom: 12px; margin-bottom: 20px;
  }}
  .place {{ font-size: 36px; font-weight: 700; }}
  .updated {{ font-size: 20px; }}
  .now {{ display: flex; align-items: center; gap: 44px; margin-bottom: 29px; }}
  .temp {{ font-size: 104px; font-weight: 700; line-height: 1; }}
  .now-details {{ font-size: 26px; line-height: 1.55; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 22px; }}
  th {{ text-align: left; border-bottom: 2px solid #000; padding: 7px 8px; }}
  td {{ padding: 7px 8px; border-bottom: 1px solid #666; }}
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


def main() -> None:
    place = os.environ.get("FMI_PLACE", "Helsinki")
    output_path = os.environ.get("OUTPUT_PATH", "docs/index.html")

    forecast = fetch_forecast(place)
    html = render_html(place, forecast)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Wrote {output_path} for {place}, {len(forecast)} forecast steps")


if __name__ == "__main__":
    main()
