/**
 * BOT Exchange Rate Filler — Web Portal JavaScript
 * Handles drag-and-drop, file upload, AJAX processing, and auto-download.
 */

document.addEventListener('DOMContentLoaded', () => {
  const dropzone = document.getElementById('dropzone');
  const fileInput = document.getElementById('fileInput');
  const fileInfo = document.getElementById('fileInfo');
  const fileName = document.getElementById('fileName');
  const fileSize = document.getElementById('fileSize');
  const fillBtn = document.getElementById('fillBtn');
  const resetBtn = document.getElementById('resetBtn');
  const revertBtn = document.getElementById('revertBtn');
  const progressSection = document.getElementById('progressSection');
  const statusText = document.getElementById('statusText');
  const resultsSection = document.getElementById('resultsSection');
  const errorBox = document.getElementById('errorBox');

  let selectedFile = null;

  // ─── Drag & Drop ──────────────────────────────────────────

  dropzone.addEventListener('click', () => {
    if (!selectedFile) fileInput.click();
  });

  dropzone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropzone.classList.add('dragover');
  });

  dropzone.addEventListener('dragleave', () => {
    dropzone.classList.remove('dragover');
  });

  dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('dragover');
    const files = e.dataTransfer.files;
    if (files.length > 0) handleFile(files[0]);
  });

  fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) handleFile(e.target.files[0]);
  });

  // ─── File Selection ───────────────────────────────────────

  function handleFile(file) {
    if (!file.name.toLowerCase().endsWith('.xlsx')) {
      showError('Please select an Excel file (.xlsx)');
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      showError('File is too large. Maximum size is 50MB.');
      return;
    }

    selectedFile = file;
    dropzone.classList.add('has-file');
    fileName.textContent = file.name;
    fileSize.textContent = formatSize(file.size);
    fillBtn.disabled = false;
    hideError();
    hideResults();
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }

  // ─── Upload & Process ─────────────────────────────────────

  fillBtn.addEventListener('click', async () => {
    if (!selectedFile) return;

    fillBtn.classList.add('loading');
    fillBtn.disabled = true;
    showProgress('Uploading file...');
    hideError();
    hideResults();

    const formData = new FormData();
    formData.append('file', selectedFile);

    try {
      updateStatus('Processing — fetching exchange rates from BOT API...');

      const response = await fetch('/api/fill', {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || `Server error (${response.status})`);
      }

      updateStatus('Download ready!');

      const jobId = response.headers.get('X-Job-ID');
      const originalFilename = response.headers.get('X-Original-Filename');

      // Extract stats from headers
      const stats = {
        sheets: response.headers.get('X-Sheets-Processed') || '0',
        filled: response.headers.get('X-Rows-Filled') || '0',
        skipped: response.headers.get('X-Rows-Skipped') || '0',
        errors: response.headers.get('X-Rows-Errors') || '0',
      };

      // Auto-download the file
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const downloadName = originalFilename || selectedFile.name;

      const a = document.createElement('a');
      a.href = url;
      a.download = downloadName;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      // Show results
      showResults(stats, downloadName);

      // Setup and show Revert button
      if (jobId && originalFilename) {
        revertBtn.href = `/api/original/${jobId}?filename=${encodeURIComponent(originalFilename)}`;
        revertBtn.style.display = 'inline-block';
      }

    } catch (err) {
      showError(err.message || 'An unexpected error occurred.');
    } finally {
      fillBtn.classList.remove('loading');
      fillBtn.disabled = false;
      hideProgress();
    }
  });

  // ─── Reset ────────────────────────────────────────────────

  resetBtn.addEventListener('click', () => {
    selectedFile = null;
    fileInput.value = '';
    dropzone.classList.remove('has-file');
    fillBtn.disabled = true;
    revertBtn.style.display = 'none';
    hideProgress();
    hideResults();
    hideError();
  });

  // ─── UI Helpers ───────────────────────────────────────────

  function showProgress(msg) {
    progressSection.classList.add('active');
    statusText.textContent = msg;
  }

  function updateStatus(msg) {
    statusText.textContent = msg;
  }

  function hideProgress() {
    progressSection.classList.remove('active');
  }

  function showResults(stats, filename) {
    document.getElementById('statSheets').textContent = stats.sheets;
    document.getElementById('statFilled').textContent = stats.filled;
    document.getElementById('statSkipped').textContent = stats.skipped;
    document.getElementById('statErrors').textContent = stats.errors;
    document.getElementById('resultFilename').textContent = filename;
    resultsSection.classList.add('active');
  }

  function hideResults() {
    resultsSection.classList.remove('active');
  }

  function showError(msg) {
    errorBox.textContent = '✗  ' + msg;
    errorBox.classList.add('active');
  }

  function hideError() {
    errorBox.classList.remove('active');
  }
});
