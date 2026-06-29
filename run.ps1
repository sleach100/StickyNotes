$ErrorActionPreference = "Stop"

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Virtual environment not found. Run .\setup.ps1 first."
}

$env:PYTHONPATH = Join-Path $PSScriptRoot "src"
& $python -m sticky_notes
