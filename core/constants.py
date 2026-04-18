#!/usr/bin/env python3
"""
core/constants.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Configuration Constants
---------------------------------------------------------------------------
Centralized configuration values. All magic numbers are documented here
with their purpose and safe default values.

Override via environment variables where noted.
"""

import os

# ── File Processing ──────────────────────────────────────────────────────
MAX_FILE_SIZE_MB: int = int(os.environ.get("BOT_MAX_FILE_MB", "50"))
"""Maximum allowed Excel file size in megabytes."""

SUPPORTED_EXCEL_EXTENSIONS: tuple = (".xlsx", ".xlsm")
"""File extensions accepted for processing."""

PREFORMAT_BUFFER_ROWS: int = 50
"""Number of rows below data to pre-format with DD/MM/YYYY."""

BACKUP_MAX_AGE_DAYS: int = int(os.environ.get("BOT_BACKUP_AGE_DAYS", "7"))
"""Auto-cleanup backups older than this many days."""

MIN_DISK_SPACE_MB: int = 100
"""Minimum free disk space (MB) required before saving a workbook."""

# ── Network ──────────────────────────────────────────────────────────────
MAX_429_RETRIES: int = int(os.environ.get("BOT_MAX_429_RETRIES", "10"))
"""Maximum retries for HTTP 429 rate limiting responses."""

API_TIMEOUT_SECONDS: float = 30.0
"""Default httpx timeout for API calls."""

API_CONNECT_TIMEOUT_SECONDS: float = 10.0
"""Default httpx connect timeout."""

# ── IPC ──────────────────────────────────────────────────────────────────
IPC_NONCE_LENGTH: int = 32
"""Length of hex nonce for IPC authentication."""

# ── Scheduler ────────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS: int = int(os.environ.get("BOT_POLL_INTERVAL", "30"))
"""Background scheduler polling interval."""

# ── Anomaly Detection ────────────────────────────────────────────────────
DEFAULT_ANOMALY_THRESHOLD_PCT: float = 5.0
"""Default day-over-day rate change threshold for anomaly guardian."""
