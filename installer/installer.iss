; ---------------------------------------------------------------------------
; BOT Exchange Rate Processor — Inno Setup Installer Script
; ---------------------------------------------------------------------------
; Produces a professional Windows Setup Wizard with:
;   - Custom install directory selection
;   - Desktop shortcut (optional checkbox)
;   - Start Menu folder with app + uninstaller
;   - Registered uninstaller in Add/Remove Programs
;   - Icon cache flush for crisp HD icons
;
; Build: iscc installer/installer.iss
; Expects PyInstaller output in dist\BOT-ExRate\
; ---------------------------------------------------------------------------

#define MyAppName "BOT Exchange Rate Processor"
#define MyAppPublisher "AAE"
#define MyAppURL "https://github.com/Dry1ceD7/BOT-Exchange-Rates"
#define MyAppExeName "BOT-ExRate.exe"

; Version is passed via /D command-line flag from CI/CD:
;   iscc /DMyAppVersion=3.0.9 installer/installer.iss
; Fallback if not provided:
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{B0T-EXRATE-2026-AAE}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} V{#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
DefaultDirName={autopf}\BOT-ExRate
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; v3.1.2: Remember install location from previous installs (registry-based)
UsePreviousAppDir=yes
; Show directory picker on first install only; skip on updates
DisableDirPage=auto
OutputDir=..\dist
OutputBaseFilename=BOT-ExRate-Setup
SetupIconFile=..\assets\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; ---------------------------------------------------------------------------
; PrivilegesRequired=lowest — dual install-location consequence (intentional)
; ---------------------------------------------------------------------------
; This installer does NOT force elevation. The effective install root depends
; on whether the user happens to run elevated:
;   * Elevated   -> {autopf} resolves to Program Files (machine-wide install).
;   * Not elevated (default) -> {autopf} resolves to
;     %LOCALAPPDATA%\Programs (per-user install, no admin prompt).
; Application data follows the install root: it lives under {app}\data in both
; cases (i.e. Program Files\BOT-ExRate\data or
; %LOCALAPPDATA%\Programs\BOT-ExRate\data). Per-user settings written by the
; app itself also live under %LOCALAPPDATA%\BOT_Exrate. Keep this in mind for
; the [UninstallDelete] / [Code] cleanup below.
; ---------------------------------------------------------------------------
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
UninstallDisplayIcon={app}\assets\icon.ico
ArchitecturesInstallIn64BitMode=x64compatible
; v3.1.2: Handle file locks when updating on server shares
CloseApplications=force
RestartApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Include entire PyInstaller --onedir output
Source: "..\dist\BOT-ExRate\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Ensure HD icon files are explicitly included (belt-and-suspenders)
Source: "..\assets\icon.ico"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "..\assets\icon.png"; DestDir: "{app}\assets"; Flags: ignoreversion

[Icons]
; Start Menu — explicitly reference the HD icon
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
; Desktop (only if user checked the box) — explicitly reference the HD icon
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"; Tasks: desktopicon

[Run]
; Flush Windows icon cache so the new HD icon is immediately visible
Filename: "{sys}\ie4uinit.exe"; Parameters: "-show"; Flags: runhidden nowait
; Option to launch app after install
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

; Purge OS-stored credentials (keyring) on uninstall. Gated behind user
; confirmation in [Code] (RemoveUserDataConfirmed); --purge-credentials is
; handled by main.py. runhidden so no console window flashes.
[UninstallRun]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--purge-credentials"; Flags: runhidden; RunOnceId: "PurgeCredentials"; Check: RemoveUserDataConfirmed

[UninstallDelete]
; Clean up any runtime-generated files. Logs live under {app}\data, not {app}.
Type: filesandordirs; Name: "{app}\__pycache__"
Type: files; Name: "{app}\data\app.log*"
Type: filesandordirs; Name: "{app}\data\logs"

[Code]
var
  RemoveUserData: Boolean;

{ Single source of truth: did the user agree to wipe their data/backups/logs?
  Used both by [UninstallRun] (Check:) and CurUninstallStepChanged below. }
function RemoveUserDataConfirmed(): Boolean;
begin
  Result := RemoveUserData;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    { Ask ONCE, before anything is removed. These are financial backups —
      never delete silently. The same answer also gates the keyring purge. }
    RemoveUserData := MsgBox(
      'Also remove saved data, backups and logs?' + #13#10 + #13#10 +
      'This permanently deletes your exchange-rate backups, cache and logs ' +
      'from both the application folder and your user profile. ' +
      'Choose No to keep them.',
      mbConfirmation, MB_YESNO) = IDYES;
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    if RemoveUserData then
    begin
      { Per-user app data written by the app itself. }
      DelTree(ExpandConstant('{localappdata}\BOT_Exrate'), True, True, True);
      { Data/backups/logs living alongside the install. }
      DelTree(ExpandConstant('{app}\data'), True, True, True);
    end;
  end;
end;

