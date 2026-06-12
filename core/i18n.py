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
        "en": "Settings",
        "th": "ตั้งค่า",
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
    # Crash-resume prompt shown at startup when an unfinished batch manifest
    # is found. _title is a dialog heading (no args); _body/_loaded take the
    # count of pending files plus the {plural} suffix from plural().
    "main.resume_title": {
        "en": "Resume unfinished batch?",
        "th": "ดำเนินการชุดงานที่ค้างต่อหรือไม่?",
    },
    "main.resume_body": {
        "en": (
            "A previous run left {count} ledger{plural} unprocessed.  "
            "Reload them into the queue?"
        ),
        "th": (
            "การทำงานครั้งก่อนเหลือสมุดบัญชี {count} ไฟล์ที่ยังไม่ได้ประมวลผล  "
            "ต้องการโหลดกลับเข้าคิวหรือไม่?"
        ),
    },
    "main.resume_loaded": {
        "en": "Resumed:  {count} ledger{plural} reloaded into the queue.",
        "th": "ดำเนินการต่อ:  โหลดสมุดบัญชี {count} ไฟล์กลับเข้าคิวแล้ว",
    },
    # Variant of main.resume_loaded shown when the saved batch's start date
    # was loaded into the manual date picker — the {date} quoted here is
    # exactly what the engine will receive (UI-hint/engine lockstep).
    "main.resume_date_loaded": {
        "en": (
            "Resumed:  {count} ledger{plural} reloaded — start date {date} "
            "loaded from the saved batch (adjust the date picker to "
            "override)."
        ),
        "th": (
            "ดำเนินการต่อ:  โหลดสมุดบัญชี {count} ไฟล์กลับเข้าคิวแล้ว — "
            "ใช้วันที่เริ่มต้น {date} จากชุดงานที่บันทึกไว้ "
            "(ปรับตัวเลือกวันที่เพื่อเปลี่ยน)"
        ),
    },
    # Warning raised when Settings changed between a crash and the resume:
    # the completed pre-crash files used the SAVED settings, so processing
    # the remainder under the current ones would mix rate bases in one batch.
    "main.resume_settings_title": {
        "en": "Settings changed since the interrupted batch",
        "th": "การตั้งค่าเปลี่ยนไปจากชุดงานที่ค้างไว้",
    },
    "main.resume_settings_body": {
        "en": (
            "The interrupted batch ran with different settings than the "
            "current ones ({changes}).  Files completed before the crash "
            "used the old settings — restore them in Settings before "
            "pressing Process Batch, or the remaining files will be "
            "written on a different rate basis."
        ),
        "th": (
            "ชุดงานที่ค้างไว้ใช้การตั้งค่าต่างจากปัจจุบัน ({changes})  "
            "ไฟล์ที่เสร็จก่อนหน้าใช้การตั้งค่าเดิม — โปรดคืนค่าการตั้งค่า"
            "ก่อนกดประมวลผล มิฉะนั้นไฟล์ที่เหลือจะใช้ฐานอัตราต่างกัน"
        ),
    },
    "main.empty_state_steps": {
        "en": (
            "1. Drop or select your Excel ledgers   "
            "2. Press Process Batch   "
            "3. Review the results below"
        ),
        "th": (
            "1. วางหรือเลือกไฟล์ Excel   "
            "2. กด ประมวลผลชุด   "
            "3. ตรวจสอบผลด้านล่าง"
        ),
    },
    "main.scanning_folder": {
        "en": "Scanning folder for Excel files...",
        "th": "กำลังสแกนโฟลเดอร์หาไฟล์ Excel...",
    },
    # ── Main window: action buttons ───────────────────────────────────
    "main.btn_process": {
        "en": "Process Batch",
        "th": "ประมวลผลทั้งชุด",
    },
    "main.btn_clear_queue": {
        "en": "Clear Queue",
        "th": "ล้างรายการ",
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
    "main.btn_verify_rates": {
        "en": "Verify Rates",
        "th": "ตรวจสอบอัตรา",
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
    "main.status_progress_ok": {
        "en": (
            "Done:  {idx} of {total}  ({remaining} remaining)  |  {fname}"
        ),
        "th": (
            "เสร็จ:  {idx} จาก {total}  (เหลือ {remaining})  |  {fname}"
        ),
    },
    "main.status_progress_skipped": {
        "en": (
            "Skipped:  {idx} of {total}  ({remaining} remaining)  |  {fname}"
        ),
        "th": (
            "ข้าม:  {idx} จาก {total}  (เหลือ {remaining})  |  {fname}"
        ),
    },
    # Window-title progress (shown in taskbar while a batch runs).
    "main.title_processing": {
        "en": "Processing {idx} of {total}",
        "th": "กำลังประมวลผล {idx} จาก {total}",
    },
    "main.title_processing_generic": {
        "en": "Processing...",
        "th": "กำลังประมวลผล...",
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
    # ── Main window: revert flow (picker, confirmation, status) ───────
    "main.revert_picker_title": {
        "en": "Select the file to revert",
        "th": "เลือกไฟล์ที่ต้องการย้อนกลับ",
    },
    "main.no_backup_title": {
        "en": "No Backup Found",
        "th": "ไม่พบไฟล์สำรอง",
    },
    "main.no_backup_body": {
        "en": (
            "No backup exists for '{name}'.\n\n"
            "A file must have been processed at least once to have a "
            "backup to restore from."
        ),
        "th": (
            "ไม่มีไฟล์สำรองสำหรับ '{name}'\n\n"
            "ไฟล์ต้องผ่านการประมวลผลอย่างน้อยหนึ่งครั้ง"
            "จึงจะมีไฟล์สำรองให้กู้คืน"
        ),
    },
    "main.confirm_revert_title": {
        "en": "Confirm Revert",
        "th": "ยืนยันการย้อนกลับ",
    },
    "main.confirm_revert_body": {
        "en": (
            "Restore '{name}' from backup dated {when}?\n\n"
            "This OVERWRITES the current file with the backup. The current "
            "version is snapshotted first (.pre-revert) so this is "
            "recoverable."
        ),
        "th": (
            "กู้คืน '{name}' จากไฟล์สำรองของวันที่ {when} หรือไม่?\n\n"
            "การดำเนินการนี้จะเขียนทับไฟล์ปัจจุบันด้วยไฟล์สำรอง "
            "ระบบจะบันทึกสำเนาไฟล์ปัจจุบันไว้ก่อน (.pre-revert) "
            "จึงสามารถกู้คืนได้"
        ),
    },
    # Fallback for {when} above, used if the backup timestamp is unknown.
    "main.revert_when_fallback": {
        "en": "the latest backup",
        "th": "ไฟล์สำรองล่าสุด",
    },
    "main.status_restoring": {
        "en": "Restoring:  {fname}...",
        "th": "กำลังกู้คืน:  {fname}...",
    },
    "main.status_reverted": {
        "en": "Reverted successfully from backup:  {backup}",
        "th": "กู้คืนจากไฟล์สำรองสำเร็จ:  {backup}",
    },
    # ── Main window: Help & About dialog ──────────────────────────────
    "main.help_btn": {
        "en": "Help",
        "th": "ช่วยเหลือ",
    },
    "main.help_title": {
        "en": "Help & About",
        "th": "ช่วยเหลือและเกี่ยวกับ",
    },
    # Two-line footer form: ownership line, then the company name.
    "main.help_license": {
        "en": (
            "Property of\n"
            "Advanced ID Asia Engineering., Ltd\n"
            "For internal accounting use."
        ),
        "th": (
            "ลิขสิทธิ์ของ\n"
            "Advanced ID Asia Engineering., Ltd\n"
            "ใช้ภายในสำหรับงานบัญชี"
        ),
    },
    "main.help_shortcuts_header": {
        "en": "Keyboard shortcuts",
        "th": "ปุ่มลัด",
    },
    # Shortcut KEYS stay in English (they are literal keystrokes); the
    # action LABELS after each em dash are translated.
    "main.help_shortcuts_body": {
        "en": (
            "F5 / Ctrl+Enter — Process Batch\n"
            "Ctrl+R — Revert Previous Edit\n"
            "Ctrl+E — Create ExRate Sheet\n"
            "F1 — this Help dialog\n"
            "Esc — close a dialog"
        ),
        "th": (
            "F5 / Ctrl+Enter — ประมวลผลทั้งชุด\n"
            "Ctrl+R — ย้อนกลับการแก้ไขก่อนหน้า\n"
            "Ctrl+E — สร้างชีตอัตราแลกเปลี่ยน\n"
            "F1 — เปิดหน้าต่างช่วยเหลือนี้\n"
            "Esc — ปิดหน้าต่าง"
        ),
    },
    "main.help_open_logs": {
        "en": "Open Logs / Audit Folder",
        "th": "เปิดโฟลเดอร์บันทึก / ตรวจสอบ",
    },
    "main.help_close": {
        "en": "Close",
        "th": "ปิด",
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
    "settings.btn_export_settings": {
        "en": "Export Settings…",
        "th": "ส่งออกการตั้งค่า…",
    },
    "settings.btn_import_settings": {
        "en": "Import Settings…",
        "th": "นำเข้าการตั้งค่า…",
    },
    "settings.export_dialog_title": {
        "en": "Export Settings to File",
        "th": "ส่งออกการตั้งค่าไปยังไฟล์",
    },
    "settings.import_dialog_title": {
        "en": "Import Settings from File",
        "th": "นำเข้าการตั้งค่าจากไฟล์",
    },
    "settings.export_ok": {
        "en": "Settings exported. Secrets (API keys) were not included.",
        "th": "ส่งออกการตั้งค่าสำเร็จ ไม่รวมคีย์ API",
    },
    "settings.export_failed": {
        "en": "Could not export settings. Check the location and try again.",
        "th": "ไม่สามารถส่งออกการตั้งค่าได้ กรุณาตรวจสอบตำแหน่งและลองอีกครั้ง",
    },
    "settings.import_ok": {
        "en": "Settings imported.",
        "th": "นำเข้าการตั้งค่าสำเร็จ",
    },
    "settings.import_failed": {
        "en": "Could not import settings. The file was not a valid settings file.",
        "th": "ไม่สามารถนำเข้าการตั้งค่าได้ ไฟล์ไม่ถูกต้อง",
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
    "token.products_guide": {
        "en": (
            "You need TWO keys from two separate BOT API products. "
            "Subscribe to both in the BOT API portal, then paste one key "
            "into each field below."
        ),
        "th": (
            "คุณต้องใช้คีย์สองรายการจากผลิตภัณฑ์ API ของ ธปท. สองรายการที่แยกจากกัน "
            "สมัครใช้งานทั้งสองรายการในพอร์ทัล API ของ ธปท. "
            "แล้ววางคีย์ลงในแต่ละช่องด้านล่าง"
        ),
    },
    "token.label_exg": {
        "en": "EXCHANGE RATE API KEY",
        "th": "คีย์ API อัตราแลกเปลี่ยน",
    },
    "token.hint_exg": {
        "en": 'From the "Daily Average Exchange Rate" API product',
        "th": 'จากผลิตภัณฑ์ API "อัตราแลกเปลี่ยนเฉลี่ยรายวัน"',
    },
    "token.label_hol": {
        "en": "HOLIDAY API KEY",
        "th": "คีย์ API วันหยุด",
    },
    "token.hint_hol": {
        "en": (
            'From the "Financial Institutions Holidays" API product '
            "(a different subscription)"
        ),
        "th": (
            'จากผลิตภัณฑ์ API "วันหยุดสถาบันการเงิน" '
            "(เป็นการสมัครใช้งานคนละรายการ)"
        ),
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
        "en": "OK: Both keys accepted — connection verified.",
        "th": "OK: คีย์ทั้งสองใช้ได้ — ยืนยันการเชื่อมต่อแล้ว",
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
        "en": "Auto-Processing",
        "th": "ประมวลผลอัตโนมัติ",
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
        "en": "Remove",
        "th": "ลบ",
    },
    "sched.status_no_folders": {
        "en": "WARNING: No folders selected — add at least one.",
        "th": "WARNING: ยังไม่ได้เลือกโฟลเดอร์ — กรุณาเพิ่มอย่างน้อยหนึ่งโฟลเดอร์",
    },
    "sched.status_next_run": {
        "en": "Next run: {time} — watching {count} folder{plural}",
        "th": "ทำงานครั้งถัดไป: {time} — เฝ้าดู {count} โฟลเดอร์",
    },
    "sched.status_next_run_some_missing": {
        "en": (
            "Next run: {time} — watching {count} folder{plural} "
            "({missing} unavailable)"
        ),
        "th": (
            "ทำงานครั้งถัดไป: {time} — เฝ้าดู {count} โฟลเดอร์ "
            "({missing} ไม่พบ)"
        ),
    },
    "sched.last_run": {
        "en": "Last run: {summary}",
        "th": "ทำงานล่าสุด: {summary}",
    },
    "sched.unavailable": {
        "en": "(unavailable)",
        "th": "(ไม่พบโฟลเดอร์)",
    },
    "sched.skip_weekends": {
        "en": "  Skip weekends",
        "th": "  ข้ามวันหยุดสุดสัปดาห์",
    },
    "sched.skip_holidays": {
        "en": "  Skip Thai holidays",
        "th": "  ข้ามวันหยุดธนาคารไทย",
    },
    # Scheduler tooltips (hover help on each control).
    "sched.tip_toggle": {
        "en": "Turn automatic daily processing on or off.",
        "th": "เปิด/ปิด การประมวลผลอัตโนมัติประจำวัน",
    },
    "sched.tip_time": {
        "en": "Time of day the scheduled batch runs (local machine time).",
        "th": "เวลาที่ตั้งให้ประมวลผลอัตโนมัติในแต่ละวัน (เวลาเครื่อง)",
    },
    "sched.tip_skip_weekends": {
        "en": "Skip scheduled runs on Saturday and Sunday.",
        "th": "ข้ามการทำงานอัตโนมัติในวันเสาร์และวันอาทิตย์",
    },
    "sched.tip_skip_holidays": {
        "en": "Skip scheduled runs on Thai bank holidays.",
        "th": "ข้ามการทำงานอัตโนมัติในวันหยุดธนาคารไทย",
    },
    "sched.tip_add": {
        "en": "Add a folder to watch for Excel ledgers.",
        "th": "เพิ่มโฟลเดอร์ที่จะเฝ้าดูไฟล์ Excel",
    },
    "sched.tip_remove": {
        "en": "Remove a folder from the watch list.",
        "th": "นำโฟลเดอร์ออกจากรายการที่เฝ้าดู",
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
        "en": "OK: Imported {count} rate entries",
        "th": "OK: นำเข้าอัตรา {count} รายการแล้ว",
    },
    # Detailed import confirmation (shown when a date span is known); the
    # plain csv.import_ok above is the no-detail fallback.
    "csv.import_ok_detail": {
        "en": (
            "OK: Imported {count} entries: {currencies} — {start} to {end}. "
            "These rates are now used automatically by Process Batch."
        ),
        "th": (
            "OK: นำเข้า {count} รายการ: {currencies} — {start} ถึง {end} "
            "ระบบจะใช้อัตราเหล่านี้โดยอัตโนมัติเมื่อประมวลผลทั้งชุด"
        ),
    },
    "csv.export_ok": {
        "en": "OK: Exported {count} rate rows",
        "th": "OK: ส่งออกอัตรา {count} แถวแล้ว",
    },
    "csv.export_empty": {
        "en": "No cached rates to export yet — fetch or import rates first.",
        "th": "ยังไม่มีอัตราที่บันทึกไว้ให้ส่งออก — กรุณาดึงหรือนำเข้าอัตราก่อน",
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
    "exrate.btn_cancel": {
        "en": "Cancel",
        "th": "ยกเลิก",
    },
    "exrate.cancelling": {
        "en": "Cancelling…",
        "th": "กำลังยกเลิก…",
    },
    "exrate.cancelled": {
        "en": "ExRate creation cancelled.",
        "th": "ยกเลิกการสร้างไฟล์ ExRate แล้ว",
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
    # ── Rate Audit (verify an existing workbook's rates against BOT) ──
    # Status-line strings; {msg} is the English progress/error detail from
    # core/rate_audit.py (core strings stay English per the SCOPE note).
    "rateaudit.picker_title": {
        "en": "Verify a workbook's ExRate rates against BOT",
        "th": "ตรวจสอบอัตราในชีต ExRate ของไฟล์งานกับข้อมูล ธปท.",
    },
    "rateaudit.status_starting": {
        "en": "Rate audit: starting…",
        "th": "ตรวจสอบอัตรา: กำลังเริ่ม…",
    },
    "rateaudit.status_progress": {
        "en": "Rate audit: {msg}",
        "th": "ตรวจสอบอัตรา: {msg}",
    },
    "rateaudit.status_applied": {
        "en": "Rate audit: applied {count} correction(s)",
        "th": "ตรวจสอบอัตรา: แก้ไขแล้ว {count} รายการ",
    },
    "rateaudit.status_all_match": {
        "en": "Rate audit: all rates already match BOT",
        "th": "ตรวจสอบอัตรา: อัตราทั้งหมดตรงกับข้อมูล ธปท. แล้ว",
    },
    "rateaudit.status_failed": {
        "en": "Rate audit failed: {msg}",
        "th": "การตรวจสอบอัตราล้มเหลว: {msg}",
    },
    # User-facing twin of core.rate_audit.LAYOUT_ERROR_MSG (which itself
    # stays English in the report/CSV for auditability).
    "rateaudit.err_layout": {
        "en": (
            "Non-standard ExRate layout — audit supports only the "
            "standard USD/EUR sheet"
        ),
        "th": (
            "รูปแบบชีต ExRate ไม่เป็นไปตามมาตรฐาน — "
            "การตรวจสอบรองรับเฉพาะชีต USD/EUR มาตรฐานเท่านั้น"
        ),
    },
    # Report dialog.
    "rateaudit.report_title": {
        "en": "Rate Audit Report",
        "th": "รายงานการตรวจสอบอัตรา",
    },
    "rateaudit.workbook_fallback": {
        "en": "(workbook)",
        "th": "(ไฟล์งาน)",
    },
    "rateaudit.head_corrected": {
        "en": "Corrected {count} rate(s) in {fname}",
        "th": "แก้ไขอัตรา {count} รายการในไฟล์ {fname}",
    },
    "rateaudit.head_differences": {
        "en": "{count} difference(s) found in {fname}",
        "th": "พบความแตกต่าง {count} รายการในไฟล์ {fname}",
    },
    "rateaudit.head_all_match": {
        "en": "All rates already match BOT — {fname}",
        "th": "อัตราทั้งหมดตรงกับข้อมูล ธปท. แล้ว — {fname}",
    },
    "rateaudit.sub_scanned": {
        "en": "Scanned {rows} trading-day row(s); compared {cells} cell(s).",
        "th": "สแกนแถววันทำการ {rows} แถว และเปรียบเทียบ {cells} เซลล์แล้ว",
    },
    "rateaudit.sub_unverifiable": {
        "en": "{count} cell(s) had no BOT data to verify.",
        "th": "เซลล์ {count} เซลล์ไม่มีข้อมูล ธปท. ให้ตรวจสอบ",
    },
    "rateaudit.col_date": {"en": "Date", "th": "วันที่"},
    "rateaudit.col_cell": {"en": "Cell", "th": "เซลล์"},
    "rateaudit.col_currency_type": {
        "en": "Currency / Type",
        "th": "สกุลเงิน / ประเภท",
    },
    "rateaudit.col_old": {"en": "Old", "th": "ค่าเดิม"},
    "rateaudit.col_new": {"en": "New", "th": "ค่าใหม่"},
    "rateaudit.col_why": {"en": "Why", "th": "เหตุผล"},
    "rateaudit.blank_value": {
        "en": "(blank)",
        "th": "(ว่าง)",
    },
    "rateaudit.no_corrections": {
        "en": (
            "No corrections were needed — every trading-day rate "
            "already matched the official BOT value."
        ),
        "th": (
            "ไม่จำเป็นต้องแก้ไขรายการใด — อัตราของทุกวันทำการ"
            "ตรงกับค่าทางการของ ธปท. อยู่แล้ว"
        ),
    },
    "rateaudit.btn_revert": {
        "en": "Revert these changes",
        "th": "ย้อนกลับการแก้ไขเหล่านี้",
    },
    "rateaudit.revert_busy": {
        "en": (
            "Busy — another operation is running. "
            "Try again when it finishes."
        ),
        "th": (
            "ไม่ว่าง — มีการทำงานอื่นกำลังดำเนินอยู่ "
            "กรุณาลองอีกครั้งเมื่อเสร็จสิ้น"
        ),
    },
    "rateaudit.csv_label": {
        "en": "CSV: {name}",
        "th": "CSV: {name}",
    },
    "rateaudit.btn_close": {
        "en": "Close",
        "th": "ปิด",
    },
    # ── Rate ticker (live-data status indicator) ──────────────────────
    "ticker.connecting": {
        "en": "SYNC",
        "th": "กำลังเชื่อมต่อ",
    },
    "ticker.live": {
        "en": "● LIVE",
        "th": "● สด",
    },
    "ticker.offline": {
        "en": "OFFLINE",
        "th": "ออฟไลน์",
    },
    # ── Version / update browser ──────────────────────────────────────
    "version.err_batch_running": {
        "en": (
            "Update will restart the app — wait for the current batch to "
            "finish first."
        ),
        "th": (
            "การอัปเดตจะรีสตาร์ทโปรแกรม — "
            "กรุณารอให้การประมวลผลชุดปัจจุบันเสร็จก่อน"
        ),
    },
    "version.restart_blocked_title": {
        "en": "Restart Postponed",
        "th": "เลื่อนการรีสตาร์ท",
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

    Resolution order: active language -> English -> the key itself. The final
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
