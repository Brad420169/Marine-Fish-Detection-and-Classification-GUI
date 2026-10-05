# Run after pixi install. Uses Windows' actual Desktop location, including OneDrive.
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This script is for Windows.' }
$ProjectDir = Split-Path -Parent $PSScriptRoot
$PixiCommand = Get-Command pixi -ErrorAction SilentlyContinue
$PixiPath = if ($PixiCommand) { $PixiCommand.Source } else { Join-Path $HOME '.pixi\bin\pixi.exe' }
if (-not (Test-Path -LiteralPath $PixiPath)) { throw 'Install Pixi first, then reopen PowerShell.' }
if (-not (Test-Path -LiteralPath (Join-Path $ProjectDir '.pixi\envs\default\python.exe'))) {
    throw 'Run pixi install in the repository first.'
}
$DesktopDir = [Environment]::GetFolderPath('Desktop')
$ProgramsDir = [Environment]::GetFolderPath('Programs')
$Shell = New-Object -ComObject WScript.Shell
foreach ($Directory in @($DesktopDir, $ProgramsDir)) {
    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
    $ShortcutPath = Join-Path $Directory 'Marine Fish Detection GUI.lnk'
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $PixiPath
    $Shortcut.Arguments = 'run --manifest-path "' + (Join-Path $ProjectDir 'pixi.toml') + '" GUI'
    $Shortcut.WorkingDirectory = $ProjectDir
    $Shortcut.IconLocation = (Join-Path $ProjectDir 'assets\icon.ico') + ',0'
    $Shortcut.Description = 'Marine fish detection and classification'
    $Shortcut.Save()
    Write-Host "Created $ShortcutPath"
}
