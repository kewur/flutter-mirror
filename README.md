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

## Setup

```bash
# Clone
git clone https://github.com/kewur/flutter-mirror.git
cd flutter-mirror

# Create venv and install deps
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Make sure your Android device is connected
adb devices
```

## Usage

### Standalone mirror server

```bash
.venv/bin/python3 mirror.py \
  --adb /path/to/adb \
  --scrcpy /path/to/scrcpy \
  --serial DEVICE_SERIAL  # optional, defaults to first device
```

Then open `http://<your-ip>:8080` on your phone.

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

### Tips

- Works great over **Tailscale** for remote access from anywhere
- The gesture recording approach means touch latency doesn't matter — your gestures are replayed with original timing on the server
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
