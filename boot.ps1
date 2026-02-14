# boot.ps1 -- ForgeGuard one-click setup and run script
# Phase 0 stub. Full implementation in Phase 5.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Info([string]$m) { Write-Host "[boot] $m" -ForegroundColor Cyan }
function Err ([string]$m) { Write-Host "[boot] $m" -ForegroundColor Red }

# -- 1. Check prerequisites -----------------------------------------------
Info "Checking prerequisites..."
$pythonCmd = $null
foreach ($candidate in @("python", "python3")) {
    try {
        $ver = & $candidate --version 2>&1
        if ($ver -match "Python\s+3\.(\d+)") {
            $minor = [int]$Matches[1]
            if ($minor -ge 12) {
                $pythonCmd = $candidate
                Info "Found $ver"
                break
            }
        }
    } catch { }
}
if (-not $pythonCmd) {
    Err "Python 3.12+ is required but was not found. Please install it and try again."
    exit 1
}

# -- 2. Create virtual environment -----------------------------------------
if (-not (Test-Path ".venv")) {
    Info "Creating virtual environment..."
    & $pythonCmd -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Err "Failed to create virtual environment."
        exit 1
    }
} else {
    Info "Virtual environment already exists."
}

# -- 3. Activate environment -----------------------------------------------
Info "Activating virtual environment..."
$activateScript = Join-Path ".venv" "Scripts" "Activate.ps1"
if (-not (Test-Path $activateScript)) {
    $activateScript = Join-Path ".venv" "bin" "Activate.ps1"
}
if (Test-Path $activateScript) {
    . $activateScript
} else {
    Err "Could not find activation script at $activateScript"
    exit 1
}

# -- 4. Install dependencies -----------------------------------------------
Info "Installing Python dependencies..."
& pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) {
    Err "Failed to install Python dependencies."
    exit 1
}

# -- 5. Prompt for credentials (stub -- will be expanded in Phase 5) --------
if (-not (Test-Path ".env")) {
    Info "No .env file found. Copying from .env.example..."
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Info "Created .env from .env.example. Please edit it with your real credentials."
    } else {
        Err ".env.example not found. Please create a .env file manually."
        exit 1
    }
}

# -- 6. Start the app -------------------------------------------------------
Info "Starting ForgeGuard..."
& python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
