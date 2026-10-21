# Install a fresh copy of Marine Fish Detection GUI after installing Pixi.
param(
    [string]$InstallDir = (Join-Path $HOME 'MarineApps')
)
$ErrorActionPreference = 'Stop'
$Step = 'Detecting OS environment'
try {
    Write-Host "$Step..."
    if ($env:OS -ne 'Windows_NT') { throw 'This installer is for Windows.' }
    Write-Host 'Windows detected.'
    $Step = 'Checking Pixi and installation directory'
    Write-Host "$Step..."
    $env:PATH = (Join-Path $HOME '.pixi\bin') + [IO.Path]::PathSeparator + $env:PATH
    if (-not (Get-Command pixi -ErrorAction SilentlyContinue)) {
        throw 'Install Pixi first, then reopen PowerShell. See the README.'
    }
    Write-Host 'Pixi is available.'
    $ProjectDir = Join-Path $InstallDir 'Marine-Fish-Detection-and-Classification-GUI'
    if (Test-Path -LiteralPath $ProjectDir) {
        throw "Destination already exists: $ProjectDir. Choose another -InstallDir or use the existing installation."
    }
    $Step = 'Checking Git and Git LFS'
    Write-Host "$Step..."
    $GitReady = $false
    $LfsReady = $false
    if (Get-Command git -ErrorAction SilentlyContinue) {
        & git --version
        $GitReady = $LASTEXITCODE -eq 0
        if ($GitReady) {
            & git lfs version
            $LfsReady = $LASTEXITCODE -eq 0
        }
    }
    if ($GitReady -and $LfsReady) {
        Write-Host 'Git and Git LFS are already installed.'
    } else {
        $Step = 'Installing Git and Git LFS'
        Write-Host "$Step..."
        & pixi global install git git-lfs
        if ($LASTEXITCODE -ne 0) { throw 'Git and Git LFS installation failed.' }
        & git --version
        if ($LASTEXITCODE -ne 0) { throw 'Git is unavailable.' }
        & git lfs version
        if ($LASTEXITCODE -ne 0) { throw 'Git LFS is unavailable.' }
        Write-Host 'Git and Git LFS installed successfully.'
    }
    $Step = 'Cloning Git repository'
    Write-Host "$Step..."
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    & git clone https://github.com/Brad420169/Marine-Fish-Detection-and-Classification-GUI.git $ProjectDir
    if ($LASTEXITCODE -ne 0) { throw 'Git clone failed.' }
    Write-Host 'Repository cloned.'
    Push-Location -LiteralPath $ProjectDir
    try {
        $Step = 'Downloading model files with Git LFS'
        Write-Host "$Step..."
        & git lfs install --local
        if ($LASTEXITCODE -ne 0) { throw 'Git LFS setup failed.' }
        & git lfs pull
        if ($LASTEXITCODE -ne 0) { throw 'Git LFS download failed.' }
        Write-Host 'Git LFS model download complete.'
        $Step = 'Installing Pixi environment'
        Write-Host "$Step (Linux reference size: approximately 8.3 GB installed; Windows size varies). Please stand by..."
        Write-Host 'Allow at least 15 GB free disk space including download caches. Downloads may take several minutes.'
        & pixi install --locked
        if ($LASTEXITCODE -ne 0) { throw 'Pixi installation failed.' }
        Write-Host 'Pixi environment installed.'
        $Step = 'Setting up desktop and Start menu shortcuts'
        Write-Host "$Step..."
        & powershell -NoProfile -ExecutionPolicy Bypass -File .\setup\create_windows_shortcut.ps1
        if ($LASTEXITCODE -ne 0) { throw 'Shortcut creation failed.' }
        Write-Host 'Shortcuts done.'
    } finally {
        Pop-Location
    }
    Write-Host "Installation complete: $ProjectDir"
    Write-Host 'You can now open Marine Fish Detection GUI using the desktop or Start menu shortcut.'
} catch {
    Write-Error "Installation failed during: $Step. $($_.Exception.Message)" -ErrorAction Continue
    exit 1
}
