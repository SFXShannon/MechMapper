# Builds dist\MechMapper-Setup-<version>.exe from dist\MW5_MECHMAPPER.exe with Inno Setup 6.
# Called by build.bat; skips quietly if Inno Setup isn't installed.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$match = Select-String -Path MW5_MECHMAPPER.py -Pattern '^APP_VERSION = "([^"]+)"'
if (-not $match) { throw "APP_VERSION not found in MW5_MECHMAPPER.py" }
$version = $match.Matches[0].Groups[1].Value

$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
    Write-Host "Inno Setup 6 not found - skipping the installer (the portable exe was still built)."
    Write-Host "Get it from https://jrsoftware.org/isdl.php to build MechMapper-Setup."
    exit 0
}

& $iscc /Qp "/DAppVersion=$version" installer.iss
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Built: dist\MechMapper-Setup-$version.exe"
