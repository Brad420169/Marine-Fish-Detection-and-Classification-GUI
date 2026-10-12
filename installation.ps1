# Install a fresh copy of Marine Fish Detection GUI after installing Pixi.
param(
    [string]$InstallDir = (Join-Path $HOME 'MarineApps')
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This installer is for Windows.' }
$env:PATH = (Join-Path $HOME '.pixi\bin') + [IO.Path]::PathSeparator + $env:PATH
if (-not (Get-Command pixi -ErrorAction SilentlyContinue)) {
    throw 'Install Pixi first, then reopen PowerShell. See the README.'
}
$ProjectDir = Join-Path $InstallDir 'Marine-Fish-Detection-and-Classification-GUI'
if (Test-Path -LiteralPath $ProjectDir) {
    throw "Destination already exists: $ProjectDir. Choose another -InstallDir or use the existing installation."
}
& pixi global install git git-lfs
if ($LASTEXITCODE -ne 0) { throw 'Git and Git LFS installation failed.' }
& git --version
if ($LASTEXITCODE -ne 0) { throw 'Git is unavailable.' }
& git lfs version
if ($LASTEXITCODE -ne 0) { throw 'Git LFS is unavailable.' }
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
& git clone https://github.com/Brad420169/Marine-Fish-Detection-and-Classification-GUI.git $ProjectDir
if ($LASTEXITCODE -ne 0) { throw 'Git clone failed.' }
Push-Location -LiteralPath $ProjectDir
try {
    & git lfs install --local
    if ($LASTEXITCODE -ne 0) { throw 'Git LFS setup failed.' }
    & git lfs pull
    if ($LASTEXITCODE -ne 0) { throw 'Git LFS download failed.' }
    & pixi install --locked
    if ($LASTEXITCODE -ne 0) { throw 'Pixi installation failed.' }
    & powershell -NoProfile -ExecutionPolicy Bypass -File .\setup\create_windows_shortcut.ps1
    if ($LASTEXITCODE -ne 0) { throw 'Shortcut creation failed.' }
} finally {
    Pop-Location
}
Write-Host "Installation complete: $ProjectDir"
