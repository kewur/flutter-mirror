#!/bin/sh
cd "$(dirname "$0")"

ADB="${ADB:-$HOME/Android/Sdk/platform-tools/adb}"
SCRCPY="${SCRCPY:-$HOME/.local/share/scrcpy/scrcpy}"
PORT="${PORT:-8080}"
SERIAL="${SERIAL:-}"

# Create venv if missing
if [ ! -d .venv ]; then
    echo "Creating venv..."
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
fi

ARGS="--adb $ADB --scrcpy $SCRCPY --port $PORT"
[ -n "$SERIAL" ] && ARGS="$ARGS --serial $SERIAL"

exec .venv/bin/python3 -u mirror.py $ARGS
