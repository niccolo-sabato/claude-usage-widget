# Build script for Claude Usage Widget
# Runs PyInstaller onedir build, copies guide, runs Inno Setup, zips extension.
# Output: releases/ClaudeUsage-Setup.exe + releases/claude-session-key-extension.zip

$ErrorActionPreference = 'Stop'

# Resolve project root (parent of scripts/)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root      = Resolve-Path (Join-Path $ScriptDir '..')
$Src       = Join-Path $Root 'src'
$Assets    = Join-Path $Src  'assets'
$Guide     = Join-Path $Root 'guide'
$Installer = Join-Path $Root 'installer'
$Build     = Join-Path $Root 'build'
$Dist      = Join-Path $Build 'dist'
$Work      = Join-Path $Build 'pyi-work'
$Releases  = Join-Path $Root 'releases'

Write-Host "Project root: $Root"

# 1. Kill any running widget instance so files are writable
Write-Host "[1/5] Stopping running widget..."
Get-Process -Name 'Claude Usage' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

# 2. PyInstaller onedir build
Write-Host "[2/5] Running PyInstaller..."
New-Item -ItemType Directory -Force -Path $Build    | Out-Null
New-Item -ItemType Directory -Force -Path $Releases | Out-Null

$PyiArgs = @(
    '--noconfirm',
    '--clean',
    '--windowed',
    '--name', 'Claude Usage',
    '--icon',       (Join-Path $Assets 'claude.ico'),
    '--add-data',   ((Join-Path $Assets 'claude.ico')  + ';.'),
    '--add-data',   ((Join-Path $Assets 'icon-bar.png') + ';.'),
    '--add-data',   ((Join-Path $Assets 'icon-github-16.png') + ';.'),
    # Pillow pulls numpy as an optional accelerator. We only use Image,
    # ImageDraw and ImageTk, none of which need it. Excluding numpy and
    # its native libs cuts ~10 MB off the installer.
    '--exclude-module', 'numpy',
    '--exclude-module', 'numpy.libs',
    '--distpath',   $Dist,
    '--workpath',   $Work,
    '--specpath',   $Build,
    (Join-Path $Src 'widget.pyw')
)
& python -m PyInstaller @PyiArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

# 3. Copy guide into dist so it ships alongside the exe
Write-Host "[3/5] Copying guide..."
$DistApp   = Join-Path $Dist 'Claude Usage'
$DistGuide = Join-Path $DistApp 'guide'
New-Item -ItemType Directory -Force -Path $DistGuide | Out-Null
Copy-Item -Force (Join-Path $Guide 'session-key-guide.html') $DistGuide

# 4. Run Inno Setup to build installer
Write-Host "[4/5] Running Inno Setup..."
$Iscc = @(
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe',
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) { throw 'ISCC.exe not found. Install Inno Setup 6.' }
& $Iscc (Join-Path $Installer 'claude-usage-setup.iss')
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE" }

# 5. Zip the extension
Write-Host "[5/5] Zipping Chrome extension..."
& (Join-Path $ScriptDir 'package-extension.ps1')

Write-Host ""
Write-Host "Build complete. Artifacts in $Releases :"
Get-ChildItem $Releases | ForEach-Object { Write-Host "  - $($_.Name)  ($([math]::Round($_.Length/1MB,2)) MB)" }
