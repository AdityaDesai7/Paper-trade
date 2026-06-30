# Run project scripts with venv packages when .venv\Scripts\python.exe is blocked
# by Windows Application Control (common with OneDrive-synced project folders).
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

if (-not $Args -or $Args.Count -eq 0) {
    Write-Error "Usage: .\run.ps1 backtest_backtrader.py"
    exit 1
}

$ProjectRoot = $PSScriptRoot
$Venv = Join-Path $ProjectRoot ".venv"
$Python = Join-Path $env:APPDATA "uv\python\cpython-3.12.13-windows-x86_64-none\python.exe"

if (-not (Test-Path $Python)) {
    Write-Error "Python 3.12 not found at $Python. Run: uv python install 3.12"
    exit 1
}

$env:VIRTUAL_ENV = $Venv
$env:PYTHONPATH = Join-Path $Venv "Lib\site-packages"

$script = $Args[0]
$scriptArgs = @()
if ($Args.Count -gt 1) {
    $scriptArgs = $Args[1..($Args.Count - 1)]
}

& $Python (Join-Path $ProjectRoot $script) @scriptArgs
