# Release Smoke Checklist

Manual checks the automated suite structurally cannot certify (live
credentials, real-display rendering performance, OS-specific behavior).
Run on a real display — macOS AND Windows — before tagging a release.
xvfb-based CI cannot stand in for any item here.

Record the release tag, date, OS/version, and a pass/fail per item in the
release PR description.

## 1. API connectivity (live keys, both products)

- [ ] Settings → Test API Connection with the real production keys:
      result must read `OK: API connected & authenticated (both keys)`.
      The BOT gateway scopes each key to one product — a green result must
      prove the exchange-rate key AND the holiday key (a batch needs both).
- [ ] Temporarily clear the HOL key (Manage API Keys) and re-test: the
      result must NAME the holiday key as the failure, not show green.
      Restore the key and confirm green again.

## 2. Legacy .xls handling (real file)

- [ ] Drop a real legacy `.xls` file (e.g. a Crystal Reports export)
      directly onto the drop zone: a Format Warning must name the file and
      say to save it as `.xlsx`.
- [ ] Drop a FOLDER containing only that `.xls`: the same warning must
      appear — never a silent no-op or a bare "No Valid Files".
- [ ] Rename the `.xls` to `.xlsx` and run a headless batch over it: the
      file must be SKIPPED with the save-as-.xlsx message; the process must
      survive and report the other files.

## 3. Appearance toggle (perf/UX class — untestable in CI)

- [ ] Open Settings over a populated main window; toggle
      Dark → Light → System. The UI must not block perceptibly (>200 ms)
      and no surface may keep the old palette — including the settings
      modal itself (it holds the input grab; it must re-theme with the
      main window).
- [ ] Repeat with a queued batch list present.

## 4. Windows-only rendering

- [ ] DPI scaling at 100% / 125% / 150%: no clipped controls, the Save
      button stays reachable on a short screen (body scrolls).
- [ ] Tray: close-to-tray, restore from tray, second-instance launch
      restores the running window (single-instance IPC).

## 5. Installed-build CLI

- [ ] From the installed build: `BOT-ExRate --headless --input <folder>`
      on a folder with one valid ledger — exit code 0, file processed,
      backup created.
- [ ] Same command on an empty folder — exit code 4 and a visible
      "No Excel files found" line.
