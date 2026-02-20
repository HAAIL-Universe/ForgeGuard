<#
.SYNOPSIS
    Move VS Code extensions and user data from C: to Z: using NTFS symlinks.
.DESCRIPTION
    1. Cleans caches, logs, crash dumps from VS Code data folder
    2. Moves .vscode\extensions and AppData\Roaming\Code to Z:\VSCodeData
    3. Creates NTFS symlinks so VS Code finds everything transparently
.NOTES
    MUST be run as Administrator (symlinks require it on some Windows configs).
    CLOSE VS CODE COMPLETELY before running this script.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Info([string]$m)  { Write-Host "[migrate] $m" -ForegroundColor Cyan }
function Warn([string]$m)  { Write-Host "[migrate] $m" -ForegroundColor Yellow }
function Ok([string]$m)    { Write-Host "[migrate] $m" -ForegroundColor Green }
function Err([string]$m)   { Write-Host "[migrate] $m" -ForegroundColor Red }

# ── Paths ──────────────────────────────────────────────────────────────────

$extSrc   = "$env:USERPROFILE\.vscode\extensions"
$dataSrc  = "$env:APPDATA\Code"

$destRoot = "Z:\VSCodeData"
$extDest  = "$destRoot\extensions"
$dataDest = "$destRoot\Code"

# ── Pre-flight checks ─────────────────────────────────────────────────────

# Check VS Code is not running
$vsProcs = Get-Process -Name "Code" -ErrorAction SilentlyContinue
if ($vsProcs) {
    Err "VS Code is still running! Close it completely, then re-run this script."
    Err "Processes found: $($vsProcs.Count)"
    exit 1
}

# Check Z: exists
if (-not (Test-Path "Z:\")) {
    Err "Z: drive not found. Make sure it's mounted."
    exit 1
}

# Check source folders exist
if (-not (Test-Path $extSrc)) { Err "Extensions folder not found: $extSrc"; exit 1 }
if (-not (Test-Path $dataSrc)) { Err "Data folder not found: $dataSrc"; exit 1 }

# Check they're not already symlinks
if ((Get-Item $extSrc).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    Warn "$extSrc is already a symlink — skipping extensions."
    $skipExt = $true
} else { $skipExt = $false }

if ((Get-Item $dataSrc).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    Warn "$dataSrc is already a symlink — skipping data."
    $skipData = $true
} else { $skipData = $false }

if ($skipExt -and $skipData) {
    Ok "Both folders are already symlinks. Nothing to do!"
    exit 0
}

# ── Step 1: Clean caches before moving ─────────────────────────────────────

Info "Step 1: Cleaning caches and logs..."

$cleanDirs = @(
    "$dataSrc\logs",
    "$dataSrc\Crashpad",
    "$dataSrc\CachedData",
    "$dataSrc\Cache",
    "$dataSrc\GPUCache",
    "$dataSrc\DawnWebGPUCache",
    "$dataSrc\DawnGraphiteCache",
    "$dataSrc\Code Cache",
    "$dataSrc\Service Worker",
    "$dataSrc\CachedExtensionVSIXs"
)

$freed = 0
foreach ($dir in $cleanDirs) {
    if (Test-Path $dir) {
        $size = (Get-ChildItem $dir -Recurse -ErrorAction SilentlyContinue |
                 Measure-Object Length -Sum -ErrorAction SilentlyContinue).Sum
        Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
        $freed += $size
        Info "  Cleaned: $(Split-Path $dir -Leaf) ($([math]::Round($size/1MB)) MB)"
    }
}
Ok "Freed $([math]::Round($freed/1MB)) MB from caches."

# Also trim workspaceStorage entries older than 30 days
$wsDir = "$dataSrc\User\workspaceStorage"
if (Test-Path $wsDir) {
    $cutoff = (Get-Date).AddDays(-30)
    $oldWorkspaces = Get-ChildItem $wsDir -Directory |
        Where-Object { $_.LastWriteTime -lt $cutoff }
    $wsFreed = 0
    foreach ($ws in $oldWorkspaces) {
        $size = (Get-ChildItem $ws.FullName -Recurse -ErrorAction SilentlyContinue |
                 Measure-Object Length -Sum -ErrorAction SilentlyContinue).Sum
        Remove-Item $ws.FullName -Recurse -Force -ErrorAction SilentlyContinue
        $wsFreed += $size
    }
    if ($oldWorkspaces.Count -gt 0) {
        Ok "Cleaned $($oldWorkspaces.Count) old workspace caches ($([math]::Round($wsFreed/1MB)) MB)"
    }
}

# ── Step 2: Create destination ─────────────────────────────────────────────

Info "Step 2: Creating destination on Z:..."
if (-not (Test-Path $destRoot)) { New-Item $destRoot -ItemType Directory | Out-Null }

# ── Step 3: Move extensions ───────────────────────────────────────────────

if (-not $skipExt) {
    Info "Step 3a: Moving extensions to $extDest ..."
    if (Test-Path $extDest) {
        Warn "Destination already exists — merging..."
    }
    # Use robocopy for reliable move (handles long paths, retries)
    & robocopy $extSrc $extDest /E /MOVE /R:2 /W:1 /NP /NFL /NDL /NJH /NJS | Out-Null

    # Remove source if robocopy left it (it should be empty)
    if (Test-Path $extSrc) { Remove-Item $extSrc -Recurse -Force -ErrorAction SilentlyContinue }

    # Create symlink
    Info "Creating symlink: $extSrc -> $extDest"
    New-Item -ItemType Junction -Path $extSrc -Target $extDest | Out-Null
    Ok "Extensions moved and symlinked."
} else {
    Info "Step 3a: Skipped (already symlinked)."
}

# ── Step 4: Move user data ────────────────────────────────────────────────

if (-not $skipData) {
    Info "Step 3b: Moving user data to $dataDest ..."
    if (Test-Path $dataDest) {
        Warn "Destination already exists — merging..."
    }
    & robocopy $dataSrc $dataDest /E /MOVE /R:2 /W:1 /NP /NFL /NDL /NJH /NJS | Out-Null

    if (Test-Path $dataSrc) { Remove-Item $dataSrc -Recurse -Force -ErrorAction SilentlyContinue }

    Info "Creating symlink: $dataSrc -> $dataDest"
    New-Item -ItemType Junction -Path $dataSrc -Target $dataDest | Out-Null
    Ok "User data moved and symlinked."
} else {
    Info "Step 3b: Skipped (already symlinked)."
}

# ── Done ──────────────────────────────────────────────────────────────────

Write-Host ""
Ok "=========================================="
Ok "  Migration complete!"
Ok "=========================================="
Write-Host ""
Info "Extensions: $extSrc -> $extDest"
Info "User data:  $dataSrc -> $dataDest"
Write-Host ""
Info "You can now open VS Code. Everything will work as before."
Info "To verify: run  Get-Item '$extSrc' | Select-Object Attributes,Target"
