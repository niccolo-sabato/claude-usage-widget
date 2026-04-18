param(
    [Parameter(Mandatory=$true)]
    [string]$Name
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$img = [System.Windows.Forms.Clipboard]::GetImage()
if ($null -eq $img) {
    Write-Host "No image in clipboard!" -ForegroundColor Red
    exit 1
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$outDir = (Resolve-Path (Join-Path $scriptDir '..\docs\images')).Path
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }

$outPath = Join-Path $outDir "$Name.png"
$img.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
Write-Host "Saved: $outPath ($($img.Width)x$($img.Height))" -ForegroundColor Green
