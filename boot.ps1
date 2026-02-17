# boot.ps1 -- ForgeGuard one-click setup and run script.
#
# Brings up the full stack from a fresh clone:
#   1. Validates prerequisites (Python 3.12+, Node 18+, psql)
#   2. Creates Python venv and installs backend deps
#   3. Installs frontend deps
#   4. Validates .env (fails fast if missing required vars)
#   5. Runs database migrations
#   6. Starts backend + frontend dev servers
#
# Usage:
#   pwsh -File boot.ps1
#   pwsh -File boot.ps1 -SkipFrontend
#   pwsh -File boot.ps1 -MigrateOnly

[CmdletBinding()]
param(
  [switch]$SkipFrontend,
  [switch]$MigrateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Info([string]$m) { Write-Host "[boot] $m" -ForegroundColor Cyan }
function Warn([string]$m) { Write-Host "[boot] $m" -ForegroundColor Yellow }
function Err ([string]$m) { Write-Host "[boot] $m" -ForegroundColor Red }
function Ok  ([string]$m) { Write-Host "[boot] $m" -ForegroundColor Green }

$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
if (-not $root) { $root = Get-Location }
Set-Location $root

# ── 1. Check prerequisites ──────────────────────────────────────────────

Info "Checking prerequisites..."

$pythonCmd = $null
foreach ($candidate in @("python3", "python")) {
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
  Err "Python 3.12+ is required but was not found."
  exit 1
}

if (-not $SkipFrontend) {
  $nodeCmd = Get-Command "node" -ErrorAction SilentlyContinue
  if (-not $nodeCmd) {
    Err "Node.js 18+ is required for frontend. Use -SkipFrontend to skip."
    exit 1
  }
  Info "Node: $(node --version)"
}

$psqlCmd = Get-Command "psql" -ErrorAction SilentlyContinue
if ($psqlCmd) { Info "psql: found on PATH" }
else { Warn "psql not on PATH -- you may need to run migrations manually." }

# ── 2. Python virtual environment ────────────────────────────────────────

$venvDir = Join-Path $root ".venv"
if (-not (Test-Path $venvDir)) {
  Info "Creating virtual environment..."
  & $pythonCmd -m venv $venvDir
  if ($LASTEXITCODE -ne 0) { Err "Failed to create virtual environment."; exit 1 }
  Ok "Virtual environment created."
} else {
  Info "Virtual environment already exists."
}

$venvPython = Join-Path $venvDir "Scripts/python.exe"
$venvPythonUnix = Join-Path $venvDir "bin/python"
$activePython = if (Test-Path $venvPython) { $venvPython } elseif (Test-Path $venvPythonUnix) { $venvPythonUnix } else { $pythonCmd }

# ── 3. Install backend dependencies ─────────────────────────────────────

Info "Installing Python dependencies..."
& $activePython -m pip install -r (Join-Path $root "requirements.txt") --quiet
if ($LASTEXITCODE -ne 0) { Err "pip install failed."; exit 1 }
Ok "Backend dependencies installed."

# ── 4. Validate .env ────────────────────────────────────────────────────

$envFile = Join-Path $root ".env"
$envExample = Join-Path $root ".env.example"

if (-not (Test-Path $envFile)) {
  if (Test-Path $envExample) {
    Copy-Item $envExample $envFile
    Warn ".env created from .env.example -- fill in your secrets before continuing."
  } else {
    Err "No .env file found. Create one with the required variables."
    Err "Required: DATABASE_URL, GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, GITHUB_WEBHOOK_SECRET, JWT_SECRET"
    exit 1
  }
}

$requiredVars = @("DATABASE_URL", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "GITHUB_WEBHOOK_SECRET", "JWT_SECRET")
$envContent = Get-Content $envFile -Raw
$missingVars = @()
foreach ($v in $requiredVars) {
  if ($envContent -notmatch "(?m)^$v\s*=\s*.+") {
    $missingVars += $v
  }
}

if ($missingVars.Count -gt 0) {
  Err "Missing or empty vars in .env: $($missingVars -join ', ')"
  Err "Edit .env and fill in these values, then re-run boot.ps1."
  exit 1
}

Ok ".env validated -- all required variables present."

# ── 5. Database migration ───────────────────────────────────────────────

$migrationFile = Join-Path $root "db/migrations/001_initial_schema.sql"

if (Test-Path $migrationFile) {
  Info "Running database migration..."
  $dbUrl = ""
  $match = Select-String -Path $envFile -Pattern '^DATABASE_URL\s*=\s*(.+)' -ErrorAction SilentlyContinue
  if ($match) { $dbUrl = $match.Matches[0].Groups[1].Value.Trim().Trim('"').Trim("'") }

  if ($dbUrl -and $psqlCmd) {
    & psql $dbUrl -f $migrationFile 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Ok "Migration applied." }
    else { Warn "Migration may have already been applied (tables exist)." }
  } else {
    Warn "Cannot run migration automatically."
    Warn "Run: psql \`$DATABASE_URL -f db/migrations/001_initial_schema.sql"
  }
} else {
  Warn "Migration file not found at db/migrations/001_initial_schema.sql"
}

if ($MigrateOnly) {
  Ok "Migration complete. Exiting."
  exit 0
}

# ── 6. Frontend setup ───────────────────────────────────────────────────

$webDir = Join-Path $root "web"

if (-not $SkipFrontend -and (Test-Path $webDir)) {
  Info "Installing frontend dependencies..."
  Push-Location $webDir
  & npm install --silent 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) { Err "npm install failed."; Pop-Location; exit 1 }
  Ok "Frontend dependencies installed."
  Pop-Location
}

# ── 7. Start servers ────────────────────────────────────────────────────

Info ""
Info "Starting ForgeGuard..."

if (-not $SkipFrontend -and (Test-Path $webDir)) {
  Info "Starting frontend dev server on port 5174..."
  $frontendJob = Start-Job -ScriptBlock {
    param($dir)
    Set-Location $dir
    & npm run dev 2>&1
  } -ArgumentList $webDir
  Info "Frontend started (background job $($frontendJob.Id))."
}

Info "Starting backend server on port 8000..."
Info "Press Ctrl+C to stop."

# Open the frontend in the default browser after a short delay
Start-Job -ScriptBlock {
  Start-Sleep -Seconds 3
  Start-Process "http://localhost:5174"
} | Out-Null

& $activePython -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
