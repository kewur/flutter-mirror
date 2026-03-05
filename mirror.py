#!/usr/bin/env python3
"""
Android screen mirror server.
Streams an Android device screen to a web browser via scrcpy + ImageMagick,
with touch input replay via ADB.
"""

import asyncio
import json
import os
import sys
import argparse
from pathlib import Path

from aiohttp import web

ADB_PATH = os.environ.get("ADB_PATH", "adb")
SCRCPY_PATH = os.environ.get("SCRCPY_PATH", str(Path.home() / ".local/share/scrcpy/scrcpy"))
SERIAL = os.environ.get("ANDROID_SERIAL", None)
TARGET_FPS = 15
JPEG_QUALITY = 50
SCRCPY_MAX_SIZE = 480
STATIC_DIR = Path(__file__).parent / "static"

clients: set[web.WebSocketResponse] = set()
screen_size = (1080, 2400)
running = True
scrcpy_proc = None
scrcpy_wid = None


def adb_cmd(*args):
    cmd = [ADB_PATH]
    if SERIAL:
        cmd.extend(["-s", SERIAL])
    cmd.extend(args)
    return cmd


async def detect_screen_size():
    global screen_size
    try:
        proc = await asyncio.create_subprocess_exec(
            *adb_cmd("shell", "wm", "size"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        for line in stdout.decode().strip().split("\n"):
            if "Override size" in line or "Physical size" in line:
                dims = line.split(":")[-1].strip().split("x")
                screen_size = (int(dims[0]), int(dims[1]))
                if "Override" in line:
                    break
    except Exception as e:
        print(f"Warning: Could not detect screen size: {e}", file=sys.stderr)
    print(f"Device screen: {screen_size[0]}x{screen_size[1]}")


async def start_scrcpy():
    """Start scrcpy and return its X11 window ID."""
    global scrcpy_proc, scrcpy_wid

    cmd = [SCRCPY_PATH, "--max-size", str(SCRCPY_MAX_SIZE),
           "--max-fps", str(TARGET_FPS + 5),
           "--no-audio", "--window-title", "FlutterMirror",
           "--render-driver", "software"]
    if SERIAL:
        cmd.extend(["--serial", SERIAL])

    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")

    scrcpy_proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
    )

    # Wait for window
    for _ in range(30):
        await asyncio.sleep(0.5)
        wid = await _find_window("FlutterMirror")
        if wid:
            scrcpy_wid = wid
            await _wm_setup()
            print(f"scrcpy window: {wid}")
            return wid

    print("ERROR: scrcpy window not found", file=sys.stderr)
    return None


async def _find_window(title):
    try:
        proc = await asyncio.create_subprocess_exec(
            "xdotool", "search", "--name", title,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
        )
        stdout, _ = await proc.communicate()
        wids = stdout.decode().strip().split("\n")
        return wids[0] if wids and wids[0] else None
    except Exception:
        return None


async def _wm_setup():
    """Move scrcpy window to scratchpad and resize to match phone aspect ratio."""
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    # Try i3 scratchpad (works on i3/sway)
    for cmd in [
        'i3-msg [title="FlutterMirror"] move scratchpad',
        'i3-msg [title="FlutterMirror"] scratchpad show',
    ]:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL, env=env,
        )
        await proc.communicate()
    # Resize to match phone aspect ratio (prevents coordinate mapping issues)
    await asyncio.sleep(0.3)
    w, h = screen_size
    win_h = 1080
    win_w = int(win_h * w / h)
    if scrcpy_wid:
        proc = await asyncio.create_subprocess_exec(
            "xdotool", "windowsize", scrcpy_wid, str(win_w), str(win_h),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL, env=env,
        )
        await proc.communicate()


async def capture_frame():
    """Capture scrcpy window using ImageMagick import."""
    if not scrcpy_wid:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "import", "-window", scrcpy_wid,
            "-quality", str(JPEG_QUALITY),
            "jpeg:-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0 and stdout:
            return stdout
    except Exception:
        pass
    return None


async def capture_loop():
    """Capture and broadcast frames."""
    interval = 1.0 / TARGET_FPS
    while running:
        if not clients:
            await asyncio.sleep(0.1)
            continue
        frame = await capture_frame()
        if frame:
            dead = set()
            for ws in clients.copy():
                try:
                    await ws.send_bytes(frame)
                except Exception:
                    dead.add(ws)
            clients.difference_update(dead)
        await asyncio.sleep(interval)


async def handle_ws(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    clients.add(ws)
    print(f"Client connected ({len(clients)} total)")

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                await handle_input(msg.data)
            elif msg.type == web.WSMsgType.ERROR:
                print(f"WebSocket error: {ws.exception()}", file=sys.stderr)
    finally:
        clients.discard(ws)
        print(f"Client disconnected ({len(clients)} total)")
    return ws


scrcpy_win_size = (480, 960)


async def detect_scrcpy_win_size():
    """Get the scrcpy window content size for coordinate mapping."""
    global scrcpy_win_size
    if not scrcpy_wid:
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            "xdotool", "getwindowgeometry", "--shell", scrcpy_wid,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
        )
        stdout, _ = await proc.communicate()
        for line in stdout.decode().strip().split("\n"):
            k, _, v = line.partition("=")
            if k == "WIDTH":
                scrcpy_win_size = (int(v), scrcpy_win_size[1])
            elif k == "HEIGHT":
                scrcpy_win_size = (scrcpy_win_size[0], int(v))
        print(f"scrcpy window size: {scrcpy_win_size[0]}x{scrcpy_win_size[1]}")
    except Exception:
        pass


async def handle_input(data):
    try:
        event = json.loads(data)
    except json.JSONDecodeError:
        return

    action = event.get("action")

    if action == "replay":
        events = event.get("events", [])
        asyncio.create_task(_replay_gestures(events))

    elif action == "key":
        keycode = str(event.get("keycode", ""))
        await _adb_input("keyevent", keycode)

    elif action == "text":
        text = event.get("text", "")
        safe = text.replace(" ", "%s")
        await _adb_input("text", safe)


async def _replay_gestures(events):
    """Replay recorded touch events via adb shell input.

    Gestures are recorded on the client with normalized (0-1) coordinates,
    then mapped to device pixel coordinates here. Each stroke (down->moves->up)
    becomes either an `input tap` or `input swipe` command.
    """
    if not events:
        return

    sw, sh = screen_size

    # Split events into strokes (down->moves->up)
    strokes = []
    current = []
    for ev in events:
        current.append(ev)
        if ev["type"] == "up":
            strokes.append(current)
            current = []
    if current:
        strokes.append(current)

    # Build a single shell script with all strokes
    cmds = []
    for stroke in strokes:
        start = stroke[0]
        end = stroke[-1]
        x1 = int(start["x"] * sw)
        y1 = int(start["y"] * sh)
        x2 = int(end["x"] * sw)
        y2 = int(end["y"] * sh)
        duration = max(end["t"] - start["t"], 50)

        if abs(x2 - x1) < 30 and abs(y2 - y1) < 30 and duration < 300:
            cmds.append(f"input tap {x1} {y1}")
        else:
            cmds.append(f"input swipe {x1} {y1} {x2} {y2} {duration}")

    script = "\n".join(cmds)
    proc = await asyncio.create_subprocess_exec(
        *adb_cmd("shell", "sh"),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate(script.encode())


async def _adb_input(*args):
    """Send input via ADB."""
    proc = await asyncio.create_subprocess_exec(
        *adb_cmd("shell", "input", *args),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    asyncio.create_task(_wait_proc(proc))


async def _wait_proc(proc):
    try:
        await proc.communicate()
    except Exception:
        pass


async def handle_index(request):
    return web.FileResponse(STATIC_DIR / "index.html")


async def on_startup(app):
    await detect_screen_size()
    wid = await start_scrcpy()
    if not wid:
        print("FATAL: Could not start scrcpy", file=sys.stderr)
        sys.exit(1)
    await detect_scrcpy_win_size()
    app["capture_task"] = asyncio.create_task(capture_loop())
    print(f"Streaming at ~{TARGET_FPS}fps via scrcpy + import")


async def on_cleanup(app):
    global running, scrcpy_proc
    running = False

    if scrcpy_proc:
        scrcpy_proc.terminate()
        try:
            await asyncio.wait_for(scrcpy_proc.wait(), timeout=3)
        except (asyncio.TimeoutError, ProcessLookupError):
            try:
                scrcpy_proc.kill()
            except ProcessLookupError:
                pass

    task = app.get("capture_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def create_app():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/ws", handle_ws)
    app.router.add_static("/static/", STATIC_DIR)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main():
    parser = argparse.ArgumentParser(description="Android screen mirror server")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--quality", type=int, default=50, help="JPEG quality 1-100")
    parser.add_argument("--max-size", type=int, default=480, help="Max scrcpy resolution")
    parser.add_argument("--adb", default=None)
    parser.add_argument("--scrcpy", default=None)
    parser.add_argument("--serial", default=None)
    args = parser.parse_args()

    global TARGET_FPS, JPEG_QUALITY, SCRCPY_MAX_SIZE, ADB_PATH, SCRCPY_PATH, SERIAL
    TARGET_FPS = args.fps
    JPEG_QUALITY = args.quality
    SCRCPY_MAX_SIZE = args.max_size
    if args.adb:
        ADB_PATH = args.adb
    if args.scrcpy:
        SCRCPY_PATH = args.scrcpy
    if args.serial:
        SERIAL = args.serial

    os.environ.setdefault("DISPLAY", ":0")

    app = create_app()
    print(f"Mirror server: http://{args.host}:{args.port}")
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
