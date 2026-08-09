"""
tools/os_control_tools.py — Operating System controls, window management, audio, display, and power tools for Phantom AI.

TOOL CONTRACT
=============
  ALL_TOOLS   list   -> OpenAI tool schemas for the tools in this file
  execute     func   -> execute(name: str, args: dict) -> str
"""

import os
import ctypes
import subprocess
import json
import psutil

# Optional screen brightness & audio control imports
try:
    import screen_brightness_control as sbc
    HAS_SBC = True
except ImportError:
    HAS_SBC = False

try:
    from pycaw.pycaw import AudioUtilities
    HAS_PYCAW = True
except ImportError:
    HAS_PYCAW = False

user32 = ctypes.windll.user32


# ── Helper functions for Window Management ────────────────────────────────────

def _find_window_by_target(target: str):
    """Find HWND handle and window details by matching window title, app name, or PID."""
    if not target or not str(target).strip():
        return None

    query = str(target).lower().strip()
    is_pid = query.isdigit()
    target_pid = int(query) if is_pid else None

    # Query active processes with main window handles via PowerShell CIM
    try:
        cmd = "Get-Process | Where-Object {$_.MainWindowTitle} | Select-Object Id, ProcessName, MainWindowTitle, MainWindowHandle | ConvertTo-Json"
        res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd], capture_output=True, text=True, timeout=3.0)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip())
            if isinstance(data, dict):
                data = [data]

            # 1. Exact or partial match on PID / ProcessName / Title
            for item in data:
                pid = item.get("Id")
                pname = str(item.get("ProcessName", "")).lower()
                title = str(item.get("MainWindowTitle", "")).lower()
                hwnd = item.get("MainWindowHandle")

                if hwnd and hwnd != 0:
                    if is_pid and pid == target_pid:
                        return (hwnd, pid, item.get("ProcessName"), item.get("MainWindowTitle"))
                    if query in pname or query in title:
                        return (hwnd, pid, item.get("ProcessName"), item.get("MainWindowTitle"))
    except Exception:
        pass

    return None


# ── Window Management Implementations ─────────────────────────────────────────

def list_open_windows() -> str:
    """List all currently open desktop windows with process IDs, app names, and window titles."""
    try:
        cmd = "Get-Process | Where-Object {$_.MainWindowTitle} | Select-Object Id, ProcessName, MainWindowTitle | ConvertTo-Json"
        res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd], capture_output=True, text=True, timeout=3.0)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip())
            if isinstance(data, dict):
                data = [data]

            window_lines = []
            for item in data:
                pid = item.get("Id")
                pname = item.get("ProcessName", "Unknown")
                title = item.get("MainWindowTitle", "").strip()
                if title:
                    window_lines.append(f"• PID {pid:6d} | {pname:22s} | Title: \"{title}\"")

            if window_lines:
                return f"Open Desktop Windows ({len(window_lines)} active):\n" + "\n".join(window_lines)

    except Exception as e:
        return f"Could not list open windows: {e}"

    return "No active desktop windows found."


def focus_window(target: str) -> str:
    """Bring a target window to the foreground and focus it by title, app name, or PID."""
    match = _find_window_by_target(target)
    if not match:
        return f"Could not find open window matching '{target}'. Use list_open_windows to check active titles."

    hwnd, pid, pname, title = match
    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE = 9
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        return f"Focused window: \"{title}\" ({pname})."
    except Exception as e:
        return f"Failed to focus window '{title}': {e}"


def minimize_window(target: str) -> str:
    """Minimize a target window by title, app name, or PID."""
    match = _find_window_by_target(target)
    if not match:
        return f"Could not find window matching '{target}'."

    hwnd, pid, pname, title = match
    try:
        user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE = 6
        return f"Minimized window: \"{title}\" ({pname})."
    except Exception as e:
        return f"Failed to minimize window '{title}': {e}"


def maximize_window(target: str) -> str:
    """Maximize a target window by title, app name, or PID."""
    match = _find_window_by_target(target)
    if not match:
        return f"Could not find window matching '{target}'."

    hwnd, pid, pname, title = match
    try:
        user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE = 3
        user32.SetForegroundWindow(hwnd)
        return f"Maximized window: \"{title}\" ({pname})."
    except Exception as e:
        return f"Failed to maximize window '{title}': {e}"


def restore_window(target: str) -> str:
    """Restore a minimized or maximized window to its normal size and position."""
    match = _find_window_by_target(target)
    if not match:
        return f"Could not find window matching '{target}'."

    hwnd, pid, pname, title = match
    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE = 9
        user32.SetForegroundWindow(hwnd)
        return f"Restored window: \"{title}\" ({pname})."
    except Exception as e:
        return f"Failed to restore window '{title}': {e}"


def resize_window(target: str, width: int, height: int, x: int = -1, y: int = -1) -> str:
    """Set the size (width, height) and optional position (x, y) of a target window."""
    match = _find_window_by_target(target)
    if not match:
        return f"Could not find window matching '{target}'."

    hwnd, pid, pname, title = match
    try:
        # Get current window position if x or y not specified
        if x < 0 or y < 0:
            rect = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            if x < 0:
                x = rect.left
            if y < 0:
                y = rect.top

        user32.ShowWindow(hwnd, 9)  # Ensure restored before resizing
        user32.MoveWindow(hwnd, int(x), int(y), int(width), int(height), True)
        return f"Resized window \"{title}\" to {width}x{height} at position ({x}, {y})."
    except Exception as e:
        return f"Failed to resize window '{title}': {e}"


# ── System Power & Session Controls ───────────────────────────────────────────

def lock_pc() -> str:
    """Lock the Windows computer workstation immediately."""
    try:
        res = user32.LockWorkStation()
        if res:
            return "Locked PC workstation."
        return "Lock command issued."
    except Exception as e:
        return f"Failed to lock PC: {e}"


def sleep_pc() -> str:
    """Put the computer into Sleep/Suspend mode."""
    try:
        subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], check=False)
        return "Putting PC to sleep..."
    except Exception as e:
        return f"Failed to put PC to sleep: {e}"


def restart_pc(delay_seconds: int = 0) -> str:
    """Restart the computer with an optional delay in seconds."""
    try:
        subprocess.run(["shutdown", "/r", "/t", str(int(delay_seconds))], check=True)
        if delay_seconds > 0:
            return f"Restarting PC in {delay_seconds} seconds."
        return "Restarting PC now..."
    except Exception as e:
        return f"Failed to initiate restart: {e}"


def shutdown_pc(delay_seconds: int = 0) -> str:
    """Shutdown the computer with an optional delay in seconds."""
    try:
        subprocess.run(["shutdown", "/s", "/t", str(int(delay_seconds))], check=True)
        if delay_seconds > 0:
            return f"Shutting down PC in {delay_seconds} seconds."
        return "Shutting down PC now..."
    except Exception as e:
        return f"Failed to initiate shutdown: {e}"


def logoff_user() -> str:
    """Log off the current Windows user session."""
    try:
        subprocess.run(["shutdown", "/l"], check=True)
        return "Logging off user..."
    except Exception as e:
        return f"Failed to log off user: {e}"


# ── Task Manager & Process Management ─────────────────────────────────────────

def open_task_manager() -> str:
    """Launch Windows Task Manager (taskmgr.exe)."""
    try:
        subprocess.Popen(["taskmgr.exe"])
        return "Opened Task Manager."
    except Exception as e:
        return f"Failed to open Task Manager: {e}"


def kill_process(target: str) -> str:
    """Terminate a process by name (e.g. 'chrome.exe', 'notepad') or process ID (e.g. '1234')."""
    if not target or not str(target).strip():
        return "No process name or PID specified to kill."

    query = str(target).strip()
    is_pid = query.isdigit()

    killed_count = 0
    names_killed = []

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            pname = proc.info["name"] or ""
            pid = proc.info["pid"]

            if is_pid and pid == int(query):
                proc.kill()
                return f"Killed process {pname} (PID {pid})."

            if not is_pid:
                q = query.lower()
                if pname.lower() == q or pname.lower() == (q + ".exe") or q in pname.lower():
                    proc.kill()
                    killed_count += 1
                    if pname not in names_killed:
                        names_killed.append(pname)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    if killed_count > 0:
        names_str = ", ".join(names_killed)
        return f"Killed {killed_count} process instance(s) of '{names_str}'."

    # Fallback to taskkill
    target_cmd = query if query.endswith(".exe") or is_pid else (query + ".exe")
    flag = "/PID" if is_pid else "/IM"
    res = subprocess.run(["taskkill", flag, target_cmd, "/F"], capture_output=True, text=True)
    if res.returncode == 0:
        return f"Killed process '{target}'."

    return f"No matching active process found for '{target}'."


# ── Audio & Volume Controls ───────────────────────────────────────────────────

def set_volume(level_pct: int) -> str:
    """Set master speaker volume level from 0 to 100%."""
    try:
        level_pct = max(0, min(100, int(level_pct)))
        if HAS_PYCAW:
            device = AudioUtilities.GetSpeakers()
            vol_ctrl = device.EndpointVolume
            vol_ctrl.SetMasterVolumeLevelScalar(level_pct / 100.0, None)
            return f"Set volume to {level_pct}%."
        else:
            return "pycaw audio library unavailable."
    except Exception as e:
        return f"Failed to set volume: {e}"


def mute_audio(mute: bool = True) -> str:
    """Mute or unmute master audio output."""
    try:
        if HAS_PYCAW:
            device = AudioUtilities.GetSpeakers()
            vol_ctrl = device.EndpointVolume
            vol_ctrl.SetMute(1 if mute else 0, None)
            state_str = "Muted" if mute else "Unmuted"
            return f"{state_str} master audio."
        else:
            return "pycaw audio library unavailable."
    except Exception as e:
        return f"Failed to set mute state: {e}"


# ── Display Brightness Control ────────────────────────────────────────────────

def change_brightness(level_pct: int) -> str:
    """Set display brightness level from 0 to 100%."""
    try:
        level_pct = max(0, min(100, int(level_pct)))
        if HAS_SBC:
            sbc.set_brightness(level_pct)
            return f"Set screen brightness to {level_pct}%."
        else:
            return "screen_brightness_control library unavailable."
    except Exception as e:
        return f"Failed to set brightness: {e}"


# ── Recycle Bin Control ───────────────────────────────────────────────────────

def empty_recycle_bin() -> str:
    """Empty the Windows Recycle Bin."""
    try:
        flags = 0x00000001 | 0x00000002 | 0x00000004  # SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
        res = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
        return "Emptied Windows Recycle Bin."
    except Exception as e:
        return f"Failed to empty Recycle Bin: {e}"


# ── OpenAI Function Call Schemas ──────────────────────────────────────────────

ALL_TOOLS = [
    {
        "name": "list_open_windows",
        "description": "List all open desktop windows with process IDs, app names, and window titles.",
        "parameters": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "focus_window",
        "description": "Bring a target window to the foreground and focus it by title, app name, or PID.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Title, app name, or PID of the window to focus."}
            },
            "required": ["target"]
        }
    },
    {
        "name": "minimize_window",
        "description": "Minimize a target window by title, app name, or PID.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Title, app name, or PID of the window to minimize."}
            },
            "required": ["target"]
        }
    },
    {
        "name": "maximize_window",
        "description": "Maximize a target window by title, app name, or PID.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Title, app name, or PID of the window to maximize."}
            },
            "required": ["target"]
        }
    },
    {
        "name": "restore_window",
        "description": "Restore a minimized or maximized window to its normal size and position.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Title, app name, or PID of the window to restore."}
            },
            "required": ["target"]
        }
    },
    {
        "name": "resize_window",
        "description": "Set the width, height, and optional position (x, y) of a target window.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Title, app name, or PID of the window to resize."},
                "width": {"type": "integer", "description": "Desired window width in pixels."},
                "height": {"type": "integer", "description": "Desired window height in pixels."},
                "x": {"type": "integer", "description": "Optional X screen coordinate (left position)."},
                "y": {"type": "integer", "description": "Optional Y screen coordinate (top position)."}
            },
            "required": ["target", "width", "height"]
        }
    },
    {
        "name": "lock_pc",
        "description": "Lock the Windows computer workstation immediately.",
        "parameters": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "sleep_pc",
        "description": "Put the computer into Sleep / Suspend mode.",
        "parameters": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "restart_pc",
        "description": "Restart the computer with an optional delay in seconds.",
        "parameters": {
            "type": "object",
            "properties": {
                "delay_seconds": {"type": "integer", "description": "Delay in seconds before restart (default: 0)."}
            },
            "required": []
        }
    },
    {
        "name": "shutdown_pc",
        "description": "Shutdown the computer with an optional delay in seconds.",
        "parameters": {
            "type": "object",
            "properties": {
                "delay_seconds": {"type": "integer", "description": "Delay in seconds before shutdown (default: 0)."}
            },
            "required": []
        }
    },
    {
        "name": "logoff_user",
        "description": "Log off the current Windows user session.",
        "parameters": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "open_task_manager",
        "description": "Launch the Windows Task Manager application.",
        "parameters": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "kill_process",
        "description": "Terminate/kill a process by name (e.g. 'chrome.exe', 'notepad') or process ID (PID).",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Process name or PID to kill."}
            },
            "required": ["target"]
        }
    },
    {
        "name": "set_volume",
        "description": "Set master speaker volume level from 0% to 100%.",
        "parameters": {
            "type": "object",
            "properties": {
                "level_pct": {"type": "integer", "description": "Volume percentage (0-100)."}
            },
            "required": ["level_pct"]
        }
    },
    {
        "name": "mute_audio",
        "description": "Mute or unmute master audio output.",
        "parameters": {
            "type": "object",
            "properties": {
                "mute": {"type": "boolean", "description": "True to mute audio, False to unmute audio."}
            },
            "required": []
        }
    },
    {
        "name": "change_brightness",
        "description": "Set display brightness level from 0% to 100%.",
        "parameters": {
            "type": "object",
            "properties": {
                "level_pct": {"type": "integer", "description": "Brightness percentage (0-100)."}
            },
            "required": ["level_pct"]
        }
    },
    {
        "name": "empty_recycle_bin",
        "description": "Empty the Windows Recycle Bin.",
        "parameters": {"type": "object", "properties": {}, "required": []}
    }
]


# ── Dispatcher ────────────────────────────────────────────────────────────────

def execute(name: str, args: dict) -> str:
    """Route tool call by name. Called automatically by registry.dispatch."""
    if name == "list_open_windows":
        return list_open_windows()
    elif name == "focus_window":
        return focus_window(args.get("target", ""))
    elif name == "minimize_window":
        return minimize_window(args.get("target", ""))
    elif name == "maximize_window":
        return maximize_window(args.get("target", ""))
    elif name == "restore_window":
        return restore_window(args.get("target", ""))
    elif name == "resize_window":
        return resize_window(
            target=args.get("target", ""),
            width=args.get("width", 800),
            height=args.get("height", 600),
            x=args.get("x", -1),
            y=args.get("y", -1)
        )
    elif name == "lock_pc":
        return lock_pc()
    elif name == "sleep_pc":
        return sleep_pc()
    elif name == "restart_pc":
        return restart_pc(args.get("delay_seconds", 0))
    elif name == "shutdown_pc":
        return shutdown_pc(args.get("delay_seconds", 0))
    elif name == "logoff_user":
        return logoff_user()
    elif name == "open_task_manager":
        return open_task_manager()
    elif name == "kill_process":
        return kill_process(args.get("target", ""))
    elif name == "set_volume":
        return set_volume(args.get("level_pct", 50))
    elif name == "mute_audio":
        return mute_audio(args.get("mute", True))
    elif name == "change_brightness":
        return change_brightness(args.get("level_pct", 100))
    elif name == "empty_recycle_bin":
        return empty_recycle_bin()
    else:
        return f"[os_control_tools] Unknown tool: '{name}'"
