#!/usr/bin/env bash
# Manual login / recovery: opens the bot's Chrome profile on :99 and exposes it over
# VNC on localhost only. Reach it from your PC with an SSH tunnel.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
DATA=${DATA_DIR:-$HERE/data}
mkdir -p "$DATA/profile"
export DISPLAY=:99 TZ=Europe/Lisbon LANG=pt_PT.UTF-8

if systemctl --user is-active --quiet insta-bot.service; then
  echo "insta-bot.service is running; stop it first: systemctl --user stop insta-bot" >&2
  exit 1
fi
exec 9>"$DATA/profile.lock"
flock -n 9 || { echo "profile is locked by another process" >&2; exit 1; }

XVFB_PID=
if ! xdpyinfo -display :99 >/dev/null 2>&1; then
  Xvfb :99 -screen 0 1366x768x24 -nolisten tcp &
  XVFB_PID=$!
  sleep 1
fi

google-chrome --user-data-dir="$DATA/profile" --window-size=1366,768 --window-position=0,0 \
  --no-first-run >/dev/null 2>&1 &
CHROME_PID=$!

x11vnc -display :99 -localhost -nopw -forever -quiet >/dev/null 2>&1 &
VNC_PID=$!

cat <<EOF

VNC ready on localhost:5900. On your PC:
    ssh -L 5900:localhost:5900 $USER@$(hostname)
then connect a VNC viewer to localhost:5900.

Log in, pass any verification, open the group, copy the thread URL into .env
(THREAD_URL=https://www.instagram.com/direct/t/...), dismiss any popups.
Then close Chrome from its window (don't log out).
EOF

wait "$CHROME_PID" || true
kill "$VNC_PID" 2>/dev/null || true
[ -n "$XVFB_PID" ] && kill "$XVFB_PID" 2>/dev/null || true

if [ -e "$DATA/STOPPED" ]; then
  echo "Previous stop reason: $(cat "$DATA/STOPPED")"
  rm "$DATA/STOPPED"
  echo "Cleared data/STOPPED; the timer will start the bot again."
fi
echo "Done."
