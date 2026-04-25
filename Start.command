#!/bin/bash
# Double-click this file in Finder to start the app and open your browser.
cd "$(dirname "$0")" || exit 1

if ! command -v python3 &>/dev/null; then
  echo "python3 was not found. Install Python 3 or run: xcode-select --install"
  echo ""
  read -r -p "Press Enter to close…"
  exit 1
fi

PORT="${PORT:-3000}"
echo "Starting flood zone checker on port ${PORT}…"
echo "Leave this window open. Press Ctrl+C to stop the server."
echo ""

(sleep 1.5 && open "http://127.0.0.1:${PORT}/") &
export PORT
exec python3 serve.py
