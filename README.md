# Flutter Mirror

Remote Android screen mirroring with touch input, designed for mobile Flutter development from anywhere.

Stream your Android device screen to a web browser on your phone, interact with it via touch, and manage Flutter builds — all orchestrated through an MCP server that AI agents (like Claude) can use.

## How it works

```
┌─────────────┐    scrcpy     ┌──────────────┐   ImageMagick   ┌──────────────┐
│   Android    │──────────────▶│  X11 Window  │────────────────▶│ JPEG frames  │
│   Device     │               │  (headless)  │    import       │              │
└─────────────┘               └──────────────┘                 └──────┬───────┘
       ▲                                                               │
       │ adb shell input                                    WebSocket  │
       │ (tap/swipe)                                        stream     │
       │                                                               ▼
┌──────┴───────┐                                           ┌──────────────────┐
│  mirror.py   │◀──── gesture replay ─────────────────────│  Browser on      │
│  (server)    │      (buffered touch events)              │  your phone      │
└──────────────┘                                           └──────────────────┘
```

1. **scrcpy** mirrors the Android screen into an X11 window (software renderer, no GPU needed)
2. **ImageMagick `import`** captures the window as JPEG at ~15fps
3. **aiohttp** streams frames over WebSocket to the browser
4. Touch gestures are **recorded on the client**, then **replayed on the server** via `adb shell input` after 1.5s of idle (or manually via the play button)

## Prerequisites

- **Linux** with X11 (tested on Debian with i3wm)
- **Android SDK** (adb) — or just platform-tools
- **scrcpy** — download from [GitHub releases](https://github.com/Genymobile/scrcpy/releases)
- **ImageMagick** — `apt install imagemagick`
- **xdotool** — `apt install xdotool`
- **Python 3.11+**

## Quick start

```bash
git clone https://github.com/kewur/flutter-mirror.git
cd flutter-mirror
adb devices  # make sure your device is connected
./start.sh
```

Then open `http://<your-ip>:8080` on your phone.

The script auto-creates the Python venv and installs dependencies on first run. Override defaults with env vars:

```bash
SERIAL=emulator-5554 PORT=9090 ./start.sh
```

### As an MCP server (for AI agents)

Register with Claude Code:

```bash
claude mcp add \
  -e ANDROID_HOME=/path/to/Android/Sdk \
  -e FLUTTER_PATH=/path/to/flutter/bin/flutter \
  -s user \
  flutter-mirror \
  /path/to/flutter-mirror/.venv/bin/python3 \
  /path/to/flutter-mirror/mcp_server.py
```

Available MCP tools:

| Tool | Description |
|------|-------------|
| `list_devices` | List connected ADB devices |
| `set_device` | Target a specific device |
| `list_emulators` | List available AVDs |
| `start_emulator` | Start an emulator (headless) |
| `stop_emulator` | Stop the emulator |
| `start_mirror` | Start the screen mirror web server |
| `stop_mirror` | Stop the mirror |
| `screenshot` | Take a screenshot (returns image) |
| `flutter_run` | Run a Flutter project |
| `hot_reload` | Trigger hot reload |
| `hot_restart` | Trigger hot restart |
| `stop_flutter` | Stop the Flutter app |
| `flutter_log` | Get recent Flutter output |
| `tap` | Tap at pixel coordinates |
| `swipe` | Swipe between coordinates |
| `press_key` | Press an Android key |
| `input_text` | Type text |
| `session_status` | Get status of everything |

### Web client controls

- **Touch/swipe** on the screen — gestures are buffered and shown as blue preview lines
- **Play button (▶)** — manually send buffered gestures immediately
- **Back (◁)** / **Home (○)** / **Recents (□)** — Android nav buttons
- **Keyboard (⌫)** — toggle text input bar

### Remote access with Tailscale

This was built to be used remotely. Install [Tailscale](https://tailscale.com) on your desktop and phone, then access the mirror from anywhere:

1. Start the mirror server on your desktop (where the Android device is connected via USB)
2. Open `http://<tailscale-ip>:8080` on your phone's browser
3. Use an SSH app like [Termius](https://termius.com) to run Claude Code on the desktop, which can use the MCP server to manage Flutter builds + the mirror

The gesture recording approach is key here — since touches are buffered locally and replayed on the server with original timing, network latency doesn't affect gesture accuracy. You draw your swipe, it gets sent as a batch, and replayed exactly as you performed it.

### Tips

- FPS and connection status are shown in the browser
- The scrcpy window is automatically moved to the i3 scratchpad to stay out of the way

## Configuration

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | 8080 | Web server port |
| `--host` | 0.0.0.0 | Bind address |
| `--fps` | 15 | Target capture FPS |
| `--quality` | 50 | JPEG quality (1-100) |
| `--max-size` | 480 | Max scrcpy resolution |
| `--adb` | `adb` | Path to adb binary |
| `--scrcpy` | `~/.local/share/scrcpy/scrcpy` | Path to scrcpy binary |
| `--serial` | auto | ADB device serial |

## License

MIT
