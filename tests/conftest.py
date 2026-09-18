import importlib.util
import json
import os
import pathlib
import shutil
import socket
import stat
import threading

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "tools" / "dsx_proton_hook.py"


@pytest.fixture(scope="session")
def hook():
    """The dsx_proton_hook module, loaded by path."""
    spec = importlib.util.spec_from_file_location("dsx_proton_hook", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeControlServer:
    """Minimal stand-in for the DSX control server."""

    def __init__(self, runtime_dir, reply):
        self.path = pathlib.Path(runtime_dir) / "dsx-linux" / "control.sock"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.reply = reply
        self.requests = []
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(str(self.path))
        self._listener.listen(4)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while True:
            try:
                connection, _ = self._listener.accept()
            except OSError:
                return
            with connection:
                data = b""
                while not data.endswith(b"\n"):
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                if data.strip():
                    self.requests.append(json.loads(data.decode()))
                connection.sendall((json.dumps(self.reply) + "\n").encode())

    def close(self):
        self._listener.close()


@pytest.fixture
def runtime_dir(tmp_path):
    path = tmp_path / "runtime"
    path.mkdir()
    return path


@pytest.fixture
def control_server(runtime_dir):
    servers = []

    def make(reply):
        server = FakeControlServer(runtime_dir, reply)
        servers.append(server)
        return server

    yield make
    for server in servers:
        server.close()


@pytest.fixture
def fake_bin(tmp_path):
    """Creates executable stand-ins on a directory that tests prepend to PATH."""
    directory = tmp_path / "bin"
    directory.mkdir()

    def make(name, script):
        path = directory / name
        path.write_text("#!/bin/sh\n" + script + "\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return path

    # Tests replace PATH with this directory, which hides the real sleep.
    # Expose its absolute path so a fake dialog can exec it directly. A dialog
    # that blocks in a *child* process would survive subprocess.run's kill and
    # hold the inherited stdout pipe open, hanging the suite.
    make.real_sleep = shutil.which("sleep") or "/bin/sleep"
    make.directory = directory
    return make


@pytest.fixture
def stub_env(runtime_dir):
    """A clean environment for running the host stub."""
    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = str(runtime_dir)
    env.pop("XDG_CURRENT_DESKTOP", None)
    return env
