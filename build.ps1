$ErrorActionPreference = "Stop"

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Virtual environment not found. Run .\setup.ps1 first."
}

& $python -m PyInstaller --noconsole --onefile --name StickyNotes --icon src\sticky_notes\assets\StickyNotes.ico --add-data "src\sticky_notes\assets\StickyNotes.ico;assets" --paths src src\sticky_notes\__main__.py

Write-Host "Built dist\StickyNotes.exe"
