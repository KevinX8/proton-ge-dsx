import ast
import json
import os
import pathlib
import runpy
import subprocess
import sys

import pytest


def run_stub(hook, message, env):
    return subprocess.run(
        [sys.executable, "-c", hook.DSX_HOST_STUB, json.dumps(message)],
        capture_output=True,
        text=True,
        env=env,
    )


SNAPSHOT = {
    "ok": True,
    "controller_connected": True,
    "battery_level": 95,
    "proxy_enabled": False,
    "ignore_game_output": False,
    "xbox_emulation_enabled": False,
}


def test_status_returns_snapshot(hook, control_server, stub_env):
    control_server(SNAPSHOT)
    result = run_stub(hook, {"op": "status"}, stub_env)
    assert result.returncode == 0
    assert json.loads(result.stdout) == SNAPSHOT


def test_status_sends_newline_framed_request(hook, control_server, stub_env):
    server = control_server(SNAPSHOT)
    run_stub(hook, {"op": "status"}, stub_env)
    assert server.requests == [{"command": "status"}]


def test_set_sends_all_three_booleans(hook, control_server, stub_env):
    server = control_server(SNAPSHOT)
    settings = {
        "proxy_enabled": True,
        "ignore_game_output": False,
        "xbox_emulation_enabled": False,
    }
    run_stub(hook, {"op": "set", "settings": settings}, stub_env)
    assert server.requests == [dict(settings, command="set_runtime")]


def test_missing_socket_reports_socket_missing(hook, stub_env):
    result = run_stub(hook, {"op": "status"}, stub_env)
    assert json.loads(result.stdout) == {"ok": False, "error": "socket_missing"}


def test_missing_runtime_dir_reports_error(hook, stub_env):
    del stub_env["XDG_RUNTIME_DIR"]
    result = run_stub(hook, {"op": "status"}, stub_env)
    assert json.loads(result.stdout) == {"ok": False, "error": "no_xdg_runtime_dir"}


def test_unknown_op_is_reported(hook, stub_env):
    result = run_stub(hook, {"op": "explode"}, stub_env)
    assert json.loads(result.stdout) == {"ok": False, "error": "unknown_op"}


def test_server_rejection_is_passed_through(hook, control_server, stub_env):
    rejection = {
        "ok": False,
        "error": "proxy_and_xbox_are_mutually_exclusive",
        "message": "runtime settings combination is invalid",
    }
    control_server(rejection)
    result = run_stub(hook, {"op": "status"}, stub_env)
    assert json.loads(result.stdout) == rejection


def test_ask_reports_yes_when_dialog_accepts(hook, fake_bin, stub_env):
    fake_bin("zenity", "exit 0")
    stub_env["PATH"] = str(fake_bin.directory)
    result = run_stub(hook, {"op": "ask", "reason": "socket_missing"}, stub_env)
    assert json.loads(result.stdout) == {"ok": True, "answer": "yes"}


def test_ask_reports_no_when_dialog_declines(hook, fake_bin, stub_env):
    fake_bin("zenity", "exit 1")
    stub_env["PATH"] = str(fake_bin.directory)
    result = run_stub(hook, {"op": "ask", "reason": "socket_missing"}, stub_env)
    assert json.loads(result.stdout) == {"ok": True, "answer": "no"}


def test_ask_reports_nodialog_when_no_tool_present(hook, fake_bin, stub_env):
    stub_env["PATH"] = str(fake_bin.directory)
    result = run_stub(hook, {"op": "ask", "reason": "socket_missing"}, stub_env)
    assert json.loads(result.stdout) == {"ok": True, "answer": "nodialog"}


def test_ask_times_out_and_reports_timeout(hook, fake_bin, stub_env):
    fake_bin("zenity", "exec %s 30" % fake_bin.real_sleep)
    stub_env["PATH"] = str(fake_bin.directory)
    stub_env["DSX_DIALOG_TIMEOUT"] = "1"
    result = run_stub(hook, {"op": "ask", "reason": "socket_missing"}, stub_env)
    assert json.loads(result.stdout) == {"ok": True, "answer": "timeout"}


def test_kde_prefers_kdialog(hook, fake_bin, stub_env):
    fake_bin("zenity", 'echo "$0" >> "$DSX_TEST_LOG"; exit 0')
    fake_bin("kdialog", 'echo "$0" >> "$DSX_TEST_LOG"; exit 0')
    log_path = fake_bin.directory.parent / "chosen.log"
    stub_env["PATH"] = str(fake_bin.directory)
    stub_env["XDG_CURRENT_DESKTOP"] = "KDE"
    stub_env["DSX_TEST_LOG"] = str(log_path)
    run_stub(hook, {"op": "ask", "reason": "socket_missing"}, stub_env)
    assert log_path.read_text().strip().endswith("kdialog")


def test_non_kde_prefers_zenity(hook, fake_bin, stub_env):
    fake_bin("zenity", 'echo "$0" >> "$DSX_TEST_LOG"; exit 0')
    fake_bin("kdialog", 'echo "$0" >> "$DSX_TEST_LOG"; exit 0')
    log_path = fake_bin.directory.parent / "chosen.log"
    stub_env["PATH"] = str(fake_bin.directory)
    stub_env["XDG_CURRENT_DESKTOP"] = "GNOME"
    stub_env["DSX_TEST_LOG"] = str(log_path)
    run_stub(hook, {"op": "ask", "reason": "socket_missing"}, stub_env)
    assert log_path.read_text().strip().endswith("zenity")


LAUNCH_CLIENT_PASSTHROUGH = 'shift 2\nexec "$@"'


@pytest.fixture
def container(hook, fake_bin, stub_env, monkeypatch):
    """Puts a passthrough launch-client on PATH and points the module at it."""
    fake_bin("steam-runtime-launch-client", LAUNCH_CLIENT_PASSTHROUGH)
    monkeypatch.setenv("PATH", str(fake_bin.directory) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("XDG_RUNTIME_DIR", stub_env["XDG_RUNTIME_DIR"])
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    monkeypatch.setenv("SteamGameId", "12345")
    monkeypatch.delenv("STEAM_COMPAT_APP_ID", raising=False)
    return hook


def test_host_call_returns_parsed_reply(container, control_server):
    control_server(SNAPSHOT)
    assert container.dsx_host_call({"op": "status"}) == SNAPSHOT


def test_host_call_returns_none_without_launch_client(hook, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert hook.dsx_host_call({"op": "status"}) is None


def test_enable_proxy_enables_and_returns_snapshot(container, control_server):
    server = control_server(SNAPSHOT)
    should_launch, snapshot = container.dsx_enable_proxy()
    assert should_launch is True
    assert snapshot == {
        "proxy_enabled": False,
        "ignore_game_output": False,
        "xbox_emulation_enabled": False,
    }
    assert server.requests[-1] == {
        "command": "set_runtime",
        "proxy_enabled": True,
        "ignore_game_output": False,
        "xbox_emulation_enabled": False,
    }


def test_enable_proxy_forces_xbox_off(container, control_server):
    server = control_server(dict(SNAPSHOT, xbox_emulation_enabled=True))
    _, snapshot = container.dsx_enable_proxy()
    assert server.requests[-1]["xbox_emulation_enabled"] is False
    assert snapshot["xbox_emulation_enabled"] is True


def test_enable_proxy_is_a_noop_when_already_on(container, control_server):
    server = control_server(dict(SNAPSHOT, proxy_enabled=True))
    should_launch, snapshot = container.dsx_enable_proxy()
    assert should_launch is True
    assert snapshot is None
    assert server.requests == [{"command": "status"}]


def test_enable_proxy_launches_anyway_when_host_unreachable(hook, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert hook.dsx_enable_proxy() == (True, None)


def test_enable_proxy_aborts_when_dialog_declines(container, fake_bin):
    fake_bin("zenity", "exit 1")
    should_launch, snapshot = container.dsx_enable_proxy()
    assert should_launch is False
    assert snapshot is None


def test_enable_proxy_launches_when_dialog_accepts(container, fake_bin):
    fake_bin("zenity", "exit 0")
    should_launch, snapshot = container.dsx_enable_proxy()
    assert should_launch is True
    assert snapshot is None


def test_restore_sends_the_snapshot(container, control_server):
    server = control_server(SNAPSHOT)
    snapshot = {
        "proxy_enabled": False,
        "ignore_game_output": True,
        "xbox_emulation_enabled": True,
    }
    container.dsx_restore_proxy(snapshot)
    assert server.requests[-1] == dict(snapshot, command="set_runtime")


def test_restore_does_nothing_for_none(container, control_server):
    server = control_server(SNAPSHOT)
    container.dsx_restore_proxy(None)
    assert server.requests == []


def test_enable_proxy_skips_maintenance_runs_without_appid(container, control_server, monkeypatch):
    """Steam runs the tool under AppID 0 for prefix upkeep at client startup."""
    server = control_server(SNAPSHOT)
    monkeypatch.delenv("SteamGameId", raising=False)
    should_launch, snapshot = container.dsx_enable_proxy()
    assert should_launch is True
    assert snapshot is None
    assert server.requests == []


def test_enable_proxy_skips_appid_zero(container, control_server, monkeypatch):
    server = control_server(SNAPSHOT)
    monkeypatch.setenv("SteamGameId", "0")
    should_launch, snapshot = container.dsx_enable_proxy()
    assert should_launch is True
    assert snapshot is None
    assert server.requests == []


def test_enable_proxy_shows_no_dialog_without_appid(container, fake_bin, monkeypatch):
    """The reported bug: a warning dialog per maintenance run, server down."""
    marker = fake_bin.directory.parent / "dialog-was-shown"
    fake_bin("zenity", 'touch "%s"; exit 1' % marker)
    monkeypatch.delenv("SteamGameId", raising=False)
    should_launch, _ = container.dsx_enable_proxy()
    assert should_launch is True
    assert not marker.exists(), "dialog shown for a non-game invocation"


def test_enable_proxy_honours_steam_compat_app_id(container, control_server, monkeypatch):
    server = control_server(SNAPSHOT)
    monkeypatch.delenv("SteamGameId", raising=False)
    monkeypatch.setenv("STEAM_COMPAT_APP_ID", "220")
    should_launch, snapshot = container.dsx_enable_proxy()
    assert should_launch is True
    assert snapshot is not None
    assert server.requests[-1]["proxy_enabled"] is True


@pytest.mark.parametrize("cancel,fail", [(False, False), (False, True), (True, False)])
def test_generated_run_restores_proxy_on_exit(cancel, fail):
    root = pathlib.Path(__file__).resolve().parent.parent
    generator = runpy.run_path(str(root / "tools/gen_proton_hook_patch.py"))
    original = '''def file_is_wine_builtin_dll(path):
    pass

class Session:
    def run(self):
        umu_without_steam = False
        self.calls.append("launch")
        if self.fail:
            raise RuntimeError("launch failed")
        return 7
'''
    patched = generator["build_patched"](original)
    tree = ast.parse(patched)
    session = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    calls = []
    snapshot = {"proxy_enabled": False}
    namespace = {
        "dsx_enable_proxy": lambda: (not cancel, snapshot),
        "dsx_restore_proxy": lambda state: calls.append(("restore", state)),
        "dsx_log": lambda message: None,
    }
    exec(compile(ast.Module(body=[session], type_ignores=[]), "proton", "exec"), namespace)
    instance = namespace["Session"]()
    instance.calls = calls
    instance.fail = fail
    if fail:
        with pytest.raises(RuntimeError, match="launch failed"):
            instance.run()
    else:
        assert instance.run() == (1 if cancel else 7)
    assert calls == ([] if cancel else ["launch", ("restore", snapshot)])


def test_stage_patches_in_place_is_idempotent(tmp_path):
    root = pathlib.Path(__file__).resolve().parent.parent
    import shutil

    shutil.copytree(root / "tools", tmp_path / "tools")
    shutil.copytree(root / "patches/dsx", tmp_path / "patches/dsx")
    shutil.copytree(root / "patches/dsx-proton", tmp_path / "patches/dsx-proton")
    prep = tmp_path / "patches/protonprep-valve-staging.sh"
    prep.write_text('    echo "WINE: RUN AUTOCONF TOOLS/MAKE_REQUESTS"\n'
                    '### END PROTON-GE ADDITIONAL CUSTOM PATCHES ###\n')
    script = tmp_path / "tools/stage-dsx-patches.sh"
    subprocess.run(["bash", str(script)], check=True, capture_output=True)
    first = prep.read_text()
    subprocess.run(["bash", str(script)], check=True, capture_output=True)
    assert prep.read_text() == first
    assert first.count('apply_all_in_dir "../patches/dsx"') == 1
    assert first.count('apply_all_in_dir "patches/dsx-proton"') == 1
