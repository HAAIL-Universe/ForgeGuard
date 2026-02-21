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
#   pwsh -File boot.ps1 -TestOnly       # run pytest then exit
#   pwsh -File boot.ps1 -Check          # ruff + mypy then exit

[CmdletBinding()]
param(
  [switch]$SkipFrontend,
  [switch]$MigrateOnly,
  [switch]$TestOnly,      # Run all tests then exit
  [switch]$Check           # Lint + type-check then exit
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

# -- 1. Check prerequisites ----------------------------------------------

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

# -- 2. Python virtual environment ----------------------------------------

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

# -- 3. Install backend dependencies -------------------------------------

Info "Installing Python dependencies..."
$ErrorActionPreference = "Continue"
& $activePython -m pip install -r (Join-Path $root "requirements.txt") --quiet 2>&1 | Out-Null
$ErrorActionPreference = "Stop"
if ($LASTEXITCODE -ne 0) { Err "pip install failed."; exit 1 }
Ok "Backend dependencies installed."

# -- 4. Validate .env ----------------------------------------------------

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

# -- 5. Database migration -----------------------------------------------

$migrationDir = Join-Path $root "db/migrations"
$migrationFiles = Get-ChildItem -Path $migrationDir -Filter "*.sql" -ErrorAction SilentlyContinue | Sort-Object Name
$pyMigrate = Join-Path $root "_migrate.py"

if ($migrationFiles.Count -gt 0) {
  Info "Running database migrations ($($migrationFiles.Count) files)..."
  $dbUrl = ""
  $match = Select-String -Path $envFile -Pattern '^DATABASE_URL\s*=\s*(.+)' -ErrorAction SilentlyContinue
  if ($match) { $dbUrl = $match.Matches[0].Groups[1].Value.Trim().Trim('"').Trim("'") }

  if ($dbUrl -and $psqlCmd) {
    # -- Preferred: run via psql --
    $failCount = 0
    foreach ($mf in $migrationFiles) {
      & psql $dbUrl -f $mf.FullName 2>&1 | Out-Null
      if ($LASTEXITCODE -ne 0) { $failCount++ }
    }
    if ($failCount -eq 0) { Ok "All $($migrationFiles.Count) migrations applied." }
    else { Warn "$failCount migration(s) may have already been applied." }
  } elseif ($dbUrl) {
    # -- Fallback: run via Python + asyncpg --
    Info "psql not found -- running migrations via Python..."
    if (Test-Path $pyMigrate) {
      & $activePython $pyMigrate $dbUrl @($migrationFiles | ForEach-Object { $_.FullName })
      if ($LASTEXITCODE -eq 0) { Ok "All migrations applied via Python." }
      else { Warn "Some migrations may have failed -- check output above." }
    } else {
      # Inline one-shot: read each .sql and execute via asyncpg
      $migScript = @'
import sys, asyncio, asyncpg
async def main():
    url = sys.argv[1]
    files = sys.argv[2:]
    conn = await asyncpg.connect(url)
    fail = 0
    for f in files:
        try:
            sql = open(f, encoding='utf-8').read()
            await conn.execute(sql)
        except Exception as e:
            print(f'  [warn] {f}: {e}')
            fail += 1
    await conn.close()
    if fail: print(f'{fail} migration(s) may have already been applied.')
    else: print(f'All {len(files)} migrations applied.')
asyncio.run(main())
'@
      & $activePython -c $migScript $dbUrl @($migrationFiles | ForEach-Object { $_.FullName })
      if ($LASTEXITCODE -eq 0) { Ok "Migrations applied via Python/asyncpg." }
      else { Warn "Some migrations may have failed -- check output above." }
    }
  } else {
    Warn "Cannot run migrations -- DATABASE_URL not found in .env"
  }
} else {
  Warn "No migration files found in db/migrations/"
}

if ($MigrateOnly) {
  Ok "Migration complete. Exiting."
  exit 0
}

# -- 5b. -TestOnly: run pytest then exit ----------------------------------

if ($TestOnly) {
  Info "Running test suite..."
  & $activePython -m pytest tests/ -q
  $code = $LASTEXITCODE
  if ($code -eq 0) { Ok "All tests passed." }
  else { Err "Tests failed (exit code $code)." }
  exit $code
}

# -- 5c. -Check: lint + type-check then exit ------------------------------

if ($Check) {
  $fail = $false
  Info "Running ruff check..."
  & $activePython -m ruff check .
  if ($LASTEXITCODE -ne 0) { $fail = $true }

  Info "Running ruff format --check..."
  & $activePython -m ruff format --check .
  if ($LASTEXITCODE -ne 0) { $fail = $true }

  Info "Running mypy..."
  & $activePython -m mypy app/ --ignore-missing-imports
  if ($LASTEXITCODE -ne 0) { $fail = $true }

  if ($fail) { Err "Quality checks failed."; exit 1 }
  Ok "All quality checks passed."
  exit 0
}

# -- 6. Frontend setup ---------------------------------------------------

$webDir = Join-Path $root "web"

if (-not $SkipFrontend -and (Test-Path $webDir)) {
  Info "Installing frontend dependencies..."
  Push-Location $webDir
  & npm install --silent 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) { Err "npm install failed."; Pop-Location; exit 1 }
  Ok "Frontend dependencies installed."
  Pop-Location
}

# -- 7. Start servers ----------------------------------------------------

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

# -- Port conflict check -------------------------------------------------
$conflictPids = @(netstat -ano 2>$null |
  Select-String '^\s+TCP\s+\S+:8000\s+\S+\s+LISTENING\s+(\d+)' |
  ForEach-Object { $_.Matches[0].Groups[1].Value } |
  Sort-Object -Unique)

if ($conflictPids.Count -gt 0) {
  Warn "Port 8000 is already in use by PID(s): $($conflictPids -join ', ')"
  Warn "This usually means a previous server session is still running."
  Warn "Run: taskkill /PID $($conflictPids -join ' /PID ') /F"
  Warn "Then re-run boot.ps1, or the new server may not respond to requests."
}
# ------------------------------------------------------------------------

Info "Press Ctrl+C to stop."

# Open the frontend in the default browser after a short delay
Start-Job -ScriptBlock {
  Start-Sleep -Seconds 3
  Start-Process "http://localhost:5174"
} | Out-Null

$ErrorActionPreference = "Continue"
& $activePython -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir app
