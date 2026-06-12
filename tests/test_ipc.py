#!/usr/bin/env python3
"""
tests/test_ipc.py
---------------------------------------------------------------------------
Security regression tests for core/ipc.py.

The IPC channel MUST NOT unpickle attacker-controlled bytes. These tests
verify the bytes-only protocol: a correct nonce triggers on_restore, while
a wrong / raw payload is rejected.
"""

import os
import shutil
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

import core.ipc as ipc

# Named-pipe path resolution + select() polling is POSIX-oriented in this
# codebase; run the live socket tests on non-Windows only.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX domain-socket IPC tests"
)


@pytest.fixture
def short_tmp_dir():
    """AF_UNIX sun_path is capped (~104 bytes on macOS, 108 on Linux);
    pytest's tmp_path nests too deep on macOS and the bind fails with
    "AF_UNIX path too long". Use a short dir directly under the system
    tempdir instead.
    """
    d = Path(tempfile.mkdtemp(prefix="ipc_"))
    sock_probe = d / "test_ipc.sock"
    assert len(str(sock_probe).encode()) < 100, (
        f"socket path still too long for sun_path: {sock_probe}"
    )
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def isolated_ipc(short_tmp_dir, monkeypatch):
    """Point the socket + nonce lockfile at a temp dir for isolation."""
    sock = str(short_tmp_dir / "test_ipc.sock")
    nonce_file = str(short_tmp_dir / ".ipc_nonce")
    monkeypatch.setattr(ipc, "_get_ipc_address", lambda: sock)
    monkeypatch.setattr(ipc, "_lockfile_path", lambda: nonce_file)
    return sock, nonce_file


def test_correct_nonce_triggers_restore(isolated_ipc):
    """A RESTORE message with the right nonce calls on_restore."""
    restored = threading.Event()
    server = ipc.SingleInstanceServer(on_restore=restored.set)
    assert server.start() is True
    try:
        # ping_running_instance reads the nonce file the server wrote.
        assert ipc.ping_running_instance() is True
        assert restored.wait(timeout=3.0) is True
    finally:
        server.stop()


def test_wrong_nonce_rejected(isolated_ipc):
    """A RESTORE with a bad nonce must NOT call on_restore."""
    from multiprocessing.connection import Client

    sock, _ = isolated_ipc
    restored = threading.Event()
    server = ipc.SingleInstanceServer(on_restore=restored.set)
    assert server.start() is True
    try:
        with Client(sock) as conn:
            conn.send_bytes(b"RESTORE:deadbeef-not-the-nonce")
        # Give the accept loop a moment to process.
        time.sleep(0.5)
        assert restored.is_set() is False
    finally:
        server.stop()


def test_raw_garbage_payload_rejected(isolated_ipc):
    """Arbitrary raw bytes must be rejected, never unpickled/executed."""
    from multiprocessing.connection import Client

    sock, _ = isolated_ipc
    restored = threading.Event()
    server = ipc.SingleInstanceServer(on_restore=restored.set)
    assert server.start() is True
    try:
        with Client(sock) as conn:
            # Bytes that would be a malicious pickle if recv() were used.
            conn.send_bytes(b"\x80\x04\x95\x00garbage")
        time.sleep(0.5)
        assert restored.is_set() is False
    finally:
        server.stop()


def test_holdopen_client_does_not_wedge_restore(isolated_ipc):
    """Round 11: a client that connects but never sends data must not
    starve the accept loop. Pre-fix, the loop blocked forever in
    conn.recv_bytes(256) on the silent holder, so a second launch's
    RESTORE sat unaccepted while the launch exited 0 believing it was
    delivered. The bounded conn.poll(2.0) drops the holder and the queued
    RESTORE is then accepted and fires on_restore."""
    from multiprocessing.connection import Client

    sock, _ = isolated_ipc
    restored = threading.Event()
    server = ipc.SingleInstanceServer(on_restore=restored.set)
    assert server.start() is True
    holder = None
    try:
        holder = Client(sock)  # connects, never sends anything
        time.sleep(0.3)  # let the accept loop pick up the holder
        # The "second launch": main.py treats True as delivered + exits 0.
        assert ipc.ping_running_instance() is True
        # RESTORE must be delivered while the holder is STILL connected.
        assert restored.wait(timeout=6.0) is True
    finally:
        if holder is not None:
            holder.close()
        server.stop()


# ───────────────────────────────────────────────────────────────────────
#  Per-user private socket path
# ───────────────────────────────────────────────────────────────────────
def test_address_is_under_per_user_private_dir():
    """The POSIX socket must live under tempdir/bot_exrate_<uid>/ipc.sock."""
    addr = ipc._get_ipc_address()
    expected_dir = os.path.join(tempfile.gettempdir(), f"bot_exrate_{os.getuid()}")
    assert addr == os.path.join(expected_dir, "ipc.sock")
    # The private runtime dir must exist and be owner-only (0700).
    assert os.path.isdir(expected_dir)
    mode = stat.S_IMODE(os.stat(expected_dir).st_mode)
    assert mode == 0o700


def test_real_per_user_path_authenticates(short_tmp_dir, monkeypatch):
    """A full RESTORE round-trip works against the real per-user path resolver.

    Only the runtime dir is redirected (into tmp); the address-building logic
    in _get_ipc_address is exercised unchanged so the new path still binds and
    authenticates.
    """
    tmp_path = short_tmp_dir
    monkeypatch.setattr(ipc, "_ipc_runtime_dir", lambda: str(tmp_path))
    monkeypatch.setattr(ipc, "_lockfile_path", lambda: str(tmp_path / ".ipc_nonce"))

    restored = threading.Event()
    server = ipc.SingleInstanceServer(on_restore=restored.set)
    assert server.start() is True
    try:
        # Socket should be the real default name under the redirected dir.
        assert ipc._get_ipc_address() == str(tmp_path / "ipc.sock")
        assert ipc.ping_running_instance() is True
        assert restored.wait(timeout=3.0) is True
        # Socket file must be owner-only.
        mode = stat.S_IMODE(os.stat(tmp_path / "ipc.sock").st_mode)
        assert mode == 0o600
    finally:
        server.stop()
