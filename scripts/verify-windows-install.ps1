$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
& ./dist/PawapoDiff-Setup.exe --install-only
if ($LASTEXITCODE) { throw 'First installation failed' }
& ./dist/PawapoDiff-Setup.exe --install-only
if ($LASTEXITCODE) { throw 'Repeat installation failed' }
$digest = (Get-Content build/setup-payload/payload.json | ConvertFrom-Json).sha256
$app = Join-Path $env:LOCALAPPDATA "PawapoDiff/versions/$digest/PawapoDiff.exe"
& $app --check-runtime
if ($LASTEXITCODE) { throw 'Packaged runtime verification failed' }
& .\.venv\Scripts\python.exe scripts/windows_standard_user.py
if ($LASTEXITCODE) { throw 'Standard-user installer/runtime verification failed' }
$hash = (Get-FileHash dist/PawapoDiff-Setup.exe -Algorithm SHA256).Hash.ToLower()
"$hash  PawapoDiff-Setup.exe" | Set-Content dist/PawapoDiff-Setup.exe.sha256 -Encoding ascii
$portableHash = (Get-FileHash dist/PawapoDiff-Portable.zip -Algorithm SHA256).Hash.ToLower()
"$portableHash  PawapoDiff-Portable.zip" | Set-Content dist/PawapoDiff-Portable.zip.sha256 -Encoding ascii
