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

import logging
import os
import secrets
import socket
import threading
from typing import Callable, Optional

from core.constants import IPC_NONCE_LENGTH, IPC_PORT

logger = logging.getLogger(__name__)

def _lockfile_path() -> str:
    """Return the path to the IPC nonce lockfile."""
    from core.paths import get_project_root
    return os.path.join(get_project_root(), "data", ".ipc_nonce")


def _generate_nonce() -> str:
    """Generate and persist a random nonce for IPC authentication."""
    nonce = secrets.token_hex(IPC_NONCE_LENGTH)
    path = _lockfile_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(nonce)
    return nonce


def _read_nonce() -> Optional[str]:
    """Read the nonce from the lockfile, or None if missing."""
    path = _lockfile_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except (FileNotFoundError, OSError):
        return None


def _cleanup_nonce():
    """Remove the nonce lockfile on shutdown."""
    try:
        os.remove(_lockfile_path())
    except (FileNotFoundError, OSError):
        pass


def ping_running_instance() -> bool:
    """
    Attempt to connect to the local IPC port.
    If successful, send an authenticated RESTORE command and return True.
    If connection fails, no instance is listening; return False.
    """
    nonce = _read_nonce()
    if nonce is None:
        return False

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.connect(("127.0.0.1", IPC_PORT))
            s.sendall(f"RESTORE:{nonce}\n".encode("utf-8"))
            return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False


class SingleInstanceServer:
    """
    Background socket server that listens for authenticated RESTORE commands.
    """
    def __init__(self, on_restore: Callable[[], None]):
        self.on_restore = on_restore
        self._server: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._nonce: Optional[str] = None

    def start(self) -> bool:
        """
        Attempt to bind and listen.
        Returns True if successful (we are the primary instance).
        Returns False if port is taken.
        """
        try:
            self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Allow address reuse just in case of rapid restarts,
            # though Windows SO_EXCLUSIVEADDRUSE is sometimes better for singletons.
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server.bind(("127.0.0.1", IPC_PORT))
            self._server.listen(1)
            self._server.settimeout(1.0)
            self._running = True

            # Generate authentication nonce for this session
            self._nonce = _generate_nonce()

            self._thread = threading.Thread(target=self._accept_loop, daemon=True)
            self._thread.start()
            logger.info("Single-Instance IPC server started on port %d", IPC_PORT)
            return True
        except OSError as e:
            logger.warning("Could not bind IPC server (port %d in use): %s", IPC_PORT, e)
            if self._server:
                self._server.close()
                self._server = None
            return False

    def _accept_loop(self):
        while self._running and self._server:
            try:
                conn, addr = self._server.accept()
                with conn:
                    conn.settimeout(1.0)
                    data = conn.recv(1024)
                    message = data.decode("utf-8", errors="ignore").strip()

                    # Authenticate: expect "RESTORE:<nonce>"
                    if message == f"RESTORE:{self._nonce}":
                        logger.info("Authenticated RESTORE signal received.")
                        self.on_restore()
                    else:
                        logger.warning(
                            "IPC: rejected unauthenticated command from %s", addr
                        )
            except socket.timeout:
                continue
            except OSError as e:
                if self._running:
                    logger.debug("IPC server accept error: %s", e)
                break

    def stop(self):
        self._running = False
        _cleanup_nonce()
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        if self._thread and self._thread.is_alive():
            self._thread = None
