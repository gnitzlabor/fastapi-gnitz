import ctypes
import os
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import gnitz
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]

# What the server logs once every listener is bound and published.
_READY_MARKER = "GnitzDB ready"

_PR_SET_PDEATHSIG = 1
_libc = ctypes.CDLL("libc.so.6", use_errno=True)


def _server_binary():
    """The gnitz-server to test against: `GNITZ_SERVER_BIN`, else one on `PATH`,
    else the one built in a `gnitzdb` checkout beside this one. The `gnitz`
    package on PyPI is the client alone, so the server has to come from
    somewhere else."""
    candidates = (
        os.environ.get("GNITZ_SERVER_BIN"),
        shutil.which("gnitz-server"),
        str(_REPO_ROOT.parent / "gnitzdb" / "gnitz-server"),
    )
    for binary in candidates:
        if binary and os.path.isfile(binary):
            return os.path.abspath(binary)
    pytest.skip("no gnitz-server binary found; set GNITZ_SERVER_BIN")


def _die_with_parent():
    # Nothing else ties the server to pytest: without this an interrupted run
    # orphans it, along with its workers and the SAL they map.
    parent = os.getppid()
    _libc.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL)
    if os.getppid() != parent:
        os._exit(1)


@pytest.fixture(scope="session")
def server():
    """Connect target (a socket path) of a gnitz-server shared by the session."""
    binary = _server_binary()
    env = os.environ.copy()
    # The server fallocates its whole SAL at startup; the production default is 1 GiB.
    env.setdefault("GNITZ_SAL_BYTES", str(128 * 1024 * 1024))
    env.setdefault("GNITZ_CPU_AFFINITY", "0")
    # Not pytest's tmp_path: a socket path has to fit sun_path's 108 bytes.
    with tempfile.TemporaryDirectory(prefix="gnitz-") as tmp:
        sock, log_path = os.path.join(tmp, "s.sock"), os.path.join(tmp, "server.log")
        with open(log_path, "ab") as log:
            proc = subprocess.Popen(
                [binary, os.path.join(tmp, "data"), sock],
                stdout=log, stderr=log, env=env,
                start_new_session=True, preexec_fn=_die_with_parent,
            )  # fmt: skip
        try:
            deadline = time.monotonic() + 10
            while _READY_MARKER not in Path(log_path).read_text(errors="replace"):
                if proc.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(
                        f"gnitz-server did not become ready (rc={proc.poll()})\n"
                        + Path(log_path).read_text(errors="replace")[-4096:]
                    )
                time.sleep(0.005)
            yield sock
        finally:
            # The whole group: the master's workers outlive it for a moment.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()


@pytest.fixture
def client(server):
    """A connection in a schema of its own, dropped with everything in it."""
    name = f"s{uuid.uuid4().hex[:12]}"
    with gnitz.connect(server, schema=name) as conn:
        conn.create_schema(name)
        yield conn
        conn.drop_schema(name)
