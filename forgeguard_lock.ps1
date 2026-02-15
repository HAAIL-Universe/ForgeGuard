<#
ForgeGuard Lock Toggle
Locks/unlocks:
  - .\Forge\scripts\ (read+execute only)
  - .\Forge\contracts\builder_contract.*
  - .\Forge\contracts\auditor_prompt.*

Usage:
  .\forgeguard_lock.ps1 -Lock
  .\forgeguard_lock.ps1 -Unlock
  .\forgeguard_lock.ps1 -Status
#>

[CmdletBinding()]
param(
  [switch]$Lock,
  [switch]$Unlock,
  [switch]$Status
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-RepoRoot {
  # Prefer git if available, else fall back to script location.
  try {
    $gitRoot = (git rev-parse --show-toplevel 2>$null).Trim()
    if ($gitRoot) { return $gitRoot }
  } catch { }
  return $PSScriptRoot
}

function Get-UserPrincipal {
  if ($env:USERDOMAIN) { return "$env:USERDOMAIN\$env:USERNAME" }
  return "$env:USERNAME"
}

function Require-Path($p) {
  if (-not (Test-Path $p)) { throw "Missing path: $p (are you in the ForgeGuard repo root?)" }
}

$root = Get-RepoRoot
$u    = Get-UserPrincipal

$scriptsPath   = Join-Path $root "Forge\scripts"
$contractsPath = Join-Path $root "Forge\contracts"

Require-Path $scriptsPath
Require-Path $contractsPath

# Target files (by prefix, any extension)
$contractFiles = Get-ChildItem $contractsPath -File -Force |
  Where-Object { $_.Name -match '^builder_contract\.' -or $_.Name -match '^auditor_prompt\.' }

if (-not $contractFiles -or $contractFiles.Count -eq 0) {
  Write-Warning "No contract files matched in $contractsPath (expected builder_contract.* and auditor_prompt.*)"
}

function Show-Status {
  Write-Host ""
  Write-Host "=== STATUS ==="
  icacls $scriptsPath | Write-Host

  foreach ($f in $contractFiles) {
    icacls $f.FullName | Write-Host
  }
  Write-Host "==============" 
  Write-Host ""
}

if ($Status -or (-not $Lock -and -not $Unlock)) {
  Show-Status
  return
}

if ($Lock) {
  Write-Host "Locking ForgeGuard targets as read-only..."

  # Folder lock: Read + Execute only (recursive via OI/CI)
  icacls $scriptsPath /inheritance:r /grant:r "${u}:(OI)(CI)RX" | Out-Null

  # File lock: Read-only
  foreach ($f in $contractFiles) {
    icacls $f.FullName /inheritance:r /grant:r "${u}:R" | Out-Null
  }

  Show-Status
  Write-Host "Locked."
  return
}

if ($Unlock) {
  Write-Host "Unlocking ForgeGuard targets (reset to inherited permissions)..."

  icacls $scriptsPath /reset /t | Out-Null

  foreach ($f in $contractFiles) {
    icacls $f.FullName /reset | Out-Null
  }

  Show-Status
  Write-Host "Unlocked."
  return
}
