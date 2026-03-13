#!/usr/bin/env python3
"""
================================================================================
  BOT Accountant Excel Filler — Internal Web Portal
  ──────────────────────────────────────────────────
  A FastAPI web application that lets accountants upload their .xlsx files
  and download the filled version — no Python or terminal required.

  USAGE:
    cd web && pip install -r requirements.txt
    uvicorn app:app --reload --host 0.0.0.0 --port 8000
    → http://localhost:8000

  ENDPOINTS:
    GET  /            — Serve the upload page
    POST /api/fill    — Accept .xlsx, process it, return filled file
    GET  /api/health  — Health check
================================================================================
"""

import os
import sys
import uuid
import shutil
import tempfile
import asyncio
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ─── Ensure parent directory is importable ────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

# Import the filler engine
from bot_acc_filler import run_filler  # noqa: E402

# ─── FastAPI App ──────────────────────────────────────────────
app = FastAPI(
    title="BOT Exchange Rate Filler",
    description="Internal web portal for filling exchange rates in accountant spreadsheets.",
    version="1.0.0",
)

# Mount static files and templates
app.mount("/static", StaticFiles(directory=os.path.join(SCRIPT_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(SCRIPT_DIR, "templates"))

# Temp directory for uploads/processing
UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "bot_filler_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Max file size: 10MB (typical accountant Excel files are <2MB)
MAX_FILE_SIZE = 10 * 1024 * 1024


# ═══════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serve the main upload page."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/health")
async def health_check():
    """Health check endpoint for monitoring."""
    return {"status": "ok", "timestamp": datetime.now().isoformat(), "version": "1.0.0"}


@app.post("/api/fill")
async def fill_excel(file: UploadFile = File(...)):
    """Accept an .xlsx file, process it, and return the filled file."""

    # Validate file type
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        if file.filename and file.filename.lower().endswith(".xls"):
            raise HTTPException(status_code=400, detail="Legacy .xls format is not supported. Please save as .xlsx first.")
        raise HTTPException(status_code=400, detail="Only .xlsx files are accepted.")

    # Sanitize filename to prevent path traversal (strip ../ sequences)
    safe_filename: str = os.path.basename(file.filename)
    if not safe_filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")

    # Validate file size
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 10MB.")

    # Create unique work directory
    job_id = str(uuid.uuid4())[:8]
    job_dir = os.path.join(UPLOAD_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    input_path = os.path.join(job_dir, safe_filename)
    base_name = os.path.splitext(safe_filename)[0]
    output_filename = f"{base_name}_updated.xlsx"
    output_path = os.path.join(job_dir, output_filename)

    try:
        # Save uploaded file
        with open(input_path, "wb") as f:
            f.write(content)

        # Collect log messages
        logs = []
        def log_fn(msg):
            logs.append(msg)

        # Run the filler pipeline
        stats = await run_filler(input_path, output_path, log_fn=log_fn)

        if not os.path.exists(output_path):
            raise HTTPException(status_code=500, detail="Processing failed. Output file was not created.")

        # Also save a copy to the project's data/output folder for easy access
        ws_output_dir = os.path.join(PARENT_DIR, "data", "output")
        os.makedirs(ws_output_dir, exist_ok=True)
        ws_output_path = os.path.join(ws_output_dir, output_filename)
        shutil.copy2(output_path, ws_output_path)

        # Return the processed file with the ORIGINAL filename
        return FileResponse(
            path=output_path,
            filename=file.filename,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "X-Job-ID": job_id,
                "X-Original-Filename": file.filename,
                "X-Sheets-Processed": str(stats.get("sheets", 0)),
                "X-Rows-Filled": str(stats.get("filled", 0)),
                "X-Rows-Skipped": str(stats.get("skipped", 0)),
                "X-Rows-Errors": str(stats.get("errors", 0)),
                "Access-Control-Expose-Headers": "X-Job-ID, X-Original-Filename, X-Sheets-Processed, X-Rows-Filled, X-Rows-Skipped, X-Rows-Errors"
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")
    finally:
        # Cleanup is deferred — FastAPI serves the file before cleanup
        # We'll let the OS handle temp cleanup, or add a periodic cleaner
        pass


@app.get("/api/original/{job_id}")
async def download_original(job_id: str, filename: str):
    """Download the original uploaded file to revert changes."""
    job_dir = os.path.join(UPLOAD_DIR, job_id)
    file_path = os.path.join(job_dir, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Original file not found or expired.")
    return FileResponse(path=file_path, filename=filename)


# ─── Startup/Shutdown events ─────────────────────────────────

@app.on_event("startup")
async def startup_event():
    """Log startup."""
    print("=" * 60)
    print("  BOT Exchange Rate Filler — Web Portal v1.0.0")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("  Ready at http://localhost:8000")
    print("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up temp files on shutdown."""
    if os.path.exists(UPLOAD_DIR):
        shutil.rmtree(UPLOAD_DIR, ignore_errors=True)
