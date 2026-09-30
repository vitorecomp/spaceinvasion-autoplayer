#!/usr/bin/env bash
set -euo pipefail

ACCOUNT_NAME="${SPACEINVASION_ACCOUNT:-default}"
PROFILE_DIR="/data/browser_profile/${ACCOUNT_NAME}"
SESSIONS_DIR="/data/sessions"
START_URL="${SPACEINVASION_START_URL:-https://spaceinvasion.bitmeup.com/?locale=en}"

# Never run Chromium as UID 0 so Chromium's native sandbox runs cleanly
# without --no-sandbox or --disable-setuid-sandbox.
if [[ "$(id -u)" -eq 0 ]]; then
  mkdir -p "$PROFILE_DIR" "$SESSIONS_DIR" /tmp/browser-home
  chown -R chrome:chrome /data/browser_profile "$SESSIONS_DIR" /tmp/browser-home 2>/dev/null || true
  exec su -s /bin/bash chrome -c "/usr/local/bin/browser-entrypoint.sh"
fi

export DISPLAY=:99
export HOME="${HOME_DIR:-/tmp/browser-home}"

mkdir -p "$PROFILE_DIR" "$SESSIONS_DIR" "$HOME/.fluxbox" "$HOME/.config" "$HOME/.cache" "$HOME/.pki"
chmod 700 /data/browser_profile "$PROFILE_DIR" "$SESSIONS_DIR" 2>/dev/null || true

# Configure Fluxbox without toolbar or background wallpaper popups
cat > "$HOME/.fluxbox/init" <<EOF
session.screen0.rootCommand: true
session.screen0.toolbar.visible: false
EOF

# Clean stale Chromium profile locks left across container restarts
rm -f "$PROFILE_DIR/SingletonLock" "$PROFILE_DIR/SingletonCookie" "$PROFILE_DIR/SingletonSocket"
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99

echo "[BROWSER] Starting Xvfb on :99 (1440x900x24) as UID $(id -u)..."
Xvfb :99 -screen 0 1440x900x24 -ac +extension GLX +render -noreset >/dev/null 2>&1 &
XVFB_PID=$!

for _ in $(seq 1 30); do
  if xdpyinfo -display :99 >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

fluxbox -display :99 >/dev/null 2>&1 &
x11vnc -display :99 -forever -shared -nopw -rfbport 5900 -quiet >/dev/null 2>&1 &
websockify --web=/usr/share/novnc 0.0.0.0:7900 127.0.0.1:5900 >/dev/null 2>&1 &

# Forward container CDP 0.0.0.0:9222 -> local Chromium 127.0.0.1:9223
socat TCP-LISTEN:9222,fork,reuseaddr,bind=0.0.0.0 TCP:127.0.0.1:9223 &

CHROMIUM_BIN="/usr/lib/chromium/chromium"
if [[ ! -x "$CHROMIUM_BIN" ]]; then
  CHROMIUM_BIN="$(command -v chromium)"
fi

echo "[BROWSER] noVNC ready at http://127.0.0.1:7900/vnc.html?autoconnect=true&resize=scale | CDP at 0.0.0.0:9222"

term_handler() {
  echo "[BROWSER] Shutting down Chromium and display services..."
  pkill -TERM -f chromium 2>/dev/null || true
  kill -TERM "$XVFB_PID" 2>/dev/null || true
  exit 0
}
trap term_handler SIGINT SIGTERM

while true; do
  rm -f "$PROFILE_DIR/SingletonLock" "$PROFILE_DIR/SingletonCookie" "$PROFILE_DIR/SingletonSocket"
  echo "[BROWSER] Launching Chromium with native sandbox and persistent profile $PROFILE_DIR..."
  "$CHROMIUM_BIN" \
    --disable-gpu \
    --password-store=basic \
    --disable-features=WebUIOmniboxPopup,OmniboxAimPopup \
    --no-first-run \
    --no-default-browser-check \
    --window-position=0,0 \
    --window-size=1440,900 \
    --remote-debugging-address=127.0.0.1 \
    --remote-debugging-port=9223 \
    --user-data-dir="$PROFILE_DIR" \
    "$START_URL" || true
  echo "[BROWSER] Chromium exited; restarting in 2s..."
  sleep 2
done
