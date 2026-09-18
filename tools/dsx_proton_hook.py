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

SOCKET_NAME = "dsx-linux/control.sock"
CONNECT_TIMEOUT = 1.0
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


def dialog_command(reason):
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    if "KDE" in desktop:
        order = ["kdialog", "zenity", "yad"]
    else:
        order = ["zenity", "kdialog", "yad"]
    text = "DSX proxy could not be enabled.\n\n%s\n\nLaunch anyway?" % reason
    for name in order:
        path = shutil.which(name)
        if path is None:
            continue
        if name == "kdialog":
            return [path, "--warningyesno", text]
        return [path, "--question", "--text", text]
    return None


def ask(reason):
    command = dialog_command(reason)
    if command is None:
        return {"ok": True, "answer": "nodialog"}
    try:
        completed = subprocess.run(command, timeout=DIALOG_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": True, "answer": "timeout"}
    except OSError:
        return {"ok": True, "answer": "nodialog"}
    return {"ok": True, "answer": "yes" if completed.returncode == 0 else "no"}


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
        result = ask(message.get("reason", ""))
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


def dsx_ask_or_abort(reason):
    """Ask whether to launch despite a failure. True means launch."""
    reply = dsx_host_call({"op": "ask", "reason": reason},
                          timeout=DSX_DIALOG_CALL_TIMEOUT)
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


def dsx_enable_proxy():
    """Enable proxy mode before a game launches.

    Returns (should_launch, snapshot), where snapshot is the runtime settings
    to restore on exit, or None when there is nothing to restore.
    """
    if dsx_game_appid() is None:
        dsx_log("no game AppID, skipping proxy control")
        return True, None
    status = dsx_host_call({"op": "status"})
    if status is None:
        return True, None
    if not status.get("ok"):
        return dsx_ask_or_abort(status.get("error", "unknown_error")), None
    if status.get("proxy_enabled"):
        dsx_log("proxy already enabled, leaving as-is")
        return True, None
    previous = {
        "proxy_enabled": False,
        "ignore_game_output": bool(status.get("ignore_game_output")),
        "xbox_emulation_enabled": bool(status.get("xbox_emulation_enabled")),
    }
    reply = dsx_host_call({"op": "set", "settings": {
        "proxy_enabled": True,
        "ignore_game_output": previous["ignore_game_output"],
        "xbox_emulation_enabled": False,
    }})
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
    reply = dsx_host_call({"op": "set", "settings": snapshot})
    if reply is None or not reply.get("ok"):
        dsx_log("failed to restore previous proxy state")
        return
    dsx_log("restored previous proxy state")
