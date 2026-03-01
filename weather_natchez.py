#!/usr/bin/env python3
"""
Weather script for Natchez, MS
Fetches current weather from wttr.in and displays it.
Requires the 'requests' library (install via pip if missing).
"""

import sys
import json
import requests

def get_weather(location: str) -> dict:
    """Fetch weather data from wttr.in in JSON format."""
    url = f"https://wttr.in/{location}?format=j1"
    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching weather data: {e}", file=sys.stderr)
        sys.exit(1)

def display_weather(data: dict):
    """Print a simple weather summary to the console."""
    current = data.get('current_condition', [{}])[0]
    area = data.get('nearest_area', [{}])[0].get('areaName', [{}])[0].get('value', 'Unknown')
    region = data.get('nearest_area', [{}])[0].get('region', [{}])[0].get('value', '')
    country = data.get('nearest_area', [{}])[0].get('country', [{}])[0].get('value', '')
    temp_c = current.get('temp_C', 'N/A')
    weather_desc = current.get('weatherDesc', [{}])[0].get('value', '')
    humidity = current.get('humidity', 'N/A')
    wind_kph = current.get('windspeedKmph', 'N/A')
    feels_like_c = current.get('FeelsLikeC', 'N/A')

    print(f"Weather for {area}, {region}, {country}:")
    print(f"  Temperature: {temp_c}°C (feels like {feels_like_c}°C)")
    print(f"  Condition: {weather_desc}")
    print(f"  Humidity: {humidity}%")
    print(f"  Wind: {wind_kph} km/h")

def main():
    location = "Natchez,MS"
    data = get_weather(location)
    display_weather(data)

if __name__ == "__main__":
    main()
