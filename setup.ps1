$ErrorActionPreference = "Stop"

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
$pythonArgs = @()

if ($pythonCommand) {
    $python = $pythonCommand.Source
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $python = "py"
    $pythonArgs = @("-3")
} else {
    Write-Error "Python was not found. Install Python 3.10+ or add it to PATH, then rerun setup."
}

if (-not (Test-Path ".venv")) {
    & $python @pythonArgs -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
