# scripts/overwrite_diff_log.ps1
# Overwrite the diff log with a structured Markdown entry.
# Default: STAGED changes only (recommended for scoped, minimal diffs).
#
# Usage:
#   git add <scoped files>
#   .\scripts\overwrite_diff_log.ps1 -Status COMPLETE `
#     -Summary @("Did X", "Did Y") `
#     -Verification @("compileall: pass", "pytest: pass") `
#     -Notes @("None") `
#     -NextSteps @("Next: do Z")
#
# Unstaged (not recommended):
#   .\scripts\overwrite_diff_log.ps1 -IncludeUnstaged
#
# Finalize (check for remaining TODOs):
#   .\scripts\overwrite_diff_log.ps1 -Finalize
#
# Open the log in VS Code:
#   .\scripts\overwrite_diff_log.ps1 -OpenInVSCode

[CmdletBinding()]
param(
  [ValidateSet("IN_PROCESS","COMPLETE","BLOCKED")]
  [string]$Status = "IN_PROCESS",

  [string[]]$Summary = @(),
  [string[]]$Verification = @(),
  [string[]]$Notes = @(),
  [string[]]$NextSteps = @(),

  [int]$MaxDiffLines = 500,

  [switch]$Finalize,
  [switch]$IncludeUnstaged,
  [switch]$OpenInVSCode
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Info([string]$m) { Write-Host "[overwrite_diff_log] $m" -ForegroundColor Cyan }
function Warn([string]$m) { Write-Host "[overwrite_diff_log] $m" -ForegroundColor Yellow }
function Err ([string]$m) { Write-Host "[overwrite_diff_log] $m" -ForegroundColor Red }

function HasCmd([string]$name) {
  return $null -ne (Get-Command $name -ErrorAction SilentlyContinue)
}

function RequireGit {
  if (-not (HasCmd "git")) { throw "git not found on PATH." }
  $ok = & git rev-parse --is-inside-work-tree 2>$null
  if ($LASTEXITCODE -ne 0 -or $ok.Trim() -ne "true") {
    throw "Not inside a git repo. Run from the repo (or a subdir)."
  }
}

function RepoRoot {
  return (& git rev-parse --show-toplevel).Trim()
}

function ResolveLogPath([string]$govRoot) {
  $pEvidence = Join-Path $govRoot "evidence\updatedifflog.md"
  if (Test-Path $pEvidence) { return $pEvidence }
  # Default: create in evidence folder
  return $pEvidence
}

function EnsureParent([string]$path) {
  $parent = Split-Path -Parent $path
  if ($parent -and -not (Test-Path $parent)) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
  }
}

function Bullets([string[]]$items, [string]$todo) {
  if ($null -eq $items -or $items.Count -eq 0) { return @("- $todo") }
  return @($items | ForEach-Object { "- $_" })
}

function Indent4([string[]]$lines) {
  if ($null -eq $lines -or $lines.Count -eq 0) { return "    (none)" }
  return ($lines -replace '^', '    ') -join "`n"
}

try {
  RequireGit
  $root = RepoRoot
  Set-Location $root

  # Governance root: parent of scripts/ dir (where evidence/ lives)
  $govRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

  $logPath = ResolveLogPath $govRoot
  EnsureParent $logPath

  $timestamp = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssK")
  $branch = (& git rev-parse --abbrev-ref HEAD).Trim()
  $head = (& git rev-parse HEAD).Trim()
  $baseHead = ""
  try {
    $baseHead = (& git rev-parse HEAD^).Trim()
  } catch {
    $baseHead = ""
  }

  if ($Finalize) {
    if (-not (Test-Path $logPath)) {
      Err "Finalize failed: evidence/updatedifflog.md not found at $logPath"
      exit 1
    }
    # Only scan the header portion (above diff hunks) so that git diff
    # output containing prior audit results doesn't cause false positives.
    $dlContent = Get-Content $logPath -Raw
    $hunksIdx = $dlContent.IndexOf('## Minimal Diff Hunks')
    $dlHeader = if ($hunksIdx -ge 0) { $dlContent.Substring(0, $hunksIdx) } else { $dlContent }
    $todoMatches = [regex]::Matches($dlHeader, '(?i)TODO:')
    if ($todoMatches.Count -gt 0) {
      Err "Finalize failed: TODO placeholders remain in diff log header."
      exit 1
    }
    Info "Finalize passed: no TODO placeholders found in header."
    exit 0
  }

  $stagedOnly = -not $IncludeUnstaged
  $basis = if ($stagedOnly) { "staged" } else { "unstaged (working tree)" }

  $nameOnlyArgs = @("diff","--name-only")
  if ($stagedOnly) { $nameOnlyArgs += "--staged" }
  $changedFiles = @((& git @nameOnlyArgs) | Where-Object { $_ -and $_.Trim().Length -gt 0 } | ForEach-Object { $_.Trim() })

  $diffArgs = @("diff","--unified=3")
  if ($stagedOnly) { $diffArgs += "--staged" }
  $patchRaw = & git @diffArgs
  $patchLines = @()
  if ($patchRaw -is [string]) { $patchLines = @($patchRaw -split "`r?`n") }
  else { $patchLines = @($patchRaw | ForEach-Object { "$_" }) }

  $truncated = $false
  $totalPatchLines = $patchLines.Count
  if ($MaxDiffLines -gt 0 -and $patchLines.Count -gt $MaxDiffLines) {
    $patchLines = $patchLines[0..($MaxDiffLines - 1)]
    $truncated = $true
  }

  if ($stagedOnly -and $changedFiles.Count -eq 0) {
    Warn "No staged changes found. Recommended: git add <scoped files> then re-run."
    Warn "If you truly want unstaged, re-run with -IncludeUnstaged."
  }

  $summaryTodo = "TODO: 1-5 bullets (what changed, why, scope)."
  $verificationTodo = "TODO: verification evidence (static -> runtime -> behavior -> contract)."
  $notesTodo = "TODO: blockers, risks, constraints."
  $nextStepsTodo = "TODO: next actions (small, specific)."

  # When Status is COMPLETE, all section parameters are mandatory.
  # Refuse to write a COMPLETE diff log with TODO: placeholders.
  if ($Status -eq "COMPLETE") {
    $missing = @()
    if ($null -eq $Summary -or $Summary.Count -eq 0)       { $missing += "-Summary" }
    if ($null -eq $Verification -or $Verification.Count -eq 0) { $missing += "-Verification" }
    if ($null -eq $Notes -or $Notes.Count -eq 0)            { $missing += "-Notes" }
    if ($null -eq $NextSteps -or $NextSteps.Count -eq 0)    { $missing += "-NextSteps" }
    if ($missing.Count -gt 0) {
      Err "Status is COMPLETE but required parameters are empty: $($missing -join ', ')"
      Err "A COMPLETE diff log cannot contain TODO: placeholders. Supply all section parameters."
      exit 1
    }
  }

  $summaryLines = Bullets -items $Summary -todo $summaryTodo
  $verificationLines = Bullets -items $Verification -todo $verificationTodo
  $notesLines = Bullets -items $Notes -todo $notesTodo
  $nextStepsLines = Bullets -items $NextSteps -todo $nextStepsTodo

  $filesLines = if ($changedFiles.Count -gt 0) { @($changedFiles | ForEach-Object { "- $_" }) } else { @("- (none detected)") }

  $statusShort = (& git status -sb).TrimEnd()
  $statusIndented = Indent4 @($statusShort -split "`r?`n")
  $patchIndented = Indent4 $patchLines

  $baseHeadLabel = if ([string]::IsNullOrWhiteSpace($baseHead)) { "N/A (no parent)" } else { $baseHead }
  $filesSection = ($filesLines -join "`n")
  $summarySection = ($summaryLines -join "`n")
  $verificationSection = ($verificationLines -join "`n")
  $notesSection = ($notesLines -join "`n")
  $nextStepsSection = ($nextStepsLines -join "`n")
  $truncNote = if ($truncated) { "`n    ... ($($totalPatchLines - $MaxDiffLines) lines truncated, $totalPatchLines total)" } else { "" }

  $body = @"
# Diff Log (overwrite each cycle)

## Cycle Metadata
- Timestamp: $timestamp
- Branch: $branch
- HEAD: $head
- BASE_HEAD: $baseHeadLabel
- Diff basis: $basis

## Cycle Status
- Status: $Status

## Summary
$summarySection

## Files Changed ($basis)
$filesSection

## git status -sb
$statusIndented

## Verification
$verificationSection

## Notes (optional)
$notesSection

## Next Steps
$nextStepsSection

## Minimal Diff Hunks
$patchIndented$truncNote

"@

  [System.IO.File]::WriteAllText($logPath, $body, [System.Text.Encoding]::UTF8)

  Info "Wrote diff log (overwritten): $logPath"
  Info ("Files listed: {0}" -f $changedFiles.Count)

  if ($OpenInVSCode) {
    if (HasCmd "code") {
      & code -g ($logPath + ":1") | Out-Null
      Info "Opened in VS Code."
    } else {
      Warn "VS Code CLI 'code' not found on PATH. Skipping open."
    }
  }

  exit 0
}
catch {
  Err $_.Exception.Message
  exit 1
}
