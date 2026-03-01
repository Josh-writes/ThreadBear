#!/usr/bin/env bash
# Simple weather script for Natchez, MS using wttr.in
# Requires curl to be installed.

LOCATION="Natchez,MS"
# Fetch a concise one‑line weather summary (temp + condition)
# ?format=3 gives: <location>: <weather>, <temp>°C
WEATHER=$(curl -s "https://wttr.in/${LOCATION}?format=3")

if [ -z "$WEATHER" ]; then
  echo "Failed to retrieve weather data. Make sure you have internet access and curl installed." >&2
  exit 1
fi

echo "$WEATHER"
