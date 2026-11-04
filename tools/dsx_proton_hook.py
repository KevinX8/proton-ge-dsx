"""DSX proxy control for the DSX-GE Proton build.

This module is embedded verbatim into the `proton` script by
patches/dsx-proton/0014-proton-auto-enable-dsx-proxy.patch. Keep it self-contained:
standard library only, and no names that collide with Proton's own globals.
Proton already defines `log`, so everything here is prefixed `dsx_`/`DSX_`.
"""

import json
import os
import shutil
import subprocess
import sys

DSX_LAUNCH_CLIENT = "steam-runtime-launch-client"
DSX_CALL_TIMEOUT = 10
DSX_DIALOG_CALL_TIMEOUT = 45

DSX_HOST_STUB = r'''
import json
import os
import shutil
import socket
import subprocess
import sys
import time

SOCKET_NAME = "dsx-linux/control.sock"
CONNECT_TIMEOUT = 1.0
MIN_ANSWER_SECONDS = 0.5
DIALOG_TIMEOUT = float(os.environ.get("DSX_DIALOG_TIMEOUT", "30"))


def control_request(payload):
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        return {"ok": False, "error": "no_xdg_runtime_dir"}
    path = os.path.join(runtime_dir, SOCKET_NAME)
    if not os.path.exists(path):
        return {"ok": False, "error": "socket_missing"}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(CONNECT_TIMEOUT)
            sock.connect(path)
            sock.sendall((json.dumps(payload) + "\n").encode())
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                if chunk.endswith(b"\n"):
                    break
        return json.loads(b"".join(chunks).decode())
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def dialog_command(text):
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    if "KDE" in desktop:
        order = ["kdialog", "zenity", "yad"]
    else:
        order = ["zenity", "kdialog", "yad"]
    for name in order:
        path = shutil.which(name)
        if path is None:
            continue
        if name == "kdialog":
            return [path, "--warningyesno", text]
        return [path, "--question", "--text", text]
    return None


def ask(message):
    text = message.get("text") or (
        "DSX proxy could not be enabled.\n\n%s\n\nLaunch anyway?" % message.get("reason", ""))
    command = dialog_command(text)
    if command is None:
        return {"ok": True, "answer": "nodialog"}
    started = time.monotonic()
    try:
        completed = subprocess.run(command, timeout=DIALOG_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": True, "answer": "timeout"}
    except OSError:
        return {"ok": True, "answer": "nodialog"}
    if completed.returncode == 0:
        return {"ok": True, "answer": "yes"}
    # A dialog that cannot open (no display, Game Mode) exits 1 at once, the
    # same code as "No"; only a dialog that stayed up long enough to read counts.
    if completed.returncode == 1 and time.monotonic() - started >= MIN_ANSWER_SECONDS:
        return {"ok": True, "answer": "no"}
    return {"ok": True, "answer": "nodialog"}


def main():
    try:
        message = json.loads(sys.argv[1])
    except (IndexError, ValueError):
        print(json.dumps({"ok": False, "error": "bad_stub_invocation"}))
        return 0
    operation = message.get("op")
    if operation == "status":
        result = control_request({"command": "status"})
    elif operation == "set":
        settings = message.get("settings") or {}
        result = control_request(dict(settings, command="set_runtime"))
    elif operation == "ask":
        result = ask(message)
    else:
        result = {"ok": False, "error": "unknown_op"}
    print(json.dumps(result))
    return 0


sys.exit(main())
'''


def dsx_log(message):
    try:
        sys.stderr.write("DSX: " + message + os.linesep)
        sys.stderr.flush()
    except OSError:
        pass


def dsx_host_call(message, timeout=DSX_CALL_TIMEOUT):
    """Run the host stub outside the container.

    Returns the parsed reply, or None when the host cannot be reached at all.
    """
    launcher = shutil.which(DSX_LAUNCH_CLIENT)
    if launcher is None:
        dsx_log("%s not found, skipping proxy control" % DSX_LAUNCH_CLIENT)
        return None
    command = [
        launcher, "--host", "--",
        "python3", "-c", DSX_HOST_STUB, json.dumps(message),
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        dsx_log("host call failed: %s" % exc)
        return None
    if completed.returncode != 0:
        dsx_log("host call exited %d: %s"
                % (completed.returncode, completed.stderr.strip()))
        return None
    try:
        return json.loads(completed.stdout)
    except ValueError:
        dsx_log("unparseable host reply: %r" % completed.stdout.strip())
        return None


def dsx_ask_or_abort(reason, text=None):
    """Ask whether to launch despite a failure. True means launch."""
    message = {"op": "ask", "reason": reason}
    if text:
        message["text"] = text
    reply = dsx_host_call(message, timeout=DSX_DIALOG_CALL_TIMEOUT)
    if reply is None or not reply.get("ok"):
        dsx_log("could not ask, launching anyway")
        return True
    answer = reply.get("answer")
    dsx_log("dialog answer: %s" % answer)
    return answer != "no"


def dsx_game_appid():
    """The AppID of the game being launched, or None when there isn't one.

    Steam also runs the compat tool for maintenance work under AppID 0 -
    prefix creation and upgrades, several times at client startup - and those
    invocations use the same `waitforexitandrun` verb a real launch does.
    Without this guard the hook fires on every one of them, which means a
    warning dialog per invocation before you have launched anything.
    """
    appid = str(os.environ.get("SteamGameId",
                               os.environ.get("STEAM_COMPAT_APP_ID", "0")))
    if not appid.isdigit() or appid == "0":
        return None
    return appid


DSX_DUALSENSE_IDS = {("054c", "0ce6"), ("054c", "0df2")}
DSX_STEAM_INPUT_TEXT = (
    "Steam Input is on for this game, so it sees an Xbox controller instead "
    "of your DualSense. Haptics and adaptive triggers won't work.\n\n"
    "Turn it off in the game's Properties > Controller > Disable Steam Input.\n\n"
    "Launch anyway?")


def dsx_steam_input_hides_dualsense():
    """True when Steam Input took the DualSense over for this launch.

    Steam hides each controller it translates from the game by listing its
    USB ids ("0x054c/0x0ce6,...") in SDL_GAMECONTROLLER_IGNORE_DEVICES.
    """
    for entry in os.environ.get("SDL_GAMECONTROLLER_IGNORE_DEVICES", "").split(","):
        ids = tuple(part.strip().lower().replace("0x", "").zfill(4)
                    for part in entry.split("/"))
        if ids in DSX_DUALSENSE_IDS:
            return True
    return False


DSX_RUNTIME_KEYS = (
    "proxy_enabled", "ignore_game_output", "xbox_emulation_enabled",
    "audio_haptics_enabled", "dualshock4_emulation_enabled", "rumble_enabled",
    "rumble_intensity", "bt_link_limit_percent", "haptics_buffer_ms",
    "experimental_direct_bt_audio", "haptics_source", "input_rate_hz",
    "low_battery_alert_enabled", "low_battery_threshold", "low_battery_interval_s",
    "low_battery_repeat", "audio_enabled", "mirror_audio", "verbose", "microphone",
    "pad_source_order", "pad_haptics_strength", "rumble_pad_audio_haptics",
)
DSX_MODE_KEYS = ("proxy_enabled", "xbox_emulation_enabled")


def dsx_runtime_fields(status):
    """The runtime block of a status reply, ready to send back in set_runtime.

    The service persists every field it receives, so a request must carry the
    whole block; a missing field would fall back to its compiled default.
    """
    return {key: status[key] for key in DSX_RUNTIME_KEYS if key in status}


def dsx_enable_proxy():
    """Enable proxy mode before a game launches.

    Returns (should_launch, snapshot), where snapshot is the runtime settings
    to restore on exit, or None when there is nothing to restore.
    """
    if dsx_game_appid() is None:
        dsx_log("no game AppID, skipping proxy control")
        return True, None
    if dsx_steam_input_hides_dualsense():
        dsx_log("Steam Input is on for the DualSense")
        if not dsx_ask_or_abort("steam_input", DSX_STEAM_INPUT_TEXT):
            return False, None
    status = dsx_host_call({"op": "status"})
    if status is None:
        return True, None
    if not status.get("ok"):
        return dsx_ask_or_abort(status.get("error", "unknown_error")), None
    if status.get("proxy_enabled"):
        dsx_log("proxy already enabled, leaving as-is")
        return True, None
    previous = dict(dsx_runtime_fields(status), proxy_enabled=False)
    reply = dsx_host_call({"op": "set", "settings": dict(
        previous, proxy_enabled=True, xbox_emulation_enabled=False)})
    if reply is None:
        return True, None
    if not reply.get("ok"):
        return dsx_ask_or_abort(reply.get("error", "rejected")), None
    dsx_log("proxy enabled")
    return True, previous


def dsx_restore_proxy(snapshot):
    """Restore the runtime settings captured before launch."""
    if snapshot is None:
        return
    # Only the mode switches the hook changed go back; settings edited while
    # the game ran are kept.
    settings = snapshot
    status = dsx_host_call({"op": "status"})
    if status is not None and status.get("ok"):
        settings = dsx_runtime_fields(status)
        settings.update({key: snapshot[key] for key in DSX_MODE_KEYS if key in snapshot})
    reply = dsx_host_call({"op": "set", "settings": settings})
    if reply is None or not reply.get("ok"):
        dsx_log("failed to restore previous proxy state")
        return
    dsx_log("restored previous proxy state")
