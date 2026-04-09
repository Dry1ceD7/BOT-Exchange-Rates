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
import sys
import threading
from multiprocessing.connection import Client, Listener
from typing import Callable, Optional

from core.constants import IPC_NONCE_LENGTH

logger = logging.getLogger(__name__)

# Fallback path logic to ensure safe OS-specific binding
def _get_ipc_address() -> str:
    """Return the native OS IPC address (Named Pipe or Domain Socket)."""
    if sys.platform == "win32":
        return r"\\.\pipe\bot_exrate_ipc"
    else:
        # Use robust tmp directory
        return "/tmp/bot_exrate_ipc.sock"

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
    # Ensure only the owner can read/write this file
    os.chmod(path, 0o600)
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

    address = _get_ipc_address()
    try:
        with Client(address) as conn:
            conn.send(f"RESTORE:{nonce}")
            return True
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        return False


class SingleInstanceServer:
    """
    Background socket server that listens for authenticated RESTORE commands.
    """
    def __init__(self, on_restore: Callable[[], None]):
        self.on_restore = on_restore
        self._listener: Optional[Listener] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._nonce: Optional[str] = None

    def start(self) -> bool:
        """
        Attempt to bind and listen.
        Returns True if successful (we are the primary instance).
        Returns False if port/pipe is taken.
        """
        address = _get_ipc_address()
        try:
            # On Unix, if the socket wasn't cleaned up, remove it first
            if sys.platform != "win32" and os.path.exists(address):
                try:
                    # Test if it's dead
                    with Client(address):
                        return False # Someone is still listening
                except ConnectionRefusedError:
                    os.remove(address) # It's a dead socket, safe to bind

            self._listener = Listener(address)
            self._running = True

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
        while self._running and self._listener:
            try:
                # Wait for connection or interruption
                if not self._listener.poll(1.0):
                    continue

                with self._listener.accept() as conn:
                    message = conn.recv()
                    # Authenticate: expect "RESTORE:<nonce>"
                    if message == f"RESTORE:{self._nonce}":
                        logger.info("Authenticated RESTORE signal received.")
                        self.on_restore()
                    else:
                        logger.warning("IPC: rejected unauthenticated command")
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
            try:
                self._listener.close()
            except OSError:
                pass
            self._listener = None
        if self._thread and self._thread.is_alive():
            self._thread = None

        # Cleanup Unix socket file
        if sys.platform != "win32":
            try:
                address = _get_ipc_address()
                if os.path.exists(address):
                    os.remove(address)
            except OSError:
                pass
