#!/usr/bin/env python3
"""
Flutter Mirror MCP Server.
Tools for managing Android emulators, screen mirroring, and Flutter dev workflow.
"""

import asyncio
import base64
import os
import sys
from io import BytesIO
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image as MCPImage

ANDROID_SDK = os.environ.get("ANDROID_HOME", os.path.expanduser("~/Android/Sdk"))
ADB = os.path.join(ANDROID_SDK, "platform-tools", "adb")
EMULATOR = os.path.join(ANDROID_SDK, "emulator", "emulator")
FLUTTER = os.environ.get("FLUTTER_PATH", os.path.expanduser("~/Programs/flutter/bin/flutter"))
SCRCPY = os.path.expanduser("~/.local/share/scrcpy/scrcpy")
MIRROR_SCRIPT = Path(__file__).parent / "mirror.py"
WORK_DIR = Path(__file__).parent
FLUTTER_LOG = WORK_DIR / "flutter.log"
MIRROR_PORT = 8080

emulator_proc = None
mirror_proc = None
flutter_proc = None
flutter_log_fh = None
target_serial = None  # ADB serial of the target device (emulator or physical)

mcp = FastMCP("flutter-mirror")


async def run_adb(*args):
    cmd = [ADB]
    if target_serial:
        cmd.extend(["-s", target_serial])
    cmd.extend(args)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return stdout.decode().strip(), stderr.decode().strip(), proc.returncode


async def get_tailscale_ip():
    try:
        proc = await asyncio.create_subprocess_exec(
            "tailscale", "ip", "-4",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip()
    except Exception:
        return "localhost"


async def wait_for_boot(timeout=120):
    for _ in range(timeout):
        out, _, rc = await run_adb("shell", "getprop", "sys.boot_completed")
        if out.strip() == "1":
            return True
        await asyncio.sleep(1)
    return False


async def _find_emulator_serial():
    """Find the serial of a running emulator."""
    proc = await asyncio.create_subprocess_exec(
        ADB, "devices",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    for line in stdout.decode().strip().split("\n")[1:]:
        if line.strip() and "emulator" in line.split()[0]:
            return line.split()[0]
    return None


async def get_screen_size():
    out, _, _ = await run_adb("shell", "wm", "size")
    for line in out.split("\n"):
        if "Override size" in line or "Physical size" in line:
            dims = line.split(":")[-1].strip().split("x")
            return int(dims[0]), int(dims[1])
    return 1080, 2400


@mcp.tool()
async def set_device(serial: str) -> str:
    """Set which ADB device to target for all commands (mirror, screenshot, tap, flutter, etc.).

    Args:
        serial: Device serial from list_devices (e.g. 'emulator-5554' or 'LGLS9987c1f4947')
    """
    global target_serial
    target_serial = serial
    return f"Target device set to: {serial}"


@mcp.tool()
async def list_emulators() -> str:
    """List available Android emulator AVDs."""
    proc = await asyncio.create_subprocess_exec(
        EMULATOR, "-list-avds",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    avds = stdout.decode().strip()
    return f"Available AVDs:\n{avds}" if avds else "No AVDs found."


@mcp.tool()
async def list_devices() -> str:
    """List connected ADB devices (emulators and physical)."""
    out, _, _ = await run_adb("devices", "-l")
    return out


@mcp.tool()
async def start_emulator(avd_name: str, no_window: bool = True) -> str:
    """Start an Android emulator.

    Args:
        avd_name: AVD name from list_emulators
        no_window: If true, run headless (no GUI window on desktop)
    """
    global emulator_proc, target_serial

    if emulator_proc and emulator_proc.returncode is None:
        return "Emulator already running. Use stop_emulator first."

    # Verify AVD exists
    proc = await asyncio.create_subprocess_exec(
        EMULATOR, "-list-avds", stdout=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    avds = stdout.decode().strip().split("\n")
    if avd_name not in avds:
        return f"AVD '{avd_name}' not found. Available: {', '.join(avds)}"

    cmd = [EMULATOR, "-avd", avd_name, "-no-audio", "-no-boot-anim"]
    if no_window:
        cmd.append("-no-window")

    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")

    emulator_proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
    )

    booted = await wait_for_boot(timeout=120)
    if booted:
        # Auto-select the emulator as target device
        serial = await _find_emulator_serial()
        if serial:
            target_serial = serial
        return f"Emulator '{avd_name}' started and booted. Target set to {serial or 'unknown'}."
    return "Emulator started but boot not confirmed yet. It may still be loading."


@mcp.tool()
async def stop_emulator() -> str:
    """Stop the running Android emulator."""
    global emulator_proc
    await run_adb("emu", "kill")
    if emulator_proc:
        try:
            emulator_proc.terminate()
            await asyncio.wait_for(emulator_proc.wait(), timeout=10)
        except (asyncio.TimeoutError, ProcessLookupError):
            try:
                emulator_proc.kill()
            except ProcessLookupError:
                pass
        emulator_proc = None
    return "Emulator stopped."


@mcp.tool()
async def start_mirror(port: int = 8080) -> str:
    """Start the screen mirror web server. Returns URL to open on phone.

    Args:
        port: Port for the web server (default 8080)
    """
    global mirror_proc, MIRROR_PORT
    MIRROR_PORT = port

    if mirror_proc and mirror_proc.returncode is None:
        ip = await get_tailscale_ip()
        return f"Mirror already running at http://{ip}:{MIRROR_PORT}"

    venv_python = str(WORK_DIR / ".venv" / "bin" / "python3")
    cmd = [venv_python, str(MIRROR_SCRIPT), "--port", str(port), "--adb", ADB, "--scrcpy", SCRCPY]
    if target_serial:
        cmd.extend(["--serial", target_serial])
    mirror_proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    await asyncio.sleep(2)
    ip = await get_tailscale_ip()
    return f"Mirror started. Open on your phone: http://{ip}:{port}"


@mcp.tool()
async def stop_mirror() -> str:
    """Stop the screen mirror web server."""
    global mirror_proc
    if not mirror_proc or mirror_proc.returncode is not None:
        mirror_proc = None
        return "Mirror was not running."
    mirror_proc.terminate()
    try:
        await asyncio.wait_for(mirror_proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        mirror_proc.kill()
    mirror_proc = None
    return "Mirror stopped."


@mcp.tool()
async def screenshot() -> MCPImage:
    """Take a screenshot of the emulator/device screen. Returns the image."""
    proc = await asyncio.create_subprocess_exec(
        ADB, "exec-out", "screencap", "-p",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if not stdout:
        raise RuntimeError(f"Screenshot failed: {stderr.decode()}")
    return MCPImage(data=stdout, format="png")


@mcp.tool()
async def flutter_run(project_path: str, device_id: str = "") -> str:
    """Run a Flutter app on the connected emulator/device.

    Args:
        project_path: Absolute path to the Flutter project directory
        device_id: Optional device ID (from list_devices). Defaults to first available.
    """
    global flutter_proc, flutter_log_fh

    if flutter_proc and flutter_proc.returncode is None:
        return "Flutter already running. Use stop_flutter first, or hot_reload / hot_restart."

    project = Path(project_path)
    if not project.is_dir():
        return f"Path not found: {project_path}"
    if not (project / "pubspec.yaml").exists():
        return f"Not a Flutter project (no pubspec.yaml): {project_path}"

    cmd = [FLUTTER, "run"]
    effective_device = device_id or target_serial
    if effective_device:
        cmd.extend(["-d", effective_device])

    flutter_log_fh = open(FLUTTER_LOG, "w")
    flutter_proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(project),
        stdin=asyncio.subprocess.PIPE,
        stdout=flutter_log_fh,
        stderr=asyncio.subprocess.STDOUT,
    )

    # Poll log for startup completion
    for _ in range(180):
        await asyncio.sleep(1)
        if flutter_proc.returncode is not None:
            content = FLUTTER_LOG.read_text()
            return f"Flutter exited (code {flutter_proc.returncode}):\n{_tail(content, 20)}"
        try:
            content = FLUTTER_LOG.read_text()
            if "Flutter run key commands" in content or "Syncing files" in content:
                return f"Flutter app running.\n{_tail(content, 10)}"
        except Exception:
            continue

    return "Flutter started but couldn't confirm readiness. Use flutter_log to check output."


@mcp.tool()
async def hot_reload() -> str:
    """Trigger Flutter hot reload (fast, preserves state)."""
    if not flutter_proc or flutter_proc.returncode is not None:
        return "Flutter not running. Use flutter_run first."
    flutter_proc.stdin.write(b"r")
    await flutter_proc.stdin.drain()
    await asyncio.sleep(3)
    content = FLUTTER_LOG.read_text()
    return f"Hot reload triggered.\n{_tail(content, 5)}"


@mcp.tool()
async def hot_restart() -> str:
    """Trigger Flutter hot restart (full restart, loses state)."""
    if not flutter_proc or flutter_proc.returncode is not None:
        return "Flutter not running. Use flutter_run first."
    flutter_proc.stdin.write(b"R")
    await flutter_proc.stdin.drain()
    await asyncio.sleep(5)
    content = FLUTTER_LOG.read_text()
    return f"Hot restart triggered.\n{_tail(content, 5)}"


@mcp.tool()
async def stop_flutter() -> str:
    """Stop the running Flutter app."""
    global flutter_proc, flutter_log_fh
    if not flutter_proc or flutter_proc.returncode is not None:
        flutter_proc = None
        return "Flutter was not running."

    flutter_proc.stdin.write(b"q")
    await flutter_proc.stdin.drain()
    try:
        await asyncio.wait_for(flutter_proc.wait(), timeout=10)
    except asyncio.TimeoutError:
        flutter_proc.kill()
    flutter_proc = None
    if flutter_log_fh:
        flutter_log_fh.close()
        flutter_log_fh = None
    return "Flutter stopped."


@mcp.tool()
async def flutter_log(lines: int = 30) -> str:
    """Get recent Flutter output.

    Args:
        lines: Number of lines to return (default 30)
    """
    if not FLUTTER_LOG.exists():
        return "No Flutter log found. Run flutter_run first."
    content = FLUTTER_LOG.read_text()
    return _tail(content, lines)


@mcp.tool()
async def tap(x: int, y: int) -> str:
    """Tap on the emulator screen at pixel coordinates.

    Args:
        x: X coordinate in pixels
        y: Y coordinate in pixels
    """
    await run_adb("shell", "input", "tap", str(x), str(y))
    return f"Tapped ({x}, {y})"


@mcp.tool()
async def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> str:
    """Swipe on the emulator screen.

    Args:
        x1: Start X
        y1: Start Y
        x2: End X
        y2: End Y
        duration_ms: Duration in milliseconds
    """
    await run_adb("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms))
    return f"Swiped ({x1},{y1}) -> ({x2},{y2})"


@mcp.tool()
async def press_key(key: str) -> str:
    """Press an Android key.

    Args:
        key: Key name (BACK, HOME, ENTER, DELETE, VOLUME_UP, VOLUME_DOWN, POWER, MENU, RECENTS, TAB) or numeric keycode
    """
    key_map = {
        "BACK": "4", "HOME": "3", "RECENTS": "187",
        "ENTER": "66", "DELETE": "67", "TAB": "61",
        "VOLUME_UP": "24", "VOLUME_DOWN": "25",
        "POWER": "26", "MENU": "82",
    }
    keycode = key_map.get(key.upper(), key)
    await run_adb("shell", "input", "keyevent", keycode)
    return f"Pressed {key}"


@mcp.tool()
async def input_text(text: str) -> str:
    """Type text into the focused input field on the emulator.

    Args:
        text: Text to type
    """
    safe = text.replace(" ", "%s")
    await run_adb("shell", "input", "text", safe)
    return f"Typed: {text}"


@mcp.tool()
async def session_status() -> str:
    """Get current status of emulator, mirror server, and Flutter."""
    parts = []

    # Emulator
    if emulator_proc and emulator_proc.returncode is None:
        devices, _, _ = await run_adb("devices", "-l")
        parts.append(f"Emulator: RUNNING\n{devices}")
    else:
        devices, _, _ = await run_adb("devices", "-l")
        has_device = any("device " in line or "emulator" in line for line in devices.split("\n")[1:] if line.strip())
        parts.append(f"Emulator: {'DEVICE CONNECTED' if has_device else 'STOPPED'}\n{devices}")

    # Mirror
    if mirror_proc and mirror_proc.returncode is None:
        ip = await get_tailscale_ip()
        parts.append(f"Mirror: RUNNING at http://{ip}:{MIRROR_PORT}")
    else:
        parts.append("Mirror: STOPPED")

    # Flutter
    if flutter_proc and flutter_proc.returncode is not None:
        parts.append(f"Flutter: EXITED (code {flutter_proc.returncode})")
    elif flutter_proc:
        parts.append("Flutter: RUNNING")
    else:
        parts.append("Flutter: STOPPED")

    # Target device
    parts.append(f"Target device: {target_serial or 'auto (first available)'}")

    # Screen info
    try:
        w, h = await get_screen_size()
        parts.append(f"Screen: {w}x{h}")
    except Exception:
        pass

    return "\n\n".join(parts)


def _tail(text: str, n: int) -> str:
    lines = text.strip().split("\n")
    return "\n".join(lines[-n:])


if __name__ == "__main__":
    mcp.run()
