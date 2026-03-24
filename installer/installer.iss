; ---------------------------------------------------------------------------
; BOT Exchange Rate Processor — Inno Setup Installer Script
; ---------------------------------------------------------------------------
; Produces a professional Windows Setup Wizard with:
;   - Custom install directory selection
;   - Desktop shortcut (optional checkbox)
;   - Start Menu folder with app + uninstaller
;   - Registered uninstaller in Add/Remove Programs
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
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checked

[Files]
; Include entire PyInstaller --onedir output
Source: "..\dist\BOT-ExRate\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
; Desktop (only if user checked the box)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Option to launch app after install
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up any runtime-generated files
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\*.log"
