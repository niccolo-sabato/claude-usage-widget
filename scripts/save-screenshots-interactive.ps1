Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$outDir = Join-Path $scriptDir '..\docs\images'
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }
$outDir = (Resolve-Path $outDir).Path

$names = @(
    "widget-standard",
    "widget-standard-expanded",
    "widget-essential",
    "widget-essential-expanded",
    "menu-dropdown"
)

Write-Host ""
Write-Host "=== Claude Usage screenshot saver ===" -ForegroundColor Cyan
Write-Host "Premi Win+V per aprire la cronologia appunti." -ForegroundColor Yellow
Write-Host "Per ogni screenshot che ti chiedo, clicca l'immagine corrispondente in Win+V,"
Write-Host "poi premi INVIO qui."
Write-Host ""

foreach ($name in $names) {
    Write-Host "--------------------------------------------" -ForegroundColor DarkCyan
    Write-Host "Prossimo: $name" -ForegroundColor Green
    Read-Host "Copia l'immagine (clicca su Win+V) e premi INVIO"

    $img = [System.Windows.Forms.Clipboard]::GetImage()
    if ($null -eq $img) {
        Write-Host "  Nessuna immagine negli appunti. Skip." -ForegroundColor Red
        continue
    }

    $outPath = Join-Path $outDir "$name.png"
    $img.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
    Write-Host "  Salvato: $outPath ($($img.Width)x$($img.Height))" -ForegroundColor Green
}

Write-Host ""
Write-Host "Fatto! File in: $outDir" -ForegroundColor Cyan
Get-ChildItem $outDir | Format-Table Name, Length -AutoSize
