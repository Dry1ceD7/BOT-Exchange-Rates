"""
core/ipc.py
---------------------------------------------------------------------------
Single-Instance IPC Manager
---------------------------------------------------------------------------
Ensures only one instance of the application runs at a time.
Provides a socket-based mechanism to wake up a minimized/tray instance
from a subsequent launch attempt.
"""

import logging
import socket
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

IPC_PORT = 45654
RESTORE_COMMAND = b"RESTORE\n"

def ping_running_instance() -> bool:
    """
    Attempt to connect to the local IPC port.
    If successful, send a RESTORE command and return True.
    If connection fails, no instance is listening; return False.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.connect(("127.0.0.1", IPC_PORT))
            s.sendall(RESTORE_COMMAND)
            return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False


class SingleInstanceServer:
    """
    Background socket server that listens for RESTORE commands.
    """
    def __init__(self, on_restore: Callable[[], None]):
        self.on_restore = on_restore
        self._server: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

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
                    if data == RESTORE_COMMAND:
                        logger.info("Received RESTORE signal from subsequent launch.")
                        # Invoke callback, which must handle thread safety if interacting with Tkinter
                        self.on_restore()
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    logger.debug("IPC server accept error: %s", e)
                break

    def stop(self):
        self._running = False
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass
            self._server = None
        if self._thread and self._thread.is_alive():
            self._thread = None
