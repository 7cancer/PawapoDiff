$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m venv .venv
if ($LASTEXITCODE) { throw 'venv failed' }
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if ($LASTEXITCODE) { throw 'pip install failed' }
Push-Location frontend
try {
    npm ci
    if ($LASTEXITCODE) { throw 'npm ci failed' }
    npm run build
    if ($LASTEXITCODE) { throw 'frontend build failed' }
} finally { Pop-Location }
& .\.venv\Scripts\python.exe -m pytest -q
if ($LASTEXITCODE) { throw 'tests failed' }
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --name PawapoDiff --onedir --paths . --collect-submodules win32com --collect-submodules uvicorn --hidden-import pythoncom --hidden-import pywintypes --add-data 'frontend/dist;frontend/dist' scripts/launcher.py
if ($LASTEXITCODE) { throw 'packaging failed' }
Write-Host 'dist\PawapoDiff\PawapoDiff.exe を起動してください。配布時はPawapoDiffフォルダー全体をコピーします。'
& .\.venv\Scripts\python.exe scripts/create_payload.py dist/PawapoDiff build/setup-payload
if ($LASTEXITCODE) { throw 'payload creation failed' }
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --name PawapoDiff-Setup --onefile --add-data 'build/setup-payload/payload.zip;.' --add-data 'build/setup-payload/payload.json;.' scripts/setup_launcher.py
if ($LASTEXITCODE) { throw 'installer packaging failed' }
Write-Host 'dist\PawapoDiff-Setup.exe を配布してください。クリックすると環境を展開して起動します。'
