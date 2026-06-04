#!/usr/bin/env python3
"""
core/i18n.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Lightweight dict-based translator (i18n)
---------------------------------------------------------------------------
The user base is a Thai accounting office, so the UI must be available in
Thai as well as English. This module is a featherweight translation layer:
no external dependencies, a single in-memory catalog, and a ``tr()`` helper.

Design rules:
  * ``tr(key, **fmt)`` returns the string for the active language, then
    ``.format(**fmt)`` if format args are passed.
  * Missing key in the active language falls back to English, then to the
    key itself — so a partially-translated catalog never crashes the UI.
  * The active language is read from settings.json ("language": "en"|"th",
    default "en") and cached. ``set_language()`` / ``reload_language()``
    refresh the cache so a Settings change applies without a process restart
    where the surface re-reads its labels.

SCOPE — what is intentionally NOT translated (compliance / debuggability):
  * log lines, audit CSV content, persisted error strings.
These stay English so an auditor or a support engineer reads one canonical
language regardless of the operator's UI choice. Only live, on-screen,
user-facing text is routed through ``tr()``.

Thai strings use a professional accounting-office register (formal, concise).
"""

import logging
import threading

logger = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = ("en", "th")

# Human-readable language names for a selector control.
LANGUAGE_LABELS = {
    "en": "English",
    "th": "ไทย",
}

# ---------------------------------------------------------------------------
# Translation catalog
# ---------------------------------------------------------------------------
# Structure: {key: {"en": "...", "th": "..."}}.
# Keys are dotted, namespaced by surface (main., settings., token., etc.).
# Strings with {placeholders} are .format()-ed by tr(); placeholder NAMES are
# identical across languages so a caller passes one kwargs dict for any locale.
CATALOG: dict[str, dict[str, str]] = {
    # ── Main window: header / footer ──────────────────────────────────
    "main.header_title": {
        "en": "Bank of Thailand  —  Ledger Processor",
        "th": "ธนาคารแห่งประเทศไทย  —  ระบบประมวลผลสมุดบัญชี",
    },
    "main.header_sub": {
        "en": "Enterprise Desktop Edition",
        "th": "รุ่นสำหรับองค์กร",
    },
    "main.settings_btn": {
        "en": "⚙  Settings",
        "th": "⚙  ตั้งค่า",
    },
    # ── Main window: date section ─────────────────────────────────────
    "main.date_section": {
        "en": "RATE EXTRACTION DATE",
        "th": "วันที่ดึงอัตราแลกเปลี่ยน",
    },
    "main.auto_detect_toggle": {
        "en": "  Auto-Detect Date Range from Ledger",
        "th": "  ตรวจหาช่วงวันที่จากสมุดบัญชีอัตโนมัติ",
    },
    "main.auto_hint": {
        "en": "Start date will be read from your Excel files automatically.",
        "th": "ระบบจะอ่านวันที่เริ่มต้นจากไฟล์ Excel ของคุณโดยอัตโนมัติ",
    },
    "main.manual_hint": {
        "en": "Manual override — select a start date below.",
        "th": "กำหนดเอง — เลือกวันที่เริ่มต้นด้านล่าง",
    },
    "main.use_today_toggle": {
        "en": "  Use Today's Date",
        "th": "  ใช้วันที่ของวันนี้",
    },
    "main.today_hint": {
        "en": "Rates will be extracted up to: {date}",
        "th": "จะดึงอัตราแลกเปลี่ยนถึงวันที่: {date}",
    },
    "main.custom_date_hint": {
        "en": "Select a custom historical start date below.",
        "th": "เลือกวันที่เริ่มต้นย้อนหลังตามต้องการด้านล่าง",
    },
    "main.label_year": {"en": "Year", "th": "ปี"},
    "main.label_month": {"en": "Month", "th": "เดือน"},
    "main.label_day": {"en": "Day", "th": "วัน"},
    # ── Main window: input / drop zone ────────────────────────────────
    "main.input_section": {
        "en": "LEDGER INPUT",
        "th": "ไฟล์สมุดบัญชี",
    },
    "main.drop_hint_dnd": {
        "en": "Drop Excel files or folders here",
        "th": "ลากไฟล์ Excel หรือโฟลเดอร์มาวางที่นี่",
    },
    "main.drop_hint_click": {
        "en": "Click to select files",
        "th": "คลิกเพื่อเลือกไฟล์",
    },
    "main.drop_sub": {
        "en": "or click to browse",
        "th": "หรือคลิกเพื่อเรียกดู",
    },
    "main.drop_change": {
        "en": "Click to change selection",
        "th": "คลิกเพื่อเปลี่ยนรายการที่เลือก",
    },
    "main.queue_ready": {
        "en": "Ready to process {count} ledger{plural}.",
        "th": "พร้อมประมวลผลสมุดบัญชี {count} ไฟล์",
    },
    # ── Main window: action buttons ───────────────────────────────────
    "main.btn_process": {
        "en": "Process Batch",
        "th": "ประมวลผลทั้งชุด",
    },
    "main.btn_revert": {
        "en": "Revert Previous Edit",
        "th": "ย้อนกลับการแก้ไขก่อนหน้า",
    },
    "main.btn_backups": {
        "en": "Browse Backups",
        "th": "เรียกดูไฟล์สำรอง",
    },
    "main.btn_exrate": {
        "en": "ExRate Sheet",
        "th": "สร้างชีตอัตราแลกเปลี่ยน",
    },
    "main.btn_reveal": {
        "en": "Show File in Folder",
        "th": "แสดงไฟล์ในโฟลเดอร์",
    },
    "main.dryrun_toggle": {
        "en": "  Simulation Mode (Dry Run)",
        "th": "  โหมดจำลอง (ทดลองโดยไม่บันทึก)",
    },
    "main.dryrun_hint": {
        "en": "Preview changes in the Processing Log without modifying files.",
        "th": "ดูตัวอย่างการเปลี่ยนแปลงในบันทึกการประมวลผลโดยไม่แก้ไขไฟล์",
    },
    # ── Main window: status lines ─────────────────────────────────────
    "main.status_ready": {
        "en": "Status:  Ready  —  Backups enabled",
        "th": "สถานะ:  พร้อม  —  เปิดใช้งานการสำรองไฟล์",
    },
    "main.status_busy": {
        "en": "Busy:  a batch is already running — please wait.",
        "th": "ไม่ว่าง:  กำลังประมวลผลอยู่ — กรุณารอสักครู่",
    },
    "main.status_complete_all": {
        "en": "Complete:  All {count} ledger{plural} processed successfully.",
        "th": "เสร็จสมบูรณ์:  ประมวลผลสมุดบัญชีทั้ง {count} ไฟล์สำเร็จ",
    },
    "main.status_complete_partial": {
        "en": (
            "Complete:  {success} succeeded, {fail} failed.  "
            "See failed files below."
        ),
        "th": (
            "เสร็จสิ้น:  สำเร็จ {success} ไฟล์, ล้มเหลว {fail} ไฟล์  "
            "ดูไฟล์ที่ล้มเหลวด้านล่าง"
        ),
    },
    "main.status_simulation": {
        "en": (
            "Simulation complete:  {count} ledger{plural} previewed "
            "(no files changed)."
        ),
        "th": (
            "การจำลองเสร็จสิ้น:  ดูตัวอย่างสมุดบัญชี {count} ไฟล์ "
            "(ไม่มีการแก้ไขไฟล์)"
        ),
    },
    "main.status_error": {
        "en": "Error:  {msg}",
        "th": "ข้อผิดพลาด:  {msg}",
    },
    "main.failed_files_header": {
        "en": "Failed files ({count}) — fix and re-run:",
        "th": "ไฟล์ที่ล้มเหลว ({count}) — แก้ไขแล้วประมวลผลใหม่:",
    },
    # ── Main window: dialogs (messagebox titles + bodies) ─────────────
    "main.invalid_date_title": {
        "en": "Invalid Date",
        "th": "วันที่ไม่ถูกต้อง",
    },
    "main.invalid_date_body": {
        "en": (
            "The selected date '{date}' is not valid.\n\n"
            "Please select a valid calendar date."
        ),
        "th": (
            "วันที่ที่เลือก '{date}' ไม่ถูกต้อง\n\n"
            "กรุณาเลือกวันที่ในปฏิทินที่ถูกต้อง"
        ),
    },
    "main.format_warning_title": {
        "en": "Format Warning",
        "th": "คำเตือนเรื่องรูปแบบไฟล์",
    },
    "main.format_warning_body": {
        "en": (
            "These files use an unsupported format:\n{names}\n\n"
            "Please save them as .xlsx or .xlsm first."
        ),
        "th": (
            "ไฟล์เหล่านี้ใช้รูปแบบที่ไม่รองรับ:\n{names}\n\n"
            "กรุณาบันทึกเป็น .xlsx หรือ .xlsm ก่อน"
        ),
    },
    "main.no_valid_files_title": {
        "en": "No Valid Files",
        "th": "ไม่พบไฟล์ที่ใช้ได้",
    },
    "main.no_valid_files_unsupported": {
        "en": "No supported Excel files found.",
        "th": "ไม่พบไฟล์ Excel ที่รองรับ",
    },
    "main.no_valid_files_empty": {
        "en": "No Excel files found in the dropped items.",
        "th": "ไม่พบไฟล์ Excel ในรายการที่ลากมาวาง",
    },
    "main.preflight_warning_title": {
        "en": "Files May Not Process",
        "th": "ไฟล์อาจประมวลผลไม่ได้",
    },
    "main.preflight_warning_body": {
        "en": (
            "These files were added but may fail when you process them:\n"
            "{reasons}\n\n"
            "Fix them now (close the file in Excel, or save within the "
            "size limit) so the batch does not stop on them."
        ),
        "th": (
            "เพิ่มไฟล์เหล่านี้แล้ว แต่อาจล้มเหลวเมื่อประมวลผล:\n"
            "{reasons}\n\n"
            "กรุณาแก้ไขก่อน (ปิดไฟล์ใน Excel หรือบันทึกให้อยู่ในขนาดที่กำหนด) "
            "เพื่อไม่ให้การประมวลผลหยุดที่ไฟล์เหล่านี้"
        ),
    },
    # ── Settings modal ────────────────────────────────────────────────
    "settings.title": {
        "en": "Settings",
        "th": "ตั้งค่า",
    },
    "settings.heading": {
        "en": "Application Settings",
        "th": "การตั้งค่าโปรแกรม",
    },
    "settings.section_appearance": {
        "en": "APPEARANCE",
        "th": "ลักษณะการแสดงผล",
    },
    "settings.section_language": {
        "en": "LANGUAGE",
        "th": "ภาษา",
    },
    "settings.language_restart_note": {
        "en": "Language applies after you reopen this window and the menus.",
        "th": "การเปลี่ยนภาษาจะมีผลเมื่อเปิดหน้าต่างและเมนูอีกครั้ง",
    },
    "settings.section_rate_type": {
        "en": "RATE TYPE",
        "th": "ประเภทอัตราแลกเปลี่ยน",
    },
    "settings.section_anomaly": {
        "en": "ANOMALY THRESHOLD (%)",
        "th": "เกณฑ์ตรวจจับความผิดปกติ (%)",
    },
    "settings.anomaly_invalid": {
        "en": "Enter a number greater than 0 (e.g. 5.0).",
        "th": "กรุณาใส่ตัวเลขที่มากกว่า 0 (เช่น 5.0)",
    },
    "settings.anomaly_nonpositive": {
        "en": "Threshold must be greater than 0.",
        "th": "เกณฑ์ต้องมากกว่า 0",
    },
    "settings.auto_update_toggle": {
        "en": "  Check for updates on startup",
        "th": "  ตรวจหาการอัปเดตเมื่อเปิดโปรแกรม",
    },
    "settings.btn_manage_keys": {
        "en": "Manage API Keys",
        "th": "จัดการคีย์ API",
    },
    "settings.btn_open_logs": {
        "en": "Open Logs / Audit Folder",
        "th": "เปิดโฟลเดอร์บันทึก / ตรวจสอบ",
    },
    "settings.open_logs_failed": {
        "en": "Could not open the logs folder.",
        "th": "ไม่สามารถเปิดโฟลเดอร์บันทึกได้",
    },
    "settings.btn_save": {
        "en": "Save and Close",
        "th": "บันทึกและปิด",
    },
    # ── Token registration dialog ─────────────────────────────────────
    "token.window_title": {
        "en": "BOT Exchange Rate — API Registration",
        "th": "BOT Exchange Rate — ลงทะเบียน API",
    },
    "token.heading": {
        "en": "API Registration",
        "th": "ลงทะเบียน API",
    },
    "token.subheading": {
        "en": "Enter your Bank of Thailand API keys to activate",
        "th": "ใส่คีย์ API ของธนาคารแห่งประเทศไทยเพื่อเปิดใช้งาน",
    },
    "token.label_exg": {
        "en": "EXCHANGE RATE API KEY",
        "th": "คีย์ API อัตราแลกเปลี่ยน",
    },
    "token.label_hol": {
        "en": "HOLIDAY API KEY",
        "th": "คีย์ API วันหยุด",
    },
    "token.placeholder_exg": {
        "en": "Paste your exchange rate API key here",
        "th": "วางคีย์ API อัตราแลกเปลี่ยนที่นี่",
    },
    "token.placeholder_hol": {
        "en": "Paste your holiday API key here",
        "th": "วางคีย์ API วันหยุดที่นี่",
    },
    "token.show_keys": {
        "en": "Show keys",
        "th": "แสดงคีย์",
    },
    "token.btn_test": {
        "en": "Test Keys",
        "th": "ทดสอบคีย์",
    },
    "token.btn_activate": {
        "en": "Activate",
        "th": "เปิดใช้งาน",
    },
    "token.btn_continue": {
        "en": "Continue anyway",
        "th": "ดำเนินการต่อ",
    },
    "token.portal_link": {
        "en": "Don't have keys? Register at apiportal.bot.or.th",
        "th": "ยังไม่มีคีย์? ลงทะเบียนที่ apiportal.bot.or.th",
    },
    "token.err_both_required": {
        "en": "Both API keys are required.",
        "th": "ต้องใส่คีย์ API ทั้งสองรายการ",
    },
    "token.err_enter_before_test": {
        "en": "Enter both keys before testing.",
        "th": "กรุณาใส่คีย์ทั้งสองก่อนทดสอบ",
    },
    "token.err_too_short": {
        "en": "API keys appear too short. Please check and try again.",
        "th": "คีย์ API สั้นเกินไป กรุณาตรวจสอบแล้วลองใหม่",
    },
    "token.testing": {
        "en": "Testing keys…",
        "th": "กำลังทดสอบคีย์…",
    },
    "token.test_ok": {
        "en": "✓ Both keys accepted — connection verified.",
        "th": "✓ คีย์ทั้งสองใช้ได้ — ยืนยันการเชื่อมต่อแล้ว",
    },
    "token.keychain_fallback": {
        "en": (
            "Could not save to the OS keychain — keys were saved to a "
            "local file instead. Check keychain access if you expected "
            "secure storage."
        ),
        "th": (
            "ไม่สามารถบันทึกลงในที่เก็บคีย์ของระบบได้ — บันทึกคีย์ไว้ในไฟล์ "
            "ภายในเครื่องแทน หากต้องการที่เก็บแบบปลอดภัยกรุณาตรวจสอบสิทธิ์การเข้าถึง"
        ),
    },
    # ── Scheduler panel ───────────────────────────────────────────────
    "sched.title": {
        "en": "⏰ Auto-Processing",
        "th": "⏰ ประมวลผลอัตโนมัติ",
    },
    "sched.run_at": {
        "en": "Run at:",
        "th": "เวลาทำงาน:",
    },
    "sched.watch_folders": {
        "en": "Watch Folders:",
        "th": "โฟลเดอร์ที่เฝ้าดู:",
    },
    "sched.btn_add": {
        "en": "+ Add Folder",
        "th": "+ เพิ่มโฟลเดอร์",
    },
    "sched.btn_remove": {
        "en": "✕ Remove",
        "th": "✕ ลบ",
    },
    "sched.status_no_folders": {
        "en": "⚠ No folders selected — add at least one.",
        "th": "⚠ ยังไม่ได้เลือกโฟลเดอร์ — กรุณาเพิ่มอย่างน้อยหนึ่งโฟลเดอร์",
    },
    "sched.status_next_run": {
        "en": "Next run: {time} — watching {count} folder{plural}",
        "th": "ทำงานครั้งถัดไป: {time} — เฝ้าดู {count} โฟลเดอร์",
    },
    "sched.skip_weekends": {
        "en": "  Skip weekends",
        "th": "  ข้ามวันหยุดสุดสัปดาห์",
    },
    "sched.skip_holidays": {
        "en": "  Skip Thai holidays",
        "th": "  ข้ามวันหยุดธนาคารไทย",
    },
    # ── CSV panel ─────────────────────────────────────────────────────
    "csv.btn_import": {
        "en": "Import Offline Rates (CSV)",
        "th": "นำเข้าอัตราแบบออฟไลน์ (CSV)",
    },
    "csv.btn_export": {
        "en": "Export Cached Rates (CSV)",
        "th": "ส่งออกอัตราที่บันทึกไว้ (CSV)",
    },
    "csv.importing": {
        "en": "Importing...",
        "th": "กำลังนำเข้า...",
    },
    "csv.exporting": {
        "en": "Exporting...",
        "th": "กำลังส่งออก...",
    },
    "csv.import_ok": {
        "en": "✓ Imported {count} rate entries",
        "th": "✓ นำเข้าอัตรา {count} รายการแล้ว",
    },
    "csv.export_ok": {
        "en": "✓ Exported {count} rate rows",
        "th": "✓ ส่งออกอัตรา {count} แถวแล้ว",
    },
    # ── ExRate dialog ─────────────────────────────────────────────────
    "exrate.window_title": {
        "en": "Create ExRate File",
        "th": "สร้างไฟล์อัตราแลกเปลี่ยน",
    },
    "exrate.heading": {
        "en": "ExRate Sheet Options",
        "th": "ตัวเลือกชีตอัตราแลกเปลี่ยน",
    },
    "exrate.section_currencies": {
        "en": "Currencies",
        "th": "สกุลเงิน",
    },
    "exrate.section_rate_types": {
        "en": "Rate Types",
        "th": "ประเภทอัตรา",
    },
    "exrate.section_date_range": {
        "en": "Date Range",
        "th": "ช่วงวันที่",
    },
    "exrate.manual_toggle": {
        "en": "  Select dates manually",
        "th": "  เลือกวันที่เอง",
    },
    "exrate.label_start": {
        "en": "Start:",
        "th": "เริ่ม:",
    },
    "exrate.label_end": {
        "en": "End:",
        "th": "สิ้นสุด:",
    },
    "exrate.btn_create": {
        "en": "Create ExRate File",
        "th": "สร้างไฟล์อัตราแลกเปลี่ยน",
    },
    "exrate.err_batch_running": {
        "en": "A batch is running — wait for it to finish first.",
        "th": "กำลังประมวลผลอยู่ — กรุณารอให้เสร็จก่อน",
    },
    "exrate.err_no_currency": {
        "en": "Select at least one currency",
        "th": "กรุณาเลือกสกุลเงินอย่างน้อยหนึ่งสกุล",
    },
    "exrate.err_no_rate_type": {
        "en": "Select at least one rate type",
        "th": "กรุณาเลือกประเภทอัตราอย่างน้อยหนึ่งประเภท",
    },
    "exrate.err_invalid_date": {
        "en": "Invalid date entered",
        "th": "วันที่ที่ใส่ไม่ถูกต้อง",
    },
    "exrate.creating": {
        "en": "Creating ExRate file...",
        "th": "กำลังสร้างไฟล์อัตราแลกเปลี่ยน...",
    },
    # ── Backup browser ────────────────────────────────────────────────
    "backup.window_title": {
        "en": "Backup Browser",
        "th": "เรียกดูไฟล์สำรอง",
    },
    "backup.heading": {
        "en": "Backup History",
        "th": "ประวัติไฟล์สำรอง",
    },
    "backup.subheading": {
        "en": "Select a backup, then Restore it over the current file.",
        "th": "เลือกไฟล์สำรอง แล้วกู้คืนทับไฟล์ปัจจุบัน",
    },
    "backup.none_found": {
        "en": (
            "No backups found yet.\n\n"
            "A file gets a backup the first time it is processed."
        ),
        "th": (
            "ยังไม่พบไฟล์สำรอง\n\n"
            "ไฟล์จะถูกสำรองเมื่อมีการประมวลผลครั้งแรก"
        ),
    },
    "backup.btn_close": {
        "en": "Close",
        "th": "ปิด",
    },
    "backup.btn_restore": {
        "en": "Restore Selected",
        "th": "กู้คืนรายการที่เลือก",
    },
}


# ---------------------------------------------------------------------------
# Active-language state (cached, thread-safe)
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_active_language: str | None = None


def _normalize(lang: str | None) -> str:
    """Coerce a raw language code to a supported one (default English)."""
    if isinstance(lang, str):
        code = lang.strip().lower()
        if code in SUPPORTED_LANGUAGES:
            return code
    return DEFAULT_LANGUAGE


def _read_language_from_settings() -> str:
    """Read the persisted language from settings.json (default English).

    Imported lazily so core.i18n stays free of a hard import cycle with
    config_manager (which imports core.paths, etc.). Any failure falls back
    to English rather than raising into the UI build path.
    """
    try:
        from core.config_manager import SettingsManager

        value = SettingsManager().get("language", DEFAULT_LANGUAGE)
    except (OSError, ValueError, ImportError) as e:
        logger.debug("Could not read language from settings: %s", e)
        return DEFAULT_LANGUAGE
    return _normalize(value)


def get_language() -> str:
    """Return the active language code, loading it from settings once."""
    global _active_language
    with _lock:
        if _active_language is None:
            _active_language = _read_language_from_settings()
        return _active_language


def set_language(lang: str) -> str:
    """Set the active language in memory (does NOT persist).

    Persisting is the Settings modal's job; this just refreshes the cached
    value so subsequently-built surfaces pick up the new language. Returns the
    normalized code actually applied.
    """
    global _active_language
    normalized = _normalize(lang)
    with _lock:
        _active_language = normalized
    return normalized


def reload_language() -> str:
    """Re-read the active language from settings.json, bypassing the cache."""
    global _active_language
    value = _read_language_from_settings()
    with _lock:
        _active_language = value
    return value


def tr(key: str, **fmt) -> str:
    """Translate ``key`` to the active language with optional formatting.

    Resolution order: active language → English → the key itself. The final
    fallback to the key (rather than an empty string) keeps a missing entry
    debuggable on screen instead of silently blanking a label.

    Any ``**fmt`` kwargs are applied with ``str.format``; a KeyError there
    (placeholder mismatch) degrades to the unformatted string rather than
    raising into the Tk build path.
    """
    lang = get_language()
    entry = CATALOG.get(key) or {}
    text = entry.get(lang) or entry.get(DEFAULT_LANGUAGE) or key
    if not fmt:
        return text
    try:
        return text.format(**fmt)
    except (KeyError, IndexError, ValueError) as e:
        logger.debug("tr() format failed for key %r: %s", key, e)
        return text


def plural(count: int) -> str:
    """Return the English plural suffix ('' or 's') for ``count``.

    A helper for the ``{plural}`` placeholder used by several catalog
    entries. Thai has no plural inflection, so the Thai strings simply omit
    the placeholder and this value is ignored there.
    """
    return "" if count == 1 else "s"
