#!/usr/bin/env python3
"""
tests/test_ipc.py
---------------------------------------------------------------------------
Security regression tests for core/ipc.py.

The IPC channel MUST NOT unpickle attacker-controlled bytes. These tests
verify the bytes-only protocol: a correct nonce triggers on_restore, while
a wrong / raw payload is rejected.
"""

import sys
import threading
import time

import pytest

import core.ipc as ipc

# Named-pipe path resolution + select() polling is POSIX-oriented in this
# codebase; run the live socket tests on non-Windows only.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX domain-socket IPC tests"
)


@pytest.fixture
def isolated_ipc(tmp_path, monkeypatch):
    """Point the socket + nonce lockfile at a temp dir for isolation."""
    sock = str(tmp_path / "test_ipc.sock")
    nonce_file = str(tmp_path / ".ipc_nonce")
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
