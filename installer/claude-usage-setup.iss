; Claude Usage Widget - Inno Setup Installer Script
; Run from the installer/ folder. All Source paths are relative to this script.

#define MyAppName "Claude Usage"
#define MyAppVersion "2.8.52"
#define MyAppPublisher "Niccolo Sabato"
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
; Close running instances before install/update.
; The filter decides which of the files we are about to replace get registered
; with RestartManager, and therefore which locks are detected. Listing only the
; main exe left every DLL in _internal unwatched: a foreign process holding one
; of them (measured: Claude Desktop's Chrome native host had VCRUNTIME140.dll
; loaded) was never closed, the copy failed, and the rollback left the app
; unable to start, since [InstallDelete] had already removed the old _internal.
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll,*.pyd

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[InstallDelete]
; Clean previous installation to avoid stale files. This runs BEFORE the new
; files are copied, so a failure here is not "the update did not happen" but
; "the app is gone": the rollback removes what was copied and cannot bring back
; what this deleted. It stays because a PyInstaller folder with leftovers from
; another Python version fails in stranger ways, but it is the reason the
; CloseApplicationsFilter above has to cover every file we replace.
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
; Relaunch the widget FIRST so its cold start overlaps with everything else.
; "postinstall" was adding a check box on the Finished wizard page, which
; simply doesn't exist under /VERYSILENT (our auto-update flow), so the widget
; never came back up. Dropping the flag runs the exe in every mode.
; runasoriginaluser: the setup runs elevated, but the widget must relaunch as
; the logged-in (non-elevated) user. Without it the process inherits the admin
; token; on machines where the standard user is not the elevating admin, the
; widget would read/write config under the admin profile's LocalAppData and the
; user's own settings would appear to vanish on the next normal start.
Filename: "{app}\{#MyAppExeName}"; Flags: nowait runasoriginaluser

[UninstallDelete]
; Clean up AppData config/log on uninstall
Type: filesandordirs; Name: "{localappdata}\Claude Usage"
