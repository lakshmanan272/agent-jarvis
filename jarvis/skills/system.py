"""System control: volume, media, power, screenshots, status."""
from __future__ import annotations

import datetime as dt
import logging
import os
import subprocess
from pathlib import Path

from jarvis.core import actuator as act
from jarvis.skills.base import ActionResult, intent

log = logging.getLogger("jarvis.skills.system")

_VOLUME_STEP = 2  # each Windows volume key press moves 2 %


def _volume_endpoint():
    """Return a pycaw volume interface, or None if pycaw isn't usable.

    Media keys work everywhere but only move in 2 % steps; pycaw lets
    "set volume to 35" land exactly. We degrade to keys when it's unavailable.
    """
    try:
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))
    except Exception:
        log.debug("pycaw unavailable, falling back to media keys", exc_info=True)
        return None


@intent(
    r"^(?:set\s+)?volume\s+(?:to\s+)?(?P<level>\d+)\s*(?:percent|%)?$",
    name="set_volume",
    priority=8,
    description="Set the system volume to an exact percentage",
    examples=("volume 40", "set volume to 100"),
)
def do_set_volume(ctx, level: str = "50", **_) -> ActionResult:
    pct = max(0, min(int(level), 100))
    endpoint = _volume_endpoint()
    if endpoint is not None:
        endpoint.SetMasterVolumeLevelScalar(pct / 100.0, None)
        return ActionResult(ok=True, say=f"Volume {pct}.")
    # Fallback: floor to zero, then step up.
    act.press("volumedown", presses=50)
    act.press("volumeup", presses=pct // _VOLUME_STEP)
    return ActionResult(ok=True, say=f"Volume {pct}.")


@intent(
    r"^(?:volume|sound)\s+(?P<direction>up|down)(?:\s+(?:by\s+)?(?P<amount>\d+))?$",
    r"^(?:turn|make)\s+(?:it\s+)?(?:the\s+)?(?:volume\s+)?"
    r"(?P<direction>up|down|louder|quieter)$",
    name="volume_step",
    examples=('volume up',),
    priority=6,
    description="Nudge the volume",
)
def do_volume_step(
    ctx, direction: str = "up", amount: str | None = None, **_
) -> ActionResult:
    up = direction in ("up", "louder")
    steps = (int(amount) // _VOLUME_STEP) if amount else 5
    act.press("volumeup" if up else "volumedown", presses=max(1, steps))
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:mute|unmute|silence)(?:\s+(?:the\s+)?(?:volume|sound|audio))?$",
    name="mute",
    priority=7,
    description="Toggle system mute",
)
def do_mute(ctx, **_) -> ActionResult:
    act.press("volumemute")
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:pause|resume|play\s*pause|play)$",
    name="media_play_pause",
    examples=('pause',),
    priority=7,
    description="Play or pause media",
)
def do_play_pause(ctx, **_) -> ActionResult:
    act.press("playpause")
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:next|skip)(?:\s+(?:track|song))?$",
    r"^(?:previous|last)\s+(?:track|song)$",
    name="media_track",
    examples=('next track',),
    priority=6,
    description="Skip to the next or previous track",
)
def do_track(ctx, **_) -> ActionResult:
    previous = ctx.last_command.startswith(("previous", "last"))
    act.press("prevtrack" if previous else "nexttrack")
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:take\s+(?:a\s+)?)?screen\s*shot(?:\s+(?:of\s+)?(?P<scope>screen|window|region))?$",
    name="screenshot",
    priority=7,
    description="Capture the screen to ~/Pictures/Jarvis",
)
def do_screenshot(ctx, scope: str | None = None, **_) -> ActionResult:
    if scope == "region":
        act.hotkey("win", "shift", "s")
        return ActionResult(ok=True, say="Pick a region.")
    try:
        import pyautogui

        out_dir = Path.home() / "Pictures" / "Jarvis"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = out_dir / f"shot-{stamp}.png"
        pyautogui.screenshot().save(path)
        return ActionResult(
            ok=True, say="Captured.", detail=str(path), data={"path": str(path)}
        )
    except Exception as exc:
        return ActionResult.fail("Screenshot failed.", str(exc))


@intent(
    r"^(?:lock|lock\s+(?:the\s+)?(?:screen|pc|computer))$",
    name="lock",
    priority=7,
    description="Lock the workstation",
)
def do_lock(ctx, **_) -> ActionResult:
    act.hotkey("win", "l")
    return ActionResult(ok=True, say="Locking.")


@intent(
    r"^(?P<action>shut\s*down|restart|reboot|sign\s*out|log\s*off|hibernate|sleep)"
    r"(?:\s+(?:the\s+)?(?:pc|computer|system|windows))?$",
    name="power",
    examples=('shutdown', 'restart', 'sleep'),
    priority=7,
    description="Shut down, restart, sleep or sign out",
    destructive=True,
)
def do_power(ctx, action: str = "", _confirmed: bool = False, **_) -> ActionResult:
    action = action.replace(" ", "")
    if not _confirmed and ctx.config.control.confirm_destructive:
        return ActionResult(ok=True, needs_confirm=f"Confirm {action}? Say yes or no.")
    commands = {
        "shutdown": ["shutdown", "/s", "/t", "0"],
        "restart": ["shutdown", "/r", "/t", "0"],
        "reboot": ["shutdown", "/r", "/t", "0"],
        "signout": ["shutdown", "/l"],
        "logoff": ["shutdown", "/l"],
        "hibernate": ["shutdown", "/h"],
    }
    if action == "sleep":
        subprocess.run(
            ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], check=False
        )
        return ActionResult(ok=True, say="Sleeping.")
    cmd = commands.get(action)
    if cmd is None:
        return ActionResult.fail(f"Unknown power action {action}.")
    subprocess.run(cmd, check=False)
    return ActionResult(ok=True, say=f"{action.title()} now.")


@intent(
    r"^(?:cancel|abort)\s+(?:the\s+)?shut\s*down$",
    name="cancel_shutdown",
    priority=9,
    description="Cancel a pending shutdown",
)
def do_cancel_shutdown(ctx, **_) -> ActionResult:
    subprocess.run(["shutdown", "/a"], check=False)
    return ActionResult(ok=True, say="Shutdown cancelled.")


@intent(
    r"^(?:what(?:'s|\s+is)?\s+(?:the\s+)?)?time(?:\s+is\s+it)?$",
    r"^what\s+time$",
    name="time",
    priority=7,
    description="Tell the time",
)
def do_time(ctx, **_) -> ActionResult:
    now = dt.datetime.now()
    return ActionResult(ok=True, say=now.strftime("It's %I:%M %p.").lstrip("0"))


@intent(
    r"^(?:what(?:'s|\s+is)?\s+(?:the\s+)?)?(?:date|day)(?:\s+(?:is\s+)?(?:it|today))?$",
    name="date",
    priority=7,
    description="Tell today's date",
)
def do_date(ctx, **_) -> ActionResult:
    now = dt.datetime.now()
    return ActionResult(ok=True, say=now.strftime("%A, %d %B %Y."))


@intent(
    r"^(?:battery|power)\s*(?:status|level|percent(?:age)?)?$",
    name="battery",
    priority=6,
    description="Report battery level",
)
def do_battery(ctx, **_) -> ActionResult:
    try:
        import psutil

        battery = psutil.sensors_battery()
    except Exception:
        battery = None
    if battery is None:
        return ActionResult(ok=True, say="No battery detected.")
    state = "charging" if battery.power_plugged else "on battery"
    return ActionResult(
        ok=True,
        say=f"{int(battery.percent)} percent, {state}.",
        data={"percent": battery.percent, "plugged": battery.power_plugged},
    )


@intent(
    r"^(?:system\s+)?(?:status|stats|performance|cpu|memory|ram)$",
    name="system_status",
    priority=6,
    description="Report CPU and memory usage",
)
def do_status(ctx, **_) -> ActionResult:
    try:
        import psutil

        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage(os.environ.get("SYSTEMDRIVE", "C:") + "\\")
    except Exception as exc:
        return ActionResult.fail("Couldn't read system stats.", str(exc))
    return ActionResult(
        ok=True,
        say=f"CPU {cpu:.0f} percent, memory {mem.percent:.0f} percent.",
        detail=(
            f"CPU {cpu:.1f}%  RAM {mem.percent:.1f}% "
            f"({mem.used / 1e9:.1f}/{mem.total / 1e9:.1f} GB)  "
            f"Disk {disk.percent:.0f}%"
        ),
        data={"cpu": cpu, "memory": mem.percent, "disk": disk.percent},
    )


@intent(
    r"^(?:night\s+light|dark\s+mode|light\s+mode)$",
    name="open_display_settings",
    examples=('dark mode',),
    description="Open display settings",
)
def do_display_settings(ctx, **_) -> ActionResult:
    os.startfile("ms-settings:display")
    return ActionResult(ok=True, say="Display settings.")


@intent(
    r"^(?:open\s+)?(?:wifi|wi\s*fi|bluetooth|network)\s*(?:settings)?$",
    name="open_network_settings",
    examples=('open wifi settings',),
    # Above open_app, which would otherwise try to launch an application
    # called "wifi settings".
    priority=5,
    description="Open network or Bluetooth settings",
)
def do_network_settings(ctx, **_) -> ActionResult:
    page = "bluetooth" if "bluetooth" in ctx.last_command else "network"
    os.startfile(f"ms-settings:{page}")
    return ActionResult(ok=True, say="")
