# Start the Kavach backend locally (Windows PowerShell). Reads backend/.env if present.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (Test-Path ".env") {
  Get-Content ".env" | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*(#.*)?$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim()) }
  }
}
if (-not $env:KAVACH_MODE) { $env:KAVACH_MODE = "local" }
if (-not $env:KAVACH_TTS) { $env:KAVACH_TTS = "auto" }
if (-not (Test-Path "assets/demo_computer_networks.pdf")) { python scripts/make_demo_pdf.py }
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
