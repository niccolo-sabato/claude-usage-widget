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

# Not Compress-Archive: on Windows PowerShell 5.1 it names the entries with
# backslashes (_locales\en\messages.json), and a reader that follows the ZIP
# format, where the separator is '/', takes that as one file name and finds no
# _locales folder at all.
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$Base = (Resolve-Path $ExtDir).Path.TrimEnd('\') + '\'
$Zip = [System.IO.Compression.ZipFile]::Open($ZipPath, 'Create')
try {
    Get-ChildItem -Path $ExtDir -Recurse -File | Sort-Object FullName | ForEach-Object {
        $Entry = $_.FullName.Substring($Base.Length).Replace('\', '/')
        [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $Zip, $_.FullName, $Entry, [System.IO.Compression.CompressionLevel]::Optimal)
    }
} finally {
    $Zip.Dispose()
}
Write-Host "Created $ZipPath"
