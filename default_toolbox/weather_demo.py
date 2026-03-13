#!/usr/bin/env python3
"""
weather_demo.py
---------------
Fetches the current weather for a location using the free Open-Meteo API.
No API key required.

Usage:
  python weather_demo.py                   # defaults to New York City
  python weather_demo.py <lat> <lon>       # custom coordinates
"""

import sys
import json
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

def get_weather(lat: float, lon: float) -> dict:
    """Call Open-Meteo's current_weather endpoint and return the parsed JSON dict."""
    base_url = "https://api.open-meteo.com/v1/forecast"
    params = (
        f"?latitude={lat}&longitude={lon}"
        "&current_weather=true"
        "&timezone=auto"
    )
    url = base_url + params
    try:
        req = Request(url, headers={"User-Agent": "weather-demo/1.0"})
        with urlopen(req, timeout=10) as resp:
            data = json.load(resp)
        return data.get("current_weather", {})
    except (URLError, HTTPError) as e:
        sys.stderr.write(f"Error contacting Open-Meteo: {e}\n")
        return {}

def readable_weather(info: dict) -> str:
    """Convert the raw JSON into a human-readable string."""
    if not info:
        return "No data received."
    weather_codes = {
        0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
        45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle", 53: "Moderate drizzle",
        55: "Dense drizzle", 56: "Light freezing drizzle", 57: "Dense freezing drizzle",
        61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain", 66: "Light freezing rain",
        67: "Heavy freezing rain", 71: "Slight snow fall", 73: "Moderate snow fall",
        75: "Heavy snow fall", 77: "Snow grains", 80: "Slight rain showers",
        81: "Moderate rain showers", 82: "Violent rain showers", 85: "Slight snow showers",
        86: "Heavy snow showers", 95: "Thunderstorm", 96: "Thunderstorm with slight hail",
        99: "Thunderstorm with heavy hail",
    }
    temp = info.get("temperature")
    wind = info.get("windspeed")
    gust = info.get("windgusts", "N/A")
    direction = info.get("winddirection")
    code = info.get("weathercode")
    description = weather_codes.get(code, "Unknown")
    return (
        f"Time (local): {info.get('time')}\n"
        f"Temperature: {temp} C\n"
        f"Wind: {wind} km/h (gusts {gust}) from {direction} degrees\n"
        f"Condition: {description} (code {code})"
    )

if __name__ == "__main__":
    default_lat, default_lon = 40.7128, -74.0060  # New York City
    try:
        lat = float(sys.argv[1]) if len(sys.argv) > 1 else default_lat
        lon = float(sys.argv[2]) if len(sys.argv) > 2 else default_lon
    except ValueError:
        sys.stderr.write("Latitude and longitude must be numbers.\n")
        sys.exit(1)

    weather = get_weather(lat, lon)
    print(readable_weather(weather))
