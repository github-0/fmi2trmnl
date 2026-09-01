#!/usr/bin/env python3
"""
Generate a static HTML weather page from FMI open data, sized for a
TRMNL e-ink display. Meant to be run on a schedule (e.g. GitHub
Actions) and the output published as a static file (e.g. GitHub
Pages) — TRMNL's Screenshot plugin (or LaraPaper's Screenshot
handler) then periodically screenshots that URL. No server, no
TRMNL-specific markup.

Uses only the Python standard library.

Env vars:
  FMI_PLACE     - Finnish place name (default: Turku)
  OUTPUT_PATH   - where to write the HTML (default: docs/index.html)
"""

import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

NS = {"BsWfs": "http://xml.fmi.fi/schema/wfs/2.0"}
PARAMETERS = "Temperature,FeelsLike,WindSpeedMS,WindDirection,Precipitation1h,WeatherSymbol3"

# Source: FMI open data documentation (ilmatieteenlaitos.fi/latauspalvelun-pikaohje),
# cross-checked against fmidev/opendata-resources filenames.
WEATHER_SYMBOLS = {
    1: "Clear", 2: "Partly cloudy", 3: "Cloudy",
    21: "Light showers", 22: "Showers", 23: "Heavy showers",
    31: "Light rain", 32: "Rain", 33: "Heavy rain",
    41: "Light snow showers", 42: "Snow showers", 43: "Heavy snow showers",
    51: "Light snow", 52: "Snowfall", 53: "Heavy snow",
    61: "Thundershowers", 62: "Heavy thundershowers",
    63: "Thunder", 64: "Heavy thunder",
    71: "Light sleet showers", 72: "Sleet showers", 73: "Heavy sleet showers",
    81: "Light sleet", 82: "Sleet", 83: "Heavy sleet",
    91: "Mist", 92: "Fog",
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


def weather_text(code: float | None) -> str:
    if code is None:
        return "—"
    return WEATHER_SYMBOLS.get(int(code), f"Code {int(code)}")


def fmt(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def render_html(place: str, forecast: list[dict]) -> str:
    now = forecast[0]
    upcoming = forecast[1:6]  # next 5 forecast steps

    rows = "\n".join(
        f"""
        <tr>
          <td>{datetime.fromisoformat(f['time'].replace('Z', '+00:00')).astimezone().strftime('%H:%M')}</td>
          <td>{fmt(f.get('Temperature'))}°</td>
          <td>{weather_text(f.get('WeatherSymbol3'))}</td>
          <td>{fmt(f.get('WindSpeedMS'), 1)} m/s</td>
          <td>{fmt(f.get('Precipitation1h'), 1)} mm</td>
        </tr>"""
        for f in upcoming
    )

    updated_local = datetime.now(timezone.utc).astimezone().strftime("%H:%M")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{place} weather</title>
<style>
  html, body {{
    margin: 0; padding: 0;
    width: 800px; height: 480px;
    background: #ffffff; color: #000000;
    font-family: 'DejaVu Sans', Arial, sans-serif;
    overflow: hidden;
  }}
  .page {{ padding: 24px 32px; box-sizing: border-box; width: 100%; height: 100%; }}
  .header {{
    display: flex; justify-content: space-between; align-items: baseline;
    border-bottom: 3px solid #000; padding-bottom: 10px; margin-bottom: 18px;
  }}
  .place {{ font-size: 34px; font-weight: 700; }}
  .updated {{ font-size: 18px; }}
  .now {{ display: flex; align-items: baseline; gap: 40px; margin-bottom: 26px; }}
  .temp {{ font-size: 96px; font-weight: 700; line-height: 1; }}
  .now-details {{ font-size: 24px; line-height: 1.5; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 20px; }}
  th {{ text-align: left; border-bottom: 2px solid #000; padding: 6px 8px; }}
  td {{ padding: 6px 8px; border-bottom: 1px solid #666; }}
</style>
</head>
<body>
  <div class="page">
    <div class="header">
      <span class="place">{place}</span>
      <span class="updated">Updated {updated_local}</span>
    </div>

    <div class="now">
      <div class="temp">{fmt(now.get('Temperature'))}°</div>
      <div class="now-details">
        {weather_text(now.get('WeatherSymbol3'))}<br>
        Feels like {fmt(now.get('FeelsLike'))}°<br>
        Wind {fmt(now.get('WindSpeedMS'), 1)} m/s &nbsp;·&nbsp; Precip {fmt(now.get('Precipitation1h'), 1)} mm
      </div>
    </div>

    <table>
      <thead>
        <tr><th>Time</th><th>Temp</th><th>Conditions</th><th>Wind</th><th>Precip</th></tr>
      </thead>
      <tbody>{rows}
      </tbody>
    </table>
  </div>
</body>
</html>
"""


def main() -> None:
    place = os.environ.get("FMI_PLACE", "Turku")
    output_path = os.environ.get("OUTPUT_PATH", "docs/index.html")

    forecast = fetch_forecast(place)
    html = render_html(place, forecast)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Wrote {output_path} for {place}, {len(forecast)} forecast steps")


if __name__ == "__main__":
    main()
