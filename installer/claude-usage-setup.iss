; Claude Usage Widget - Inno Setup Installer Script
; Run from the installer/ folder. All Source paths are relative to this script.

#define MyAppName "Claude Usage"
#define MyAppVersion "2.5.8"
#define MyAppPublisher "Omakase"
#define MyAppExeName "Claude Usage.exe"
#define MyAppIcon "claude.ico"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-CLAUDE000001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\releases
OutputBaseFilename=ClaudeUsage-Setup
SetupIconFile=..\src\assets\claude.ico
UninstallDisplayIcon={app}\claude.ico
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
; Close running instances before install/update
CloseApplications=force
CloseApplicationsFilter=Claude Usage.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[InstallDelete]
; Clean previous installation to avoid stale files
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; Main exe and internal dependencies (produced by PyInstaller into build/dist/)
Source: "..\build\dist\Claude Usage\Claude Usage.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\dist\Claude Usage\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
; Resources
Source: "..\src\assets\claude.ico"; DestDir: "{app}"; Flags: ignoreversion
; Guide
Source: "..\guide\session-key-guide.html"; DestDir: "{app}\guide"; Flags: ignoreversion
Source: "..\src\assets\icon-bar.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\src\assets\icon-app.png"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Desktop shortcut
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppIcon}"; WorkingDir: "{app}"
; Start Menu shortcut
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppIcon}"; WorkingDir: "{app}"
; Start Menu uninstall shortcut
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
; Flush Windows icon cache after install
Filename: "ie4uinit.exe"; Parameters: "-show"; Flags: runhidden nowait
; Option to launch after install
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Claude Usage"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up AppData config/log on uninstall
Type: filesandordirs; Name: "{localappdata}\Claude Usage"
