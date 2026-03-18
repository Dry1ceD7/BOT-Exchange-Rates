# -*- mode: python ; coding: utf-8 -*-
# =========================================================================
#  BOT Exchange Rate Processor — V2.4.0 macOS Build Spec
# =========================================================================
#  Build:  pyinstaller BOT_Exchange_Rate_v2.4.0_MAC.spec
#  Output: dist/BOT_Exchange_Rate_v2.4.0_MAC.app
# =========================================================================

import os
import sys

# ── Resolve venv site-packages path ──────────────────────────────────────
VENV_SP = os.path.join(SPECPATH, 'venv', 'lib', 'python3.14', 'site-packages')

a = Analysis(
    ['main.py'],
    pathex=[SPECPATH],
    binaries=[],
    datas=[
        # CustomTkinter (ships its own assets: themes, fonts, images)
        (os.path.join(VENV_SP, 'customtkinter'), 'customtkinter/'),

        # tkinterdnd2 — native Tcl/Tk drag-and-drop extension
        (os.path.join(VENV_SP, 'tkinterdnd2'), 'tkinterdnd2/'),

        # Core modules (ensures they're found by the frozen import system)
        ('core/', 'core/'),
        ('gui/', 'gui/'),
    ],
    hiddenimports=[
        # --- GUI ---
        'customtkinter',
        'tkinterdnd2',
        'tkinter',
        'tkinter.messagebox',
        'tkinter.filedialog',

        # --- Networking ---
        'httpx',
        'httpx._transports',
        'httpx._transports.default',
        'httpcore',
        'anyio',
        'anyio._backends',
        'anyio._backends._asyncio',
        'sniffio',
        'h11',
        'certifi',
        'idna',

        # --- Data Validation ---
        'pydantic',
        'pydantic.main',
        'pydantic_core',
        'annotated_types',

        # --- Excel Processing ---
        'openpyxl',
        'openpyxl.cell',
        'openpyxl.utils',
        'openpyxl.utils.exceptions',
        'openpyxl.reader',
        'openpyxl.reader.excel',
        'et_xmlfile',

        # --- Environment ---
        'dotenv',

        # --- Retry Logic ---
        'tenacity',

        # --- Stdlib (sometimes missed by PyInstaller) ---
        'decimal',
        'sqlite3',
        'threading',
        'asyncio',
        'shutil',
        'glob',
        'atexit',
        'typing',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim unnecessary heavy packages from the bundle
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'PIL',
        'cv2',
        'torch',
        'tensorflow',
        'pytest',
        '_pytest',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='BOT_Exchange_Rate_v2.4.0_MAC',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                      # No terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,                   # Universal (arm64 + x86_64)
    codesign_identity=None,
    entitlements_file=None,
)

app = BUNDLE(
    exe,
    name='BOT_Exchange_Rate_v2.4.0_MAC.app',
    icon=None,
    bundle_identifier='com.bot.exrate.processor',
    info_plist={
        'CFBundleShortVersionString': '2.4.0',
        'CFBundleVersion': '2.4.0',
        'NSHighResolutionCapable': True,
        'CFBundleName': 'BOT Exchange Rate Processor',
    },
)
