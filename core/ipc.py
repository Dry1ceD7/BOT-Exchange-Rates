"""
core/ipc.py
---------------------------------------------------------------------------
Single-Instance IPC Manager
---------------------------------------------------------------------------
Ensures only one instance of the application runs at a time.
Provides a socket-based mechanism to wake up a minimized/tray instance
from a subsequent launch attempt.

Security: Uses a random nonce stored in a lockfile to authenticate IPC
commands, preventing arbitrary local processes from triggering RESTORE.
"""

import contextlib
import getpass
import hmac
import logging
import os
import secrets
import sys
import tempfile
import threading
from collections.abc import Callable
from multiprocessing.connection import Client, Listener
from pathlib import Path

from core.constants import IPC_NONCE_LENGTH

logger = logging.getLogger(__name__)


def _ipc_runtime_dir() -> str:
    """Return a per-user private directory for the IPC socket (POSIX).

    Placing the socket under a 0700 dir owned by the current user prevents
    other local users from connecting (or pre-creating the socket path).
    """
    try:
        uid = os.getuid()  # POSIX only
    except AttributeError:  # pragma: no cover - non-POSIX
        uid = 0
    # Keep d as str: it is joined into the IPC socket address, which
    # multiprocessing.connection requires to be a plain string.
    d = str(Path(tempfile.gettempdir()) / f"bot_exrate_{uid}")
    try:
        dir_path = Path(d)
        dir_path.mkdir(parents=True, exist_ok=True)
        dir_path.chmod(0o700)
    except OSError:
        pass
    return d


def _get_ipc_address() -> str:
    """Return the native OS IPC address (Named Pipe or Domain Socket)."""
    if sys.platform == "win32":
        # Suffix the pipe name with the username so it is per-user.
        try:
            user = getpass.getuser()
        except Exception:  # pragma: no cover - getuser env edge cases
            user = "default"
        safe_user = "".join(c for c in user if c.isalnum()) or "default"
        return rf"\\.\pipe\bot_exrate_ipc_{safe_user}"
    # Per-user private dir, socket created 0700-protected.
    # multiprocessing.connection requires a str address on Unix.
    return str(Path(_ipc_runtime_dir()) / "ipc.sock")

def _lockfile_path() -> str:
    """Return the path to the IPC nonce lockfile."""
    from core.paths import get_project_root
    return str(Path(get_project_root()) / "data" / ".ipc_nonce")


def _generate_nonce() -> str:
    """Generate and persist a random nonce for IPC authentication."""
    nonce = secrets.token_hex(IPC_NONCE_LENGTH)
    path = Path(_lockfile_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(nonce)
    # Ensure only the owner can read/write this file
    path.chmod(0o600)
    return nonce


def _read_nonce() -> str | None:
    """Read the nonce from the lockfile, or None if missing."""
    path = Path(_lockfile_path())
    try:
        with path.open(encoding="utf-8") as f:
            return f.read().strip()
    except (FileNotFoundError, OSError):
        return None


def _cleanup_nonce():
    """Remove the nonce lockfile on shutdown."""
    with contextlib.suppress(FileNotFoundError, OSError):
        Path(_lockfile_path()).unlink()


def ping_running_instance() -> bool:
    """
    Attempt to connect to the local IPC port.
    If successful, send an authenticated RESTORE command and return True.
    If connection fails, no instance is listening; return False.
    """
    nonce = _read_nonce()
    if nonce is None:
        return False

    address = _get_ipc_address()
    try:
        with Client(address) as conn:
            # SECURITY: send_bytes transmits raw bytes only. We never use
            # send()/recv() because multiprocessing.connection pickles
            # objects, and recv() would UNPICKLE attacker-controlled bytes
            # before any authentication — a local pickle RCE vector.
            conn.send_bytes(f"RESTORE:{nonce}".encode())
            return True
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        return False


class SingleInstanceServer:
    """
    Background socket server that listens for authenticated RESTORE commands.
    """
    def __init__(self, on_restore: Callable[[], None]):
        self.on_restore = on_restore
        self._listener: Listener | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._nonce: str | None = None

    def start(self) -> bool:
        """
        Attempt to bind and listen.
        Returns True if successful (we are the primary instance).
        Returns False if port/pipe is taken.
        """
        address = _get_ipc_address()
        try:
            # On Unix, if the socket wasn't cleaned up, remove it first
            if sys.platform != "win32" and Path(address).exists():
                try:
                    # Test if it's dead
                    with Client(address):
                        return False # Someone is still listening
                except ConnectionRefusedError:
                    Path(address).unlink() # It's a dead socket, safe to bind

            # SECURITY: set a restrictive umask around bind so the socket is
            # never world-accessible in the window before chmod runs.
            if sys.platform != "win32":
                old_umask = os.umask(0o077)
                try:
                    self._listener = Listener(address)
                finally:
                    os.umask(old_umask)
            else:
                self._listener = Listener(address)
            self._running = True

            # SECURITY: restrict the unix socket to the owner only (0o600)
            # so other local users cannot connect and attempt RESTORE.
            if sys.platform != "win32":
                with contextlib.suppress(OSError):
                    Path(address).chmod(0o600)

            # Generate authentication nonce for this session
            self._nonce = _generate_nonce()

            self._thread = threading.Thread(target=self._accept_loop, daemon=True)
            self._thread.start()
            logger.info("Single-Instance IPC server started on %s", address)
            return True
        except OSError as e:
            logger.warning("Could not bind IPC server (address in use): %s", e)
            if self._listener:
                self._listener.close()
                self._listener = None
            return False

    def _accept_loop(self):
        import select

        while self._running and self._listener:
            try:
                # Use select() to poll the listener socket with a timeout,
                # allowing the loop to check self._running periodically.
                # Listener.poll() does NOT exist — it's a Connection method.
                if sys.platform != "win32":
                    try:
                        # NOTE: _listener._listener._socket is an
                        # undocumented internal of multiprocessing.
                        # Wrapped in try/except for cross-version safety.
                        sock = self._listener._listener._socket
                        readable, _, _ = select.select([sock], [], [], 1.0)
                        if not readable:
                            continue
                    except AttributeError:
                        # Fallback: brief sleep to avoid hot-looping,
                        # accept() below will block until a connection.
                        import time
                        time.sleep(1.0)
                        if not self._running:
                            break
                # On Windows named pipes, select() doesn't work.
                # We rely on the short timeout from Client side and
                # the daemon thread flag to stop on shutdown.

                conn = self._listener.accept()
                try:
                    # SECURITY: recv_bytes returns raw bytes and does NOT
                    # unpickle. Cap at 256 bytes to bound memory. We compare
                    # with hmac.compare_digest (constant-time) and NEVER
                    # interpret the payload as a pickled object.
                    raw = conn.recv_bytes(256)
                    expected = f"RESTORE:{self._nonce}".encode()
                    if hmac.compare_digest(raw, expected):
                        logger.info("Authenticated RESTORE signal received.")
                        self.on_restore()
                    else:
                        logger.warning("IPC: rejected unauthenticated command")
                except (EOFError, OSError):
                    pass
                finally:
                    conn.close()
            except EOFError:
                continue
            except OSError as e:
                if self._running:
                    logger.debug("IPC server accept error: %s", e)
                break

    def stop(self):
        self._running = False
        _cleanup_nonce()
        if self._listener:
            with contextlib.suppress(OSError):
                self._listener.close()
            self._listener = None
        if self._thread and self._thread.is_alive():
            self._thread = None

        # Cleanup Unix socket file
        if sys.platform != "win32":
            try:
                address = _get_ipc_address()
                if Path(address).exists():
                    Path(address).unlink()
            except OSError:
                pass
