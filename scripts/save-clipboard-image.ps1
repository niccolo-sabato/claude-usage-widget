param(
    [Parameter(Mandatory=$true)]
    [string]$Name
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$img = [System.Windows.Forms.Clipboard]::GetImage()
if ($img -eq $null) {
    Write-Host "No image in clipboard!" -ForegroundColor Red
    exit 1
}

$outDir = "C:\Users\Kanjiro\Scripts\claude-usage-widget\docs\images"
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }

$outPath = Join-Path $outDir "$Name.png"
$img.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
Write-Host "Saved: $outPath ($($img.Width)x$($img.Height))" -ForegroundColor Green
