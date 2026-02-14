# scripts/watch_audit.ps1
# Continuous file watcher that auto-triggers the audit script when evidence files change.
# Runs in a background terminal (or separate VS Code instance) alongside the builder.
#
# Usage:
#   pwsh -File .\Forge\scripts\watch_audit.ps1
#   pwsh -File .\Forge\scripts\watch_audit.ps1 -WatchPath "Forge/evidence"
#   pwsh -File .\Forge\scripts\watch_audit.ps1 -Trigger "updatedifflog.md"
#   pwsh -File .\Forge\scripts\watch_audit.ps1 -DryRun
#
# What it does:
#   1. Watches the evidence/ directory for file changes.
#   2. When updatedifflog.md (or a custom trigger file) is written, it:
#      a. Parses the diff log to extract the files-changed list and phase.
#      b. Runs run_audit.ps1 with those parameters.
#      c. Logs the result to the console with a timestamp.
#   3. Debounces rapid writes (500ms) to avoid duplicate triggers.
#   4. Runs indefinitely until Ctrl+C.
#
# Exit codes:
#   0 -- Clean shutdown (Ctrl+C).
#   1 -- Setup error.

[CmdletBinding()]
param(
  [string]$WatchPath = "",
  [string]$Trigger = "updatedifflog.md",
  [int]$DebounceMs = 2000,
  [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Helpers ──────────────────────────────────────────────────────────────────

function Info([string]$m)  { Write-Host "[watch] $(Get-Date -Format 'HH:mm:ss') $m" -ForegroundColor Cyan }
function Warn([string]$m)  { Write-Host "[watch] $(Get-Date -Format 'HH:mm:ss') $m" -ForegroundColor Yellow }
function Good([string]$m)  { Write-Host "[watch] $(Get-Date -Format 'HH:mm:ss') $m" -ForegroundColor Green }
function Bad([string]$m)   { Write-Host "[watch] $(Get-Date -Format 'HH:mm:ss') $m" -ForegroundColor Red }
function Dim([string]$m)   { Write-Host "[watch] $(Get-Date -Format 'HH:mm:ss') $m" -ForegroundColor DarkGray }

function ParseDiffLogForFiles([string]$diffLogPath) {
  # Extract the files-changed list from updatedifflog.md
  # Supports multiple section header formats:
  #   ## Files Changed      (original format)
  #   ## Files Created       (table format)
  #   ## Files Modified      (table format)
  # Extracts from both bullet lists ("- filename") and markdown tables ("| filename | ... |")
  if (-not (Test-Path $diffLogPath)) { return ,@() }

  $content = Get-Content $diffLogPath
  $inFilesSection = $false
  $files = @()
  $skipTableHeader = 0

  foreach ($line in $content) {
    if ($line -match '^\s*##\s+Files\s+(Changed|Created|Modified)') {
      $inFilesSection = $true
      $skipTableHeader = 0
      continue
    }
    if ($inFilesSection) {
      if ($line -match '^\s*##\s') {
        # Hit the next section header -- stop this section but keep scanning for more
        $inFilesSection = $false
        continue
      }
      # Skip markdown table header row and separator
      if ($line -match '^\s*\|\s*(File|----)') {
        continue
      }
      # Bullet list format: - filename
      if ($line -match '^\s*-\s+(.+)$') {
        $f = $Matches[1].Trim()
        if ($f -ne "(none detected)" -and $f -ne "(none)") {
          $files += $f
        }
      }
      # Table format: | filename | description |
      if ($line -match '^\s*\|\s*([^|]+)\s*\|') {
        $f = $Matches[1].Trim()
        # Skip empty cells and placeholder text
        if ($f -and $f -ne "File" -and $f -ne "----" -and $f -notmatch '^-+$') {
          $files += $f
        }
      }
    }
  }

  if ($files.Count -eq 0) { return @() }
  return $files
}

function ParseDiffLogForPhase([string]$diffLogPath) {
  # Try to extract a phase identifier from the diff log summary or metadata
  if (-not (Test-Path $diffLogPath)) { return "unknown" }

  $content = Get-Content $diffLogPath -Raw

  # Look for "Phase N" or "Phase N --" patterns
  if ($content -match '(?i)Phase\s+(\d+)\s*[\---:]?\s*([^\r\n]*)') {
    $phaseNum = $Matches[1]
    $phaseName = $Matches[2].Trim()
    if ($phaseName) {
      return "Phase $phaseNum -- $phaseName"
    }
    return "Phase $phaseNum"
  }

  return "unknown"
}

# ── Resolve paths ────────────────────────────────────────────────────────────

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$govRoot = Split-Path -Parent $scriptDir  # Forge/ governance folder

# Project root: git root if available, else governance root
try {
  $gitRoot = (& git rev-parse --show-toplevel 2>$null)
  if ($LASTEXITCODE -eq 0 -and $gitRoot) {
    $projectRoot = $gitRoot.Trim()
  } else {
    $projectRoot = $govRoot
  }
} catch {
  $projectRoot = $govRoot
}

# Resolve watch path
if ($WatchPath -eq "") {
  $resolvedWatchPath = Join-Path $govRoot "evidence"
} else {
  $resolvedWatchPath = Resolve-Path $WatchPath -ErrorAction SilentlyContinue
  if (-not $resolvedWatchPath) {
    $resolvedWatchPath = Join-Path $projectRoot $WatchPath
  }
}

if (-not (Test-Path $resolvedWatchPath)) {
  New-Item -ItemType Directory -Force -Path $resolvedWatchPath | Out-Null
  Info "Created watch directory: $resolvedWatchPath"
}

$auditScript = Join-Path $scriptDir "run_audit.ps1"
if (-not (Test-Path $auditScript)) {
  Bad "run_audit.ps1 not found at: $auditScript"
  exit 1
}

# ── Banner ───────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  ╔══════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║         FORGE AUDIT WATCHER -- ACTIVE             ║" -ForegroundColor Cyan
Write-Host "  ╠══════════════════════════════════════════════════╣" -ForegroundColor Cyan
Write-Host "  ║  Watching:  $($resolvedWatchPath | Split-Path -Leaf)/" -ForegroundColor White
Write-Host "  ║  Trigger:   $Trigger" -ForegroundColor White
Write-Host "  ║  Debounce:  ${DebounceMs}ms" -ForegroundColor White
Write-Host "  ║  Gov root:  $govRoot" -ForegroundColor DarkGray
Write-Host "  ║  Proj root: $projectRoot" -ForegroundColor DarkGray
if ($DryRun) {
  Write-Host "  ║  Mode:      DRY RUN (parse only, no audit)" -ForegroundColor Yellow
} else {
  Write-Host "  ║  Mode:      LIVE (will run audit on trigger)" -ForegroundColor Green
}
Write-Host "  ╠══════════════════════════════════════════════════╣" -ForegroundColor Cyan
Write-Host "  ║  Ctrl+X  = manual audit trigger                  ║" -ForegroundColor White
Write-Host "  ║  Ctrl+C  = stop watcher                         ║" -ForegroundColor DarkGray
Write-Host "  ╚══════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── State ────────────────────────────────────────────────────────────────────

$lastTriggerTime = [DateTime]::MinValue
$auditCount = 0
$passCount = 0
$failCount = 0

# ── File watcher setup ───────────────────────────────────────────────────────

$watcher = New-Object System.IO.FileSystemWatcher
$watcher.Path = $resolvedWatchPath
$watcher.Filter = $Trigger
$watcher.NotifyFilter = [System.IO.NotifyFilters]::LastWrite -bor [System.IO.NotifyFilters]::FileName
$watcher.IncludeSubdirectories = $false
$watcher.EnableRaisingEvents = $false  # We'll poll with WaitForChanged

Info "Watcher ready. Waiting for '$Trigger' to be written..."
Dim "Builder can work freely -- this watcher is passive and read-only."
Write-Host ""

# ── Main loop ────────────────────────────────────────────────────────────────

try {
  $watcher.EnableRaisingEvents = $true

  while ($true) {
    # Use WaitForChanged with a timeout so Ctrl+C works
    $result = $watcher.WaitForChanged([System.IO.WatcherChangeTypes]::Changed, 1000)

    # Check for manual trigger keypress (Ctrl+X)
    if ([Console]::KeyAvailable) {
      $key = [Console]::ReadKey($true)
      if ($key.Modifiers -band [ConsoleModifiers]::Control -and $key.Key -eq [ConsoleKey]::X) {
        Write-Host ""
        Write-Host "  ┌──────────────────────────────────────────────────┐" -ForegroundColor Magenta
        Info "MANUAL TRIGGER: Ctrl+X pressed"
        Write-Host "  └──────────────────────────────────────────────────┘" -ForegroundColor Magenta

        $diffLogPath = Join-Path $resolvedWatchPath $Trigger
        $claimedFiles = @(ParseDiffLogForFiles $diffLogPath)
        $phase = ParseDiffLogForPhase $diffLogPath

        if (-not $claimedFiles -or $claimedFiles.Count -eq 0) {
          Warn "No files found in diff log. Cannot run manual audit without a file list."
        } else {
          $claimedFilesStr = $claimedFiles -join ", "
          Info "Phase:  $phase"
          Info "Files:  $claimedFilesStr"

          if ($DryRun) {
            Good "DRY RUN -- would run: run_audit.ps1 -ClaimedFiles `"$claimedFilesStr`" -Phase `"$phase`""
          } else {
            $auditCount++
            Info "Running manual audit #$auditCount..."
            Write-Host ""
            try {
              $auditOutput = & pwsh -File $auditScript -ClaimedFiles $claimedFilesStr -Phase $phase 2>&1
              $auditExit = $LASTEXITCODE
              foreach ($line in $auditOutput) {
                $lineStr = "$line"
                if ($lineStr -match 'PASS') { Write-Host "  $lineStr" -ForegroundColor Green }
                elseif ($lineStr -match 'FAIL') { Write-Host "  $lineStr" -ForegroundColor Red }
                elseif ($lineStr -match 'WARN') { Write-Host "  $lineStr" -ForegroundColor Yellow }
                else { Write-Host "  $lineStr" -ForegroundColor Gray }
              }
              Write-Host ""
              if ($auditExit -eq 0) {
                $passCount++
                Good "AUDIT #$auditCount RESULT: ALL PASS [$passCount pass / $failCount fail total]"
              } else {
                $failCount++
                Bad  "AUDIT #$auditCount RESULT: FAIL (exit $auditExit) [$passCount pass / $failCount fail total]"
              }
            } catch {
              $failCount++
              Bad "AUDIT #$auditCount ERROR: $_"
            }
          }
        }
        Write-Host ""
        Dim "Resuming watch..."
        continue
      }
    }

    if ($result.TimedOut) {
      continue
    }

    # Debounce: skip if triggered too recently
    $now = [DateTime]::Now
    $elapsed = ($now - $lastTriggerTime).TotalMilliseconds
    if ($elapsed -lt $DebounceMs) {
      Dim "Debounced (${elapsed}ms since last trigger)"
      continue
    }
    $lastTriggerTime = $now

    # Small delay to let the writer finish flushing
    Start-Sleep -Milliseconds 300

    Write-Host ""
    Write-Host "  ┌──────────────────────────────────────────────────┐" -ForegroundColor Yellow
    Info "TRIGGER DETECTED: $Trigger changed"
    Write-Host "  └──────────────────────────────────────────────────┘" -ForegroundColor Yellow

    # Parse the diff log
    $diffLogPath = Join-Path $resolvedWatchPath $Trigger
    $claimedFiles = @(ParseDiffLogForFiles $diffLogPath)
    $phase = ParseDiffLogForPhase $diffLogPath

    if (-not $claimedFiles -or $claimedFiles.Count -eq 0) {
      Warn "No files found in diff log. Checking if Status is IN_PROCESS..."
      $dlContent = Get-Content $diffLogPath -Raw -ErrorAction SilentlyContinue
      if ($dlContent -match '(?i)Status:\s*IN_PROCESS') {
        Dim "Diff log status is IN_PROCESS -- builder is mid-cycle. Skipping audit."
        Dim "Will re-trigger when Status changes to COMPLETE."
        continue
      }
      Warn "Could not parse files from diff log. Skipping audit."
      continue
    }

    $claimedFilesStr = $claimedFiles -join ", "
    Info "Phase:  $phase"
    Info "Files:  $claimedFilesStr"

    if ($DryRun) {
      Good "DRY RUN -- would run: run_audit.ps1 -ClaimedFiles `"$claimedFilesStr`" -Phase `"$phase`""
      continue
    }

    # Run the audit
    $auditCount++
    Info "Running audit #$auditCount..."
    Write-Host ""

    try {
      $auditOutput = & pwsh -File $auditScript -ClaimedFiles $claimedFilesStr -Phase $phase 2>&1
      $auditExit = $LASTEXITCODE

      # Display the audit output
      foreach ($line in $auditOutput) {
        $lineStr = "$line"
        if ($lineStr -match 'PASS') {
          Write-Host "  $lineStr" -ForegroundColor Green
        } elseif ($lineStr -match 'FAIL') {
          Write-Host "  $lineStr" -ForegroundColor Red
        } elseif ($lineStr -match 'WARN') {
          Write-Host "  $lineStr" -ForegroundColor Yellow
        } else {
          Write-Host "  $lineStr" -ForegroundColor Gray
        }
      }

      Write-Host ""
      if ($auditExit -eq 0) {
        $passCount++
        Good "AUDIT #$auditCount RESULT: ALL PASS [$passCount pass / $failCount fail total]"
      } else {
        $failCount++
        Bad  "AUDIT #$auditCount RESULT: FAIL (exit $auditExit) [$passCount pass / $failCount fail total]"
      }
    } catch {
      $failCount++
      Bad "AUDIT #$auditCount ERROR: $_"
    }

    Write-Host ""
    Dim "Resuming watch..."
  }
} catch {
  if ($_.Exception.Message -match 'pipeline has been stopped') {
    # Ctrl+C -- clean shutdown
    Write-Host ""
  } else {
    Bad "Watcher error: $_"
  }
} finally {
  $watcher.EnableRaisingEvents = $false
  $watcher.Dispose()

  Write-Host ""
  Write-Host "  ╔══════════════════════════════════════════════════╗" -ForegroundColor Cyan
  Write-Host "  ║         FORGE AUDIT WATCHER -- STOPPED            ║" -ForegroundColor Cyan
  Write-Host "  ╠══════════════════════════════════════════════════╣" -ForegroundColor Cyan
  Write-Host "  ║  Total audits:  $auditCount" -ForegroundColor White
  Write-Host "  ║  Passed:        $passCount" -ForegroundColor Green
  Write-Host "  ║  Failed:        $failCount" -ForegroundColor $(if ($failCount -gt 0) { "Red" } else { "Green" })
  Write-Host "  ╚══════════════════════════════════════════════════╝" -ForegroundColor Cyan
  Write-Host ""
}
