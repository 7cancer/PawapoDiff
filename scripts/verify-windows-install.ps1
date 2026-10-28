$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
function Invoke-VerifiedCommand {
    param([string]$Name, [string]$Executable, [string[]]$ArgumentList)
    $output = & $Executable @ArgumentList 2>&1
    $code = $LASTEXITCODE
    $output | ForEach-Object { Write-Host $_ }
    if ($code) {
        $message = ($output | Out-String)
        if ($message.Length -gt 8000) { $message = $message.Substring($message.Length - 8000) }
        $message = $message.Replace('%', '%25').Replace("`r", '%0D').Replace("`n", '%0A')
        Write-Host "::error title=${Name}::exit=$code $message"
        throw "$Name failed"
    }
}
Invoke-VerifiedCommand -Name 'First installation' -Executable './dist/PawapoDiff-Setup.exe' -ArgumentList @('--install-only')
Invoke-VerifiedCommand -Name 'Repeat installation' -Executable './dist/PawapoDiff-Setup.exe' -ArgumentList @('--install-only')
$digest = (Get-Content build/setup-payload/payload.json | ConvertFrom-Json).sha256
$app = Join-Path $env:LOCALAPPDATA "PawapoDiff/versions/$digest/PawapoDiff.exe"
Invoke-VerifiedCommand -Name 'Packaged runtime' -Executable $app -ArgumentList @('--check-runtime')
Invoke-VerifiedCommand -Name 'Standard-user verification' -Executable '.\.venv\Scripts\python.exe' -ArgumentList @('scripts/windows_standard_user.py')
$hash = (Get-FileHash dist/PawapoDiff-Setup.exe -Algorithm SHA256).Hash.ToLower()
"$hash  PawapoDiff-Setup.exe" | Set-Content dist/PawapoDiff-Setup.exe.sha256 -Encoding ascii
$portableHash = (Get-FileHash dist/PawapoDiff-Portable.zip -Algorithm SHA256).Hash.ToLower()
"$portableHash  PawapoDiff-Portable.zip" | Set-Content dist/PawapoDiff-Portable.zip.sha256 -Encoding ascii
