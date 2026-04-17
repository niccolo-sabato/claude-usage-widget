# Zip the Chrome extension into releases/claude-session-key-extension.zip

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root      = Resolve-Path (Join-Path $ScriptDir '..')
$ExtDir    = Join-Path $Root 'extension'
$Releases  = Join-Path $Root 'releases'
$ZipPath   = Join-Path $Releases 'claude-session-key-extension.zip'

if (-not (Test-Path $ExtDir)) { throw "Extension directory not found: $ExtDir" }
New-Item -ItemType Directory -Force -Path $Releases | Out-Null
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }

Compress-Archive -Path (Join-Path $ExtDir '*') -DestinationPath $ZipPath -CompressionLevel Optimal
Write-Host "Created $ZipPath"
