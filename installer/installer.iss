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
OutputDir=..\dist
OutputBaseFilename=BOT-ExRate-Setup
SetupIconFile=..\assets\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\assets\icon.ico
ArchitecturesInstallIn64BitMode=x64compatible

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

[UninstallDelete]
; Clean up any runtime-generated files
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\*.log"

