#!/bin/bash
# SatTope startup script
# Starts phoenixd in the background, then launches the web app

cd "$(dirname "$0")"

# ── 1. Start phoenixd (merchant Lightning wallet) ──────────────────────────
PHOENIXD_BIN="${PHOENIXD_PATH:-$HOME/Downloads/phoenixd-0.7.3-macos-arm64/phoenixd}"

if [ ! -f "$PHOENIXD_BIN" ]; then
  echo "⚠️  phoenixd not found at $PHOENIXD_BIN"
  echo "   Set PHOENIXD_PATH in your environment or update start.sh"
else
  # Kill any existing phoenixd process
  pkill -f phoenixd 2>/dev/null

  echo "⚡ Starting phoenixd..."
  "$PHOENIXD_BIN" --agree-to-terms-of-service &
  PHOENIXD_PID=$!
  echo "   phoenixd PID: $PHOENIXD_PID"

  # Wait until phoenixd API is ready (up to 30s)
  echo "   Waiting for phoenixd to be ready..."
  for i in $(seq 1 30); do
    if curl -s -u ":$(grep http-password ~/.phoenix/phoenix.conf | cut -d= -f2)" \
        http://127.0.0.1:9740/getinfo > /dev/null 2>&1; then
      echo "   ✅ phoenixd ready"
      break
    fi
    sleep 1
  done
fi

# ── 2. Start the SatTope web app ───────────────────────────────────────────
echo "🚀 Starting SatTope..."
source .venv/bin/activate
exec uvicorn main:app --host 0.0.0.0 --port 8000
